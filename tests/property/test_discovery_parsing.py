# Feature: ha-usbip-esp32-client, Property 6: Discovery output parsing (ESP32 format)
"""Property tests verifying discovery output parsing for usbip list output.

For any `usbip list -r` output line matching the ESP32 format
`<whitespace><busid>: <manufacturer> : <product>`, the parser SHALL extract
the busid, manufacturer name, and product name correctly. Lines not matching
this format SHALL not produce false-positive device entries.

**Validates: Requirements 13.1, 13.2**
"""

import re

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from usbip_addon.discovery import DeviceDiscovery, DiscoveredDevice


# --- Instance under test ---
discovery = DeviceDiscovery()


# --- Strategies ---

# Busid patterns like "1-1", "2-1", "1-2.3", "10-3.4.5"
busid_strategy = st.from_regex(
    r"[0-9]{1,2}-[0-9]{1,2}(\.[0-9]{1,2}){0,3}", fullmatch=True
)

# Manufacturer: non-empty string without ":" characters (to avoid ambiguous splits)
manufacturer_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Zs"),
        blacklist_characters=(":", "\n", "\r", "\x00"),
    ),
    min_size=1,
    max_size=40,
).filter(lambda s: s.strip() != "")

# Product: non-empty string without ":" characters
product_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Zs"),
        blacklist_characters=(":", "\n", "\r", "\x00"),
    ),
    min_size=1,
    max_size=40,
).filter(lambda s: s.strip() != "")

# vid:pid pattern like (0bda:5411)
vid_pid_strategy = st.from_regex(
    r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}", fullmatch=True
).map(lambda vp: f"({vp})")

# Leading whitespace (at least 1 space or tab)
leading_whitespace_strategy = st.from_regex(r"[ \t]{1,8}", fullmatch=True)


# --- Property Tests: Valid Lines ---

@settings(max_examples=100)
@given(
    whitespace=leading_whitespace_strategy,
    busid=busid_strategy,
    manufacturer=manufacturer_strategy,
    product=product_strategy,
)
def test_valid_line_extracts_busid_correctly(
    whitespace: str, busid: str, manufacturer: str, product: str
):
    """For any valid line matching the ESP32 format, the parser SHALL
    extract the correct busid.

    **Validates: Requirements 13.1, 13.2**
    """
    line = f"{whitespace}{busid}: {manufacturer} : {product}"
    devices = discovery._parse_output(line)

    assert len(devices) == 1, (
        f"Expected 1 device from valid line, got {len(devices)}\n"
        f"  line={line!r}"
    )
    assert devices[0].busid == busid, (
        f"Expected busid={busid!r}, got {devices[0].busid!r}\n"
        f"  line={line!r}"
    )


@settings(max_examples=100)
@given(
    whitespace=leading_whitespace_strategy,
    busid=busid_strategy,
    manufacturer=manufacturer_strategy,
    product=product_strategy,
)
def test_valid_line_extracts_manufacturer_correctly(
    whitespace: str, busid: str, manufacturer: str, product: str
):
    """For any valid line matching the ESP32 format, the parser SHALL
    extract the correct manufacturer name (trimmed).

    **Validates: Requirements 13.1, 13.2**
    """
    line = f"{whitespace}{busid}: {manufacturer} : {product}"
    devices = discovery._parse_output(line)

    assert len(devices) == 1, (
        f"Expected 1 device from valid line, got {len(devices)}\n"
        f"  line={line!r}"
    )
    assert devices[0].manufacturer == manufacturer.strip(), (
        f"Expected manufacturer={manufacturer.strip()!r}, "
        f"got {devices[0].manufacturer!r}\n"
        f"  line={line!r}"
    )


@settings(max_examples=100)
@given(
    whitespace=leading_whitespace_strategy,
    busid=busid_strategy,
    manufacturer=manufacturer_strategy,
    product=product_strategy,
)
def test_valid_line_extracts_product_correctly(
    whitespace: str, busid: str, manufacturer: str, product: str
):
    """For any valid line matching the ESP32 format, the parser SHALL
    extract the correct product name (trimmed).

    **Validates: Requirements 13.1, 13.2**
    """
    line = f"{whitespace}{busid}: {manufacturer} : {product}"
    devices = discovery._parse_output(line)

    assert len(devices) == 1, (
        f"Expected 1 device from valid line, got {len(devices)}\n"
        f"  line={line!r}"
    )
    assert devices[0].product == product.strip(), (
        f"Expected product={product.strip()!r}, got {devices[0].product!r}\n"
        f"  line={line!r}"
    )


@settings(max_examples=100)
@given(
    whitespace=leading_whitespace_strategy,
    busid=busid_strategy,
    manufacturer=manufacturer_strategy,
    product=product_strategy,
    vid_pid=vid_pid_strategy,
)
def test_valid_line_with_vid_pid_strips_trailing_vid_pid(
    whitespace: str, busid: str, manufacturer: str, product: str, vid_pid: str
):
    """For any valid line with a trailing (vid:pid) on the product, the parser
    SHALL strip the (vid:pid) suffix and return only the product name.

    **Validates: Requirements 13.1, 13.2**
    """
    line = f"{whitespace}{busid}: {manufacturer} : {product} {vid_pid}"
    devices = discovery._parse_output(line)

    assert len(devices) == 1, (
        f"Expected 1 device from valid line with vid:pid, got {len(devices)}\n"
        f"  line={line!r}"
    )
    # The product should have the (vid:pid) stripped
    assert devices[0].product == product.strip(), (
        f"Expected product={product.strip()!r} (vid:pid stripped), "
        f"got {devices[0].product!r}\n"
        f"  line={line!r}"
    )


# --- Property Tests: Invalid Lines (No False Positives) ---

@settings(max_examples=100)
@given(
    text=st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "P", "S"),
            blacklist_characters=("\x00",),
        ),
        min_size=0,
        max_size=80,
    ),
)
def test_line_without_leading_whitespace_produces_no_devices(text: str):
    """Lines without leading whitespace SHALL not produce false-positive
    device entries.

    **Validates: Requirements 13.1, 13.2**
    """
    # Ensure the line does NOT start with whitespace
    line = text.lstrip()
    assume(len(line) > 0)

    devices = discovery._parse_output(line)
    assert len(devices) == 0, (
        f"Expected no devices from line without leading whitespace, "
        f"got {len(devices)}\n  line={line!r}"
    )


@settings(max_examples=100)
@given(
    whitespace=leading_whitespace_strategy,
    text=st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "Zs"),
            blacklist_characters=(":", "\n", "\r", "\x00"),
        ),
        min_size=1,
        max_size=60,
    ),
)
def test_line_without_colons_produces_no_devices(whitespace: str, text: str):
    """Lines with leading whitespace but no colon separators SHALL not
    produce false-positive device entries.

    **Validates: Requirements 13.1, 13.2**
    """
    # The text has no ":" in it, so it can't match the pattern
    line = f"{whitespace}{text}"
    devices = discovery._parse_output(line)
    assert len(devices) == 0, (
        f"Expected no devices from line without colons, got {len(devices)}\n"
        f"  line={line!r}"
    )


@settings(max_examples=100)
@given(
    whitespace=leading_whitespace_strategy,
    text=st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "Zs"),
            blacklist_characters=(":", "\n", "\r", "\x00"),
        ),
        min_size=1,
        max_size=40,
    ),
)
def test_line_with_single_colon_only_produces_no_devices(whitespace: str, text: str):
    """Lines with only one colon (e.g. '<ws>busid: something' but no second colon
    for manufacturer:product split) SHALL not produce false-positive device entries.

    **Validates: Requirements 13.1, 13.2**
    """
    # Format: <ws><text>:<text> — only one colon, no second colon for split
    line = f"{whitespace}{text}:{text}"
    # Ensure there's only one colon
    assume(line.count(":") == 1)

    devices = discovery._parse_output(line)
    assert len(devices) == 0, (
        f"Expected no devices from line with only one colon, got {len(devices)}\n"
        f"  line={line!r}"
    )


@settings(max_examples=100)
@given(data=st.data())
def test_multiline_output_with_header_lines_produces_no_false_positives(data):
    """Full usbip list output containing header/separator lines SHALL not
    produce false-positive device entries from those non-device lines.

    **Validates: Requirements 13.1, 13.2**
    """
    # Generate output similar to real usbip list headers
    server_ip = data.draw(
        st.from_regex(r"192\.168\.[0-9]{1,3}\.[0-9]{1,3}", fullmatch=True)
    )
    header_lines = [
        "Exportable USB devices",
        "======================",
        f" - {server_ip}",
        "",
    ]
    output = "\n".join(header_lines)

    devices = discovery._parse_output(output)
    assert len(devices) == 0, (
        f"Expected no devices from header-only output, got {len(devices)}\n"
        f"  output={output!r}"
    )


@settings(max_examples=100)
@given(
    whitespace=leading_whitespace_strategy,
    busid=busid_strategy,
    manufacturer=manufacturer_strategy,
    product=product_strategy,
)
def test_full_output_with_valid_device_extracts_correctly(
    whitespace: str, busid: str, manufacturer: str, product: str
):
    """When valid device lines are embedded in full usbip list output
    (with headers), the parser SHALL extract only the valid device entries.

    **Validates: Requirements 13.1, 13.2**
    """
    output = (
        "Exportable USB devices\n"
        "======================\n"
        " - 192.168.1.100\n"
        f"{whitespace}{busid}: {manufacturer} : {product}\n"
    )

    devices = discovery._parse_output(output)
    assert len(devices) == 1, (
        f"Expected 1 device from full output, got {len(devices)}\n"
        f"  output={output!r}"
    )
    assert devices[0].busid == busid
    assert devices[0].manufacturer == manufacturer.strip()
    assert devices[0].product == product.strip()
