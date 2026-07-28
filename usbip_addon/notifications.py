"""Home Assistant persistent notification sender with per-device cooldown.

Sends notifications via the Supervisor API endpoint for device events
(loss, recovery, reattach failure, flapping). Applies a 300-second
per-device cooldown using monotonic time to prevent notification spam.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6
"""

import time
import logging
from typing import Dict
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import json

from usbip_addon.config import AddonConfig

logger = logging.getLogger("notifications")


class NotificationManager:
    """Sends HA persistent notifications with per-device cooldown.

    Uses the Supervisor API to create persistent notifications in Home
    Assistant. Each notification is subject to a 300-second cooldown
    per device key (device_name:server) to prevent flooding.

    The manager respects the notifications_enabled config flag — when
    disabled, all notifications are silently suppressed.

    Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6
    """

    COOLDOWN_SECONDS = 300
    ENDPOINT = "/core/api/services/persistent_notification/create"

    def __init__(self, config: AddonConfig):
        """Initialize the notification manager.

        Args:
            config: AddonConfig instance for reading notifications_enabled
                    and the supervisor token.
        """
        self._config = config
        self._last_sent: Dict[str, float] = {}  # device_key -> monotonic time

    def notify_device_lost(self, device_name: str, server: str) -> None:
        """Send a device loss notification.

        Notifies the user that a device has been lost and is no longer
        attached. Subject to cooldown and enabled check.

        Args:
            device_name: Friendly name of the device.
            server: IP address of the ESP32 server.

        Requirements: 10.1
        """
        title = "USB/IP: Device Lost"
        message = (
            f"Device '{device_name}' on server {server} is no longer attached. "
            f"Attempting reattachment."
        )
        self._send(device_name, server, title, message)

    def notify_device_recovered(self, device_name: str, server: str) -> None:
        """Send a device recovery notification.

        Notifies the user that a previously lost device has been
        successfully reattached. Subject to cooldown and enabled check.

        Args:
            device_name: Friendly name of the device.
            server: IP address of the ESP32 server.

        Requirements: 10.2
        """
        title = "USB/IP: Device Recovered"
        message = (
            f"Device '{device_name}' on server {server} has been "
            f"successfully reattached."
        )
        self._send(device_name, server, title, message)

    def notify_reattach_failed(self, device_name: str, server: str) -> None:
        """Send a reattach failure notification.

        Notifies the user that all reattach attempts have been exhausted
        and manual intervention is required. Subject to cooldown and
        enabled check.

        Args:
            device_name: Friendly name of the device.
            server: IP address of the ESP32 server.

        Requirements: 10.3
        """
        title = "USB/IP: Reattach Failed"
        message = (
            f"Device '{device_name}' on server {server} could not be reattached "
            f"after all retry attempts. Manual intervention required."
        )
        self._send(device_name, server, title, message)

    def notify_flapping(
        self, device_name: str, server: str, level: str, count: int
    ) -> None:
        """Send a flapping state notification.

        Notifies the user that a device is experiencing instability
        (repeated disconnect/reconnect cycles). Subject to cooldown
        and enabled check.

        Args:
            device_name: Friendly name of the device.
            server: IP address of the ESP32 server.
            level: Flapping severity level ("warning" or "critical").
            count: Number of recovery events in the flapping window.

        Requirements: 14.3, 14.4
        """
        level_label = level.capitalize()
        title = f"USB/IP: Flapping {level_label}"
        message = (
            f"Device '{device_name}' on server {server} is flapping "
            f"({level} level). {count} recoveries detected in the "
            f"monitoring window."
        )
        self._send(device_name, server, title, message)

    def _send(
        self, device_name: str, server: str, title: str, message: str
    ) -> None:
        """Send a notification if enabled and not in cooldown.

        Checks the notifications_enabled config flag, applies per-device
        cooldown, and sends via the Supervisor API. API failures are
        logged as warnings without retry.

        Args:
            device_name: Friendly name of the device.
            server: IP address of the ESP32 server.
            title: Notification title (already prefixed with "USB/IP:").
            message: Notification body message.

        Requirements: 10.4, 10.5, 10.6
        """
        # Respect notifications_enabled config flag (Req 10.6)
        if not self._config.notifications_enabled:
            return

        # Apply per-device cooldown (Req 10.4)
        device_key = f"{device_name}:{server}"
        now = time.monotonic()
        last = self._last_sent.get(device_key)
        if last is not None and (now - last) < self.COOLDOWN_SECONDS:
            return

        # Attempt to send the notification
        try:
            self._post_notification(title, message)
            self._last_sent[device_key] = now
        except (URLError, HTTPError, OSError) as exc:
            # Log warning on API failure, no retry (Req 10.5)
            logger.warning(
                "Failed to send notification for device '%s' on %s: %s",
                device_name,
                server,
                exc,
            )

    def _post_notification(self, title: str, message: str) -> None:
        """POST the notification to the Supervisor API.

        Sends a JSON payload to the persistent_notification/create
        endpoint with the supervisor token for authentication.

        Args:
            title: Notification title.
            message: Notification body.

        Raises:
            URLError: On network/connection failure.
            HTTPError: On non-2xx response from the API.
        """
        url = f"{AddonConfig.SUPERVISOR_URL}{self.ENDPOINT}"
        payload = json.dumps({"title": title, "message": message}).encode("utf-8")

        request = Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._config.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        response = urlopen(request, timeout=10)
        response.close()
