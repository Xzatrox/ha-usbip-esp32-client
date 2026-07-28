"""Configuration module for the USB/IP ESP32 Client add-on.

Reads add-on configuration from the Home Assistant Supervisor API
and provides validated, typed access to all configuration parameters.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 9.1, 9.5, 18.1-18.4
"""

import os
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import json

from usbip_addon.logging_config import get_logger

logger = get_logger("config")

# Maximum number of devices supported (VHCI high-speed port capacity)
MAX_DEVICES = 8

# Default configuration values (Req 6.5)
DEFAULTS = {
    "log_level": "info",
    "monitor_interval": 30,
    "reattach_retries": 3,
    "attach_delay": 2,
    "notifications_enabled": True,
    "flap_warning_threshold": 3,
    "flap_critical_threshold": 5,
    "flap_window_seconds": 600,
    "flap_clear_seconds": 900,
    "devices": [],
}

# Valid ranges for integer config values (Req 6.3, 18.2)
VALID_RANGES = {
    "monitor_interval": (10, 300),
    "reattach_retries": (0, 10),
    "attach_delay": (0, 30),
    "flap_warning_threshold": (1, 20),
    "flap_critical_threshold": (2, 50),
    "flap_window_seconds": (60, 3600),
    "flap_clear_seconds": (60, 7200),
}

VALID_LOG_LEVELS = ("debug", "info", "warning", "error")


@dataclass
class DeviceEntry:
    """A single configured ESP32 USB/IP server device.

    Represents one device to be attached from an ESP32 server.

    Attributes:
        server: IP address of the ESP32 server (required).
        name: Friendly display name (required).
        port: TCP port for USB/IP (default 3240, range 1-65535).
        busid: Remote device bus ID (default "1-1").
    """

    server: str
    name: str
    port: int = 3240
    busid: str = "1-1"

    @property
    def key(self) -> str:
        """Unique key for tracking: server:port:busid."""
        return f"{self.server}:{self.port}:{self.busid}"

    def validate(self) -> Optional[str]:
        """Validate the device entry fields.

        Returns:
            An error message string if validation fails, None if valid.
        """
        if not self.server or not isinstance(self.server, str):
            return "Device 'server' field is required and must be a non-empty string"

        if not self.name or not isinstance(self.name, str):
            return "Device 'name' field is required and must be a non-empty string"

        if not isinstance(self.port, int) or self.port < 1 or self.port > 65535:
            return f"Device 'port' must be an integer between 1 and 65535, got {self.port}"

        if not self.busid or not isinstance(self.busid, str):
            return "Device 'busid' must be a non-empty string"

        return None


class AddonConfig:
    """Reads configuration from the HA Supervisor API.

    Fetches add-on options from the Supervisor API endpoint
    http://supervisor/addons/self/info, with retry logic for startup
    reliability. Validates all parameters and provides typed property
    access with defaults for missing fields.

    Requirements: 6.1, 6.2, 6.7
    """

    SUPERVISOR_URL = "http://supervisor"

    def __init__(self):
        """Initialize the configuration reader.

        Reads the SUPERVISOR_TOKEN from the environment variable.
        """
        self.token: str = os.environ.get("SUPERVISOR_TOKEN", "")
        self._cache: Optional[dict] = None
        self._cache_time: float = 0

    def read_config(self, retries: int = 3, delay: float = 5.0) -> dict:
        """Read configuration from the Supervisor API.

        Makes a GET request to /addons/self/info and extracts the
        'options' dict from the response.

        Args:
            retries: Number of retry attempts on failure (Req 6.7).
            delay: Seconds to wait between retries.

        Returns:
            The options dict from the Supervisor API response.

        Raises:
            RuntimeError: If all retry attempts fail.
        """
        url = f"{self.SUPERVISOR_URL}/addons/self/info"
        last_error: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            try:
                request = Request(url)
                request.add_header("Authorization", f"Bearer {self.token}")
                request.add_header("Content-Type", "application/json")

                with urlopen(request, timeout=10) as response:
                    data = json.loads(response.read().decode("utf-8"))

                options = data.get("data", {}).get("options", {})
                self._cache = options
                self._cache_time = time.monotonic()
                return options

            except (URLError, HTTPError, OSError, json.JSONDecodeError) as e:
                last_error = e
                logger.warning(
                    "Failed to read config from Supervisor API "
                    "(attempt %d/%d): %s",
                    attempt,
                    retries,
                    str(e),
                )
                if attempt < retries:
                    time.sleep(delay)

        raise RuntimeError(
            f"Failed to read configuration after {retries} attempts: {last_error}"
        )

    def _get_option(self, key: str, default=None):
        """Get a config option, applying defaults for missing fields."""
        if self._cache is None:
            self.read_config()
        value = self._cache.get(key)
        if value is None:
            return default if default is not None else DEFAULTS.get(key)
        return value

    def _get_int_option(self, key: str) -> int:
        """Get an integer option, clamped to valid range with default fallback."""
        value = self._get_option(key)
        default = DEFAULTS[key]

        if not isinstance(value, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                return default

        # Clamp to valid range if defined
        if key in VALID_RANGES:
            min_val, max_val = VALID_RANGES[key]
            if value < min_val or value > max_val:
                logger.warning(
                    "Config '%s' value %d out of range [%d, %d], using default %d",
                    key,
                    value,
                    min_val,
                    max_val,
                    default,
                )
                return default

        return value

    @property
    def devices(self) -> List[DeviceEntry]:
        """Parsed and validated device entries.

        Applies defaults for optional fields, validates each entry,
        rejects duplicates and entries exceeding the 8-device limit.

        Requirements: 6.4, 6.8, 9.1, 9.5
        """
        raw_devices = self._get_option("devices", [])

        if not raw_devices:
            logger.warning("Device list is empty; no devices will be attached")
            return []

        entries: List[DeviceEntry] = []
        seen_servers: set = set()

        for i, raw in enumerate(raw_devices):
            if not isinstance(raw, dict):
                logger.warning("Device entry %d is not a dict, skipping", i)
                continue

            # Enforce max device limit (Req 9.1)
            if len(entries) >= MAX_DEVICES:
                logger.error(
                    "Maximum device limit (%d) reached, skipping device: %s",
                    MAX_DEVICES,
                    raw.get("name", f"entry {i}"),
                )
                continue

            # Parse with defaults for optional fields
            server = raw.get("server", "")
            name = raw.get("name", "")
            port = raw.get("port", 3240)
            busid = raw.get("busid", "1-1")

            # Ensure port is int
            if not isinstance(port, int):
                try:
                    port = int(port)
                except (TypeError, ValueError):
                    port = 3240

            entry = DeviceEntry(server=server, name=name, port=port, busid=busid)

            # Validate entry
            error = entry.validate()
            if error:
                logger.warning("Device entry %d invalid: %s", i, error)
                continue

            # Reject duplicate server IPs (Req 9.5)
            if entry.server in seen_servers:
                logger.warning(
                    "Duplicate server IP '%s' (device '%s'), skipping",
                    entry.server,
                    entry.name,
                )
                continue

            seen_servers.add(entry.server)
            entries.append(entry)

        return entries

    @property
    def monitor_interval(self) -> int:
        """Monitoring interval in seconds (range 10-300, default 30)."""
        return self._get_int_option("monitor_interval")

    @property
    def reattach_retries(self) -> int:
        """Number of reattach retries (range 0-10, default 3)."""
        return self._get_int_option("reattach_retries")

    @property
    def attach_delay(self) -> int:
        """Delay between device attachments in seconds (range 0-30, default 2)."""
        return self._get_int_option("attach_delay")

    @property
    def log_level(self) -> str:
        """Configured log level (debug/info/warning/error, default 'info')."""
        value = self._get_option("log_level", "info")
        if not isinstance(value, str) or value.lower() not in VALID_LOG_LEVELS:
            return "info"
        return value.lower()

    @property
    def notifications_enabled(self) -> bool:
        """Whether persistent notifications are enabled (default True)."""
        value = self._get_option("notifications_enabled", True)
        if isinstance(value, bool):
            return value
        return True

    @property
    def flap_warning_threshold(self) -> int:
        """Flapping warning threshold count (range 1-20, default 3)."""
        return self._get_int_option("flap_warning_threshold")

    @property
    def flap_critical_threshold(self) -> int:
        """Flapping critical threshold count (range 2-50, default 5)."""
        return self._get_int_option("flap_critical_threshold")

    @property
    def flap_window_seconds(self) -> int:
        """Flapping detection window in seconds (range 60-3600, default 600)."""
        return self._get_int_option("flap_window_seconds")

    @property
    def flap_clear_seconds(self) -> int:
        """Flapping clear period in seconds (range 60-7200, default 900)."""
        return self._get_int_option("flap_clear_seconds")
