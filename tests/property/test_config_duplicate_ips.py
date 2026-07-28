# Feature: ha-usbip-esp32-client, Property 5: Duplicate server IP detection
"""Property test verifying duplicate server IP detection in device lists.

*For any* device list containing two or more entries with the same server IP address,
the validator SHALL identify and reject the duplicate entries while preserving the
first occurrence.

**Validates: Requirements 9.5**
"""

from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from usbip_addon.config import AddonConfig, DeviceEntry


# Strategy for valid IPv4 addresses
ipv4_strategy = st.tuples(
    st.integers(min_value=1, max_value=254),
    st.integers(min_value=0, max_value=254),
    st.integers(min_value=0, max_value=254),
    st.integers(min_value=1, max_value=254),
).map(lambda t: f"{t[0]}.{t[1]}.{t[2]}.{t[3]}")

# Strategy for valid device names (non-empty alphanumeric)
name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Zs")),
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip() != "")

# Strategy for a single valid raw device dict
def device_dict_strategy(ip_strategy=ipv4_strategy):
    """Generate a valid raw device dict as would come from config."""
    return st.fixed_dictionaries({
        "server": ip_strategy,
        "name": name_strategy,
        "port": st.integers(min_value=1, max_value=65535),
        "busid": st.just("1-1"),
    })


def _make_config_with_devices(raw_devices: list) -> AddonConfig:
    """Create an AddonConfig instance with a pre-set device list cache.

    The AddonConfig._cache stores the options dict directly (not the full
    API response), so we set it to a flat dict with a "devices" key.
    """
    config = AddonConfig.__new__(AddonConfig)
    config.token = "test-token"
    config._cache = {
        "devices": raw_devices,
        "log_level": "info",
        "monitor_interval": 30,
        "reattach_retries": 3,
        "attach_delay": 2,
        "notifications_enabled": True,
        "flap_warning_threshold": 3,
        "flap_critical_threshold": 5,
        "flap_window_seconds": 600,
        "flap_clear_seconds": 900,
    }
    config._cache_time = 1000.0
    return config


@settings(max_examples=100)
@given(
    devices=st.lists(device_dict_strategy(), min_size=1, max_size=8).filter(
        lambda ds: len(set(d["server"] for d in ds)) == len(ds)
    )
)
def test_unique_ips_all_preserved(devices):
    """For any list of unique server IPs, all entries are preserved.

    When all device entries have distinct server IP addresses, every entry
    should appear in the resulting device list (no false rejections).
    """
    config = _make_config_with_devices(devices)
    result = config.devices

    assert len(result) == len(devices), (
        f"Expected {len(devices)} devices with unique IPs, got {len(result)}"
    )

    # Verify all server IPs are present
    result_ips = [d.server for d in result]
    input_ips = [d["server"] for d in devices]
    assert result_ips == input_ips, (
        f"Expected IPs {input_ips}, got {result_ips}"
    )


@settings(max_examples=100)
@given(
    unique_devices=st.lists(
        device_dict_strategy(), min_size=1, max_size=7
    ).filter(lambda ds: len(set(d["server"] for d in ds)) == len(ds)),
    dup_index=st.data(),
)
def test_duplicate_ips_first_occurrence_kept(unique_devices, dup_index):
    """For any list with duplicate IPs, only the first occurrence of each IP is kept.

    When a device list contains entries with the same server IP, only the first
    occurrence should be preserved and subsequent duplicates should be rejected.
    """
    # Pick a device whose IP to duplicate and insert position
    source_idx = dup_index.draw(
        st.integers(min_value=0, max_value=len(unique_devices) - 1)
    )
    insert_pos = dup_index.draw(
        st.integers(min_value=source_idx + 1, max_value=len(unique_devices))
    )

    # Create a duplicate entry with the same server IP but different name
    dup_entry = {
        "server": unique_devices[source_idx]["server"],
        "name": "DUPLICATE_DEVICE",
        "port": 3240,
        "busid": "1-1",
    }

    # Insert duplicate into the list
    modified_devices = list(unique_devices)
    modified_devices.insert(insert_pos, dup_entry)

    config = _make_config_with_devices(modified_devices)
    result = config.devices

    # The duplicate entry should be rejected, only first occurrence kept
    duplicate_ip = unique_devices[source_idx]["server"]
    occurrences = [d for d in result if d.server == duplicate_ip]

    assert len(occurrences) == 1, (
        f"Expected exactly 1 entry for IP {duplicate_ip}, got {len(occurrences)}"
    )

    # The kept entry should be the first one (the original, not the duplicate)
    assert occurrences[0].name == unique_devices[source_idx]["name"], (
        f"Expected first occurrence name '{unique_devices[source_idx]['name']}', "
        f"got '{occurrences[0].name}'"
    )


@settings(max_examples=100)
@given(
    devices=st.lists(device_dict_strategy(), min_size=1, max_size=8),
)
def test_result_never_contains_duplicate_ips(devices):
    """The resulting list never contains two entries with the same server IP.

    Regardless of the input (with or without duplicates), the output device
    list must have all unique server IPs.
    """
    config = _make_config_with_devices(devices)
    result = config.devices

    result_ips = [d.server for d in result]
    assert len(result_ips) == len(set(result_ips)), (
        f"Result contains duplicate IPs: {result_ips}"
    )


@settings(max_examples=100)
@given(
    base_devices=st.lists(device_dict_strategy(), min_size=2, max_size=6),
)
def test_order_of_unique_entries_preserved(base_devices):
    """The order of unique entries is preserved (first-seen order).

    After duplicate rejection, the remaining entries should appear in the
    same relative order as they appeared in the original list.
    """
    # Create a list with some duplicates mixed in
    # Take the first device's IP and append a duplicate at the end
    devices_with_dup = list(base_devices) + [
        {
            "server": base_devices[0]["server"],
            "name": "Duplicate Entry",
            "port": 3240,
            "busid": "1-1",
        }
    ]

    config = _make_config_with_devices(devices_with_dup)
    result = config.devices

    # Get the expected order: first occurrence of each unique IP, in input order
    seen = set()
    expected_order = []
    for d in devices_with_dup:
        if d["server"] not in seen:
            seen.add(d["server"])
            expected_order.append(d["server"])

    # Limit to max 8 (device limit)
    expected_order = expected_order[:8]

    result_ips = [d.server for d in result]
    assert result_ips == expected_order, (
        f"Expected IP order {expected_order}, got {result_ips}"
    )
