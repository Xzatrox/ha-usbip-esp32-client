"""USB/IP CLI client wrapper module.

Wraps the `usbip` command-line tool with structured return values for
attach, detach, port listing, and sysfs remount operations.

Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 7.1, 7.2
"""

import re
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional

from usbip_addon.logging_config import get_logger

logger = get_logger("usbip_client")


@dataclass
class AttachResult:
    """Result of a usbip attach operation.

    Attributes:
        success: Whether the attach command returned exit code 0.
        port: Assigned VHCI port number on success (from usbip port parsing).
        stderr: Stderr output from the command (useful for diagnostics).
    """

    success: bool
    port: Optional[int]
    stderr: str


@dataclass
class PortEntry:
    """A single attached device from usbip port output.

    Attributes:
        port: Local VHCI port number.
        server: Remote server IP address.
        busid: Remote device bus ID.
        device_info: Full device info string from the port listing.
    """

    port: int
    server: str
    busid: str
    device_info: str


# Pattern to parse usbip port output lines
# Handles multiple format variations:
#   "Port 00: <Server IP> -> usbip://192.168.1.100:3240/1-1"  (design doc format)
#   "          00 -> usbip://192.168.1.100:3240/1-1"          (real usbip output)
PORT_LINE_PATTERN = re.compile(
    r"(?:Port\s+)?(\d+)\s*(?::.*)?->\s*usbip://([^:/]+)(?::\d+)?/(\S+)"
)


class UsbipClient:
    """Wrapper around the usbip CLI tool.

    Provides structured interfaces to the usbip command-line operations
    including attach, detach, port listing, and sysfs remount.

    All operations are executed via subprocess and return structured
    results rather than raw command output.
    """

    USBIP_BIN = "usbip"
    MOUNT_BIN = "mount"
    SYSFS_REMOUNT_DELAY = 0.5  # seconds to wait after successful remount

    def attach(
        self, server: str, busid: str = "1-1", port: Optional[int] = None
    ) -> AttachResult:
        """Execute usbip attach --remote=<server> --busid=<busid>.

        Optionally includes --tcp-port <port> when a custom port is specified.

        Args:
            server: IP address of the remote USB/IP server.
            busid: Remote device bus ID (default "1-1").
            port: Optional custom TCP port (uses --tcp-port flag if set).

        Returns:
            AttachResult with success status, assigned port, and stderr output.

        Requirements: 3.1, 3.2
        """
        cmd = [self.USBIP_BIN, "attach", f"--remote={server}", f"--busid={busid}"]

        if port is not None:
            cmd.extend(["--tcp-port", str(port)])

        logger.debug("Executing: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
        except subprocess.TimeoutExpired:
            logger.error("usbip attach timed out for %s (busid=%s)", server, busid)
            return AttachResult(success=False, port=None, stderr="Command timed out")
        except FileNotFoundError:
            logger.error("usbip binary not found")
            return AttachResult(
                success=False, port=None, stderr="usbip binary not found"
            )

        if result.returncode != 0:
            logger.warning(
                "usbip attach failed for %s (busid=%s): %s",
                server,
                busid,
                result.stderr.strip(),
            )
            return AttachResult(
                success=False, port=None, stderr=result.stderr.strip()
            )

        # Attach succeeded - try to determine assigned port
        assigned_port = self._find_assigned_port(server, busid)

        logger.info(
            "usbip attach succeeded for %s (busid=%s), port=%s",
            server,
            busid,
            assigned_port,
        )
        return AttachResult(
            success=True, port=assigned_port, stderr=result.stderr.strip()
        )

    def detach(self, port: int) -> bool:
        """Execute usbip detach --port=<port>.

        Args:
            port: Local VHCI port number to detach.

        Returns:
            True if detach succeeded (exit code 0), False otherwise.

        Requirements: 7.1
        """
        cmd = [self.USBIP_BIN, "detach", "--port", str(port)]

        logger.debug("Executing: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            )
        except subprocess.TimeoutExpired:
            logger.error("usbip detach timed out for port %d", port)
            return False
        except FileNotFoundError:
            logger.error("usbip binary not found")
            return False

        if result.returncode != 0:
            logger.warning(
                "usbip detach failed for port %d: %s",
                port,
                result.stderr.strip(),
            )
            return False

        logger.debug("usbip detach succeeded for port %d", port)
        return True

    def detach_remote(self, server: str, busid: str) -> bool:
        """Execute usbip detach -r <server> -b <busid> (pre-detach).

        Used to clean up stale remote attachments before re-attaching.
        Failures are expected and non-fatal.

        Args:
            server: IP address of the remote USB/IP server.
            busid: Remote device bus ID.

        Returns:
            True if pre-detach succeeded (exit code 0), False otherwise.

        Requirements: 3.1
        """
        cmd = [self.USBIP_BIN, "detach", "-r", server, "-b", busid]

        logger.debug("Executing pre-detach: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            )
        except subprocess.TimeoutExpired:
            logger.debug("usbip detach -r timed out for %s (busid=%s)", server, busid)
            return False
        except FileNotFoundError:
            logger.debug("usbip binary not found for pre-detach")
            return False

        if result.returncode != 0:
            logger.debug(
                "usbip detach -r failed for %s (busid=%s): %s",
                server,
                busid,
                result.stderr.strip(),
            )
            return False

        logger.debug("usbip detach -r succeeded for %s (busid=%s)", server, busid)
        return True

    def list_ports(self) -> List[PortEntry]:
        """Execute usbip port and parse output.

        Parses the output to extract port numbers, server IPs, and bus IDs
        of currently attached devices.

        Returns:
            List of PortEntry objects representing attached devices.
            Returns empty list if the command fails or produces no output.

        Requirements: 3.4, 7.1
        """
        cmd = [self.USBIP_BIN, "port"]

        logger.debug("Executing: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            )
        except subprocess.TimeoutExpired:
            logger.error("usbip port timed out")
            return []
        except FileNotFoundError:
            logger.error("usbip binary not found")
            return []

        if result.returncode != 0:
            logger.warning("usbip port failed: %s", result.stderr.strip())
            return []

        return self._parse_port_output(result.stdout)

    def remount_sysfs(self) -> bool:
        """Execute mount -o remount -t sysfs sysfs /sys.

        Remounts sysfs with read-write access inside the container.
        Waits 0.5 seconds after a successful remount before returning.

        Returns:
            True if remount succeeded, False otherwise.
            On failure, logs a warning (attach should proceed regardless).

        Requirements: 2.1, 2.2
        """
        cmd = [self.MOUNT_BIN, "-o", "remount", "-t", "sysfs", "sysfs", "/sys"]

        logger.debug("Executing sysfs remount: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            )
        except subprocess.TimeoutExpired:
            logger.warning("sysfs remount timed out")
            return False
        except FileNotFoundError:
            logger.warning("mount binary not found")
            return False

        if result.returncode != 0:
            logger.warning(
                "sysfs remount failed: %s", result.stderr.strip()
            )
            return False

        # Wait after successful remount (Req 2.1)
        time.sleep(self.SYSFS_REMOUNT_DELAY)
        logger.debug("sysfs remount succeeded, waited %.1fs", self.SYSFS_REMOUNT_DELAY)
        return True

    def _find_assigned_port(self, server: str, busid: str) -> Optional[int]:
        """Determine the assigned VHCI port after a successful attach.

        Runs usbip port and searches for the entry matching the given
        server and busid.

        Args:
            server: Server IP to match.
            busid: Bus ID to match.

        Returns:
            The port number if found, None otherwise.
        """
        ports = self.list_ports()
        for entry in ports:
            if entry.server == server and entry.busid == busid:
                return entry.port
        return None

    @staticmethod
    def _parse_port_output(output: str) -> List[PortEntry]:
        """Parse usbip port command output into PortEntry objects.

        Expected format:
            Imported USB devices
            ====================
            Port 00: <Server IP> -> usbip://192.168.1.100:3240/1-1
            Port 01: <Server IP> -> usbip://192.168.1.101:3240/1-1

        Args:
            output: Raw stdout from `usbip port` command.

        Returns:
            List of parsed PortEntry objects.
        """
        entries: List[PortEntry] = []

        for line in output.splitlines():
            match = PORT_LINE_PATTERN.search(line)
            if match:
                port_num = int(match.group(1))
                server_ip = match.group(2)
                busid = match.group(3)

                entries.append(
                    PortEntry(
                        port=port_num,
                        server=server_ip,
                        busid=busid,
                        device_info=line.strip(),
                    )
                )

        return entries

    @staticmethod
    def build_attach_command(
        server: str, busid: str = "1-1", port: Optional[int] = None
    ) -> List[str]:
        """Build the usbip attach command arguments.

        Utility method for testing command construction without execution.

        Args:
            server: IP address of the remote USB/IP server.
            busid: Remote device bus ID (default "1-1").
            port: Optional custom TCP port.

        Returns:
            List of command arguments.
        """
        cmd = ["usbip", "attach", f"--remote={server}", f"--busid={busid}"]
        if port is not None:
            cmd.extend(["--tcp-port", str(port)])
        return cmd

    @staticmethod
    def build_detach_remote_command(server: str, busid: str) -> List[str]:
        """Build the usbip detach -r command arguments.

        Utility method for testing command construction without execution.

        Args:
            server: IP address of the remote USB/IP server.
            busid: Remote device bus ID.

        Returns:
            List of command arguments.
        """
        return ["usbip", "detach", "-r", server, "-b", busid]
