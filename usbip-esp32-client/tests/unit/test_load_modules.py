"""Unit tests for cont-init.d/load_modules.py script.

Tests the kernel module loading logic:
- Success path: module loads and verifies correctly
- Already loaded: module is already present, skips loading
- Failure: modprobe returns non-zero exit code
- Verify-after-load failure: modprobe succeeds but verification fails

Requirements: 1.1, 1.2, 1.3, 1.4
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest


# The load_modules.py script is at rootfs/etc/cont-init.d/ which isn't a
# standard Python package. We import the functions directly by patching
# the module-level logger and testing the logic functions.

# We need to import the functions from the script
import sys
import os

# Add the rootfs path so we can import the script as a module
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "rootfs", "etc", "cont-init.d"
))

from load_modules import is_module_loaded, verify_module_loaded, load_module, SYSFS_PATH, MODULE_NAME


class TestIsModuleLoaded:
    """Tests for is_module_loaded() function."""

    @patch("os.path.isdir")
    def test_returns_true_when_sysfs_path_exists(self, mock_isdir):
        """When /sys/module/vhci_hcd exists, module is loaded."""
        mock_isdir.return_value = True
        assert is_module_loaded() is True
        mock_isdir.assert_called_once_with(SYSFS_PATH)

    @patch("os.path.isdir")
    def test_returns_false_when_sysfs_path_missing(self, mock_isdir):
        """When /sys/module/vhci_hcd does not exist, module is not loaded."""
        mock_isdir.return_value = False
        assert is_module_loaded() is False


class TestVerifyModuleLoaded:
    """Tests for verify_module_loaded() function."""

    @patch("os.path.isdir")
    def test_returns_true_when_sysfs_path_exists(self, mock_isdir):
        """Verification via sysfs path check."""
        mock_isdir.return_value = True
        assert verify_module_loaded() is True

    @patch("subprocess.run")
    @patch("os.path.isdir")
    def test_falls_back_to_lsmod_when_sysfs_missing(self, mock_isdir, mock_run):
        """When sysfs path missing, falls back to lsmod."""
        mock_isdir.return_value = False
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="vhci_hcd   12345  0\nsome_module  456  1\n",
        )
        assert verify_module_loaded() is True

    @patch("subprocess.run")
    @patch("os.path.isdir")
    def test_returns_false_when_both_checks_fail(self, mock_isdir, mock_run):
        """Returns False when sysfs and lsmod both indicate not loaded."""
        mock_isdir.return_value = False
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="some_other_module  456  1\n",
        )
        assert verify_module_loaded() is False

    @patch("subprocess.run")
    @patch("os.path.isdir")
    def test_returns_false_when_lsmod_fails(self, mock_isdir, mock_run):
        """Returns False when sysfs missing and lsmod command fails."""
        mock_isdir.return_value = False
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="lsmod", timeout=10)
        assert verify_module_loaded() is False


class TestLoadModule:
    """Tests for load_module() main logic."""

    @patch("load_modules.verify_module_loaded")
    @patch("subprocess.run")
    @patch("load_modules.is_module_loaded")
    def test_success_loads_and_verifies(self, mock_is_loaded, mock_run, mock_verify):
        """Happy path: module not loaded, modprobe succeeds, verification passes."""
        mock_is_loaded.return_value = False
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_verify.return_value = True

        with pytest.raises(SystemExit) as exc_info:
            load_module()
        assert exc_info.value.code == 0

    @patch("load_modules.is_module_loaded")
    def test_already_loaded_skips(self, mock_is_loaded):
        """When module is already loaded, exits with 0 without calling modprobe."""
        mock_is_loaded.return_value = True

        with pytest.raises(SystemExit) as exc_info:
            load_module()
        assert exc_info.value.code == 0

    @patch("subprocess.run")
    @patch("load_modules.is_module_loaded")
    def test_modprobe_failure_exits_nonzero(self, mock_is_loaded, mock_run):
        """When modprobe returns non-zero, exits with 1."""
        mock_is_loaded.return_value = False
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="modprobe: FATAL: Module vhci-hcd not found",
        )

        with pytest.raises(SystemExit) as exc_info:
            load_module()
        assert exc_info.value.code == 1

    @patch("load_modules.verify_module_loaded")
    @patch("subprocess.run")
    @patch("load_modules.is_module_loaded")
    def test_verify_after_load_failure_exits_zero_with_warning(
        self, mock_is_loaded, mock_run, mock_verify
    ):
        """When modprobe succeeds but verification fails, exits 0 (warning only)."""
        mock_is_loaded.return_value = False
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_verify.return_value = False

        with pytest.raises(SystemExit) as exc_info:
            load_module()
        # Req 1.4: log warning and continue (exit 0)
        assert exc_info.value.code == 0

    @patch("subprocess.run")
    @patch("load_modules.is_module_loaded")
    def test_modprobe_binary_not_found(self, mock_is_loaded, mock_run):
        """When modprobe binary not found, exits with 1."""
        mock_is_loaded.return_value = False
        mock_run.side_effect = FileNotFoundError("No such file: /sbin/modprobe")

        with pytest.raises(SystemExit) as exc_info:
            load_module()
        assert exc_info.value.code == 1

    @patch("subprocess.run")
    @patch("load_modules.is_module_loaded")
    def test_modprobe_timeout(self, mock_is_loaded, mock_run):
        """When modprobe times out, exits with 1."""
        mock_is_loaded.return_value = False
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="modprobe", timeout=30)

        with pytest.raises(SystemExit) as exc_info:
            load_module()
        assert exc_info.value.code == 1
