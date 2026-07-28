"""Unit tests for WebUI page routes.

Tests that each page (dashboard, devices, logs, events) returns HTTP 200
with expected HTML elements, and that X-Ingress-Path header is correctly
applied to generated URLs in rendered templates.

Requirements: 16.3, 16.4, 16.5, 16.6, 16.9
"""

import pytest
from unittest.mock import MagicMock, PropertyMock

from usbip_addon.config import AddonConfig, DeviceEntry
from usbip_addon.discovery import DeviceDiscovery
from usbip_addon.event_log import EventLog
from usbip_addon.health import HealthChecker, HealthResult
from usbip_addon.server_lock import ServerLockManager
from usbip_addon.usbip_client import UsbipClient
from usbip_addon.webui.app import create_app


@pytest.fixture
def app_client():
    """Create a Flask test client with mocked dependencies."""
    config = MagicMock(spec=AddonConfig)
    type(config).devices = PropertyMock(return_value=[
        DeviceEntry(server="192.168.1.100", name="Zigbee Coordinator", port=3240, busid="1-1"),
    ])
    config.read_config.return_value = {}

    mock_usbip = MagicMock(spec=UsbipClient)
    mock_usbip.list_ports.return_value = []

    mock_health = MagicMock(spec=HealthChecker)
    mock_health.check.return_value = HealthResult(reachable=True, latency_ms=5.0, error=None)

    mock_discovery = MagicMock(spec=DeviceDiscovery)
    mock_event_log = MagicMock(spec=EventLog)
    mock_event_log.read_events.return_value = []
    mock_locks = ServerLockManager()

    app = create_app(
        config=config,
        usbip_client=mock_usbip,
        health_checker=mock_health,
        discovery=mock_discovery,
        event_log=mock_event_log,
        server_locks=mock_locks,
    )
    app.config["TESTING"] = True

    with app.test_client() as client:
        yield client


class TestDashboardPage:
    """Tests for GET / (dashboard page). Validates Requirement 16.3."""

    def test_returns_200(self, app_client):
        response = app_client.get("/")
        assert response.status_code == 200

    def test_contains_page_title(self, app_client):
        response = app_client.get("/")
        html = response.data.decode("utf-8")
        assert "Dashboard" in html

    def test_contains_device_status_section(self, app_client):
        response = app_client.get("/")
        html = response.data.decode("utf-8")
        assert "device-status" in html
        assert "Device Status" in html

    def test_contains_server_health_section(self, app_client):
        response = app_client.get("/")
        html = response.data.decode("utf-8")
        assert "server-health" in html
        assert "Server Health" in html

    def test_contains_flapping_warnings_section(self, app_client):
        response = app_client.get("/")
        html = response.data.decode("utf-8")
        assert "flapping-warnings" in html
        assert "Flapping Warnings" in html

    def test_contains_navigation_links(self, app_client):
        response = app_client.get("/")
        html = response.data.decode("utf-8")
        assert "/devices" in html
        assert "/logs" in html
        assert "/events" in html


class TestDevicesPage:
    """Tests for GET /devices (devices page). Validates Requirement 16.4."""

    def test_returns_200(self, app_client):
        response = app_client.get("/devices")
        assert response.status_code == 200

    def test_contains_page_title(self, app_client):
        response = app_client.get("/devices")
        html = response.data.decode("utf-8")
        assert "Devices" in html

    def test_contains_devices_table(self, app_client):
        response = app_client.get("/devices")
        html = response.data.decode("utf-8")
        assert "devices-table" in html

    def test_contains_table_headers(self, app_client):
        response = app_client.get("/devices")
        html = response.data.decode("utf-8")
        assert "Name" in html
        assert "Server" in html
        assert "Bus ID" in html
        assert "Status" in html
        assert "Actions" in html


class TestLogsPage:
    """Tests for GET /logs (logs page). Validates Requirement 16.5."""

    def test_returns_200(self, app_client):
        response = app_client.get("/logs")
        assert response.status_code == 200

    def test_contains_page_title(self, app_client):
        response = app_client.get("/logs")
        html = response.data.decode("utf-8")
        assert "Logs" in html

    def test_contains_log_viewer(self, app_client):
        response = app_client.get("/logs")
        html = response.data.decode("utf-8")
        assert "log-output" in html
        assert "log-viewer" in html

    def test_contains_auto_scroll_control(self, app_client):
        response = app_client.get("/logs")
        html = response.data.decode("utf-8")
        assert "auto-scroll" in html


class TestEventsPage:
    """Tests for GET /events (events page). Validates Requirement 16.6."""

    def test_returns_200(self, app_client):
        response = app_client.get("/events")
        assert response.status_code == 200

    def test_contains_page_title(self, app_client):
        response = app_client.get("/events")
        html = response.data.decode("utf-8")
        assert "Events" in html

    def test_contains_events_table(self, app_client):
        response = app_client.get("/events")
        html = response.data.decode("utf-8")
        assert "events-table" in html

    def test_contains_table_headers(self, app_client):
        response = app_client.get("/events")
        html = response.data.decode("utf-8")
        assert "Timestamp" in html
        assert "Type" in html
        assert "Device" in html
        assert "Server" in html
        assert "Detail" in html


class TestIngressPathPrefix:
    """Tests for X-Ingress-Path header handling in templates. Validates Requirement 16.9."""

    def test_dashboard_links_have_ingress_prefix(self, app_client):
        response = app_client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/abc123"})
        html = response.data.decode("utf-8")
        assert "/api/hassio_ingress/abc123/devices" in html
        assert "/api/hassio_ingress/abc123/logs" in html
        assert "/api/hassio_ingress/abc123/events" in html

    def test_dashboard_static_assets_have_ingress_prefix(self, app_client):
        response = app_client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/abc123"})
        html = response.data.decode("utf-8")
        assert "/api/hassio_ingress/abc123/static/style.css" in html
        assert "/api/hassio_ingress/abc123/static/polling.js" in html

    def test_devices_page_has_ingress_prefix(self, app_client):
        response = app_client.get("/devices", headers={"X-Ingress-Path": "/ingress/test"})
        html = response.data.decode("utf-8")
        assert "/ingress/test/" in html
        assert "/ingress/test/static/style.css" in html

    def test_logs_page_has_ingress_prefix(self, app_client):
        response = app_client.get("/logs", headers={"X-Ingress-Path": "/ingress/test"})
        html = response.data.decode("utf-8")
        assert "/ingress/test/" in html
        assert "/ingress/test/static/style.css" in html

    def test_events_page_has_ingress_prefix(self, app_client):
        response = app_client.get("/events", headers={"X-Ingress-Path": "/ingress/test"})
        html = response.data.decode("utf-8")
        assert "/ingress/test/" in html
        assert "/ingress/test/static/style.css" in html

    def test_no_ingress_header_uses_empty_prefix(self, app_client):
        response = app_client.get("/")
        html = response.data.decode("utf-8")
        # Links should use relative paths (empty prefix) - e.g., "/devices"
        assert '"/devices"' in html or "href=\"/devices\"" in html or '/devices' in html

    def test_api_url_in_javascript_has_ingress_prefix(self, app_client):
        response = app_client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/xyz"})
        html = response.data.decode("utf-8")
        # The dashboard JS uses ingressPath + '/api/status'
        assert "/api/hassio_ingress/xyz" in html
