#!/command/with-contenv python3
"""cont-init.d script to load the vhci-hcd kernel module.

This script runs during container initialization (before services start).
If modprobe fails, it exits non-zero to prevent services from starting.

Requirements: 1.1, 1.2, 1.3, 1.4, 12.3
"""

import os
import subprocess
import sys

from usbip_addon.logging_config import configure_logging, get_logger

# Configure logging early
configure_logging("info")
logger = get_logger("load_modules")

MODULE_NAME = "vhci-hcd"
SYSFS_PATH = "/sys/module/vhci_hcd"


def is_module_loaded() -> bool:
    """Check if vhci_hcd module is already loaded via sysfs."""
    return os.path.isdir(SYSFS_PATH)


def verify_module_loaded() -> bool:
    """Verify module is loaded via /sys/module/vhci_hcd or lsmod."""
    if os.path.isdir(SYSFS_PATH):
        return True

    # Fallback: check lsmod output
    try:
        result = subprocess.run(
            ["lsmod"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and "vhci_hcd" in result.stdout:
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return False


def load_module() -> None:
    """Load the vhci-hcd kernel module.

    Exits non-zero on modprobe failure to prevent services from starting.
    Logs warning if modprobe succeeds but verification fails.
    """
    # Check if already loaded
    if is_module_loaded():
        logger.info("Module %s is already loaded, skipping", MODULE_NAME)
        sys.exit(0)

    # Attempt to load the module
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

    # If modprobe failed, log error and exit non-zero (fatal)
    if result.returncode != 0:
        stderr_output = result.stderr.strip() if result.stderr else "(no output)"
        logger.error(
            "Failed to load module %s (exit code %d): %s",
            MODULE_NAME,
            result.returncode,
            stderr_output,
        )
        sys.exit(1)

    # modprobe succeeded - verify module is actually loaded
    if verify_module_loaded():
        logger.info("Module %s loaded and verified successfully", MODULE_NAME)
        sys.exit(0)
    else:
        # modprobe returned 0 but verification failed - log warning, continue
        logger.warning(
            "modprobe %s succeeded but module could not be verified "
            "in /sys/module/vhci_hcd or lsmod output",
            MODULE_NAME,
        )
        sys.exit(0)


if __name__ == "__main__":
    load_module()
