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


def test_watchdog_repair_arguments_splat_as_named_parameters():
    text = (SCRIPTS / "watchdog.ps1").read_text(encoding="utf-8-sig")
    assert "[hashtable]$Arguments" in text
    assert (
        text.count("-Arguments @{ Port = $effectivePort; WaitSeconds = $WaitSeconds }")
        == 3
    )
    assert "-Arguments @('-Port'" not in text


def test_watchdog_json_mode_suppresses_helper_output():
    text = (SCRIPTS / "watchdog.ps1").read_text(encoding="utf-8-sig")
    assert "if ($Json)" in text
    assert "& $path @Arguments *>$null" in text


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


def test_watchdog_cooldown_blocks_both_restart_paths():
    text = (SCRIPTS / "watchdog.ps1").read_text(encoding="utf-8-sig")
    unreachable = text.split("if (-not $health.Reachable) {", 1)[1].split("} elseif (", 1)[0]
    unhealthy = text.split("} elseif ($terminalStarted", 1)[1]

    assert "Test-RestartAllowed" in unreachable
    assert "Test-RestartAllowed" in unhealthy
    assert "$result.RestartSkipped = $true" in unreachable
    assert unreachable.index("if ($code -eq 0) {") < unreachable.index(
        "$result.Action = 'api-restarted'"
    )
    assert unreachable.index("if ($code -eq 0) {") < unreachable.index(
        "$result.ApiRestarted = $true"
    )
    assert unhealthy.index("if ($code -eq 0) {") < unhealthy.index("$result.Action = 'api-restarted'")

    # Health must be refreshed after a terminal launch before repair decisions.
    assert "if ($terminalStarted) {" in text
    assert text.count("Invoke-ApiHealth -Port $effectivePort") >= 2


def test_bootstrap_passes_plain_values_to_installer():
    text = (SCRIPTS / "bootstrap.ps1").read_text(encoding="utf-8-sig")
    assert "$installArgs.Add($PythonPath)" in text
    assert "$installArgs.Add($Domain)" in text
    assert "('\"{0}\"' -f $PythonPath)" not in text
    assert "('\"{0}\"' -f $Domain)" not in text


def test_installer_tracks_failures_and_exits_nonzero():
    text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8-sig")
    assert "$installFailed = $false" in text
    assert text.count("$installFailed = $true") >= 6
    assert "if ($installFailed) { exit 1 }" in text
    assert "Write-KV 'Local domain URL'" in text
    assert text.rstrip().endswith("exit 0")


def test_installer_generates_caddyfile_for_resolved_domain():
    text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8-sig")
    assert "$caddyContent" in text
    assert "('http://{0} {{' -f $domainName)" in text
    assert "`tbind 127.0.0.1" in text
    assert "reverse_proxy 127.0.0.1:8080" in text
    assert "auto_https off" in text
    assert "admin off" in text
    assert text.index("$domainName =") < text.index("$caddyContent =")

    caddyfile = (ROOT / "caddy" / "Caddyfile").read_text(encoding="utf-8-sig")
    assert "http://mt5.nazbeen.test {" in caddyfile


def test_api_process_helper_filters_by_configured_port():
    common = (SCRIPTS / "_common.ps1").read_text(encoding="utf-8-sig")
    helper = common.split("function Get-ApiProcesses {", 1)[1].split("function Get-CaddyProcesses", 1)[0]
    assert "param([int]$Port = 0)" in helper
    assert "Get-ApiPort" in helper
    assert "--port" in helper
    assert "-Port" in helper
    assert r"app\.main:app" in helper

    stop = (SCRIPTS / "stop_api.ps1").read_text(encoding="utf-8-sig")
    start = (SCRIPTS / "start_api.ps1").read_text(encoding="utf-8-sig")
    assert "Get-ApiProcesses -Port $effectivePort" in stop
    assert "Get-ApiProcesses -Port $effectivePort" in start
    assert "graceful" not in stop.lower()
    assert "Terminat" in stop
    # Unrelated port owners are still protected.
    assert "leaving it running" in stop


def test_readme_documents_stop_script_and_domain_override():
    text = (ROOT / "README.md").read_text(encoding="utf-8-sig")
    assert 'Stop-ScheduledTask -TaskName "NazbeenMT5API"' not in text
    assert r"scripts\stop_api.ps1" in text
    assert "~30s" in text
    assert "120s" in text
    assert "-Domain" in text
    assert "CORS_ORIGINS" in text
