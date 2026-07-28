"""Unit tests for the USB/IP client module.

Tests parsing, command construction, and error handling
of the UsbipClient class.
"""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from usbip_addon.usbip_client import (
    UsbipClient,
    AttachResult,
    PortEntry,
    PORT_LINE_PATTERN,
)


class TestPortOutputParsing:
    """Tests for _parse_port_output static method."""

    def test_parse_design_doc_format(self):
        """Parse the format shown in the design document."""
        output = (
            "Imported USB devices\n"
            "====================\n"
            "Port 00: <Server IP> -> usbip://192.168.1.100:3240/1-1\n"
            "Port 01: <Server IP> -> usbip://192.168.1.101:3240/1-1\n"
        )
        entries = UsbipClient._parse_port_output(output)
        assert len(entries) == 2
        assert entries[0].port == 0
        assert entries[0].server == "192.168.1.100"
        assert entries[0].busid == "1-1"
        assert entries[1].port == 1
        assert entries[1].server == "192.168.1.101"
        assert entries[1].busid == "1-1"

    def test_parse_real_world_format(self):
        """Parse the multiline format from real usbip output."""
        output = (
            "Imported USB devices\n"
            "====================\n"
            "Port 00: <Port in Use> at Full/Low Speed(1)\n"
            "          Realtek Semiconductor Corp. : unknown product\n"
            "          00 -> usbip://192.168.1.100:3240/1-1\n"
            "              -> remote bus/dev 001/002\n"
        )
        entries = UsbipClient._parse_port_output(output)
        assert len(entries) == 1
        assert entries[0].port == 0
        assert entries[0].server == "192.168.1.100"
        assert entries[0].busid == "1-1"

    def test_parse_empty_output(self):
        """Empty output returns empty list."""
        entries = UsbipClient._parse_port_output("")
        assert entries == []

    def test_parse_no_devices(self):
        """Header only, no devices attached."""
        output = "Imported USB devices\n====================\n"
        entries = UsbipClient._parse_port_output(output)
        assert entries == []

    def test_parse_multiple_ports(self):
        """Parse multiple devices from design doc format."""
        output = (
            "Imported USB devices\n"
            "====================\n"
            "Port 00: <Server IP> -> usbip://10.0.0.1:3240/1-1\n"
            "Port 01: <Server IP> -> usbip://10.0.0.2:5000/2-1\n"
            "Port 05: <Server IP> -> usbip://172.16.0.1:3240/1-1\n"
        )
        entries = UsbipClient._parse_port_output(output)
        assert len(entries) == 3
        assert entries[0] == PortEntry(0, "10.0.0.1", "1-1", entries[0].device_info)
        assert entries[1] == PortEntry(1, "10.0.0.2", "2-1", entries[1].device_info)
        assert entries[2] == PortEntry(5, "172.16.0.1", "1-1", entries[2].device_info)

    def test_parse_custom_port_in_url(self):
        """Custom port in usbip URL is ignored (we only extract server and busid)."""
        output = "Port 03: <Server> -> usbip://192.168.1.50:5000/1-1\n"
        entries = UsbipClient._parse_port_output(output)
        assert len(entries) == 1
        assert entries[0].server == "192.168.1.50"
        assert entries[0].busid == "1-1"


class TestCommandConstruction:
    """Tests for command building static methods."""

    def test_attach_basic(self):
        """Basic attach command without custom port."""
        cmd = UsbipClient.build_attach_command("192.168.1.100", "1-1")
        assert cmd == ["usbip", "attach", "--remote=192.168.1.100", "--busid=1-1"]

    def test_attach_with_port(self):
        """Attach command with custom TCP port."""
        cmd = UsbipClient.build_attach_command("192.168.1.100", "1-1", 5000)
        assert cmd == [
            "usbip", "attach", "--remote=192.168.1.100",
            "--busid=1-1", "--tcp-port", "5000",
        ]

    def test_attach_default_busid(self):
        """Attach command uses default busid '1-1'."""
        cmd = UsbipClient.build_attach_command("10.0.0.1")
        assert cmd == ["usbip", "attach", "--remote=10.0.0.1", "--busid=1-1"]

    def test_detach_remote_command(self):
        """Pre-detach command uses -r and -b flags."""
        cmd = UsbipClient.build_detach_remote_command("192.168.1.100", "1-1")
        assert cmd == ["usbip", "detach", "-r", "192.168.1.100", "-b", "1-1"]

    def test_detach_remote_custom_busid(self):
        """Pre-detach with non-default busid."""
        cmd = UsbipClient.build_detach_remote_command("10.0.0.5", "2-1")
        assert cmd == ["usbip", "detach", "-r", "10.0.0.5", "-b", "2-1"]


class TestAttach:
    """Tests for the attach method with mocked subprocess."""

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_attach_success(self, mock_run):
        """Successful attach returns success=True."""
        # First call: attach command succeeds
        attach_result = MagicMock()
        attach_result.returncode = 0
        attach_result.stderr = ""
        attach_result.stdout = ""

        # Second call: list_ports for finding assigned port
        port_result = MagicMock()
        port_result.returncode = 0
        port_result.stdout = "Port 00: <Server> -> usbip://192.168.1.100:3240/1-1\n"
        port_result.stderr = ""

        mock_run.side_effect = [attach_result, port_result]

        client = UsbipClient()
        result = client.attach("192.168.1.100", "1-1")

        assert result.success is True
        assert result.port == 0
        assert result.stderr == ""

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_attach_failure(self, mock_run):
        """Failed attach returns success=False with stderr."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error: attach failed"
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        client = UsbipClient()
        result = client.attach("192.168.1.100", "1-1")

        assert result.success is False
        assert result.port is None
        assert "attach failed" in result.stderr

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_attach_timeout(self, mock_run):
        """Attach timeout returns failure."""
        mock_run.side_effect = subprocess.TimeoutExpired("usbip", 30)

        client = UsbipClient()
        result = client.attach("192.168.1.100", "1-1")

        assert result.success is False
        assert result.port is None
        assert "timed out" in result.stderr

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_attach_binary_not_found(self, mock_run):
        """Missing usbip binary returns failure."""
        mock_run.side_effect = FileNotFoundError()

        client = UsbipClient()
        result = client.attach("192.168.1.100", "1-1")

        assert result.success is False
        assert result.port is None
        assert "not found" in result.stderr

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_attach_with_custom_port(self, mock_run):
        """Attach with custom port includes --tcp-port flag."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""

        port_result = MagicMock()
        port_result.returncode = 0
        port_result.stdout = ""
        port_result.stderr = ""

        mock_run.side_effect = [mock_result, port_result]

        client = UsbipClient()
        client.attach("192.168.1.100", "1-1", port=5000)

        # Verify the attach command included --tcp-port
        call_args = mock_run.call_args_list[0]
        cmd = call_args[0][0]
        assert "--tcp-port" in cmd
        assert "5000" in cmd


class TestDetach:
    """Tests for the detach method."""

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_detach_success(self, mock_run):
        """Successful detach returns True."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        client = UsbipClient()
        assert client.detach(0) is True

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_detach_failure(self, mock_run):
        """Failed detach returns False."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        mock_run.return_value = mock_result

        client = UsbipClient()
        assert client.detach(0) is False

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_detach_command_uses_port_flag(self, mock_run):
        """Detach command uses --port flag with correct port number."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        client = UsbipClient()
        client.detach(5)

        call_args = mock_run.call_args[0][0]
        assert call_args == ["usbip", "detach", "--port", "5"]


class TestDetachRemote:
    """Tests for the detach_remote method."""

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_detach_remote_success(self, mock_run):
        """Successful pre-detach returns True."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        client = UsbipClient()
        assert client.detach_remote("192.168.1.100", "1-1") is True

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_detach_remote_failure_is_expected(self, mock_run):
        """Failed pre-detach returns False (non-fatal)."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error: no matching device"
        mock_run.return_value = mock_result

        client = UsbipClient()
        assert client.detach_remote("192.168.1.100", "1-1") is False

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_detach_remote_command_format(self, mock_run):
        """Pre-detach uses correct -r and -b flags."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        client = UsbipClient()
        client.detach_remote("10.0.0.5", "2-1")

        call_args = mock_run.call_args[0][0]
        assert call_args == ["usbip", "detach", "-r", "10.0.0.5", "-b", "2-1"]


class TestRemountSysfs:
    """Tests for the remount_sysfs method."""

    @patch("usbip_addon.usbip_client.time.sleep")
    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_remount_success_waits(self, mock_run, mock_sleep):
        """Successful remount waits 0.5s before returning."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        client = UsbipClient()
        assert client.remount_sysfs() is True
        mock_sleep.assert_called_once_with(0.5)

    @patch("usbip_addon.usbip_client.time.sleep")
    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_remount_failure_no_wait(self, mock_run, mock_sleep):
        """Failed remount does not wait and returns False."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "mount: permission denied"
        mock_run.return_value = mock_result

        client = UsbipClient()
        assert client.remount_sysfs() is False
        mock_sleep.assert_not_called()

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_remount_command_format(self, mock_run):
        """Remount uses correct mount command arguments."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        client = UsbipClient()
        with patch("usbip_addon.usbip_client.time.sleep"):
            client.remount_sysfs()

        call_args = mock_run.call_args[0][0]
        assert call_args == [
            "mount", "-o", "remount", "-t", "sysfs", "sysfs", "/sys"
        ]


class TestListPorts:
    """Tests for the list_ports method."""

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_list_ports_success(self, mock_run):
        """Successful port listing returns parsed entries."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "Imported USB devices\n"
            "====================\n"
            "Port 00: <Server> -> usbip://192.168.1.100:3240/1-1\n"
        )
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        client = UsbipClient()
        entries = client.list_ports()

        assert len(entries) == 1
        assert entries[0].port == 0
        assert entries[0].server == "192.168.1.100"
        assert entries[0].busid == "1-1"

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_list_ports_failure(self, mock_run):
        """Failed port listing returns empty list."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        client = UsbipClient()
        entries = client.list_ports()

        assert entries == []

    @patch("usbip_addon.usbip_client.subprocess.run")
    def test_list_ports_binary_not_found(self, mock_run):
        """Missing binary returns empty list."""
        mock_run.side_effect = FileNotFoundError()

        client = UsbipClient()
        entries = client.list_ports()

        assert entries == []
