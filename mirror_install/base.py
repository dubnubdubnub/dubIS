import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class MirrorConfig:
    push_port: int
    read_port: int
    token_file: str
    snapshot_file: str
    allowlist: list
    python_exe: str
    daemon_script: str


class Installer(ABC):
    @abstractmethod
    def install(self, cfg: MirrorConfig) -> None: ...
    @abstractmethod
    def uninstall(self) -> None: ...
    @abstractmethod
    def is_installed(self) -> bool: ...
    @abstractmethod
    def is_running(self) -> bool: ...


def get_installer() -> Installer:
    if sys.platform == "win32":
        from mirror_install.windows import WindowsInstaller
        return WindowsInstaller()
    raise NotImplementedError(
        f"mirror autostart not yet implemented for {sys.platform}")
