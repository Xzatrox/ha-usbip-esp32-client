"""Unit tests for WebUI API endpoints.

Tests that each API endpoint returns the correct JSON structure and
handles error conditions appropriately.

Endpoints tested:
- GET /api/status — device status and health
- POST /api/attach — attach device by server IP
- POST /api/detach — detach device by port number
- GET /api/discover — run discovery for server
- GET /api/events — read event log entries
- GET /api/logs — fetch logs from Supervisor API

Requirements: 16.7, 16.9
"""

import json
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from usbip_addon.config import AddonConfig, DeviceEntry
from usbip_addon.discovery import DeviceDiscovery, DiscoveredDevice, DiscoveryResult
from usbip_addon.event_log import EventLog
from usbip_addon.health import HealthChecker, HealthResult
from usbip_addon.server_lock import ServerLockManager
from usbip_addon.usbip_client import AttachResult, PortEntry, UsbipClient
from usbip_addon.webui.app import create_app


@pytest.fixture
def mock_deps():
    """Create mocked dependencies for the Flask app and return them as a dict."""
    config = MagicMock(spec=AddonConfig)
    type(config).devices = PropertyMock(return_value=[
        DeviceEntry(server="192.168.1.100", name="Zigbee Coordinator", port=3240, busid="1-1"),
        DeviceEntry(server="192.168.1.101", name="BT Dongle", port=3240, busid="1-1"),
    ])
    config.read_config.return_value = {}
    config.SUPERVISOR_URL = "http://supervisor"

    mock_usbip = MagicMock(spec=UsbipClient)
    mock_usbip.list_ports.return_value = []
    mock_usbip.detach.return_value = True
    mock_usbip.detach_remote.return_value = True
    mock_usbip.remount_sysfs.return_value = True

    mock_health = MagicMock(spec=HealthChecker)
    mock_health.check.return_value = HealthResult(reachable=True, latency_ms=5.0, error=None)

    mock_discovery = MagicMock(spec=DeviceDiscovery)
    mock_event_log = MagicMock(spec=EventLog)
    mock_event_log.read_events.return_value = []
    mock_locks = ServerLockManager()

    return {
        "config": config,
        "usbip_client": mock_usbip,
        "health_checker": mock_health,
        "discovery": mock_discovery,
        "event_log": mock_event_log,
        "server_locks": mock_locks,
    }


@pytest.fixture
def app_client(mock_deps):
    """Create a Flask test client with mocked dependencies."""
    app = create_app(**mock_deps)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestApiStatus:
    """Tests for GET /api/status endpoint."""

    def test_returns_200(self, app_client):
        response = app_client.get("/api/status")
        assert response.status_code == 200

    def test_returns_json_with_devices_key(self, app_client):
        response = app_client.get("/api/status")
        data = response.get_json()
        assert "devices" in data

    def test_returns_json_with_ports_key(self, app_client):
        response = app_client.get("/api/status")
        data = response.get_json()
        assert "ports" in data

    def test_devices_list_matches_configured_count(self, app_client):
        response = app_client.get("/api/status")
        data = response.get_json()
        assert len(data["devices"]) == 2

    def test_device_entry_has_expected_fields(self, app_client):
        response = app_client.get("/api/status")
        data = response.get_json()
        device = data["devices"][0]
        assert "name" in device
        assert "server" in device
        assert "port" in device
        assert "busid" in device
        assert "attached" in device
        assert "health" in device

    def test_device_health_has_expected_fields(self, app_client):
        response = app_client.get("/api/status")
        data = response.get_json()
        health = data["devices"][0]["health"]
        assert "reachable" in health
        assert "latency_ms" in health
        assert "error" in health

    def test_device_shows_attached_when_port_matches(self, mock_deps):
        """Device should show attached=True when usbip port lists it."""
        mock_deps["usbip_client"].list_ports.return_value = [
            PortEntry(port=0, server="192.168.1.100", busid="1-1", device_info="test")
        ]
        app = create_app(**mock_deps)
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.get("/api/status")
            data = response.get_json()
            device = data["devices"][0]
            assert device["attached"] is True
            assert device["attached_port"] == 0


class TestApiAttach:
    """Tests for POST /api/attach endpoint."""

    def test_returns_400_when_no_body(self, app_client):
        response = app_client.post("/api/attach", content_type="application/json")
        assert response.status_code == 400

    def test_returns_400_when_server_field_missing(self, app_client):
        response = app_client.post(
            "/api/attach",
            data=json.dumps({"name": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "server" in data["error"].lower()

    def test_returns_404_for_unknown_server(self, app_client):
        response = app_client.post(
            "/api/attach",
            data=json.dumps({"server": "10.0.0.99"}),
            content_type="application/json",
        )
        assert response.status_code == 404
        data = response.get_json()
        assert "error" in data

    def test_returns_success_on_attach(self, mock_deps):
        mock_deps["usbip_client"].attach.return_value = AttachResult(
            success=True, port=0, stderr=""
        )
        app = create_app(**mock_deps)
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.post(
                "/api/attach",
                data=json.dumps({"server": "192.168.1.100"}),
                content_type="application/json",
            )
            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True
            assert "port" in data
            assert "device" in data
            assert "server" in data

    def test_returns_503_when_health_check_fails(self, mock_deps):
        mock_deps["health_checker"].check.return_value = HealthResult(
            reachable=False, latency_ms=None, error="Connection refused"
        )
        app = create_app(**mock_deps)
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.post(
                "/api/attach",
                data=json.dumps({"server": "192.168.1.100"}),
                content_type="application/json",
            )
            assert response.status_code == 503
            data = response.get_json()
            assert data["success"] is False
            assert "error" in data

    def test_returns_500_when_attach_fails(self, mock_deps):
        mock_deps["usbip_client"].attach.return_value = AttachResult(
            success=False, port=None, stderr="device busy"
        )
        app = create_app(**mock_deps)
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.post(
                "/api/attach",
                data=json.dumps({"server": "192.168.1.100"}),
                content_type="application/json",
            )
            assert response.status_code == 500
            data = response.get_json()
            assert data["success"] is False
            assert "error" in data


class TestApiDetach:
    """Tests for POST /api/detach endpoint."""

    def test_returns_400_when_no_body(self, app_client):
        response = app_client.post("/api/detach", content_type="application/json")
        assert response.status_code == 400

    def test_returns_400_when_port_field_missing(self, app_client):
        response = app_client.post(
            "/api/detach",
            data=json.dumps({"device": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "port" in data["error"].lower()

    def test_returns_400_for_invalid_port_type(self, app_client):
        response = app_client.post(
            "/api/detach",
            data=json.dumps({"port": "not_a_number"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_returns_success_on_detach(self, mock_deps):
        mock_deps["usbip_client"].detach.return_value = True
        app = create_app(**mock_deps)
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.post(
                "/api/detach",
                data=json.dumps({"port": 0}),
                content_type="application/json",
            )
            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True
            assert data["port"] == 0

    def test_returns_500_on_detach_failure(self, mock_deps):
        mock_deps["usbip_client"].detach.return_value = False
        app = create_app(**mock_deps)
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.post(
                "/api/detach",
                data=json.dumps({"port": 5}),
                content_type="application/json",
            )
            assert response.status_code == 500
            data = response.get_json()
            assert data["success"] is False
            assert "error" in data


class TestApiDiscover:
    """Tests for GET /api/discover endpoint."""

    def test_returns_400_when_server_param_missing(self, app_client):
        response = app_client.get("/api/discover")
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "server" in data["error"].lower()

    def test_returns_devices_on_success(self, mock_deps):
        mock_deps["discovery"].discover.return_value = DiscoveryResult(
            success=True,
            devices=[
                DiscoveredDevice(busid="1-1", manufacturer="Realtek", product="USB Hub")
            ],
        )
        app = create_app(**mock_deps)
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.get("/api/discover?server=192.168.1.100")
            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True
            assert "devices" in data
            assert len(data["devices"]) == 1
            assert data["devices"][0]["busid"] == "1-1"
            assert data["devices"][0]["manufacturer"] == "Realtek"
            assert data["devices"][0]["product"] == "USB Hub"

    def test_returns_500_on_discovery_failure(self, mock_deps):
        mock_deps["discovery"].discover.return_value = DiscoveryResult(
            success=False, devices=[], error="Connection timed out"
        )
        app = create_app(**mock_deps)
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.get("/api/discover?server=192.168.1.100")
            assert response.status_code == 500
            data = response.get_json()
            assert data["success"] is False
            assert "error" in data


class TestApiEvents:
    """Tests for GET /api/events endpoint."""

    def test_returns_200(self, app_client):
        response = app_client.get("/api/events")
        assert response.status_code == 200

    def test_returns_json_with_events_key(self, app_client):
        response = app_client.get("/api/events")
        data = response.get_json()
        assert "events" in data

    def test_returns_json_with_count_key(self, app_client):
        response = app_client.get("/api/events")
        data = response.get_json()
        assert "count" in data

    def test_returns_events_from_event_log(self, mock_deps):
        mock_deps["event_log"].read_events.return_value = [
            {"ts": "2024-01-15T10:00:00Z", "type": "attach_ok", "device": "Zigbee", "server": "192.168.1.100", "detail": "Attached to port 0"},
            {"ts": "2024-01-15T09:55:00Z", "type": "device_lost", "device": "Zigbee", "server": "192.168.1.100", "detail": "Device not found in port list"},
        ]
        app = create_app(**mock_deps)
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.get("/api/events")
            data = response.get_json()
            assert data["count"] == 2
            assert len(data["events"]) == 2
            assert data["events"][0]["type"] == "attach_ok"

    def test_respects_limit_param(self, mock_deps):
        app = create_app(**mock_deps)
        app.config["TESTING"] = True
        with app.test_client() as client:
            client.get("/api/events?limit=50")
            mock_deps["event_log"].read_events.assert_called_with(limit=50)


class TestApiLogs:
    """Tests for GET /api/logs endpoint."""

    @patch("urllib.request.urlopen")
    def test_returns_json_with_logs_key(self, mock_urlopen, app_client):
        mock_response = MagicMock()
        mock_response.read.return_value = b"line1\nline2\nline3"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        response = app_client.get("/api/logs")
        data = response.get_json()
        assert "logs" in data

    @patch("urllib.request.urlopen")
    def test_returns_log_lines_as_list(self, mock_urlopen, app_client):
        mock_response = MagicMock()
        mock_response.read.return_value = b"2024-01-15 INFO startup\n2024-01-15 INFO ready"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        response = app_client.get("/api/logs")
        data = response.get_json()
        assert "logs" in data
        assert len(data["logs"]) == 2

    @patch("urllib.request.urlopen")
    def test_returns_502_on_supervisor_api_failure(self, mock_urlopen, app_client):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("Connection refused")

        response = app_client.get("/api/logs")
        # The endpoint returns 502 on Supervisor API failures
        assert response.status_code == 502
        data = response.get_json()
        assert "logs" in data
        assert data["logs"] == []
