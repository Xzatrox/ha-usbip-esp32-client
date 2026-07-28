"""Device discovery module for USB/IP ESP32 servers.

Discovers USB devices on ESP32 servers by executing `usbip list -r <server>`
and parsing the output. The ESP32 USB/IP server produces output in the format:

    Exportable USB devices
    ======================
     - 192.168.1.100
          1-1: Realtek Semiconductor Corp. : unknown product (0bda:5411)

The regex matches lines like:
    <whitespace><busid>: <manufacturer> : <product>

extracting busid, manufacturer, and product name.
"""

import re
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from usbip_addon.logging_config import get_logger

logger = get_logger("discovery")


@dataclass
class DiscoveredDevice:
    """A USB device discovered on an ESP32 server."""

    busid: str
    manufacturer: str
    product: str


@dataclass
class DiscoveryResult:
    """Result of a discovery operation against an ESP32 server."""

    success: bool
    devices: List[DiscoveredDevice] = field(default_factory=list)
    error: Optional[str] = None


class DeviceDiscovery:
    """Discovers USB devices on ESP32 servers via usbip list.

    Executes `usbip list -r <server>` (with optional `--tcp-port <port>`)
    and parses the output to extract available USB devices. Handles the
    10-second subprocess timeout per Requirement 13.5.
    """

    # Regex matching ESP32 usbip list output format:
    # <whitespace><busid>: <manufacturer> : <product>
    # The busid is a non-whitespace token, manufacturer and product are
    # separated by " : " with possible trailing content like (vid:pid).
    DEVICE_PATTERN = re.compile(
        r"^\s+(\S+):\s*(.+?)\s*:\s*(.+?)\s*$"
    )

    TIMEOUT = 10  # seconds

    def discover(self, server: str, port: Optional[int] = None) -> DiscoveryResult:
        """Execute usbip list -r <server> and parse output.

        Args:
            server: IP address of the ESP32 server.
            port: Optional custom TCP port. If specified, adds --tcp-port flag.

        Returns:
            DiscoveryResult with success status, list of discovered devices,
            and error message if the operation failed.
        """
        cmd = ["usbip", "list", "-r", server]
        if port is not None:
            cmd.extend(["--tcp-port", str(port)])

        logger.debug("Running discovery: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            error_msg = f"Discovery timed out after {self.TIMEOUT}s for server {server}"
            logger.error(error_msg)
            return DiscoveryResult(success=False, error=error_msg)
        except FileNotFoundError:
            error_msg = "usbip binary not found on system PATH"
            logger.error(error_msg)
            return DiscoveryResult(success=False, error=error_msg)
        except OSError as e:
            error_msg = f"Failed to execute usbip: {e}"
            logger.error(error_msg)
            return DiscoveryResult(success=False, error=error_msg)

        if result.returncode != 0:
            stderr = result.stderr.strip() if result.stderr else "unknown error"
            error_msg = (
                f"usbip list failed for server {server} "
                f"(exit code {result.returncode}): {stderr}"
            )
            logger.error(error_msg)
            return DiscoveryResult(success=False, error=error_msg)

        # Parse the output
        devices = self._parse_output(result.stdout)

        # Log busid warnings per Requirement 13.6
        for device in devices:
            if device.busid != "1-1":
                logger.warning(
                    "Unexpected busid '%s' on server %s (expected '1-1')",
                    device.busid,
                    server,
                )

        logger.info(
            "Discovery on %s found %d device(s)", server, len(devices)
        )

        return DiscoveryResult(success=True, devices=devices)

    def _parse_output(self, output: str) -> List[DiscoveredDevice]:
        """Parse usbip list output and extract device entries.

        Args:
            output: Raw stdout from `usbip list -r` command.

        Returns:
            List of DiscoveredDevice instances parsed from the output.
        """
        devices: List[DiscoveredDevice] = []

        for line in output.splitlines():
            match = self.DEVICE_PATTERN.match(line)
            if match:
                busid = match.group(1)
                manufacturer = match.group(2).strip()
                product = match.group(3).strip()

                # Strip trailing (vid:pid) pattern from product if present
                product = re.sub(r"\s*\([0-9a-fA-F]{4}:[0-9a-fA-F]{4}\)\s*$", "", product)

                devices.append(
                    DiscoveredDevice(
                        busid=busid,
                        manufacturer=manufacturer,
                        product=product,
                    )
                )

        return devices
