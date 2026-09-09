"""Best-effort memory detection for the local picture/PDF reader (VLM) tier choice.

Seven independent probes, each **dependency-free** (no pynvml, no wmi, no psutil) and
each returning ``None`` on *any* failure — missing binary, non-zero exit, unparseable
output, permission error. ``detect_budget()`` walks the platform-appropriate chain and
returns the first probe that answers, as a :class:`BudgetInfo`.

    | Target                            | Probe                                        |
    |-----------------------------------|----------------------------------------------|
    | NVIDIA (any OS)                   | ``nvidia-smi --query-gpu=memory.total,used`` |
    | Windows capacity, vendor-neutral  | registry ``HardwareInformation.qwMemorySize``|
    | Windows usage, vendor-neutral     | ``\\GPU Adapter Memory(*)\\Dedicated Usage`` |
    | Apple unified                     | ``sysctl hw.memsize`` x wireable fraction    |
    | AMD / Linux                       | ``rocm-smi --showmeminfo vram``              |
    | Linux system RAM                  | ``/proc/meminfo``                            |
    | Windows system RAM                | ``Win32_ComputerSystem.TotalPhysicalMemory`` |

Two policy rules, both learned from the tiering that shipped in ``ca07608`` and was
deleted in ``ca819c3``. That code got both wrong; they are the whole reason this module
exists rather than a two-line ``nvidia-smi`` call:

1. **Report FREE memory, not total.** The deleted ``_MODEL_TIERS`` keyed off *total*
   VRAM. On a 24 GiB 3090 already holding a 27B model that says "24 GiB, serve the 32B"
   while ~4 GiB is actually free, and the load OOMs. Every probe therefore reports free
   separately from total, and ``free_bytes`` is ``None`` — never optimistically equal to
   total — whenever headroom genuinely cannot be determined. ``reader_tiers.choose_tier``
   declines on ``None``: unknown is never a pass.
2. **Pick the LARGEST adapter, never the first.** A machine enumerating an RTX 3090
   *and* an AMD Radeon integrated GPU at 0.5 GiB will hand index 0 to whichever the OS
   feels like, and index-0 logic then concludes nothing fits. Every multi-adapter probe
   runs its candidates through :func:`_pick_largest`.

.. warning::
   Never read the video controller's ``AdapterRAM`` WMI property. Measured on a 24 GiB
   RTX 3090 it reports ``4293918720`` (~4.0 GiB) because the property is a 32-bit DWORD
   that saturates at 4 GB. It is the first API most code reaches for and it is wrong;
   the registry ``qwMemorySize`` value above is the 64-bit, vendor-neutral replacement.
   ``tests/python/test_reader_memory.py`` asserts no executable line in this module ever
   names it.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_MIB = 1024 ** 2
_GIB = 1024 ** 3

# Apple's unified memory is shared with the CPU and the window server, so only a
# fraction is wireable by the GPU. macOS' own default limit is roughly 70-75% of
# hw.memsize; 0.70 is the conservative end of that range and doubles as this probe's
# headroom allowance (which is why the Apple probe reports free == wireable rather
# than pretending the whole machine is available).
_APPLE_WIREABLE_FRACTION = 0.70

# Display adapter class. Every display device gets an instance subkey (0000, 0001, ...)
# under this key, and each carries the 64-bit VRAM size. Vendor-neutral: NVIDIA, AMD
# and Intel adapters all populate it.
_DISPLAY_CLASS_GUID = "{4d36e968-e325-11ce-bfc1-08002be10318}"
_DISPLAY_CLASS_KEY = rf"SYSTEM\CurrentControlSet\Control\Class\{_DISPLAY_CLASS_GUID}"
_QWORD_MEMORY_SIZE = "HardwareInformation.qwMemorySize"

# Vendor-neutral dedicated-VRAM usage. See probe_windows_gpu_dedicated_usage for the
# LUID instance-naming limitation this counter carries.
_DEDICATED_USAGE_COUNTER = r"\GPU Adapter Memory(*)\Dedicated Usage"

# Only TotalPhysicalMemory: Win32_ComputerSystem carries no free-memory property, so
# this probe reports capacity and leaves free unknown (see BudgetInfo.free_bytes).
_WIN_SYSTEM_RAM_COMMAND = (
    "powershell", "-NoProfile", "-NonInteractive", "-Command",
    "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
)

# Module-level so tests can point it at a fixture without patching open().
_MEMINFO_PATH = "/proc/meminfo"

# Every probe is on the critical path of a UI click, so no probe may hang the app.
_PROBE_TIMEOUT = 5.0


@dataclass(frozen=True)
class Adapter:
    """One candidate memory pool, as seen by a single probe.

    ``free_bytes`` is ``None`` when the probe can read capacity but not headroom (the
    Windows registry, for instance, knows how much VRAM an adapter has and nothing
    about how much of it is in use).
    """

    name: str
    total_bytes: int
    free_bytes: int | None


@dataclass(frozen=True)
class BudgetInfo:
    """The memory budget a reader tier may be chosen against.

    ``free_bytes`` is what ``reader_tiers.choose_tier`` reads — never ``total_bytes``
    (policy rule 1). ``None`` means "capacity known, headroom unknown", which declines
    a tier rather than guessing.

    ``unified`` distinguishes memory shared with the CPU/OS (Apple unified memory,
    system RAM) from a discrete card's dedicated VRAM. Callers need it because the two
    fail differently: overcommitting unified memory swaps, overcommitting VRAM OOMs.
    """

    total_bytes: int
    free_bytes: int | None
    source: str
    unified: bool
    adapter: str = ""
    note: str = ""

    @property
    def free_gib(self) -> float | None:
        return None if self.free_bytes is None else self.free_bytes / _GIB

    @property
    def total_gib(self) -> float:
        return self.total_bytes / _GIB


def _run(argv) -> str | None:
    """Run ``argv`` and return stdout, or ``None`` on any failure whatsoever."""
    try:
        proc = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        # Missing binary (FileNotFoundError), no permission, timeout expired.
        logger.debug("memory probe %s did not run", argv[0], exc_info=True)
        return None
    if proc.returncode != 0:
        logger.debug("memory probe %s exited %s", argv[0], proc.returncode)
        return None
    return proc.stdout or ""


def _pick_largest(adapters: list[Adapter]) -> Adapter | None:
    """The largest adapter — **never** ``adapters[0]`` (policy rule 2).

    Ranked by free bytes first, since free is what decides whether a model fits, and
    by total as the tiebreak. A probe that cannot see headroom passes ``free=None`` for
    every candidate, so the ranking degrades cleanly to largest-by-capacity. ``-1``
    sorts unknown headroom below a measured zero: a card we know is full is still a
    better-understood answer than one we know nothing about.
    """
    if not adapters:
        return None
    return max(adapters, key=lambda a: (a.free_bytes if a.free_bytes is not None else -1,
                                        a.total_bytes))


# --------------------------------------------------------------------------- probes


def probe_nvidia_smi() -> BudgetInfo | None:
    """NVIDIA VRAM via ``nvidia-smi``. Reports the largest GPU by free VRAM.

    Restored from the probe added in ``ca07608`` and deleted in ``ca819c3``, with the
    two policy fixes applied: it queries ``memory.used`` alongside ``memory.total`` so
    free is real, and it ranks all GPUs instead of taking ``splitlines()[0]``.
    """
    out = _run(["nvidia-smi", "--query-gpu=memory.total,memory.used",
                "--format=csv,noheader,nounits"])
    if out is None:
        return None
    adapters: list[Adapter] = []
    for index, line in enumerate(out.splitlines()):
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < 2:
            continue
        try:
            total = int(fields[0]) * _MIB
            used = int(fields[1]) * _MIB
        except ValueError:
            continue  # header row, "[N/A]" on a MIG/vGPU device, or plain garbage
        if total <= 0:
            continue
        adapters.append(Adapter(f"nvidia:{index}", total, max(total - used, 0)))
    best = _pick_largest(adapters)
    if best is None:
        return None
    return BudgetInfo(best.total_bytes, best.free_bytes, "nvidia-smi",
                      unified=False, adapter=best.name)


def _winreg():
    """The stdlib ``winreg`` module, or ``None`` off Windows. Patched wholesale in tests."""
    try:
        import winreg
    except ImportError:
        return None
    return winreg


def _coerce_qword(raw) -> int | None:
    """``HardwareInformation.qwMemorySize`` as an int.

    Drivers register it as REG_QWORD (winreg hands back an ``int``) but some register it
    as REG_BINARY (little-endian bytes), so both shapes are accepted.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        return int.from_bytes(bytes(raw[:8]), "little")
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def probe_windows_registry_vram() -> BudgetInfo | None:
    """Vendor-neutral VRAM capacity from the display class registry key.

    Enumerates every display adapter instance and returns the **largest**. Headroom is
    not in the registry, so ``free_bytes`` is ``None``; ``_windows_gpu_budget`` pairs
    this with :func:`probe_windows_gpu_dedicated_usage` to fill it in.
    """
    winreg = _winreg()
    if winreg is None:
        return None
    adapters: list[Adapter] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS_KEY) as root:
            subkey_count = winreg.QueryInfoKey(root)[0]
            for i in range(subkey_count):
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    continue
                try:
                    with winreg.OpenKey(root, name) as key:
                        raw = winreg.QueryValueEx(key, _QWORD_MEMORY_SIZE)[0]
                except OSError:
                    continue  # e.g. the "Configuration" subkey, or a non-display class member
                size = _coerce_qword(raw)
                if size and size > 0:
                    adapters.append(Adapter(f"registry:{name}", size, None))
    except OSError:
        return None
    best = _pick_largest(adapters)
    if best is None:
        return None
    return BudgetInfo(best.total_bytes, None, "windows-registry",
                      unified=False, adapter=best.name,
                      note="capacity only; registry carries no headroom")


def probe_windows_gpu_dedicated_usage() -> int | None:
    """Bytes of dedicated VRAM in use, from the ``GPU Adapter Memory`` perf counter.

    Returns the **maximum** across instances, not a per-adapter figure, and that is a
    real limitation rather than an oversight: this counter's instance names are adapter
    LUIDs (``luid_0x00000000_0x0001078c_phys_0``), not friendly names, and nothing in
    the counter set maps a LUID back to the registry instance key or PCI device that
    :func:`probe_windows_registry_vram` enumerated. So usage genuinely cannot be
    attributed to a *named* adapter here. Taking the max is a documented heuristic: the
    heaviest consumer of dedicated VRAM on a mixed discrete+integrated machine is
    overwhelmingly the discrete card, which is also the adapter the registry probe
    selected. It is a conservative error in the right direction — over-reporting usage
    understates free memory and picks a smaller model, which loads; understating usage
    would pick a model that OOMs.

    (Correlating properly would mean going through DXGI/D3DKMT for each adapter's LUID,
    which is a ctypes COM binding this module deliberately does not take on.)
    """
    out = _run(["typeperf", _DEDICATED_USAGE_COUNTER, "-sc", "1"])
    if out is None:
        return None
    values: list[float] = []
    for line in out.splitlines():
        fields = [f.strip().strip('"') for f in line.split('","')]
        for field in fields[1:]:  # column 0 is the timestamp / counter-path header
            try:
                values.append(float(field))
            except ValueError:
                continue
    if not values:
        return None
    return int(max(values))


def _windows_gpu_budget() -> BudgetInfo | None:
    """Registry capacity + perf-counter usage, combined into one budget."""
    capacity = probe_windows_registry_vram()
    if capacity is None:
        return None
    used = probe_windows_gpu_dedicated_usage()
    if used is None:
        return capacity  # free stays None: choose_tier will decline rather than guess
    free = max(capacity.total_bytes - used, 0)
    return BudgetInfo(capacity.total_bytes, free, "windows-registry+perfcounter",
                      unified=False, adapter=capacity.adapter,
                      note="usage is the max over LUID instances, not per-adapter")


def _sysctl_int(name: str) -> int | None:
    out = _run(["sysctl", "-n", name])
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def probe_apple_unified() -> BudgetInfo | None:
    """Apple silicon unified memory: ``hw.memsize`` x the GPU-wireable fraction.

    ``iogpu.wired_limit_mb`` overrides the fraction when the operator has set it, but
    **0 means "use the default"**, not "no GPU memory" — so only a non-zero value is
    honoured. The reported free is the wireable figure: the fraction *is* the headroom
    allowance for unified memory shared with the CPU and window server.
    """
    total = _sysctl_int("hw.memsize")
    if total is None or total <= 0:
        return None
    limit_mb = _sysctl_int("iogpu.wired_limit_mb")
    if limit_mb and limit_mb > 0:
        wireable = min(limit_mb * _MIB, total)
        note = f"iogpu.wired_limit_mb={limit_mb}"
    else:
        wireable = int(total * _APPLE_WIREABLE_FRACTION)
        note = f"default wireable fraction {_APPLE_WIREABLE_FRACTION:.2f}"
    return BudgetInfo(total, wireable, "apple-unified",
                      unified=True, adapter="unified-memory", note=note)


_ROCM_TOTAL_RE = re.compile(r"GPU\[(\d+)\][^\n]*?VRAM Total Memory\s*\(B\)\s*:\s*(\d+)", re.I)
_ROCM_USED_RE = re.compile(r"GPU\[(\d+)\][^\n]*?VRAM Total Used Memory\s*\(B\)\s*:\s*(\d+)", re.I)


def probe_rocm_smi() -> BudgetInfo | None:
    """AMD VRAM via ``rocm-smi --showmeminfo vram``. Largest GPU by free VRAM."""
    out = _run(["rocm-smi", "--showmeminfo", "vram"])
    if out is None:
        return None
    totals = {m.group(1): int(m.group(2)) for m in _ROCM_TOTAL_RE.finditer(out)}
    used = {m.group(1): int(m.group(2)) for m in _ROCM_USED_RE.finditer(out)}
    adapters = [
        Adapter(f"rocm:{gpu}", total, max(total - used.get(gpu, 0), 0))
        for gpu, total in totals.items()
        if total > 0
    ]
    best = _pick_largest(adapters)
    if best is None:
        return None
    return BudgetInfo(best.total_bytes, best.free_bytes, "rocm-smi",
                      unified=False, adapter=best.name)


_MEMINFO_RE = re.compile(r"^(\w+):\s+(\d+)\s*kB\s*$", re.M)


def probe_linux_meminfo() -> BudgetInfo | None:
    """Linux system RAM from ``/proc/meminfo``.

    ``MemTotal`` is the capacity; ``MemAvailable`` is the kernel's own estimate of what
    a new allocation can actually get, which is exactly the free figure policy rule 1
    wants. A kernel too old to publish ``MemAvailable`` leaves free unknown.
    """
    try:
        text = Path(_MEMINFO_PATH).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields = {m.group(1): int(m.group(2)) * 1024 for m in _MEMINFO_RE.finditer(text)}
    total = fields.get("MemTotal")
    if not total or total <= 0:
        return None
    return BudgetInfo(total, fields.get("MemAvailable"), "proc-meminfo",
                      unified=True, adapter="system-ram",
                      note="CPU inference on shared system RAM")


def probe_windows_system_ram() -> BudgetInfo | None:
    """Windows system RAM from ``Win32_ComputerSystem.TotalPhysicalMemory``.

    Capacity only — that class exposes no free-memory property, so ``free_bytes`` stays
    ``None`` and no tier is chosen off this probe alone. It is the last resort of the
    chain and exists so the UI can say "no GPU found, 32 GiB system RAM" instead of
    "unknown".
    """
    out = _run(_WIN_SYSTEM_RAM_COMMAND)
    if out is None:
        return None
    for line in out.splitlines():
        token = line.strip()
        if token.isdigit() and int(token) > 0:
            return BudgetInfo(int(token), None, "windows-system-ram",
                              unified=True, adapter="system-ram",
                              note="capacity only; no free-memory property on this class")
    return None


# ------------------------------------------------------------------------ selection


def probe_chain() -> list:
    """The probes to try, in order, for the current platform.

    ``nvidia-smi`` leads everywhere because it is the only probe that reports total and
    used from one authoritative source; the vendor-neutral and system-RAM probes are
    progressively weaker fallbacks behind it.
    """
    chain = [probe_nvidia_smi]
    if sys.platform == "darwin":
        chain.append(probe_apple_unified)
    elif sys.platform == "win32":
        chain += [_windows_gpu_budget, probe_windows_system_ram]
    else:
        chain += [probe_rocm_smi, probe_linux_meminfo]
    return chain


def detect_budget() -> BudgetInfo | None:
    """The first memory budget any probe can establish, or ``None`` if none can.

    Never raises: a probe that throws something unforeseen is logged and skipped, since
    a failed hardware guess must degrade the reader to ``off``, not break import.
    """
    for probe in probe_chain():
        try:
            info = probe()
        except Exception:
            logger.debug("memory probe %s raised", getattr(probe, "__name__", probe),
                         exc_info=True)
            continue
        if info is not None:
            logger.debug("memory budget from %s: total=%.2f GiB free=%s",
                         info.source, info.total_gib,
                         "unknown" if info.free_gib is None else f"{info.free_gib:.2f} GiB")
            return info
    return None
