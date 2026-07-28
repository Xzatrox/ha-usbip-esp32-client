"""Unit tests for cont-finish.d/detach_devices.py script.

Tests the shutdown device detachment logic:
- Graceful detach: lists ports and detaches each one
- Blind detach: when port listing fails, detaches ports 0-15
- Missing binary: when usbip not found, logs warning and exits 0

Requirements: 7.1, 7.2, 7.3, 7.4
"""

import subprocess
import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest

# Add the rootfs path so we can import the script
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "rootfs", "etc", "cont-finish.d"
))

from detach_devices import (
    check_usbip_binary,
    list_attached_ports,
    detach_port,
    detach_all,
    main,
    BLIND_DETACH_RANGE,
    DETACH_DELAY,
)


class TestCheckUsbipBinary:
    """Tests for check_usbip_binary() function."""

    @patch("subprocess.run")
    def test_binary_found(self, mock_run):
        """Returns True when usbip binary is available."""
        mock_run.return_value = MagicMock(returncode=0, stdout="usbip (usbip-utils 2.0)")
        assert check_usbip_binary() is True

    @patch("subprocess.run")
    def test_binary_not_found(self, mock_run):
        """Returns False when usbip binary is not on PATH."""
        mock_run.side_effect = FileNotFoundError("No such file: usbip")
        assert check_usbip_binary() is False

    @patch("subprocess.run")
    def test_binary_exists_but_timeout(self, mock_run):
        """Returns True even if usbip version times out (binary exists)."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="usbip", timeout=5)
        assert check_usbip_binary() is True

    @patch("subprocess.run")
    def test_binary_exists_but_oserror(self, mock_run):
        """Returns True on OSError (binary exists but had execution issue)."""
        mock_run.side_effect = OSError("Permission denied")
        assert check_usbip_binary() is True


class TestListAttachedPorts:
    """Tests for list_attached_ports() function."""

    @patch("usbip_addon.usbip_client.UsbipClient")
    def test_returns_port_numbers(self, mock_client_cls):
        """Returns list of port integers from UsbipClient."""
        from usbip_addon.usbip_client import PortEntry

        mock_client = MagicMock()
        mock_client.list_ports.return_value = [
            PortEntry(port=0, server="192.168.1.100", busid="1-1", device_info="..."),
            PortEntry(port=1, server="192.168.1.101", busid="1-1", device_info="..."),
        ]
        mock_client_cls.return_value = mock_client

        result = list_attached_ports()
        assert result == [0, 1]

    @patch("usbip_addon.usbip_client.UsbipClient")
    def test_returns_none_on_empty_ports(self, mock_client_cls):
        """Returns None when no ports are attached."""
        mock_client = MagicMock()
        mock_client.list_ports.return_value = []
        mock_client_cls.return_value = mock_client

        result = list_attached_ports()
        assert result is None

    @patch("usbip_addon.usbip_client.UsbipClient")
    def test_returns_none_on_exception(self, mock_client_cls):
        """Returns None when list_ports raises an exception."""
        mock_client = MagicMock()
        mock_client.list_ports.side_effect = RuntimeError("subprocess error")
        mock_client_cls.return_value = mock_client

        result = list_attached_ports()
        assert result is None

    @patch("usbip_addon.usbip_client.UsbipClient")
    def test_raises_on_file_not_found(self, mock_client_cls):
        """Propagates FileNotFoundError up for main() to handle."""
        mock_client = MagicMock()
        mock_client.list_ports.side_effect = FileNotFoundError("usbip not found")
        mock_client_cls.return_value = mock_client

        with pytest.raises(FileNotFoundError):
            list_attached_ports()


class TestDetachAll:
    """Tests for detach_all() function."""

    @patch("detach_devices.time.sleep")
    @patch("detach_devices.detach_port")
    @patch("detach_devices.list_attached_ports")
    def test_graceful_detach_listed_ports(self, mock_list, mock_detach, mock_sleep):
        """Detaches each listed port with delay between them."""
        mock_list.return_value = [0, 1, 2]
        mock_detach.return_value = True

        detach_all()

        # Verify detach called for each port
        assert mock_detach.call_count == 3
        mock_detach.assert_any_call(0)
        mock_detach.assert_any_call(1)
        mock_detach.assert_any_call(2)

        # Verify delays between detach commands (not after last)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(DETACH_DELAY)

    @patch("detach_devices.time.sleep")
    @patch("detach_devices.detach_port")
    @patch("detach_devices.list_attached_ports")
    def test_blind_detach_on_list_failure(self, mock_list, mock_detach, mock_sleep):
        """When port listing fails, performs blind detach on ports 0-15."""
        mock_list.return_value = None  # listing failed
        mock_detach.return_value = True

        detach_all()

        # Verify blind detach covers all 16 ports
        assert mock_detach.call_count == 16
        for port in range(16):
            mock_detach.assert_any_call(port)

    @patch("detach_devices.time.sleep")
    @patch("detach_devices.detach_port")
    @patch("detach_devices.list_attached_ports")
    def test_counts_successes_and_failures(self, mock_list, mock_detach, mock_sleep):
        """Correctly counts successful and failed detachments."""
        mock_list.return_value = [0, 1, 2]
        # Port 1 fails to detach
        mock_detach.side_effect = [True, False, True]

        # detach_all just logs internally; verify detach_port calls
        detach_all()

        assert mock_detach.call_count == 3


class TestMain:
    """Tests for the main() entry point."""

    @patch("detach_devices.detach_all")
    @patch("detach_devices.check_usbip_binary")
    def test_graceful_flow(self, mock_check, mock_detach_all):
        """Normal flow: binary found, detach_all called, exits 0."""
        mock_check.return_value = True

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_detach_all.assert_called_once()

    @patch("detach_devices.detach_all")
    @patch("detach_devices.check_usbip_binary")
    def test_missing_binary_exits_zero(self, mock_check, mock_detach_all):
        """When usbip binary not found, logs warning and exits 0 (Req 7.4)."""
        mock_check.return_value = False

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        # detach_all should NOT be called
        mock_detach_all.assert_not_called()

    @patch("detach_devices.detach_all")
    @patch("detach_devices.check_usbip_binary")
    def test_file_not_found_during_detach(self, mock_check, mock_detach_all):
        """When usbip disappears during detach, exits 0."""
        mock_check.return_value = True
        mock_detach_all.side_effect = FileNotFoundError("usbip gone")

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    @patch("detach_devices.detach_all")
    @patch("detach_devices.check_usbip_binary")
    def test_unexpected_error_exits_zero(self, mock_check, mock_detach_all):
        """On unexpected errors, still exits 0 (don't block shutdown)."""
        mock_check.return_value = True
        mock_detach_all.side_effect = RuntimeError("something went wrong")

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
