"""Unit tests for services.d/usbip/run (attach service).

Tests the attach service logic:
- Normal flow: reads config, discovers, health checks, attaches
- Empty device list: logs warning, sleeps forever
- API retry: handles config read failures

Requirements: 3.1, 3.3, 6.1, 6.7, 6.8
"""

import importlib.util
import importlib.machinery
import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest

from usbip_addon.config import DeviceEntry
from usbip_addon.health import HealthResult
from usbip_addon.discovery import DiscoveryResult, DiscoveredDevice
from usbip_addon.usbip_client import AttachResult, PortEntry


def _load_run_module():
    """Load the 'run' script from the usbip service directory as a module."""
    script_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "rootfs", "etc", "services.d", "usbip", "run"
    )
    script_path = os.path.abspath(script_path)
    loader = importlib.machinery.SourceFileLoader("usbip_run", script_path)
    spec = importlib.util.spec_from_loader("usbip_run", loader, origin=script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load the module once for all tests
usbip_run = _load_run_module()


class TestAttachServiceMain:
    """Tests for the attach service main() function."""

    @patch("usbip_addon.server_lock.ServerLockManager")
    @patch("usbip_addon.event_log.EventLog")
    @patch("usbip_addon.health.HealthChecker")
    @patch("usbip_addon.discovery.DeviceDiscovery")
    @patch("usbip_addon.usbip_client.UsbipClient")
    @patch("usbip_addon.config.AddonConfig")
    def test_normal_flow_single_device(
        self,
        mock_config_cls,
        mock_usbip_cls,
        mock_discovery_cls,
        mock_health_cls,
        mock_event_log_cls,
        mock_locks_cls,
    ):
        """Normal flow: one device, health check passes, attach succeeds."""
        # Setup config
        config = MagicMock()
        config.log_level = "info"
        config.devices = [
            DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1")
        ]
        config.attach_delay = 2
        config.reattach_retries = 3
        mock_config_cls.return_value = config

        # Setup discovery
        discovery = MagicMock()
        discovery.discover.return_value = DiscoveryResult(
            success=True,
            devices=[DiscoveredDevice(busid="1-1", manufacturer="Test", product="Device")],
            error=None,
        )
        mock_discovery_cls.return_value = discovery

        # Setup health checker
        health = MagicMock()
        health.check.return_value = HealthResult(reachable=True, latency_ms=5.0, error=None)
        mock_health_cls.return_value = health

        # Setup usbip client
        usbip = MagicMock()
        usbip.detach_remote.return_value = True
        usbip.remount_sysfs.return_value = True
        usbip.attach.return_value = AttachResult(success=True, port=0, stderr="")
        mock_usbip_cls.return_value = usbip

        # Setup event log
        event_log = MagicMock()
        mock_event_log_cls.return_value = event_log

        # Setup server locks (real context manager behavior)
        lock_mgr = MagicMock()
        lock_mgr.lock.return_value.__enter__ = MagicMock(return_value=None)
        lock_mgr.lock.return_value.__exit__ = MagicMock(return_value=False)
        mock_locks_cls.return_value = lock_mgr

        # Patch _sleep_forever and time.sleep to prevent blocking
        with patch.object(usbip_run, "_sleep_forever"), \
             patch.object(usbip_run, "time") as mock_time, \
             patch.object(usbip_run, "configure_logging"), \
             patch.object(usbip_run, "AddonConfig", mock_config_cls), \
             patch.object(usbip_run, "UsbipClient", mock_usbip_cls), \
             patch.object(usbip_run, "DeviceDiscovery", mock_discovery_cls), \
             patch.object(usbip_run, "HealthChecker", mock_health_cls), \
             patch.object(usbip_run, "EventLog", mock_event_log_cls), \
             patch.object(usbip_run, "ServerLockManager", mock_locks_cls):
            usbip_run.main()

        # Verify config was read
        config.read_config.assert_called_once_with(retries=3, delay=5.0)

        # Verify discovery was run
        discovery.discover.assert_called_once()

        # Verify health check
        health.check.assert_called_once_with("192.168.1.100", port=3240)

        # Verify pre-detach
        usbip.detach_remote.assert_called_once_with("192.168.1.100", "1-1")

        # Verify sysfs remount
        usbip.remount_sysfs.assert_called_once()

        # Verify attach
        usbip.attach.assert_called_once()

        # Verify attach_ok event recorded
        event_log.record.assert_called_with(
            "attach_ok", "Zigbee", "192.168.1.100", "Attached to port 0"
        )

    def test_empty_device_list_idles(self):
        """With empty device list, logs warning and sleeps forever."""
        config = MagicMock()
        config.log_level = "info"
        config.devices = []

        mock_config_cls = MagicMock(return_value=config)

        with patch.object(usbip_run, "_sleep_forever") as mock_sleep_forever, \
             patch.object(usbip_run, "configure_logging"), \
             patch.object(usbip_run, "AddonConfig", mock_config_cls):
            usbip_run.main()

        # Verify sleep_forever was called (service idles)
        mock_sleep_forever.assert_called_once()

    def test_config_read_failure_exits(self):
        """When Supervisor API config read fails after retries, exits non-zero."""
        config = MagicMock()
        config.read_config.side_effect = RuntimeError("Supervisor unreachable")
        mock_config_cls = MagicMock(return_value=config)

        with patch.object(usbip_run, "configure_logging"), \
             patch.object(usbip_run, "AddonConfig", mock_config_cls):
            with pytest.raises(SystemExit) as exc_info:
                usbip_run.main()
            assert exc_info.value.code == 1

    def test_health_check_failure_skips_device(self):
        """When health check fails, device is skipped and attach_fail event recorded."""
        config = MagicMock()
        config.log_level = "info"
        config.devices = [
            DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1")
        ]
        config.attach_delay = 2
        config.reattach_retries = 3

        mock_config_cls = MagicMock(return_value=config)

        discovery = MagicMock()
        discovery.discover.return_value = DiscoveryResult(success=True, devices=[], error=None)
        mock_discovery_cls = MagicMock(return_value=discovery)

        # Health check fails
        health = MagicMock()
        health.check.return_value = HealthResult(
            reachable=False, latency_ms=None, error="Connection refused"
        )
        mock_health_cls = MagicMock(return_value=health)

        usbip = MagicMock()
        mock_usbip_cls = MagicMock(return_value=usbip)

        event_log = MagicMock()
        mock_event_log_cls = MagicMock(return_value=event_log)

        lock_mgr = MagicMock()
        lock_mgr.lock.return_value.__enter__ = MagicMock(return_value=None)
        lock_mgr.lock.return_value.__exit__ = MagicMock(return_value=False)
        mock_locks_cls = MagicMock(return_value=lock_mgr)

        with patch.object(usbip_run, "_sleep_forever"), \
             patch.object(usbip_run, "time") as mock_time, \
             patch.object(usbip_run, "configure_logging"), \
             patch.object(usbip_run, "AddonConfig", mock_config_cls), \
             patch.object(usbip_run, "UsbipClient", mock_usbip_cls), \
             patch.object(usbip_run, "DeviceDiscovery", mock_discovery_cls), \
             patch.object(usbip_run, "HealthChecker", mock_health_cls), \
             patch.object(usbip_run, "EventLog", mock_event_log_cls), \
             patch.object(usbip_run, "ServerLockManager", mock_locks_cls):
            usbip_run.main()

        # Verify attach was NOT called
        usbip.attach.assert_not_called()

        # Verify attach_fail event was recorded
        event_log.record.assert_called_once_with(
            "attach_fail",
            "Zigbee",
            "192.168.1.100",
            "Health check failed: Connection refused",
        )

    def test_attach_retries_exhausted(self):
        """When all attach attempts fail, attach_fail event is recorded."""
        config = MagicMock()
        config.log_level = "info"
        config.devices = [
            DeviceEntry(server="192.168.1.100", name="Zigbee", port=3240, busid="1-1")
        ]
        config.attach_delay = 2
        config.reattach_retries = 2

        mock_config_cls = MagicMock(return_value=config)

        discovery = MagicMock()
        discovery.discover.return_value = DiscoveryResult(success=True, devices=[], error=None)
        mock_discovery_cls = MagicMock(return_value=discovery)

        health = MagicMock()
        health.check.return_value = HealthResult(reachable=True, latency_ms=5.0, error=None)
        mock_health_cls = MagicMock(return_value=health)

        # All attach attempts fail
        usbip = MagicMock()
        usbip.detach_remote.return_value = True
        usbip.remount_sysfs.return_value = True
        usbip.attach.return_value = AttachResult(
            success=False, port=None, stderr="connection refused"
        )
        mock_usbip_cls = MagicMock(return_value=usbip)

        event_log = MagicMock()
        mock_event_log_cls = MagicMock(return_value=event_log)

        lock_mgr = MagicMock()
        lock_mgr.lock.return_value.__enter__ = MagicMock(return_value=None)
        lock_mgr.lock.return_value.__exit__ = MagicMock(return_value=False)
        mock_locks_cls = MagicMock(return_value=lock_mgr)

        with patch.object(usbip_run, "_sleep_forever"), \
             patch.object(usbip_run, "time") as mock_time, \
             patch.object(usbip_run, "configure_logging"), \
             patch.object(usbip_run, "AddonConfig", mock_config_cls), \
             patch.object(usbip_run, "UsbipClient", mock_usbip_cls), \
             patch.object(usbip_run, "DeviceDiscovery", mock_discovery_cls), \
             patch.object(usbip_run, "HealthChecker", mock_health_cls), \
             patch.object(usbip_run, "EventLog", mock_event_log_cls), \
             patch.object(usbip_run, "ServerLockManager", mock_locks_cls):
            usbip_run.main()

        # Verify attach was called reattach_retries times
        assert usbip.attach.call_count == 2

        # Verify attach_fail event was recorded with appropriate message
        last_call = event_log.record.call_args_list[-1]
        assert last_call[0][0] == "attach_fail"
        assert "All 2 attempts failed" in last_call[0][3]
