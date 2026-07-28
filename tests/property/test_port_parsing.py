# Feature: ha-usbip-esp32-client, Property 2: usbip port output parsing
"""Property tests verifying usbip port output parsing.

For any valid `usbip port` output containing one or more attached device entries
(each with a port number, server IP, and busid), the parser SHALL correctly
extract all port numbers and their associated server/busid pairs.

**Validates: Requirements 3.4, 4.1**
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from usbip_addon.usbip_client import UsbipClient, PortEntry


# --- Strategies ---

# Port numbers in VHCI range (0-15)
port_number_strategy = st.integers(min_value=0, max_value=15)

# Valid IPv4 octets and full addresses
ipv4_strategy = st.tuples(
    st.integers(min_value=1, max_value=254),
    st.integers(min_value=0, max_value=255),
    st.integers(min_value=0, max_value=255),
    st.integers(min_value=1, max_value=254),
).map(lambda t: f"{t[0]}.{t[1]}.{t[2]}.{t[3]}")

# TCP port for the usbip:// URL (1-65535)
tcp_port_strategy = st.integers(min_value=1, max_value=65535)

# Bus ID patterns like "1-1", "2-1", "1-1.2"
busid_strategy = st.one_of(
    st.tuples(
        st.integers(min_value=1, max_value=9),
        st.integers(min_value=1, max_value=9),
    ).map(lambda t: f"{t[0]}-{t[1]}"),
    st.tuples(
        st.integers(min_value=1, max_value=9),
        st.integers(min_value=1, max_value=9),
        st.integers(min_value=1, max_value=9),
    ).map(lambda t: f"{t[0]}-{t[1]}.{t[2]}"),
)


# Strategy for a single device entry (port, server IP, TCP port, busid)
device_entry_strategy = st.tuples(
    port_number_strategy,
    ipv4_strategy,
    tcp_port_strategy,
    busid_strategy,
)


def _build_port_output(entries):
    """Build synthetic usbip port output from a list of (port, server, tcp_port, busid) tuples.

    Format:
        Imported USB devices
        ====================
        Port XX: <Server IP> -> usbip://IP:PORT/BUSID
    """
    lines = [
        "Imported USB devices",
        "====================",
    ]
    for port_num, server_ip, tcp_port, busid in entries:
        lines.append(
            f"Port {port_num:02d}: <{server_ip}> -> usbip://{server_ip}:{tcp_port}/{busid}"
        )
    return "\n".join(lines)


# --- Tests ---


@settings(max_examples=100)
@given(
    entries=st.lists(
        device_entry_strategy,
        min_size=1,
        max_size=8,
        unique_by=lambda e: e[0],  # Unique by port number
    ),
)
def test_parser_extracts_correct_number_of_entries(entries):
    """For any valid usbip port output with N device entries, the parser SHALL
    extract exactly N entries.

    **Validates: Requirements 3.4, 4.1**
    """
    output = _build_port_output(entries)
    parsed = UsbipClient._parse_port_output(output)

    assert len(parsed) == len(entries), (
        f"Expected {len(entries)} entries, got {len(parsed)}\n"
        f"Output:\n{output}\n"
        f"Parsed: {parsed}"
    )


@settings(max_examples=100)
@given(
    entries=st.lists(
        device_entry_strategy,
        min_size=1,
        max_size=8,
        unique_by=lambda e: e[0],  # Unique by port number
    ),
)
def test_parser_extracts_correct_port_numbers(entries):
    """For any valid usbip port output, each parsed entry SHALL have the correct
    port number.

    **Validates: Requirements 3.4, 4.1**
    """
    output = _build_port_output(entries)
    parsed = UsbipClient._parse_port_output(output)

    expected_ports = sorted(e[0] for e in entries)
    actual_ports = sorted(p.port for p in parsed)

    assert actual_ports == expected_ports, (
        f"Expected ports {expected_ports}, got {actual_ports}\n"
        f"Output:\n{output}"
    )


@settings(max_examples=100)
@given(
    entries=st.lists(
        device_entry_strategy,
        min_size=1,
        max_size=8,
        unique_by=lambda e: e[0],  # Unique by port number
    ),
)
def test_parser_extracts_correct_server_ips(entries):
    """For any valid usbip port output, each parsed entry SHALL have the correct
    server IP address.

    **Validates: Requirements 3.4, 4.1**
    """
    output = _build_port_output(entries)
    parsed = UsbipClient._parse_port_output(output)

    # Build lookup by port number for comparison
    expected_by_port = {e[0]: e[1] for e in entries}
    for p in parsed:
        assert p.server == expected_by_port[p.port], (
            f"For port {p.port}, expected server {expected_by_port[p.port]!r}, "
            f"got {p.server!r}\nOutput:\n{output}"
        )


@settings(max_examples=100)
@given(
    entries=st.lists(
        device_entry_strategy,
        min_size=1,
        max_size=8,
        unique_by=lambda e: e[0],  # Unique by port number
    ),
)
def test_parser_extracts_correct_busids(entries):
    """For any valid usbip port output, each parsed entry SHALL have the correct
    busid.

    **Validates: Requirements 3.4, 4.1**
    """
    output = _build_port_output(entries)
    parsed = UsbipClient._parse_port_output(output)

    # Build lookup by port number for comparison
    expected_by_port = {e[0]: e[3] for e in entries}
    for p in parsed:
        assert p.busid == expected_by_port[p.port], (
            f"For port {p.port}, expected busid {expected_by_port[p.port]!r}, "
            f"got {p.busid!r}\nOutput:\n{output}"
        )
