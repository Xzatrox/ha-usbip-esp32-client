"""Shared test fixtures and Hypothesis configuration for the USB/IP ESP32 Client add-on.

Provides common fixtures used across property, unit, and integration tests:
- mock_config: Mocked AddonConfig with sensible defaults
- mock_supervisor_response: Raw Supervisor API response data
- mock_subprocess: Patched subprocess.run
- flask_test_client: Flask test client for WebUI testing
- tmp_event_log: EventLog with PATH redirected to a temp file

Also configures Hypothesis with a project-wide settings profile
(min 100 examples per property test).
"""

import os
import tempfile
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from hypothesis import settings, HealthCheck

from usbip_addon.config import AddonConfig, DeviceEntry
from usbip_addon.event_log import EventLog


# ---------------------------------------------------------------------------
# Hypothesis configuration: project-wide profile with min 100 examples
# ---------------------------------------------------------------------------

settings.register_profile(
    "ci",
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "dev",
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))


# ---------------------------------------------------------------------------
# Fixtures: Configuration mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config():
    """Create a mocked AddonConfig with sensible default property values.

    Returns an AddonConfig MagicMock with all properties pre-configured
    to return default values. Tests can override individual properties
    as needed.
    """
    config = MagicMock(spec=AddonConfig)
    type(config).token = PropertyMock(return_value="test-supervisor-token")
    config.SUPERVISOR_URL = "http://supervisor"

    # Default property values matching DEFAULTS in config.py
    type(config).log_level = PropertyMock(return_value="info")
    type(config).monitor_interval = PropertyMock(return_value=30)
    type(config).reattach_retries = PropertyMock(return_value=3)
    type(config).attach_delay = PropertyMock(return_value=2)
    type(config).notifications_enabled = PropertyMock(return_value=True)
    type(config).flap_warning_threshold = PropertyMock(return_value=3)
    type(config).flap_critical_threshold = PropertyMock(return_value=5)
    type(config).flap_window_seconds = PropertyMock(return_value=600)
    type(config).flap_clear_seconds = PropertyMock(return_value=900)

    # Default device list with one device
    type(config).devices = PropertyMock(return_value=[
        DeviceEntry(server="192.168.1.100", name="Zigbee Coordinator", port=3240, busid="1-1"),
    ])

    # read_config returns a valid options dict
    config.read_config.return_value = {
        "log_level": "info",
        "monitor_interval": 30,
        "reattach_retries": 3,
        "attach_delay": 2,
        "notifications_enabled": True,
        "flap_warning_threshold": 3,
        "flap_critical_threshold": 5,
        "flap_window_seconds": 600,
        "flap_clear_seconds": 900,
        "devices": [
            {"server": "192.168.1.100", "name": "Zigbee Coordinator", "port": 3240, "busid": "1-1"}
        ],
    }

    return config


@pytest.fixture
def mock_supervisor_response():
    """Provide raw Supervisor API response data for /addons/self/info.

    Returns the full JSON structure as returned by the Supervisor API,
    suitable for use with urllib mocks.
    """
    return {
        "result": "ok",
        "data": {
            "name": "USB/IP ESP32 Client",
            "slug": "ha-usbip-esp32-client",
            "state": "started",
            "version": "1.0.0",
            "options": {
                "log_level": "info",
                "monitor_interval": 30,
                "reattach_retries": 3,
                "attach_delay": 2,
                "notifications_enabled": True,
                "flap_warning_threshold": 3,
                "flap_critical_threshold": 5,
                "flap_window_seconds": 600,
                "flap_clear_seconds": 900,
                "devices": [
                    {
                        "server": "192.168.1.100",
                        "name": "Zigbee Coordinator",
                        "port": 3240,
                        "busid": "1-1",
                    },
                    {
                        "server": "192.168.1.101",
                        "name": "BT Dongle",
                        "port": 3240,
                        "busid": "1-1",
                    },
                ],
            },
        },
    }


# ---------------------------------------------------------------------------
# Fixtures: Subprocess mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_subprocess():
    """Patch subprocess.run and return the mock for assertion/configuration.

    The mock is configured to return a successful CompletedProcess by default.
    Tests can override return_value or side_effect as needed.

    Usage:
        def test_something(mock_subprocess):
            mock_subprocess.return_value = subprocess.CompletedProcess(
                args=["usbip", "attach"], returncode=0, stdout="", stderr=""
            )
    """
    with patch("subprocess.run") as mock_run:
        # Default: successful execution with empty output
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        yield mock_run


# ---------------------------------------------------------------------------
# Fixtures: Flask test client
# ---------------------------------------------------------------------------


@pytest.fixture
def flask_test_client(mock_config):
    """Create a Flask test client for WebUI testing.

    The app is created with mocked dependencies (config, usbip_client,
    health_checker, discovery, event_log, server_locks) to isolate
    WebUI logic from external systems.

    Returns:
        Flask test client with test mode enabled.
    """
    from usbip_addon.webui.app import create_app
    from usbip_addon.usbip_client import UsbipClient
    from usbip_addon.health import HealthChecker, HealthResult
    from usbip_addon.discovery import DeviceDiscovery
    from usbip_addon.server_lock import ServerLockManager

    mock_usbip = MagicMock(spec=UsbipClient)
    mock_usbip.list_ports.return_value = []

    mock_health = MagicMock(spec=HealthChecker)
    mock_health.check.return_value = HealthResult(reachable=True, latency_ms=5.0, error=None)

    mock_discovery = MagicMock(spec=DeviceDiscovery)
    mock_event_log = MagicMock(spec=EventLog)
    mock_event_log.read_events.return_value = []
    mock_locks = ServerLockManager()

    app = create_app(
        config=mock_config,
        usbip_client=mock_usbip,
        health_checker=mock_health,
        discovery=mock_discovery,
        event_log=mock_event_log,
        server_locks=mock_locks,
    )
    app.config["TESTING"] = True

    with app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Fixtures: Temporary event log
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_event_log(tmp_path):
    """Create an EventLog instance with PATH redirected to a temp file.

    The EventLog.PATH class attribute is patched to point to a temporary
    file that is automatically cleaned up after the test. This prevents
    tests from writing to /tmp/usbip_events.jsonl.

    Yields:
        An EventLog instance configured to use a temporary file path.
    """
    tmp_file = tmp_path / "usbip_events.jsonl"
    original_path = EventLog.PATH

    EventLog.PATH = str(tmp_file)
    event_log = EventLog()

    yield event_log

    # Restore original path
    EventLog.PATH = original_path
