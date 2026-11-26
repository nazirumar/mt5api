"""Static checks for Windows automation scripts (they are not executed in tests)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


@pytest.mark.parametrize(
    "name",
    [
        "install.ps1",
        "uninstall.ps1",
        "install_autostart.ps1",
        "uninstall_autostart.ps1",
        "setup_domain.ps1",
        "remove_domain.ps1",
        "start_api.ps1",
        "stop_api.ps1",
        "restart_api.ps1",
        "check_status.ps1",
        "diagnose.ps1",
    ],
)
def test_required_scripts_exist(name: str):
    assert (SCRIPTS / name).is_file(), f"Missing script: {name}"


def test_scheduled_task_names():
    text = (SCRIPTS / "install_autostart.ps1").read_text(encoding="utf-8-sig")
    for name in ("NazbeenMT5Terminal", "NazbeenMT5API", "NazbeenMT5Proxy"):
        assert name in text


def test_domain_script_preserves_hosts_file():
    text = (SCRIPTS / "setup_domain.ps1").read_text(encoding="utf-8-sig").lower()
    assert "mt5.nazbeen.test" in text
    assert "hosts" in text
    assert "flushdns" in text
    assert "administrator" in text or "admin" in text
    assert (
        "test-path" in text
        or "get-content" in text
        or "readalllines" in text
        or "test-hostsentry" in text
    )


def test_uninstall_does_not_delete_user_data_by_default():
    text = (SCRIPTS / "uninstall.ps1").read_text(encoding="utf-8-sig")
    assert "DeleteData" in text
    assert ".env" in text


def test_scripts_detect_both_terminal_executables():
    text = (SCRIPTS / "_common.ps1").read_text(encoding="utf-8-sig")
    assert "terminal64.exe" in text
    assert "terminal.exe" in text
