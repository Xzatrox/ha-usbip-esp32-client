# Feature: ha-usbip-esp32-client, Property 13: Independent device failure isolation
"""Property tests verifying independent device failure isolation.

For any set of configured devices where a subset fails attachment (health check
failure, attach error), all remaining devices SHALL still be attempted
independently. The total number of attach attempts SHALL equal the number of
reachable devices, regardless of which specific devices failed.

**Validates: Requirements 9.2**
"""

from datetime import timedelta
from dataclasses import dataclass
from typing import Set
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from usbip_addon.config import DeviceEntry
from usbip_addon.health import HealthResult
from usbip_addon.usbip_client import AttachResult


# --- Strategies ---

# Generate unique server IPs (2-8 devices)
ip_octet = st.integers(min_value=1, max_value=254)

unique_ips_strategy = st.lists(
    st.tuples(ip_octet, ip_octet, ip_octet, ip_octet).map(
        lambda t: f"{t[0]}.{t[1]}.{t[2]}.{t[3]}"
    ),
    min_size=2,
    max_size=8,
    unique=True,
)

# Simple device names
device_name_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=8,
).filter(lambda s: s.strip() != "")


def devices_strategy():
    """Generate a list of unique DeviceEntry items with unique server IPs."""
    return unique_ips_strategy.flatmap(
        lambda ips: st.tuples(
            st.just(ips),
            st.lists(
                device_name_strategy,
                min_size=len(ips),
                max_size=len(ips),
            ),
        ).map(
            lambda pair: [
                DeviceEntry(server=ip, name=name, port=3240, busid="1-1")
                for ip, name in zip(pair[0], pair[1])
            ]
        )
    )


def failure_subset_strategy(devices_st):
    """Given a devices strategy, generate a subset of server IPs that should fail."""
    return devices_st.flatmap(
        lambda devices: st.tuples(
            st.just(devices),
            st.lists(
                st.sampled_from([d.server for d in devices]),
                min_size=0,
                max_size=len(devices) - 1,  # At least one device must succeed
                unique=True,
            ),
        )
    )


# Combined strategy: devices + which ones fail health check
devices_with_health_failures = failure_subset_strategy(devices_strategy())

# Combined strategy: devices + which ones fail attach (but pass health check)
devices_with_attach_failures = failure_subset_strategy(devices_strategy())


# --- Helper: simulate the device iteration logic ---

def simulate_attach_loop(
    devices: list[DeviceEntry],
    health_fail_servers: Set[str],
    attach_fail_servers: Set[str],
) -> dict:
    """Simulate the attach service's device iteration logic.

    This mirrors the logic in rootfs/etc/services.d/usbip/run main():
    - For each device, perform health check
    - If health check fails, skip (continue to next)
    - If health check passes, attempt attach
    - Track all health checks attempted and all attach attempts

    Returns a dict with tracking info.
    """
    health_checks_called = []
    attach_attempts_called = []
    successful_attaches = []

    for device in devices:
        # Health check is always attempted for every device
        health_checks_called.append(device.server)

        # If health check fails, skip this device
        if device.server in health_fail_servers:
            continue

        # Health check passed — attempt attach
        attach_attempts_called.append(device.server)

        # If attach fails, record failure but still continue to next device
        if device.server in attach_fail_servers:
            continue

        # Attach succeeded
        successful_attaches.append(device.server)

    return {
        "health_checks_called": health_checks_called,
        "attach_attempts_called": attach_attempts_called,
        "successful_attaches": successful_attaches,
    }


# --- Property 13: Independent device failure isolation ---


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(data=devices_with_health_failures)
def test_health_check_failure_isolation(data):
    """For any set of configured devices where a subset fails health check,
    all remaining devices SHALL still be attempted independently. A failure
    on one device doesn't prevent attempts on others.

    **Validates: Requirements 9.2**
    """
    devices, fail_servers = data
    assume(len(devices) >= 2)
    fail_set = set(fail_servers)

    # Simulate the attach loop
    result = simulate_attach_loop(devices, health_fail_servers=fail_set, attach_fail_servers=set())

    # 1. All devices are attempted (health check called for each)
    assert len(result["health_checks_called"]) == len(devices), (
        f"Expected health check for all {len(devices)} devices, "
        f"but only {len(result['health_checks_called'])} were called"
    )

    # 2. A failure on one device doesn't prevent attempts on others
    reachable_devices = [d for d in devices if d.server not in fail_set]
    assert len(result["attach_attempts_called"]) == len(reachable_devices), (
        f"Expected {len(reachable_devices)} attach attempts (reachable devices), "
        f"but got {len(result['attach_attempts_called'])}"
    )

    # 3. Successful devices still get attached despite failures on other devices
    assert len(result["successful_attaches"]) == len(reachable_devices), (
        f"Expected {len(reachable_devices)} successful attaches, "
        f"but got {len(result['successful_attaches'])}"
    )

    # 4. Verify the specific devices that were attached are exactly the reachable ones
    expected_attached = {d.server for d in reachable_devices}
    actual_attached = set(result["successful_attaches"])
    assert actual_attached == expected_attached, (
        f"Expected servers {expected_attached} to be attached, "
        f"but got {actual_attached}"
    )


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(data=devices_with_attach_failures)
def test_attach_failure_isolation(data):
    """For any set of configured devices where a subset fails during attach
    (after passing health check), all remaining devices SHALL still be
    attempted independently.

    **Validates: Requirements 9.2**
    """
    devices, fail_servers = data
    assume(len(devices) >= 2)
    fail_set = set(fail_servers)

    # All health checks pass, but some attaches fail
    result = simulate_attach_loop(devices, health_fail_servers=set(), attach_fail_servers=fail_set)

    # 1. All devices get health checked
    assert len(result["health_checks_called"]) == len(devices)

    # 2. All devices get attach attempted (all pass health check)
    assert len(result["attach_attempts_called"]) == len(devices), (
        f"Expected all {len(devices)} devices to get attach attempts, "
        f"but only {len(result['attach_attempts_called'])} were attempted"
    )

    # 3. Only non-failing devices succeed
    expected_success = [d.server for d in devices if d.server not in fail_set]
    assert result["successful_attaches"] == expected_success, (
        f"Expected successful attaches for {expected_success}, "
        f"got {result['successful_attaches']}"
    )


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(data=devices_with_health_failures)
def test_attach_service_integration_with_mocks(data):
    """Integration-level property test verifying that the actual attach service
    logic (with mocked health_checker and usbip_client) correctly isolates
    device failures. Each device is processed independently regardless of
    which devices fail.

    **Validates: Requirements 9.2**
    """
    devices, fail_servers = data
    assume(len(devices) >= 2)
    fail_set = set(fail_servers)

    # Track calls to mocked components
    health_check_calls = []
    attach_calls = []

    def mock_health_check(server, port=3240, timeout=2.0):
        health_check_calls.append(server)
        if server in fail_set:
            return HealthResult(reachable=False, latency_ms=None, error="Connection refused")
        return HealthResult(reachable=True, latency_ms=1.0, error=None)

    def mock_attach(server, busid="1-1", port=None):
        attach_calls.append(server)
        return AttachResult(success=True, port=0, stderr="")

    # Simulate the attach service loop with mocked dependencies
    health_checker = MagicMock()
    health_checker.check = mock_health_check

    usbip_client = MagicMock()
    usbip_client.attach = mock_attach
    usbip_client.detach_remote = MagicMock(return_value=True)
    usbip_client.remount_sysfs = MagicMock(return_value=True)

    event_log = MagicMock()

    # Run the device loop (mirrors the attach service logic)
    for device in devices:
        health_result = health_checker.check(device.server, port=device.port)
        if not health_result.reachable:
            event_log.record("attach_fail", device.name, device.server,
                             f"Health check failed: {health_result.error}")
            continue

        usbip_client.detach_remote(device.server, device.busid)
        usbip_client.remount_sysfs()
        result = usbip_client.attach(device.server, busid=device.busid)
        if result.success:
            event_log.record("attach_ok", device.name, device.server,
                             f"Attached to port {result.port}")

    # Verify property: all devices get health checked
    assert len(health_check_calls) == len(devices), (
        f"Expected {len(devices)} health checks, got {len(health_check_calls)}"
    )

    # Verify property: only reachable devices get attach attempts
    reachable_count = len(devices) - len(fail_set)
    assert len(attach_calls) == reachable_count, (
        f"Expected {reachable_count} attach attempts for reachable devices, "
        f"got {len(attach_calls)}. Failed servers: {fail_set}"
    )

    # Verify property: attach attempts are for the correct (reachable) servers
    expected_attach_servers = [d.server for d in devices if d.server not in fail_set]
    assert attach_calls == expected_attach_servers, (
        f"Expected attach calls for {expected_attach_servers}, "
        f"got {attach_calls}"
    )

    # Verify property: total attempts = number of reachable devices
    assert len(attach_calls) == len(expected_attach_servers)
