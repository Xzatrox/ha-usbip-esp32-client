#!/command/with-contenv python3
"""cont-finish.d script to detach all USB/IP devices during shutdown.

This script runs during container shutdown to cleanly release all attached
USB/IP devices, freeing kernel VHCI resources and preventing stale ports.

Flow:
1. Try to list attached ports via UsbipClient.list_ports()
2. If successful and ports found: detach each port with 0.5s delay
3. If list_ports() returns empty or fails: blind detach ports 0-15
4. Handle FileNotFoundError for missing usbip binary: log warning, exit 0
5. Log summary: X detached successfully, Y failed

Requirements: 7.1, 7.2, 7.3, 7.4, 12.4
"""

import subprocess
import sys
import time

from usbip_addon.logging_config import configure_logging, get_logger

# Configure logging early
configure_logging("info")
logger = get_logger("detach_devices")

DETACH_DELAY = 0.5  # seconds between detach commands
BLIND_DETACH_RANGE = range(0, 16)  # ports 0-15 inclusive


def check_usbip_binary() -> bool:
    """Check if the usbip binary is available on the system PATH.

    Returns:
        True if the binary is found, False otherwise.
    """
    try:
        subprocess.run(
            ["usbip", "version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return True
    except FileNotFoundError:
        return False
    except (subprocess.TimeoutExpired, OSError):
        # Binary exists but had an issue - still consider it available
        return True


def list_attached_ports() -> list:
    """List currently attached USB/IP ports.

    Returns:
        List of port numbers (integers) if successful, or None if
        the port listing failed.
    """
    from usbip_addon.usbip_client import UsbipClient

    client = UsbipClient()
    try:
        ports = client.list_ports()
    except FileNotFoundError:
        # usbip binary not found - propagate up
        raise
    except Exception as e:
        logger.warning("Failed to list ports: %s", e)
        return None

    if not ports:
        return None

    return [entry.port for entry in ports]


def detach_port(port: int) -> bool:
    """Detach a single port.

    Args:
        port: VHCI port number to detach.

    Returns:
        True if detach succeeded, False otherwise.
    """
    from usbip_addon.usbip_client import UsbipClient

    client = UsbipClient()
    return client.detach(port)


def detach_all() -> None:
    """Detach all attached USB/IP devices.

    Lists attached ports and detaches each with a delay.
    Falls back to blind detach (ports 0-15) if listing fails.
    Logs a summary of detached/failed counts.
    """
    detached = 0
    failed = 0

    # Try to get list of attached ports
    ports = list_attached_ports()

    if ports is None:
        # Port listing failed or returned empty - blind detach ports 0-15
        logger.info(
            "Port listing failed or empty, performing blind detach of ports 0-15"
        )
        for port in BLIND_DETACH_RANGE:
            if detach_port(port):
                detached += 1
            else:
                failed += 1

            # Delay between detach commands (skip after last)
            if port < 15:
                time.sleep(DETACH_DELAY)
    else:
        # Detach each listed port with delay
        logger.info("Found %d attached port(s), detaching", len(ports))
        for i, port in enumerate(ports):
            if detach_port(port):
                detached += 1
            else:
                failed += 1

            # Delay between detach commands (skip after last)
            if i < len(ports) - 1:
                time.sleep(DETACH_DELAY)

    # Log summary
    logger.info(
        "Shutdown detach complete: %d detached successfully, %d failed",
        detached,
        failed,
    )


def main() -> None:
    """Main entry point for the shutdown detach script."""
    logger.info("Starting shutdown device detachment")

    # Check if usbip binary is available
    if not check_usbip_binary():
        logger.warning("usbip binary not found, skipping detachment")
        sys.exit(0)

    try:
        detach_all()
    except FileNotFoundError:
        # usbip binary disappeared between check and use
        logger.warning("usbip binary not found during detachment, exiting")
        sys.exit(0)
    except Exception as e:
        logger.error("Unexpected error during shutdown detachment: %s", e)
        # Exit 0 anyway - this is a shutdown script, don't block shutdown
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
