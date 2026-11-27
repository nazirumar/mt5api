"""Static checks for Windows automation scripts (they are not executed in tests)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


@pytest.mark.parametrize(
    "name",
    [
        "bootstrap.ps1",
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
        "watchdog.ps1",
    ],
)
def test_required_scripts_exist(name: str):
    assert (SCRIPTS / name).is_file(), f"Missing script: {name}"


def test_scheduled_task_names():
    text = (SCRIPTS / "install_autostart.ps1").read_text(encoding="utf-8-sig")
    for name in (
        "NazbeenMT5Terminal",
        "NazbeenMT5API",
        "NazbeenMT5Proxy",
        "NazbeenMT5Watchdog",
    ):
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


def test_watchdog_runs_every_five_minutes_and_is_battery_safe():
    text = (SCRIPTS / "watchdog.ps1").read_text(encoding="utf-8-sig")
    installer = (SCRIPTS / "install_autostart.ps1").read_text(encoding="utf-8-sig")
    assert "Invoke-ApiHealth" in text
    assert "restart_api.ps1" in text or "start_api.ps1" in text
    assert "RepetitionInterval (New-TimeSpan -Minutes 5)" in installer
    assert "AllowStartIfOnBatteries" in installer
    assert "DontStopIfGoingOnBatteries" in installer


def test_bootstrap_delegates_to_installer():
    text = (SCRIPTS / "bootstrap.ps1").read_text(encoding="utf-8-sig")
    assert "install.ps1" in text
    assert "New-RandomApiKey" not in text
    assert "pytest" in text


def test_ci_workflow_runs_pytest_on_windows():
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow.is_file(), "Missing GitHub Actions CI workflow"
    text = workflow.read_text(encoding="utf-8-sig")
    assert "runs-on: windows-latest" in text
    assert "python -m pytest -q" in text
    assert "'3.12'" in text and "'3.13'" in text


def test_env_example_documents_rotation_and_limits():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "API_KEY_PREVIOUS",
        "MAX_CANDLE_RANGE_DAYS",
        "MAX_TICK_RANGE_DAYS",
        "HISTORY_RATE_LIMIT_PER_MINUTE",
        "CANDLE_STREAM_ENABLED",
    ):
        assert key in text
    assert "PUBLIC_ENDPOINTS" not in text
