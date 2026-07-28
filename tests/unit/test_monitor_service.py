"""Unit tests for services.d/monitor/run (monitor service).

Tests the monitor service logic:
- Initial delay: waits 15 seconds before first check
- Detection: identifies missing devices from usbip port output
- Reattach flow: health check → sysfs remount → attach with retries

Requirements: 4.1, 4.2, 4.3
"""

import importlib.util
import importlib.machinery
import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest

from usbip_addon.config import DeviceEntry
from usbip_addon.health import HealthResult
from usbip_addon.usbip_client import AttachResult, PortEntry


def _load_run_module():
    """Load the 'run' script from the monitor service directory as a module."""
    script_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "rootfs", "etc", "services.d", "monitor", "run"
    )
    script_path = os.path.abspath(script_path)
    loader = importlib.machinery.SourceFileLoader("monitor_run", script_path)
    spec = importlib.util.spec_from_loader("monitor_run", loader, origin=script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load the module once for all tests
monitor_run = _load_run_module()


class TestDetectMissingDevices:
    """Tests for detect_missing_devices() function."""

    def test_no_missing_devices(self):
        """When all configured devices are attached, returns empty list."""
        config = MagicMock()
        config.devices = [
            DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1"),
            DeviceEntry(server="192.168.1.101", name="BT", port=3240, busid="1-1"),
        ]

        attached_ports = [
            PortEntry(port=0, server="192.168.1.100", busid="1-1", device_info="..."),
            PortEntry(port=1, server="192.168.1.101", busid="1-1", device_info="..."),
        ]

        missing = monitor_run.detect_missing_devices(config, attached_ports)
        assert missing == []

    def test_one_device_missing(self):
        """When one device is not in attached ports, returns it as missing."""
        config = MagicMock()
        config.devices = [
            DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1"),
            DeviceEntry(server="192.168.1.101", name="BT", port=3240, busid="1-1"),
        ]

        # Only first device is attached
        attached_ports = [
            PortEntry(port=0, server="192.168.1.100", busid="1-1", device_info="..."),
        ]

        missing = monitor_run.detect_missing_devices(config, attached_ports)
        assert len(missing) == 1
        assert missing[0].name == "BT"
        assert missing[0].server == "192.168.1.101"

    def test_all_devices_missing(self):
        """When no devices are attached, returns all configured devices."""
        config = MagicMock()
        config.devices = [
            DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1"),
            DeviceEntry(server="192.168.1.101", name="BT", port=3240, busid="1-1"),
        ]

        attached_ports = []

        missing = monitor_run.detect_missing_devices(config, attached_ports)
        assert len(missing) == 2

    def test_busid_mismatch_detected_as_missing(self):
        """When attached port has different busid, device is still missing."""
        config = MagicMock()
        config.devices = [
            DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1"),
        ]

        # Same server but different busid
        attached_ports = [
            PortEntry(port=0, server="192.168.1.100", busid="2-1", device_info="..."),
        ]

        missing = monitor_run.detect_missing_devices(config, attached_ports)
        assert len(missing) == 1
        assert missing[0].busid == "1-1"


class TestReattachDevice:
    """Tests for reattach_device() function."""

    def test_successful_reattach_on_first_attempt(self):
        """Happy path: health check passes, attach succeeds on first try."""
        device = DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1")
        config = MagicMock()
        config.reattach_retries = 3
        config.attach_delay = 2

        usbip_client = MagicMock()
        usbip_client.remount_sysfs.return_value = True
        usbip_client.attach.return_value = AttachResult(success=True, port=0, stderr="")

        health_checker = MagicMock()
        health_checker.check.return_value = HealthResult(reachable=True, latency_ms=5.0, error=None)

        event_log = MagicMock()

        result = monitor_run.reattach_device(device, config, usbip_client, health_checker, event_log)

        assert result is True
        health_checker.check.assert_called_once_with("192.168.1.100", port=3240)
        usbip_client.remount_sysfs.assert_called_once()
        usbip_client.attach.assert_called_once()

    @patch("time.sleep")
    def test_health_check_failure_counts_as_attempt(self, mock_sleep):
        """When health check fails, it counts against retry limit."""
        device = DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1")
        config = MagicMock()
        config.reattach_retries = 2
        config.attach_delay = 0

        usbip_client = MagicMock()
        health_checker = MagicMock()
        # All health checks fail
        health_checker.check.return_value = HealthResult(
            reachable=False, latency_ms=None, error="Connection refused"
        )
        event_log = MagicMock()

        with patch.object(monitor_run, "time") as mock_time_mod:
            result = monitor_run.reattach_device(device, config, usbip_client, health_checker, event_log)

        assert result is False
        # Health check called for each retry attempt
        assert health_checker.check.call_count == 2
        # Attach never called because health check failed
        usbip_client.attach.assert_not_called()

    def test_attach_failure_retries(self):
        """When attach fails, retries up to configured max."""
        device = DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1")
        config = MagicMock()
        config.reattach_retries = 3
        config.attach_delay = 0

        usbip_client = MagicMock()
        usbip_client.remount_sysfs.return_value = True
        # First two fail, third succeeds
        usbip_client.attach.side_effect = [
            AttachResult(success=False, port=None, stderr="busy"),
            AttachResult(success=False, port=None, stderr="busy"),
            AttachResult(success=True, port=0, stderr=""),
        ]

        health_checker = MagicMock()
        health_checker.check.return_value = HealthResult(reachable=True, latency_ms=5.0, error=None)

        event_log = MagicMock()

        with patch.object(monitor_run, "time") as mock_time_mod:
            result = monitor_run.reattach_device(device, config, usbip_client, health_checker, event_log)

        assert result is True
        assert usbip_client.attach.call_count == 3

    def test_all_retries_exhausted_returns_false(self):
        """When all retries fail, returns False."""
        device = DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1")
        config = MagicMock()
        config.reattach_retries = 2
        config.attach_delay = 0

        usbip_client = MagicMock()
        usbip_client.remount_sysfs.return_value = True
        usbip_client.attach.return_value = AttachResult(
            success=False, port=None, stderr="device busy"
        )

        health_checker = MagicMock()
        health_checker.check.return_value = HealthResult(reachable=True, latency_ms=5.0, error=None)

        event_log = MagicMock()

        with patch.object(monitor_run, "time") as mock_time_mod:
            result = monitor_run.reattach_device(device, config, usbip_client, health_checker, event_log)

        assert result is False
        assert usbip_client.attach.call_count == 2


class TestMonitorServiceMain:
    """Tests for the monitor service main() function."""

    def test_initial_delay_15_seconds(self):
        """Monitor waits 15 seconds before first check (Req 4.2)."""
        config = MagicMock()
        config.log_level = "info"
        config.monitor_interval = 30
        config.reattach_retries = 3
        config.devices = []

        mock_config_cls = MagicMock(return_value=config)

        usbip = MagicMock()
        usbip.list_ports.return_value = []
        mock_usbip_cls = MagicMock(return_value=usbip)

        mock_health_cls = MagicMock()
        mock_event_log_cls = MagicMock()
        mock_notifier_cls = MagicMock()

        flapping = MagicMock()
        flapping.evaluate.return_value = None
        flapping.check_clear.return_value = False
        mock_flapping_cls = MagicMock(return_value=flapping)

        mock_locks_cls = MagicMock()

        sleep_calls = []

        def track_sleep(seconds):
            sleep_calls.append(seconds)
            # Exit after initial delay and first cycle
            if len(sleep_calls) >= 2:
                raise KeyboardInterrupt()

        with patch.object(monitor_run, "configure_logging"), \
             patch.object(monitor_run, "AddonConfig", mock_config_cls), \
             patch.object(monitor_run, "UsbipClient", mock_usbip_cls), \
             patch.object(monitor_run, "HealthChecker", mock_health_cls), \
             patch.object(monitor_run, "EventLog", mock_event_log_cls), \
             patch.object(monitor_run, "NotificationManager", mock_notifier_cls), \
             patch.object(monitor_run, "FlappingDetector", mock_flapping_cls), \
             patch.object(monitor_run, "ServerLockManager", mock_locks_cls), \
             patch.object(monitor_run.time, "sleep", side_effect=track_sleep):
            with pytest.raises((SystemExit, KeyboardInterrupt)):
                monitor_run.main()

        # Verify first sleep call was 15 seconds (initial delay)
        assert sleep_calls[0] == 15

    def test_detects_missing_device_and_records_event(self):
        """When a device is missing, records device_lost event and attempts reattach."""
        device = DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1")

        config = MagicMock()
        config.log_level = "info"
        config.monitor_interval = 30
        config.reattach_retries = 1
        config.attach_delay = 0
        config.devices = [device]

        mock_config_cls = MagicMock(return_value=config)

        # No ports attached → device is missing
        usbip = MagicMock()
        usbip.list_ports.return_value = []
        usbip.remount_sysfs.return_value = True
        usbip.attach.return_value = AttachResult(success=True, port=0, stderr="")
        mock_usbip_cls = MagicMock(return_value=usbip)

        health = MagicMock()
        health.check.return_value = HealthResult(reachable=True, latency_ms=5.0, error=None)
        mock_health_cls = MagicMock(return_value=health)

        event_log = MagicMock()
        mock_event_log_cls = MagicMock(return_value=event_log)

        notifier = MagicMock()
        mock_notifier_cls = MagicMock(return_value=notifier)

        flapping = MagicMock()
        flapping.evaluate.return_value = None
        flapping.check_clear.return_value = False
        mock_flapping_cls = MagicMock(return_value=flapping)

        lock_mgr = MagicMock()
        lock_mgr.lock.return_value.__enter__ = MagicMock(return_value=None)
        lock_mgr.lock.return_value.__exit__ = MagicMock(return_value=False)
        mock_locks_cls = MagicMock(return_value=lock_mgr)

        call_count = [0]

        def sleep_side_effect(seconds):
            call_count[0] += 1
            if call_count[0] >= 2:  # After initial delay and first cycle
                raise KeyboardInterrupt()

        with patch.object(monitor_run, "configure_logging"), \
             patch.object(monitor_run, "AddonConfig", mock_config_cls), \
             patch.object(monitor_run, "UsbipClient", mock_usbip_cls), \
             patch.object(monitor_run, "HealthChecker", mock_health_cls), \
             patch.object(monitor_run, "EventLog", mock_event_log_cls), \
             patch.object(monitor_run, "NotificationManager", mock_notifier_cls), \
             patch.object(monitor_run, "FlappingDetector", mock_flapping_cls), \
             patch.object(monitor_run, "ServerLockManager", mock_locks_cls), \
             patch.object(monitor_run.time, "sleep", side_effect=sleep_side_effect):
            with pytest.raises((SystemExit, KeyboardInterrupt)):
                monitor_run.main()

        # Verify device_lost event recorded
        event_log.record.assert_any_call(
            "device_lost",
            "Zigbee",
            "192.168.1.100",
            "Device no longer attached (busid=1-1)",
        )

        # Verify loss notification sent
        notifier.notify_device_lost.assert_called_once_with("Zigbee", "192.168.1.100")

    def test_successful_reattach_records_recovery(self):
        """When reattach succeeds, records reattach_ok and recovery notification."""
        device = DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1")

        config = MagicMock()
        config.log_level = "info"
        config.monitor_interval = 30
        config.reattach_retries = 1
        config.attach_delay = 0
        config.devices = [device]

        mock_config_cls = MagicMock(return_value=config)

        usbip = MagicMock()
        usbip.list_ports.return_value = []  # Device missing
        usbip.remount_sysfs.return_value = True
        usbip.attach.return_value = AttachResult(success=True, port=0, stderr="")
        mock_usbip_cls = MagicMock(return_value=usbip)

        health = MagicMock()
        health.check.return_value = HealthResult(reachable=True, latency_ms=5.0, error=None)
        mock_health_cls = MagicMock(return_value=health)

        event_log = MagicMock()
        mock_event_log_cls = MagicMock(return_value=event_log)

        notifier = MagicMock()
        mock_notifier_cls = MagicMock(return_value=notifier)

        flapping = MagicMock()
        flapping.evaluate.return_value = None
        flapping.check_clear.return_value = False
        mock_flapping_cls = MagicMock(return_value=flapping)

        lock_mgr = MagicMock()
        lock_mgr.lock.return_value.__enter__ = MagicMock(return_value=None)
        lock_mgr.lock.return_value.__exit__ = MagicMock(return_value=False)
        mock_locks_cls = MagicMock(return_value=lock_mgr)

        call_count = [0]

        def sleep_side_effect(seconds):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise KeyboardInterrupt()

        with patch.object(monitor_run, "configure_logging"), \
             patch.object(monitor_run, "AddonConfig", mock_config_cls), \
             patch.object(monitor_run, "UsbipClient", mock_usbip_cls), \
             patch.object(monitor_run, "HealthChecker", mock_health_cls), \
             patch.object(monitor_run, "EventLog", mock_event_log_cls), \
             patch.object(monitor_run, "NotificationManager", mock_notifier_cls), \
             patch.object(monitor_run, "FlappingDetector", mock_flapping_cls), \
             patch.object(monitor_run, "ServerLockManager", mock_locks_cls), \
             patch.object(monitor_run.time, "sleep", side_effect=sleep_side_effect):
            with pytest.raises((SystemExit, KeyboardInterrupt)):
                monitor_run.main()

        # Verify reattach_ok event recorded
        event_log.record.assert_any_call(
            "reattach_ok",
            "Zigbee",
            "192.168.1.100",
            "Device successfully reattached",
        )

        # Verify recovery notification sent
        notifier.notify_device_recovered.assert_called_once_with("Zigbee", "192.168.1.100")

        # Verify flapping tracker received recovery
        flapping.record_recovery.assert_called_once_with(device.key)
