"""MetaTrader 5 terminal discovery, process detection and launching.

Modern MT5 installs use ``terminal64.exe``; older/portable installs may use
``terminal.exe``.  Both names are handled here.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..config import Settings

TERMINAL_EXECUTABLES = ("terminal64.exe", "terminal.exe")


def _executable_variants(base: Path) -> list[Path]:
    if base.suffix.lower() == ".exe":
        return [base]
    return [base / name for name in TERMINAL_EXECUTABLES]


def _registry_paths() -> list[Path]:
    paths: list[Path] = []
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return paths

    hives = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
    keys = [
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for hive in hives:
        for base in keys:
            try:
                root = winreg.OpenKey(hive, base)
            except OSError:
                continue
            index = 0
            while index < 500:
                try:
                    sub_name = winreg.EnumKey(root, index)
                    index += 1
                except OSError:
                    break
                lowered = sub_name.lower()
                if "metatrader" not in lowered and "metaquotes" not in lowered:
                    continue
                try:
                    sub = winreg.OpenKey(root, sub_name)
                    for value_name in ("InstallLocation", "DisplayIcon"):
                        try:
                            raw, _ = winreg.QueryValueEx(sub, value_name)
                        except OSError:
                            continue
                        cleaned = str(raw).strip('"').split(",")[0]
                        if cleaned:
                            paths.extend(_executable_variants(Path(cleaned)))
                    winreg.CloseKey(sub)
                except OSError:
                    continue
            winreg.CloseKey(root)
    return paths


def candidate_terminal_paths(settings: Settings) -> list[Path]:
    candidates: list[Path] = []
    if settings.MT5_PATH:
        candidates.append(Path(settings.MT5_PATH).expanduser())

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    user_profile = os.environ.get("USERPROFILE", "")

    bases = [
        program_files,
        program_files_x86,
        local_appdata,
        user_profile,
        os.path.join(user_profile, "Desktop") if user_profile else "",
    ]
    for base in bases:
        if not base:
            continue
        for folder in ("MetaTrader 5", "MetaTrader 5 Terminal"):
            candidates.extend(_executable_variants(Path(base) / folder))

    candidates.extend(_registry_paths())

    appdata = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    if appdata.exists():
        try:
            for child in appdata.iterdir():
                if child.is_dir():
                    candidates.extend(_executable_variants(child))
        except OSError:
            pass

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def discover_terminal_path(settings: Settings) -> Path | None:
    for candidate in candidate_terminal_paths(settings):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def is_terminal_process_running(expected_path: Path | None = None) -> bool:
    """Detect an MT5 terminal, not an unrelated MT4 terminal.exe process."""
    if expected_path is not None:
        try:
            if expected_path.is_file():
                expected = str(expected_path.resolve()).lower()
            else:
                expected = str(expected_path).lower()
        except OSError:
            expected = ""
    else:
        expected = ""
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -in @('terminal.exe','terminal64.exe') } | "
                "ForEach-Object { $_.ExecutablePath }",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0:
        paths = [line.strip().lower() for line in result.stdout.splitlines() if line.strip()]
        for path in paths:
            if expected and path == expected:
                return True
            if "metatrader 5" in path:
                return True
        return False
    # Fallback: process-name-only detection when WMI is unavailable.
    try:
        listing = subprocess.run(
            ["tasklist", "/NH"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    lowered = listing.stdout.lower()
    return any(name in lowered for name in TERMINAL_EXECUTABLES)


def launch_terminal(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        subprocess.Popen(
            [str(path)],
            cwd=str(path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except OSError:
        return False
