# Feature: ha-usbip-esp32-client, Property 4: Configuration and DeviceEntry validation
"""Property tests verifying configuration and DeviceEntry validation logic.

For any configuration input, the validator SHALL accept values within declared
ranges (monitor_interval 10-300, reattach_retries 0-10, attach_delay 0-30,
port 1-65535, non-empty server, non-empty name) and reject values outside
those ranges. Device lists with more than 8 entries SHALL reject the excess entries.

**Validates: Requirements 6.3, 6.4, 9.1, 18.2**
"""

from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from usbip_addon.config import (
    AddonConfig,
    DeviceEntry,
    DEFAULTS,
    MAX_DEVICES,
    VALID_RANGES,
)


# --- Strategies ---

# Valid non-empty strings for server/name fields
non_empty_string_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
        blacklist_characters=("\x00",),
    ),
    min_size=1,
    max_size=50,
).filter(lambda s: s.strip() != "" and len(s) > 0)

# Valid port numbers (1-65535)
valid_port_strategy = st.integers(min_value=1, max_value=65535)

# Invalid port numbers (outside 1-65535)
invalid_port_strategy = st.one_of(
    st.integers(max_value=0),
    st.integers(min_value=65536),
)

# Valid busid strings (non-empty)
valid_busid_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=10,
).filter(lambda s: len(s.strip()) > 0)

# Strategy for valid DeviceEntry instances
valid_device_entry_strategy = st.builds(
    DeviceEntry,
    server=non_empty_string_strategy,
    name=non_empty_string_strategy,
    port=valid_port_strategy,
    busid=valid_busid_strategy,
)


def _make_config_with_cache(options: dict) -> AddonConfig:
    """Create an AddonConfig with pre-populated cache to avoid API calls."""
    config = AddonConfig.__new__(AddonConfig)
    config.token = "test-token"
    config._cache = options
    config._cache_time = 0
    return config


# --- Test 1: Valid DeviceEntry values always pass validate() ---

@settings(max_examples=100)
@given(
    server=non_empty_string_strategy,
    name=non_empty_string_strategy,
    port=valid_port_strategy,
    busid=valid_busid_strategy,
)
def test_valid_device_entry_passes_validation(
    server: str, name: str, port: int, busid: str
):
    """For any DeviceEntry with non-empty server, non-empty name,
    port in range 1-65535, and non-empty busid, validate() SHALL return None
    (indicating valid).

    **Validates: Requirements 6.4, 18.2**
    """
    entry = DeviceEntry(server=server, name=name, port=port, busid=busid)
    result = entry.validate()
    assert result is None, (
        f"Expected valid DeviceEntry to pass validation, got error: {result}\n"
        f"  server={server!r}, name={name!r}, port={port}, busid={busid!r}"
    )


# --- Test 2: Invalid DeviceEntry values always fail validate() ---

@settings(max_examples=100)
@given(
    name=non_empty_string_strategy,
    port=valid_port_strategy,
    busid=valid_busid_strategy,
)
def test_empty_server_fails_validation(name: str, port: int, busid: str):
    """DeviceEntry with empty server SHALL fail validation.

    **Validates: Requirements 6.4, 18.2**
    """
    entry = DeviceEntry(server="", name=name, port=port, busid=busid)
    result = entry.validate()
    assert result is not None, "Expected empty server to fail validation"
    assert "server" in result.lower()


@settings(max_examples=100)
@given(
    server=non_empty_string_strategy,
    port=valid_port_strategy,
    busid=valid_busid_strategy,
)
def test_empty_name_fails_validation(server: str, port: int, busid: str):
    """DeviceEntry with empty name SHALL fail validation.

    **Validates: Requirements 6.4, 18.2**
    """
    entry = DeviceEntry(server=server, name="", port=port, busid=busid)
    result = entry.validate()
    assert result is not None, "Expected empty name to fail validation"
    assert "name" in result.lower()


@settings(max_examples=100)
@given(
    server=non_empty_string_strategy,
    name=non_empty_string_strategy,
    port=invalid_port_strategy,
    busid=valid_busid_strategy,
)
def test_port_out_of_range_fails_validation(
    server: str, name: str, port: int, busid: str
):
    """DeviceEntry with port outside 1-65535 SHALL fail validation.

    **Validates: Requirements 6.4, 18.2**
    """
    entry = DeviceEntry(server=server, name=name, port=port, busid=busid)
    result = entry.validate()
    assert result is not None, (
        f"Expected port {port} to fail validation"
    )
    assert "port" in result.lower()


# --- Test 3: Config integer options within valid ranges are accepted as-is ---

@settings(max_examples=100)
@given(
    monitor_interval=st.integers(min_value=10, max_value=300),
    reattach_retries=st.integers(min_value=0, max_value=10),
    attach_delay=st.integers(min_value=0, max_value=30),
)
def test_valid_integer_options_accepted(
    monitor_interval: int, reattach_retries: int, attach_delay: int
):
    """For any integer config values within their declared valid ranges,
    the config module SHALL return those values unchanged.

    **Validates: Requirements 6.3**
    """
    options = {
        "monitor_interval": monitor_interval,
        "reattach_retries": reattach_retries,
        "attach_delay": attach_delay,
        "devices": [],
    }
    config = _make_config_with_cache(options)

    assert config.monitor_interval == monitor_interval, (
        f"Expected monitor_interval={monitor_interval}, got {config.monitor_interval}"
    )
    assert config.reattach_retries == reattach_retries, (
        f"Expected reattach_retries={reattach_retries}, got {config.reattach_retries}"
    )
    assert config.attach_delay == attach_delay, (
        f"Expected attach_delay={attach_delay}, got {config.attach_delay}"
    )


# --- Test 4: Config integer options outside valid ranges fall back to defaults ---

@settings(max_examples=100)
@given(
    value=st.one_of(
        st.integers(max_value=9),
        st.integers(min_value=301),
    ),
)
def test_monitor_interval_out_of_range_uses_default(value: int):
    """monitor_interval values outside [10, 300] SHALL fall back to default (30).

    **Validates: Requirements 6.3**
    """
    options = {"monitor_interval": value, "devices": []}
    config = _make_config_with_cache(options)

    assert config.monitor_interval == DEFAULTS["monitor_interval"], (
        f"Expected default {DEFAULTS['monitor_interval']} for out-of-range "
        f"monitor_interval={value}, got {config.monitor_interval}"
    )


@settings(max_examples=100)
@given(
    value=st.one_of(
        st.integers(max_value=-1),
        st.integers(min_value=11),
    ),
)
def test_reattach_retries_out_of_range_uses_default(value: int):
    """reattach_retries values outside [0, 10] SHALL fall back to default (3).

    **Validates: Requirements 6.3**
    """
    options = {"reattach_retries": value, "devices": []}
    config = _make_config_with_cache(options)

    assert config.reattach_retries == DEFAULTS["reattach_retries"], (
        f"Expected default {DEFAULTS['reattach_retries']} for out-of-range "
        f"reattach_retries={value}, got {config.reattach_retries}"
    )


@settings(max_examples=100)
@given(
    value=st.one_of(
        st.integers(max_value=-1),
        st.integers(min_value=31),
    ),
)
def test_attach_delay_out_of_range_uses_default(value: int):
    """attach_delay values outside [0, 30] SHALL fall back to default (2).

    **Validates: Requirements 6.3**
    """
    options = {"attach_delay": value, "devices": []}
    config = _make_config_with_cache(options)

    assert config.attach_delay == DEFAULTS["attach_delay"], (
        f"Expected default {DEFAULTS['attach_delay']} for out-of-range "
        f"attach_delay={value}, got {config.attach_delay}"
    )


# --- Test 5: Device lists with >8 entries only produce max 8 DeviceEntry results ---

@settings(max_examples=100)
@given(
    num_devices=st.integers(min_value=9, max_value=20),
)
def test_max_8_devices_enforced(num_devices: int):
    """Device lists with more than 8 entries SHALL only produce max 8
    DeviceEntry results; excess entries are rejected.

    **Validates: Requirements 9.1**
    """
    # Generate a list of valid device dicts with unique server IPs
    raw_devices = [
        {
            "server": f"192.168.1.{i + 1}",
            "name": f"Device {i + 1}",
            "port": 3240,
            "busid": "1-1",
        }
        for i in range(num_devices)
    ]

    options = {"devices": raw_devices}
    config = _make_config_with_cache(options)

    devices = config.devices
    assert len(devices) <= MAX_DEVICES, (
        f"Expected at most {MAX_DEVICES} devices, got {len(devices)} "
        f"from {num_devices} configured entries"
    )
    assert len(devices) == MAX_DEVICES, (
        f"Expected exactly {MAX_DEVICES} devices when {num_devices} > {MAX_DEVICES} "
        f"are configured, got {len(devices)}"
    )


@settings(max_examples=100)
@given(
    num_devices=st.integers(min_value=1, max_value=8),
)
def test_devices_within_limit_all_accepted(num_devices: int):
    """Device lists with 8 or fewer valid entries SHALL all be accepted.

    **Validates: Requirements 9.1**
    """
    raw_devices = [
        {
            "server": f"192.168.1.{i + 1}",
            "name": f"Device {i + 1}",
            "port": 3240,
            "busid": "1-1",
        }
        for i in range(num_devices)
    ]

    options = {"devices": raw_devices}
    config = _make_config_with_cache(options)

    devices = config.devices
    assert len(devices) == num_devices, (
        f"Expected {num_devices} devices, got {len(devices)}"
    )
