"""Unit tests for the notifications module.

Tests NotificationManager behavior including:
- Sending notifications for device loss, recovery, reattach failure, flapping
- 300-second per-device cooldown via monotonic timer
- Respecting notifications_enabled config flag
- Graceful handling of API failures (log warning, no retry)
- Title prefix "USB/IP:" on all notifications
"""

import json
import time
from unittest.mock import patch, MagicMock, PropertyMock
from urllib.error import URLError, HTTPError

import pytest

from usbip_addon.notifications import NotificationManager
from usbip_addon.config import AddonConfig


@pytest.fixture
def mock_config():
    """Create a mock AddonConfig with notifications enabled."""
    config = MagicMock(spec=AddonConfig)
    type(config).notifications_enabled = PropertyMock(return_value=True)
    type(config).token = PropertyMock(return_value="test-token-123")
    config.SUPERVISOR_URL = "http://supervisor"
    return config


@pytest.fixture
def manager(mock_config):
    """Create a NotificationManager with mocked config."""
    return NotificationManager(mock_config)


class TestNotificationManagerInit:
    """Tests for NotificationManager initialization."""

    def test_init_stores_config(self, mock_config):
        mgr = NotificationManager(mock_config)
        assert mgr._config is mock_config

    def test_init_empty_last_sent(self, mock_config):
        mgr = NotificationManager(mock_config)
        assert mgr._last_sent == {}

    def test_cooldown_constant(self):
        assert NotificationManager.COOLDOWN_SECONDS == 300

    def test_endpoint_constant(self):
        assert NotificationManager.ENDPOINT == "/core/api/services/persistent_notification/create"


class TestNotifyDeviceLost:
    """Tests for notify_device_lost."""

    @patch("usbip_addon.notifications.urlopen")
    def test_sends_notification_with_correct_title(self, mock_urlopen, manager):
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))
        assert body["title"] == "USB/IP: Device Lost"

    @patch("usbip_addon.notifications.urlopen")
    def test_sends_notification_with_device_info_in_message(self, mock_urlopen, manager):
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))
        assert "Zigbee Stick" in body["message"]
        assert "192.168.1.100" in body["message"]


class TestNotifyDeviceRecovered:
    """Tests for notify_device_recovered."""

    @patch("usbip_addon.notifications.urlopen")
    def test_sends_recovery_notification(self, mock_urlopen, manager):
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        manager.notify_device_recovered("BT Dongle", "10.0.0.5")

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))
        assert body["title"] == "USB/IP: Device Recovered"
        assert "BT Dongle" in body["message"]
        assert "10.0.0.5" in body["message"]


class TestNotifyReattachFailed:
    """Tests for notify_reattach_failed."""

    @patch("usbip_addon.notifications.urlopen")
    def test_sends_failure_notification(self, mock_urlopen, manager):
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        manager.notify_reattach_failed("Zigbee Stick", "192.168.1.100")

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))
        assert body["title"] == "USB/IP: Reattach Failed"
        assert "Manual intervention required" in body["message"]
        assert "Zigbee Stick" in body["message"]
        assert "192.168.1.100" in body["message"]


class TestNotifyFlapping:
    """Tests for notify_flapping."""

    @patch("usbip_addon.notifications.urlopen")
    def test_sends_warning_level_notification(self, mock_urlopen, manager):
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        manager.notify_flapping("Zigbee Stick", "192.168.1.100", "warning", 3)

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))
        assert body["title"] == "USB/IP: Flapping Warning"
        assert "3" in body["message"]

    @patch("usbip_addon.notifications.urlopen")
    def test_sends_critical_level_notification(self, mock_urlopen, manager):
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        manager.notify_flapping("BT Dongle", "10.0.0.5", "critical", 5)

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))
        assert body["title"] == "USB/IP: Flapping Critical"
        assert "5" in body["message"]
        assert "BT Dongle" in body["message"]


class TestCooldown:
    """Tests for per-device 300-second cooldown enforcement."""

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_first_notification_always_sent(self, mock_monotonic, mock_urlopen, manager):
        mock_monotonic.return_value = 1000.0
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")

        assert mock_urlopen.called

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_second_notification_within_cooldown_suppressed(
        self, mock_monotonic, mock_urlopen, manager
    ):
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        # First call at t=1000
        mock_monotonic.return_value = 1000.0
        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")
        assert mock_urlopen.call_count == 1

        # Second call at t=1100 (within 300s cooldown)
        mock_monotonic.return_value = 1100.0
        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")
        assert mock_urlopen.call_count == 1  # Not called again

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_notification_sent_after_cooldown_expires(
        self, mock_monotonic, mock_urlopen, manager
    ):
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        # First call at t=1000
        mock_monotonic.return_value = 1000.0
        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")
        assert mock_urlopen.call_count == 1

        # Second call at t=1300 (exactly at 300s boundary)
        mock_monotonic.return_value = 1300.0
        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")
        assert mock_urlopen.call_count == 2

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_cooldown_is_per_device(self, mock_monotonic, mock_urlopen, manager):
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        mock_monotonic.return_value = 1000.0
        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")
        assert mock_urlopen.call_count == 1

        # Different device, same time - should send
        mock_monotonic.return_value = 1000.0
        manager.notify_device_lost("BT Dongle", "10.0.0.5")
        assert mock_urlopen.call_count == 2

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_different_notification_types_share_device_cooldown(
        self, mock_monotonic, mock_urlopen, manager
    ):
        """Cooldown is per device_key, not per notification type."""
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        mock_monotonic.return_value = 1000.0
        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")
        assert mock_urlopen.call_count == 1

        # Same device, different notification type, within cooldown
        mock_monotonic.return_value = 1050.0
        manager.notify_device_recovered("Zigbee Stick", "192.168.1.100")
        assert mock_urlopen.call_count == 1  # Suppressed by cooldown


class TestNotificationsEnabled:
    """Tests for notifications_enabled config flag."""

    @patch("usbip_addon.notifications.urlopen")
    def test_disabled_suppresses_all_notifications(self, mock_urlopen, mock_config):
        type(mock_config).notifications_enabled = PropertyMock(return_value=False)
        mgr = NotificationManager(mock_config)

        mgr.notify_device_lost("Zigbee Stick", "192.168.1.100")
        mgr.notify_device_recovered("Zigbee Stick", "192.168.1.100")
        mgr.notify_reattach_failed("Zigbee Stick", "192.168.1.100")
        mgr.notify_flapping("Zigbee Stick", "192.168.1.100", "warning", 3)

        assert not mock_urlopen.called


class TestAPIFailureHandling:
    """Tests for graceful API failure handling."""

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_url_error_logged_as_warning(self, mock_monotonic, mock_urlopen, manager):
        mock_monotonic.return_value = 1000.0
        mock_urlopen.side_effect = URLError("Connection refused")

        with patch("usbip_addon.notifications.logger") as mock_logger:
            manager.notify_device_lost("Zigbee Stick", "192.168.1.100")
            mock_logger.warning.assert_called_once()
            assert "Zigbee Stick" in mock_logger.warning.call_args[0][1]

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_http_error_logged_as_warning(self, mock_monotonic, mock_urlopen, manager):
        mock_monotonic.return_value = 1000.0
        mock_urlopen.side_effect = HTTPError(
            "http://supervisor/...", 500, "Internal Server Error", {}, None
        )

        with patch("usbip_addon.notifications.logger") as mock_logger:
            manager.notify_device_lost("Zigbee Stick", "192.168.1.100")
            mock_logger.warning.assert_called_once()

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_api_failure_does_not_update_cooldown(
        self, mock_monotonic, mock_urlopen, manager
    ):
        """Failed API call should not update last_sent, so retry is possible."""
        mock_monotonic.return_value = 1000.0
        mock_urlopen.side_effect = URLError("Connection refused")

        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")

        # Cooldown should not be set since the notification failed
        assert "Zigbee Stick:192.168.1.100" not in manager._last_sent

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_api_failure_allows_immediate_retry(
        self, mock_monotonic, mock_urlopen, manager
    ):
        """After a failed send, next attempt should go through."""
        # First attempt fails
        mock_monotonic.return_value = 1000.0
        mock_urlopen.side_effect = URLError("Connection refused")
        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")

        # Second attempt succeeds immediately
        mock_monotonic.return_value = 1001.0
        mock_response = MagicMock()
        mock_urlopen.side_effect = None
        mock_urlopen.return_value = mock_response
        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")

        assert mock_urlopen.call_count == 2


class TestAuthorizationHeader:
    """Tests for correct authorization header usage."""

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_uses_bearer_token_from_config(
        self, mock_monotonic, mock_urlopen, manager
    ):
        mock_monotonic.return_value = 1000.0
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")

        request = mock_urlopen.call_args[0][0]
        assert request.get_header("Authorization") == "Bearer test-token-123"
        assert request.get_header("Content-type") == "application/json"

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_posts_to_correct_endpoint(self, mock_monotonic, mock_urlopen, manager):
        mock_monotonic.return_value = 1000.0
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        manager.notify_device_lost("Zigbee Stick", "192.168.1.100")

        request = mock_urlopen.call_args[0][0]
        assert request.full_url == (
            "http://supervisor/core/api/services/persistent_notification/create"
        )
        assert request.method == "POST"


class TestTitlePrefix:
    """Tests that all notification titles have USB/IP: prefix."""

    @patch("usbip_addon.notifications.urlopen")
    @patch("usbip_addon.notifications.time.monotonic")
    def test_all_titles_have_prefix(self, mock_monotonic, mock_urlopen, manager):
        mock_monotonic.return_value = 0.0
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        calls = [
            lambda: manager.notify_device_lost("D", "1.2.3.4"),
            lambda: manager.notify_device_recovered("D", "1.2.3.4"),
            lambda: manager.notify_reattach_failed("D", "1.2.3.4"),
            lambda: manager.notify_flapping("D", "1.2.3.4", "warning", 3),
        ]

        for i, call_fn in enumerate(calls):
            # Reset cooldown by advancing time far enough
            mock_monotonic.return_value = float(i * 400)
            call_fn()

        for call in mock_urlopen.call_args_list:
            request = call[0][0]
            body = json.loads(request.data.decode("utf-8"))
            assert body["title"].startswith("USB/IP:"), (
                f"Title '{body['title']}' doesn't start with 'USB/IP:'"
            )
