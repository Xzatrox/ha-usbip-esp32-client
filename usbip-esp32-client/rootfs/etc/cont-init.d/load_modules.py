#!/command/with-contenv python3
"""cont-init.d script to load the vhci-hcd kernel module and verify VHCI access.

Runs during container init. Loads module, remounts sysfs, verifies VHCI.
"""

import os
import subprocess
import sys
import time

from usbip_addon.logging_config import configure_logging, get_logger

configure_logging("info")
logger = get_logger("load_modules")

MODULE_NAME = "vhci-hcd"
SYSFS_MODULE_PATH = "/sys/module/vhci_hcd"
SYSFS_PLATFORM_PATH = "/sys/devices/platform/vhci_hcd.0"


def run(cmd):
    """Run a command and return (rc, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


def diagnose():
    """Print extensive diagnostics about VHCI state."""
    logger.info("=== VHCI DIAGNOSTICS ===")

    # Check /sys/module/vhci_hcd
    exists = os.path.isdir(SYSFS_MODULE_PATH)
    logger.info("/sys/module/vhci_hcd exists: %s", exists)

    # Check /sys/devices/platform/vhci_hcd*
    try:
        platform_entries = os.listdir("/sys/devices/platform")
        vhci = [e for e in platform_entries if "vhci" in e.lower()]
        logger.info("/sys/devices/platform vhci entries: %s", vhci or "(none)")
    except OSError as e:
        logger.error("Cannot list /sys/devices/platform: %s", e)

    # Check if /sys is mounted rw
    rc, out, _ = run(["mount"])
    for line in out.splitlines():
        if "sysfs" in line or "/sys" in line:
            logger.info("Mount: %s", line.strip())

    # Check lsmod for vhci
    rc, out, _ = run(["lsmod"])
    if rc == 0:
        for line in out.splitlines():
            if "vhci" in line:
                logger.info("lsmod: %s", line.strip())

    # Try running usbip version
    rc, out, err = run(["usbip", "version"])
    logger.info("usbip version: rc=%d out='%s' err='%s'", rc, out.strip(), err.strip())

    # Try usbip port
    rc, out, err = run(["usbip", "port"])
    logger.info("usbip port: rc=%d err='%s'", rc, err.strip()[:200])

    # Check /dev/vhci
    logger.info("/dev/vhci exists: %s", os.path.exists("/dev/vhci"))

    # Check platform path contents
    if os.path.isdir(SYSFS_PLATFORM_PATH):
        try:
            entries = os.listdir(SYSFS_PLATFORM_PATH)
            logger.info("%s contents: %s", SYSFS_PLATFORM_PATH, entries[:20])
        except OSError as e:
            logger.info("%s listdir error: %s", SYSFS_PLATFORM_PATH, e)
    else:
        logger.info("%s does NOT exist", SYSFS_PLATFORM_PATH)

    logger.info("=== END DIAGNOSTICS ===")


def main():
    """Load vhci-hcd and verify VHCI access."""
    # Step 1: Load module if needed
    if os.path.isdir(SYSFS_MODULE_PATH):
        logger.info("Module %s already loaded", MODULE_NAME)
    else:
        logger.info("Loading kernel module %s", MODULE_NAME)
        rc, out, err = run(["/sbin/modprobe", MODULE_NAME])
        if rc != 0:
            logger.error("modprobe failed (rc=%d): %s", rc, err)
            diagnose()
            sys.exit(1)
        logger.info("Module loaded successfully")

    # Step 2: Remount sysfs
    logger.info("Remounting sysfs...")
    rc, _, err = run(["mount", "-o", "remount", "-t", "sysfs", "sysfs", "/sys"])
    if rc != 0:
        logger.warning("sysfs remount failed: %s", err)
    else:
        logger.info("sysfs remounted OK")
    time.sleep(1)

    # Step 3: Check VHCI platform device
    if os.path.isdir(SYSFS_PLATFORM_PATH):
        logger.info("VHCI platform device found at %s", SYSFS_PLATFORM_PATH)

        # Try to read status file directly
        status_path = os.path.join(SYSFS_PLATFORM_PATH, "status")
        try:
            with open(status_path, "r") as f:
                lines = f.readlines()
            logger.info("VHCI status has %d lines, first: %s", len(lines), lines[0].strip() if lines else "(empty)")
        except OSError as e:
            logger.error("Cannot read %s: %s", status_path, e)

        # Check the path that usbip tool actually uses
        driver_path = "/sys/bus/platform/drivers/vhci_hcd"
        logger.info("%s exists: %s", driver_path, os.path.isdir(driver_path))
        if os.path.isdir(driver_path):
            try:
                entries = os.listdir(driver_path)
                logger.info("%s contents: %s", driver_path, entries)
            except OSError as e:
                logger.error("Cannot list %s: %s", driver_path, e)
        
        # Also check /sys/bus/platform/devices
        dev_path = "/sys/bus/platform/devices"
        if os.path.isdir(dev_path):
            try:
                entries = os.listdir(dev_path)
                vhci = [e for e in entries if "vhci" in e]
                logger.info("%s vhci entries: %s", dev_path, vhci)
            except OSError as e:
                logger.error("Cannot list %s: %s", dev_path, e)

        # Check /sys/class
        class_path = "/sys/class/usbip"
        logger.info("%s exists: %s", class_path, os.path.isdir(class_path))

        # Try strace equivalent - run usbip with strace if available
        rc, out, err = run(["strace", "-e", "openat", "-f", "usbip", "port"])
        if rc != -1:
            # Filter for vhci-related opens
            relevant = [l for l in err.splitlines() if "vhci" in l.lower() or "ENOENT" in l or "platform" in l]
            for line in relevant[:10]:
                logger.info("strace: %s", line)
        else:
            logger.info("strace not available")

        # Try usbip port
        rc, out, err = run(["usbip", "port"])
        if rc == 0:
            logger.info("usbip port works! output: %s", out.strip()[:200])
        else:
            logger.warning("usbip port still fails: %s", err.strip()[:200])
    else:
        logger.error("VHCI platform device NOT found after remount!")
        diagnose()

    sys.exit(0)


if __name__ == "__main__":
    main()
