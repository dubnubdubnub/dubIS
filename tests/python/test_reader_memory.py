"""Tests for reader_memory's seven best-effort probes.

Every value used as a fixture here is a *measured* one, quoted verbatim from the design
doc's test matrix rather than invented, so a regression in parsing shows up as a
recognisable number:

    25769803776   RTX 3090 registry HardwareInformation.qwMemorySize (exactly 24 GiB)
    21582418739   \\GPU Adapter Memory(*)\\Dedicated Usage on that machine (20.1 GiB)
    4293918720    the *broken* video-controller AdapterRAM for the same card (~4.0 GiB,
                  a saturated 32-bit DWORD) — present here only so a test can prove the
                  module never asks for it
    536870912     the AMD Radeon integrated GPU on the same machine (0.5 GiB)
    137438953472  hw.memsize on an M4 Max (128 GiB unified)

The two policy rules from the design doc get their own tests, because the deleted
``ca07608`` tiering got both wrong: free-not-total, and largest-adapter-not-first.
"""

from __future__ import annotations

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

import reader_memory

# --- measured fixture values ------------------------------------------------------
VRAM_3090 = 25769803776          # 24 GiB
DEDICATED_USAGE = 21582418739    # 20.1 GiB
BROKEN_ADAPTER_RAM = 4293918720  # the saturated DWORD; never to be consulted
VRAM_AMD_IGPU = 536870912        # 0.5 GiB
MAC_MEMSIZE = 137438953472       # 128 GiB
MIB = 1024 ** 2
GIB = 1024 ** 3


class FakeCompleted:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def fake_run(handler):
    """Build a ``subprocess.run`` stand-in dispatching on argv[0].

    ``handler`` maps argv -> FakeCompleted, or raises to simulate a missing binary.
    """
    def _run(argv, **kwargs):
        return handler(list(argv))
    return _run


def patch_run(monkeypatch, handler):
    monkeypatch.setattr(reader_memory.subprocess, "run", fake_run(handler))


def only(binary, stdout, returncode=0):
    """Handler where ``binary`` answers and every other binary is absent."""
    def _handler(argv):
        if argv[0] == binary:
            return FakeCompleted(stdout, returncode)
        raise FileNotFoundError(argv[0])
    return _handler


# =================================================================== nvidia-smi


def test_nvidia_smi_parses_total_and_used(monkeypatch):
    patch_run(monkeypatch, only("nvidia-smi", "24576, 3277\n"))
    info = reader_memory.probe_nvidia_smi()
    assert info is not None
    assert info.total_bytes == 24576 * MIB
    assert info.free_bytes == (24576 - 3277) * MIB
    assert info.source == "nvidia-smi"
    assert info.unified is False


def test_nvidia_smi_queries_used_not_only_total(monkeypatch):
    """Policy rule 1 at the argv level: the probe must ask for memory.used."""
    seen = []

    def handler(argv):
        seen.append(argv)
        return FakeCompleted("24576, 3277\n")

    patch_run(monkeypatch, handler)
    reader_memory.probe_nvidia_smi()
    query = next(a for a in seen[0] if a.startswith("--query-gpu="))
    assert "memory.total" in query and "memory.used" in query


def test_nvidia_smi_picks_largest_free_not_first(monkeypatch):
    """Policy rule 2: two GPUs, the roomy one is listed second."""
    patch_run(monkeypatch, only("nvidia-smi", "8192, 8000\n24576, 4276\n"))
    info = reader_memory.probe_nvidia_smi()
    assert info is not None
    assert info.adapter == "nvidia:1"
    assert info.total_bytes == 24576 * MIB
    assert info.free_bytes == (24576 - 4276) * MIB


def test_nvidia_smi_missing_binary_returns_none(monkeypatch):
    def handler(argv):
        raise FileNotFoundError(argv[0])

    patch_run(monkeypatch, handler)
    assert reader_memory.probe_nvidia_smi() is None


def test_nvidia_smi_nonzero_exit_returns_none(monkeypatch):
    patch_run(monkeypatch, only("nvidia-smi", "24576, 3277\n", returncode=9))
    assert reader_memory.probe_nvidia_smi() is None


def test_nvidia_smi_timeout_returns_none(monkeypatch):
    def handler(argv):
        raise subprocess.TimeoutExpired(argv, 5.0)

    patch_run(monkeypatch, handler)
    assert reader_memory.probe_nvidia_smi() is None


@pytest.mark.parametrize("garbage", ["", "\n", "not a number\n", "[N/A], [N/A]\n",
                                     "24576\n", "0, 0\n"])
def test_nvidia_smi_garbage_returns_none(monkeypatch, garbage):
    patch_run(monkeypatch, only("nvidia-smi", garbage))
    assert reader_memory.probe_nvidia_smi() is None


# ============================================================ windows registry VRAM


class FakeKey:
    def __init__(self, subkeys=None, values=None):
        self.subkeys = subkeys or {}
        self.values = values or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    """Enough of stdlib ``winreg`` to drive the display-class enumeration."""

    HKEY_LOCAL_MACHINE = object()

    def __init__(self, tree, root_path=None):
        self.tree = tree
        self.root_path = root_path
        self.opened = []

    def OpenKey(self, key, sub):  # noqa: N802 - mirrors the stdlib spelling
        if key is self.HKEY_LOCAL_MACHINE:
            self.opened.append(sub)
            if self.root_path is not None and sub != self.root_path:
                raise OSError("no such key")
            return self.tree
        if sub not in key.subkeys:
            raise OSError("no such key")
        return key.subkeys[sub]

    def QueryInfoKey(self, key):  # noqa: N802
        return (len(key.subkeys), len(key.values), 0)

    def EnumKey(self, key, index):  # noqa: N802
        names = list(key.subkeys)
        if index >= len(names):
            raise OSError("no more items")
        return names[index]

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in key.values:
            raise OSError("no such value")
        return (key.values[name], 11)


QW = reader_memory._QWORD_MEMORY_SIZE

# The measured machine: the 0.5 GiB AMD integrated GPU is instance 0000, the 3090 is
# instance 0001 — index-0 logic on this tree concludes nothing fits.
MAULER_TREE = FakeKey(subkeys={
    "0000": FakeKey(values={QW: VRAM_AMD_IGPU, "AdapterRAM": BROKEN_ADAPTER_RAM}),
    "0001": FakeKey(values={QW: VRAM_3090, "AdapterRAM": BROKEN_ADAPTER_RAM}),
    "Configuration": FakeKey(),  # a real subkey that carries no VRAM value
})


def patch_winreg(monkeypatch, fake):
    monkeypatch.setattr(reader_memory, "_winreg", lambda: fake)


def test_registry_uses_the_display_class_guid(monkeypatch):
    fake = FakeWinreg(MAULER_TREE)
    patch_winreg(monkeypatch, fake)
    reader_memory.probe_windows_registry_vram()
    assert fake.opened
    assert "{4d36e968-e325-11ce-bfc1-08002be10318}" in fake.opened[0]


def test_registry_picks_largest_adapter_not_first(monkeypatch):
    """Policy rule 2 on the measured two-adapter machine."""
    patch_winreg(monkeypatch, FakeWinreg(MAULER_TREE))
    info = reader_memory.probe_windows_registry_vram()
    assert info is not None
    assert info.total_bytes == VRAM_3090
    assert info.adapter == "registry:0001"


def test_registry_reports_capacity_without_pretending_it_is_free(monkeypatch):
    """Policy rule 1: no headroom in the registry means free is unknown, not total."""
    patch_winreg(monkeypatch, FakeWinreg(MAULER_TREE))
    info = reader_memory.probe_windows_registry_vram()
    assert info is not None
    assert info.free_bytes is None


def test_registry_accepts_reg_binary_qword(monkeypatch):
    tree = FakeKey(subkeys={
        "0000": FakeKey(values={QW: VRAM_3090.to_bytes(8, "little")}),
    })
    patch_winreg(monkeypatch, FakeWinreg(tree))
    info = reader_memory.probe_windows_registry_vram()
    assert info is not None
    assert info.total_bytes == VRAM_3090


def test_registry_absent_winreg_returns_none(monkeypatch):
    patch_winreg(monkeypatch, None)
    assert reader_memory.probe_windows_registry_vram() is None


def test_registry_missing_class_key_returns_none(monkeypatch):
    patch_winreg(monkeypatch, FakeWinreg(MAULER_TREE, root_path="some-other-key"))
    assert reader_memory.probe_windows_registry_vram() is None


def test_registry_no_adapter_carries_vram_returns_none(monkeypatch):
    tree = FakeKey(subkeys={"0000": FakeKey(), "Configuration": FakeKey()})
    patch_winreg(monkeypatch, FakeWinreg(tree))
    assert reader_memory.probe_windows_registry_vram() is None


@pytest.mark.parametrize("raw", ["not a number", None, 0, b"", True])
def test_registry_garbage_value_returns_none(monkeypatch, raw):
    patch_winreg(monkeypatch, FakeWinreg(FakeKey(subkeys={"0000": FakeKey(values={QW: raw})})))
    assert reader_memory.probe_windows_registry_vram() is None


# ======================================================== windows dedicated usage

# typeperf's real shape. The instance names are LUIDs, which is exactly why usage
# cannot be attributed to a named adapter.
TYPEPERF_OUT = (
    '"(PDH-CSV 4.0) (Pacific Daylight Time)(420)",'
    '"\\\\MAULER\\GPU Adapter Memory(luid_0x00000000_0x0001078c_phys_0)\\Dedicated Usage",'
    '"\\\\MAULER\\GPU Adapter Memory(luid_0x00000000_0x0000d3f1_phys_0)\\Dedicated Usage"\n'
    f'"08/21/2026 11:02:31.412","{DEDICATED_USAGE}.000000","41943040.000000"\n'
    "\nExiting, please wait...\nThe command completed successfully.\n"
)


def test_dedicated_usage_parses_max_across_luid_instances(monkeypatch):
    patch_run(monkeypatch, only("typeperf", TYPEPERF_OUT))
    assert reader_memory.probe_windows_gpu_dedicated_usage() == DEDICATED_USAGE


def test_dedicated_usage_requests_the_documented_counter(monkeypatch):
    seen = []

    def handler(argv):
        seen.append(argv)
        return FakeCompleted(TYPEPERF_OUT)

    patch_run(monkeypatch, handler)
    reader_memory.probe_windows_gpu_dedicated_usage()
    assert r"\GPU Adapter Memory(*)\Dedicated Usage" in seen[0]


def test_dedicated_usage_missing_typeperf_returns_none(monkeypatch):
    patch_run(monkeypatch, only("nothing-matches", ""))
    assert reader_memory.probe_windows_gpu_dedicated_usage() is None


def test_dedicated_usage_nonzero_exit_returns_none(monkeypatch):
    patch_run(monkeypatch, only("typeperf", TYPEPERF_OUT, returncode=1))
    assert reader_memory.probe_windows_gpu_dedicated_usage() is None


@pytest.mark.parametrize("garbage", ["", "\n", "Error: no valid counters.\n",
                                     '"just one column"\n'])
def test_dedicated_usage_garbage_returns_none(monkeypatch, garbage):
    patch_run(monkeypatch, only("typeperf", garbage))
    assert reader_memory.probe_windows_gpu_dedicated_usage() is None


def test_windows_gpu_budget_combines_capacity_and_usage(monkeypatch):
    """The measured machine end to end: 24 GiB card, 20.1 GiB resident."""
    monkeypatch.setattr(sys, "platform", "win32")
    patch_winreg(monkeypatch, FakeWinreg(MAULER_TREE))
    patch_run(monkeypatch, only("typeperf", TYPEPERF_OUT))
    info = reader_memory.detect_budget()
    assert info is not None
    assert info.total_bytes == VRAM_3090
    assert info.free_bytes == VRAM_3090 - DEDICATED_USAGE
    # ~4.4 GiB free on a 24 GiB card: the exact case where keying off total would have
    # promised the operator a model four times too big.
    assert info.free_bytes < 5 * GIB
    assert info.total_bytes > 20 * GIB


def test_windows_gpu_budget_leaves_free_unknown_without_the_counter(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    patch_winreg(monkeypatch, FakeWinreg(MAULER_TREE))
    patch_run(monkeypatch, only("nothing-matches", ""))
    info = reader_memory.detect_budget()
    assert info is not None
    assert info.total_bytes == VRAM_3090
    assert info.free_bytes is None


# ================================================== the AdapterRAM prohibition


def _executable_source(path: Path) -> str:
    """The module's source with comments and *docstrings* removed — nothing else.

    Docstrings have to go because the module is *required* to warn about the broken
    property by name, so a plain substring scan would trip over its own documentation.
    Ordinary string literals must stay: a WMI query is a string literal, so a scan that
    threw those away would be blind to the exact mistake it exists to catch.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in list(ast.walk(tree)):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and len(body) > 1
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            del body[0]
    return ast.unparse(tree)  # unparse also drops every comment


@pytest.mark.parametrize("forbidden", ["AdapterRAM", "Win32_VideoController"])
def test_no_executable_line_names_the_broken_adapter_ram_property(forbidden):
    src = _executable_source(Path(reader_memory.__file__))
    assert forbidden not in src


def test_the_source_scan_would_notice_a_wmi_query_for_it(tmp_path):
    """Guard the guard: the scan must survive docstrings and still see a real query."""
    module = tmp_path / "offender.py"
    module.write_text(
        '"""Never use AdapterRAM."""\n'
        'X = 1\n'
        'def probe():\n'
        '    """Docstring mentioning Win32_VideoController."""\n'
        '    return run(["powershell", "(Get-CimInstance Win32_VideoController).AdapterRAM"])\n'
    )
    src = _executable_source(module)
    assert "AdapterRAM" in src
    assert "Win32_VideoController" in src

    clean = tmp_path / "clean.py"
    clean.write_text('"""Never use AdapterRAM on Win32_VideoController."""\nX = 1\nY = 2\n')
    assert "AdapterRAM" not in _executable_source(clean)


def test_no_probe_ever_issues_adapter_ram(monkeypatch, tmp_path):
    """Run every probe on every platform and inspect the argv actually issued."""
    issued = []

    def handler(argv):
        issued.append(" ".join(argv))
        raise FileNotFoundError(argv[0])

    patch_run(monkeypatch, handler)
    patch_winreg(monkeypatch, FakeWinreg(MAULER_TREE))
    monkeypatch.setattr(reader_memory, "_MEMINFO_PATH", str(tmp_path / "absent"))

    for platform in ("win32", "darwin", "linux"):
        monkeypatch.setattr(sys, "platform", platform)
        reader_memory.detect_budget()
    for probe in (reader_memory.probe_nvidia_smi,
                  reader_memory.probe_rocm_smi,
                  reader_memory.probe_apple_unified,
                  reader_memory.probe_windows_gpu_dedicated_usage,
                  reader_memory.probe_windows_system_ram,
                  reader_memory.probe_windows_registry_vram,
                  reader_memory.probe_linux_meminfo):
        probe()

    assert issued, "expected the probes to have attempted at least one command"
    joined = " ".join(issued)
    assert "AdapterRAM" not in joined
    assert "Win32_VideoController" not in joined
    # And the registry probe asks for the 64-bit value instead.
    assert QW == "HardwareInformation.qwMemorySize"


# ==================================================================== apple unified


def sysctl_handler(values):
    def _handler(argv):
        if argv[0] != "sysctl":
            raise FileNotFoundError(argv[0])
        name = argv[-1]
        if name not in values:
            return FakeCompleted("", returncode=1)  # what sysctl does for unknown keys
        return FakeCompleted(f"{values[name]}\n")
    return _handler


def test_apple_applies_the_wireable_fraction(monkeypatch):
    patch_run(monkeypatch, sysctl_handler({"hw.memsize": MAC_MEMSIZE,
                                           "iogpu.wired_limit_mb": 0}))
    info = reader_memory.probe_apple_unified()
    assert info is not None
    assert info.total_bytes == MAC_MEMSIZE
    assert info.unified is True
    assert info.free_bytes == int(MAC_MEMSIZE * reader_memory._APPLE_WIREABLE_FRACTION)
    assert info.free_bytes < MAC_MEMSIZE  # never the whole machine
    # 89.6 GiB on the measured 128 GiB M4 Max. macOS' own default wired limit on a
    # machine that size is ~75% (~96 GiB, the figure in the design doc's test matrix);
    # 0.70 is deliberately the conservative end of that range, since this fraction
    # doubles as the headroom allowance for memory shared with the CPU.
    assert 85 * GIB < info.free_bytes < 96 * GIB


def test_apple_zero_wired_limit_means_default_not_zero(monkeypatch):
    """``iogpu.wired_limit_mb: 0`` is "use the default", not "no GPU memory"."""
    patch_run(monkeypatch, sysctl_handler({"hw.memsize": MAC_MEMSIZE,
                                           "iogpu.wired_limit_mb": 0}))
    info = reader_memory.probe_apple_unified()
    assert info is not None and info.free_bytes > 0


def test_apple_honours_a_non_zero_wired_limit(monkeypatch):
    patch_run(monkeypatch, sysctl_handler({"hw.memsize": MAC_MEMSIZE,
                                           "iogpu.wired_limit_mb": 32768}))
    info = reader_memory.probe_apple_unified()
    assert info is not None
    assert info.free_bytes == 32768 * MIB
    assert "32768" in info.note


def test_apple_clamps_an_over_large_wired_limit(monkeypatch):
    patch_run(monkeypatch, sysctl_handler({"hw.memsize": MAC_MEMSIZE,
                                           "iogpu.wired_limit_mb": 999999}))
    info = reader_memory.probe_apple_unified()
    assert info is not None
    assert info.free_bytes == MAC_MEMSIZE


def test_apple_missing_wired_limit_key_falls_back_to_the_fraction(monkeypatch):
    patch_run(monkeypatch, sysctl_handler({"hw.memsize": MAC_MEMSIZE}))
    info = reader_memory.probe_apple_unified()
    assert info is not None
    assert info.free_bytes == int(MAC_MEMSIZE * reader_memory._APPLE_WIREABLE_FRACTION)


@pytest.mark.parametrize("bad", ["", "\n", "garbage\n", "0\n"])
def test_apple_bad_memsize_returns_none(monkeypatch, bad):
    patch_run(monkeypatch, only("sysctl", bad))
    assert reader_memory.probe_apple_unified() is None


def test_apple_missing_sysctl_returns_none(monkeypatch):
    patch_run(monkeypatch, only("nothing-matches", ""))
    assert reader_memory.probe_apple_unified() is None


# ========================================================================= rocm-smi

ROCM_OUT = """
========================= ROCm System Management Interface =========================
================================== Memory Usage ==================================
GPU[0]\t\t: VRAM Total Memory (B): 536870912
GPU[0]\t\t: VRAM Total Used Memory (B): 268435456
GPU[1]\t\t: VRAM Total Memory (B): 17163091968
GPU[1]\t\t: VRAM Total Used Memory (B): 1073741824
==================================================================================
"""


def test_rocm_parses_total_and_used_and_picks_largest(monkeypatch):
    patch_run(monkeypatch, only("rocm-smi", ROCM_OUT))
    info = reader_memory.probe_rocm_smi()
    assert info is not None
    assert info.adapter == "rocm:1"
    assert info.total_bytes == 17163091968
    assert info.free_bytes == 17163091968 - 1073741824
    assert info.unified is False


def test_rocm_missing_binary_returns_none(monkeypatch):
    patch_run(monkeypatch, only("nothing-matches", ""))
    assert reader_memory.probe_rocm_smi() is None


def test_rocm_nonzero_exit_returns_none(monkeypatch):
    patch_run(monkeypatch, only("rocm-smi", ROCM_OUT, returncode=2))
    assert reader_memory.probe_rocm_smi() is None


@pytest.mark.parametrize("garbage", ["", "\n", "ERROR: no AMD GPUs found\n"])
def test_rocm_garbage_returns_none(monkeypatch, garbage):
    patch_run(monkeypatch, only("rocm-smi", garbage))
    assert reader_memory.probe_rocm_smi() is None


# =================================================================== /proc/meminfo

MEMINFO = """MemTotal:       32871784 kB
MemFree:         1200000 kB
MemAvailable:   20481234 kB
Buffers:          123456 kB
"""


def test_meminfo_reads_total_and_available(monkeypatch, tmp_path):
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO)
    monkeypatch.setattr(reader_memory, "_MEMINFO_PATH", str(path))
    info = reader_memory.probe_linux_meminfo()
    assert info is not None
    assert info.total_bytes == 32871784 * 1024
    assert info.free_bytes == 20481234 * 1024
    assert info.unified is True


def test_meminfo_without_memavailable_leaves_free_unknown(monkeypatch, tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("MemTotal:       32871784 kB\nMemFree:  1200000 kB\n")
    monkeypatch.setattr(reader_memory, "_MEMINFO_PATH", str(path))
    info = reader_memory.probe_linux_meminfo()
    assert info is not None
    assert info.free_bytes is None


def test_meminfo_missing_file_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(reader_memory, "_MEMINFO_PATH", str(tmp_path / "absent"))
    assert reader_memory.probe_linux_meminfo() is None


def test_meminfo_is_a_directory_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(reader_memory, "_MEMINFO_PATH", str(tmp_path))
    assert reader_memory.probe_linux_meminfo() is None


@pytest.mark.parametrize("garbage", ["", "\n", "total: lots\n", "MemTotal: 0 kB\n"])
def test_meminfo_garbage_returns_none(monkeypatch, tmp_path, garbage):
    path = tmp_path / "meminfo"
    path.write_text(garbage)
    monkeypatch.setattr(reader_memory, "_MEMINFO_PATH", str(path))
    assert reader_memory.probe_linux_meminfo() is None


# =============================================================== windows system RAM


def test_windows_system_ram_parses_total(monkeypatch):
    patch_run(monkeypatch, only("powershell", f"{MAC_MEMSIZE}\n"))
    info = reader_memory.probe_windows_system_ram()
    assert info is not None
    assert info.total_bytes == MAC_MEMSIZE
    # Capacity only: policy rule 1 forbids passing that off as headroom.
    assert info.free_bytes is None
    assert info.unified is True


def test_windows_system_ram_queries_computersystem_not_videocontroller(monkeypatch):
    seen = []

    def handler(argv):
        seen.append(argv)
        return FakeCompleted("34359738368\n")

    patch_run(monkeypatch, handler)
    reader_memory.probe_windows_system_ram()
    command = " ".join(seen[0])
    assert "Win32_ComputerSystem" in command
    assert "TotalPhysicalMemory" in command
    assert "VideoController" not in command


def test_windows_system_ram_missing_powershell_returns_none(monkeypatch):
    patch_run(monkeypatch, only("nothing-matches", ""))
    assert reader_memory.probe_windows_system_ram() is None


def test_windows_system_ram_nonzero_exit_returns_none(monkeypatch):
    patch_run(monkeypatch, only("powershell", "34359738368\n", returncode=1))
    assert reader_memory.probe_windows_system_ram() is None


@pytest.mark.parametrize("garbage", ["", "\n", "ObjectNotFound\n", "0\n", "3.4e10\n"])
def test_windows_system_ram_garbage_returns_none(monkeypatch, garbage):
    patch_run(monkeypatch, only("powershell", garbage))
    assert reader_memory.probe_windows_system_ram() is None


# ======================================================== chain / detect_budget


def test_probe_chain_is_platform_scoped(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert reader_memory.probe_apple_unified in reader_memory.probe_chain()
    assert reader_memory.probe_linux_meminfo not in reader_memory.probe_chain()

    monkeypatch.setattr(sys, "platform", "linux")
    chain = reader_memory.probe_chain()
    assert reader_memory.probe_rocm_smi in chain
    assert reader_memory.probe_linux_meminfo in chain
    assert reader_memory.probe_apple_unified not in chain

    monkeypatch.setattr(sys, "platform", "win32")
    chain = reader_memory.probe_chain()
    assert reader_memory.probe_windows_system_ram in chain
    assert reader_memory.probe_apple_unified not in chain

    # nvidia-smi leads on every platform: it is the only single-source total+used.
    for platform in ("darwin", "linux", "win32"):
        monkeypatch.setattr(sys, "platform", platform)
        assert reader_memory.probe_chain()[0] is reader_memory.probe_nvidia_smi


def test_detect_budget_prefers_nvidia_smi(monkeypatch, tmp_path):
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO)
    monkeypatch.setattr(reader_memory, "_MEMINFO_PATH", str(path))
    monkeypatch.setattr(sys, "platform", "linux")
    patch_run(monkeypatch, only("nvidia-smi", "24576, 3277\n"))
    info = reader_memory.detect_budget()
    assert info is not None and info.source == "nvidia-smi"


def test_detect_budget_falls_through_to_meminfo(monkeypatch, tmp_path):
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO)
    monkeypatch.setattr(reader_memory, "_MEMINFO_PATH", str(path))
    monkeypatch.setattr(sys, "platform", "linux")
    patch_run(monkeypatch, only("nothing-matches", ""))
    info = reader_memory.detect_budget()
    assert info is not None and info.source == "proc-meminfo"


def test_detect_budget_returns_none_when_every_probe_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(reader_memory, "_MEMINFO_PATH", str(tmp_path / "absent"))
    patch_winreg(monkeypatch, None)
    patch_run(monkeypatch, only("nothing-matches", ""))
    for platform in ("darwin", "linux", "win32"):
        monkeypatch.setattr(sys, "platform", platform)
        assert reader_memory.detect_budget() is None


def test_detect_budget_survives_a_probe_that_raises(monkeypatch, tmp_path):
    """A probe blowing up must degrade the reader, never break the caller."""
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO)
    monkeypatch.setattr(reader_memory, "_MEMINFO_PATH", str(path))
    monkeypatch.setattr(sys, "platform", "linux")

    def boom():
        raise RuntimeError("nvidia-smi went sideways")

    monkeypatch.setattr(reader_memory, "probe_nvidia_smi", boom)
    patch_run(monkeypatch, only("nothing-matches", ""))
    info = reader_memory.detect_budget()
    assert info is not None and info.source == "proc-meminfo"


def test_detect_budget_darwin_uses_the_apple_probe(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    patch_run(monkeypatch, sysctl_handler({"hw.memsize": MAC_MEMSIZE,
                                           "iogpu.wired_limit_mb": 0}))
    info = reader_memory.detect_budget()
    assert info is not None
    assert info.source == "apple-unified"
    assert info.unified is True


# ================================================================= _pick_largest


def test_pick_largest_never_returns_the_first_when_a_bigger_one_exists():
    adapters = [reader_memory.Adapter("igpu", VRAM_AMD_IGPU, VRAM_AMD_IGPU),
                reader_memory.Adapter("3090", VRAM_3090, VRAM_3090 - DEDICATED_USAGE)]
    assert reader_memory._pick_largest(adapters).name == "3090"
    assert reader_memory._pick_largest(list(reversed(adapters))).name == "3090"


def test_pick_largest_falls_back_to_capacity_when_free_is_unknown():
    adapters = [reader_memory.Adapter("igpu", VRAM_AMD_IGPU, None),
                reader_memory.Adapter("3090", VRAM_3090, None)]
    assert reader_memory._pick_largest(adapters).name == "3090"


def test_pick_largest_empty_is_none():
    assert reader_memory._pick_largest([]) is None


# Guard against the module quietly acquiring a dependency: every probe here has to work
# on a stock interpreter, which is why they shell out instead of importing pynvml/wmi.
def test_module_imports_no_third_party_dependency():
    src = Path(reader_memory.__file__).read_text(encoding="utf-8")
    tree = tokenize.generate_tokens(io.StringIO(src).readline)
    imported = set()
    prev = None
    for tok in tree:
        if prev in ("import", "from") and tok.type == tokenize.NAME:
            imported.add(tok.string)
        if tok.type == tokenize.NAME:
            prev = tok.string
        elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                              tokenize.DEDENT, tokenize.COMMENT):
            prev = None
    assert imported <= {"annotations", "logging", "re", "subprocess", "sys",
                        "dataclasses", "dataclass", "pathlib", "Path", "winreg",
                        "__future__"}
