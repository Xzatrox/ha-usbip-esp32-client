#!/command/with-contenv python3
"""cont-init.d script to load the vhci-hcd kernel module and verify VHCI access.

This script runs during container initialization (before services start).
It loads the module, remounts sysfs to expose the platform device, and
verifies the VHCI driver is accessible.

Requirements: 1.1, 1.2, 1.3, 1.4, 12.3
"""

import os
import subprocess
import sys
import time

from usbip_addon.logging_config import configure_logging, get_logger

# Configure logging early
configure_logging("info")
logger = get_logger("load_modules")

MODULE_NAME = "vhci-hcd"
SYSFS_MODULE_PATH = "/sys/module/vhci_hcd"
SYSFS_PLATFORM_PATH = "/sys/devices/platform/vhci_hcd.0"


def is_module_loaded() -> bool:
    """Check if vhci_hcd module is already loaded via sysfs."""
    return os.path.isdir(SYSFS_MODULE_PATH)


def remount_sysfs() -> bool:
    """Remount sysfs to expose platform devices inside container."""
    logger.info("Remounting sysfs to expose VHCI platform device...")
    try:
        result = subprocess.run(
            ["mount", "-o", "remount", "-t", "sysfs", "sysfs", "/sys"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning("sysfs remount failed: %s", result.stderr.strip())
            return False
        time.sleep(0.5)
        logger.info("sysfs remounted successfully")
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("sysfs remount error: %s", e)
        return False


def verify_vhci_platform_device() -> bool:
    """Check if the VHCI platform device is accessible at the expected sysfs path."""
    if os.path.isdir(SYSFS_PLATFORM_PATH):
        status_path = os.path.join(SYSFS_PLATFORM_PATH, "status")
        if os.path.exists(status_path):
            logger.info("VHCI platform device verified at %s", SYSFS_PLATFORM_PATH)
            return True
        else:
            logger.warning("VHCI platform dir exists but no 'status' file")
            return True  # dir exists, might still work
    return False


def load_module() -> None:
    """Load the vhci-hcd kernel module and verify VHCI driver access."""
    # Step 1: Load or verify module
    if is_module_loaded():
        logger.info("Module %s is already loaded", MODULE_NAME)
    else:
        logger.info("Loading kernel module %s", MODULE_NAME)
        try:
            result = subprocess.run(
                ["/sbin/modprobe", MODULE_NAME],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            logger.error("modprobe binary not found at /sbin/modprobe")
            sys.exit(1)
        except subprocess.TimeoutExpired:
            logger.error("modprobe %s timed out", MODULE_NAME)
            sys.exit(1)
        except OSError as e:
            logger.error("Failed to execute modprobe %s: %s", MODULE_NAME, e)
            sys.exit(1)

        if result.returncode != 0:
            stderr_output = result.stderr.strip() if result.stderr else "(no output)"
            logger.error(
                "Failed to load module %s (exit code %d): %s",
                MODULE_NAME,
                result.returncode,
                stderr_output,
            )
            sys.exit(1)

        logger.info("Module %s loaded successfully", MODULE_NAME)

    # Step 2: Remount sysfs to expose platform devices
    remount_sysfs()

    # Step 3: Verify VHCI platform device is accessible
    if verify_vhci_platform_device():
        logger.info("VHCI driver fully operational")
        sys.exit(0)

    # Platform device not found — try remount again
    logger.warning(
        "VHCI platform device not found at %s, retrying remount...",
        SYSFS_PLATFORM_PATH,
    )
    remount_sysfs()
    time.sleep(1)

    if verify_vhci_platform_device():
        logger.info("VHCI driver accessible after retry")
        sys.exit(0)

    # Log diagnostic info and continue (don't block startup)
    logger.error(
        "VHCI platform device NOT accessible at %s. "
        "The usbip tool will fail. Check that kernel module vhci-hcd "
        "is loaded on the HOST and the container has proper device access.",
        SYSFS_PLATFORM_PATH,
    )
    # List what's in /sys/devices/platform for debugging
    try:
        entries = os.listdir("/sys/devices/platform")
        vhci_entries = [e for e in entries if "vhci" in e.lower()]
        logger.info("Platform devices with 'vhci': %s", vhci_entries or "(none)")
        logger.info("Total platform entries: %d", len(entries))
    except OSError as e:
        logger.error("Cannot list /sys/devices/platform: %s", e)

    # Exit 0 to not block startup — services will report the error
    sys.exit(0)


if __name__ == "__main__":
    load_module()
