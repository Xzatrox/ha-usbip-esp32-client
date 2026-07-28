# Feature: ha-usbip-esp32-client, Property 1: DeviceEntry to usbip command parameter mapping
"""Property tests verifying usbip command parameter construction.

For any valid DeviceEntry (with server IP, busid, and optional custom port),
the generated `usbip attach` command SHALL include `--remote=<server>`,
`--busid=<busid>`, and `--tcp-port <port>` (if custom port specified), and
the pre-detach command SHALL use `-r <server> -b <busid>`.

**Validates: Requirements 3.1, 3.2**
"""

from typing import Optional

from hypothesis import given, settings
from hypothesis import strategies as st

from usbip_addon.usbip_client import UsbipClient


# --- Strategies ---

# Valid IPv4 addresses
ipv4_strategy = st.tuples(
    st.integers(min_value=1, max_value=255),
    st.integers(min_value=0, max_value=255),
    st.integers(min_value=0, max_value=255),
    st.integers(min_value=1, max_value=254),
).map(lambda t: f"{t[0]}.{t[1]}.{t[2]}.{t[3]}")

# Valid bus IDs (patterns like "1-1", "2-1", "1-1.2", etc.)
busid_strategy = st.from_regex(r"[1-9]-[1-9](\.[1-9])?", fullmatch=True)

# Optional custom ports (1-65535 or None)
optional_port_strategy = st.one_of(
    st.none(),
    st.integers(min_value=1, max_value=65535),
)


# --- Test 1: Attach command always starts with ["usbip", "attach"] ---

@settings(max_examples=100)
@given(
    server=ipv4_strategy,
    busid=busid_strategy,
    port=optional_port_strategy,
)
def test_attach_command_starts_with_usbip_attach(
    server: str, busid: str, port: Optional[int]
):
    """The generated attach command SHALL always start with ["usbip", "attach"].

    **Validates: Requirements 3.1, 3.2**
    """
    cmd = UsbipClient.build_attach_command(server=server, busid=busid, port=port)
    assert cmd[0] == "usbip", f"Expected cmd[0]='usbip', got {cmd[0]!r}"
    assert cmd[1] == "attach", f"Expected cmd[1]='attach', got {cmd[1]!r}"


# --- Test 2: Attach command contains correct --remote flag ---

@settings(max_examples=100)
@given(
    server=ipv4_strategy,
    busid=busid_strategy,
    port=optional_port_strategy,
)
def test_attach_command_contains_remote_flag(
    server: str, busid: str, port: Optional[int]
):
    """The generated attach command SHALL contain `--remote=<server>` exactly.

    **Validates: Requirements 3.1, 3.2**
    """
    cmd = UsbipClient.build_attach_command(server=server, busid=busid, port=port)
    expected_remote = f"--remote={server}"
    assert expected_remote in cmd, (
        f"Expected {expected_remote!r} in command {cmd}, but not found"
    )


# --- Test 3: Attach command contains correct --busid flag ---

@settings(max_examples=100)
@given(
    server=ipv4_strategy,
    busid=busid_strategy,
    port=optional_port_strategy,
)
def test_attach_command_contains_busid_flag(
    server: str, busid: str, port: Optional[int]
):
    """The generated attach command SHALL contain `--busid=<busid>` exactly.

    **Validates: Requirements 3.1, 3.2**
    """
    cmd = UsbipClient.build_attach_command(server=server, busid=busid, port=port)
    expected_busid = f"--busid={busid}"
    assert expected_busid in cmd, (
        f"Expected {expected_busid!r} in command {cmd}, but not found"
    )


# --- Test 4: Attach command includes --tcp-port when port is specified ---

@settings(max_examples=100)
@given(
    server=ipv4_strategy,
    busid=busid_strategy,
    port=st.integers(min_value=1, max_value=65535),
)
def test_attach_command_includes_tcp_port_when_specified(
    server: str, busid: str, port: int
):
    """If port is not None, the command SHALL contain "--tcp-port" followed
    by str(port).

    **Validates: Requirements 3.2**
    """
    cmd = UsbipClient.build_attach_command(server=server, busid=busid, port=port)
    assert "--tcp-port" in cmd, (
        f"Expected '--tcp-port' in command {cmd} when port={port}"
    )
    tcp_port_idx = cmd.index("--tcp-port")
    assert tcp_port_idx + 1 < len(cmd), (
        "'--tcp-port' is the last element with no value following it"
    )
    assert cmd[tcp_port_idx + 1] == str(port), (
        f"Expected port value '{port}' after '--tcp-port', "
        f"got {cmd[tcp_port_idx + 1]!r}"
    )


# --- Test 5: Attach command does NOT include --tcp-port when port is None ---

@settings(max_examples=100)
@given(
    server=ipv4_strategy,
    busid=busid_strategy,
)
def test_attach_command_excludes_tcp_port_when_none(server: str, busid: str):
    """If port is None, the command SHALL NOT contain "--tcp-port".

    **Validates: Requirements 3.2**
    """
    cmd = UsbipClient.build_attach_command(server=server, busid=busid, port=None)
    assert "--tcp-port" not in cmd, (
        f"Expected '--tcp-port' NOT in command {cmd} when port is None"
    )


# --- Test 6: Pre-detach command always starts with ["usbip", "detach"] ---

@settings(max_examples=100)
@given(
    server=ipv4_strategy,
    busid=busid_strategy,
)
def test_detach_remote_command_starts_with_usbip_detach(
    server: str, busid: str
):
    """The pre-detach command SHALL always start with ["usbip", "detach"].

    **Validates: Requirements 3.1**
    """
    cmd = UsbipClient.build_detach_remote_command(server=server, busid=busid)
    assert cmd[0] == "usbip", f"Expected cmd[0]='usbip', got {cmd[0]!r}"
    assert cmd[1] == "detach", f"Expected cmd[1]='detach', got {cmd[1]!r}"


# --- Test 7: Pre-detach command contains -r followed by server ---

@settings(max_examples=100)
@given(
    server=ipv4_strategy,
    busid=busid_strategy,
)
def test_detach_remote_command_contains_r_flag(server: str, busid: str):
    """The pre-detach command SHALL contain "-r" followed by the server address.

    **Validates: Requirements 3.1**
    """
    cmd = UsbipClient.build_detach_remote_command(server=server, busid=busid)
    assert "-r" in cmd, f"Expected '-r' in command {cmd}"
    r_idx = cmd.index("-r")
    assert r_idx + 1 < len(cmd), "'-r' is the last element with no value following it"
    assert cmd[r_idx + 1] == server, (
        f"Expected server '{server}' after '-r', got {cmd[r_idx + 1]!r}"
    )


# --- Test 8: Pre-detach command contains -b followed by busid ---

@settings(max_examples=100)
@given(
    server=ipv4_strategy,
    busid=busid_strategy,
)
def test_detach_remote_command_contains_b_flag(server: str, busid: str):
    """The pre-detach command SHALL contain "-b" followed by the busid.

    **Validates: Requirements 3.1**
    """
    cmd = UsbipClient.build_detach_remote_command(server=server, busid=busid)
    assert "-b" in cmd, f"Expected '-b' in command {cmd}"
    b_idx = cmd.index("-b")
    assert b_idx + 1 < len(cmd), "'-b' is the last element with no value following it"
    assert cmd[b_idx + 1] == busid, (
        f"Expected busid '{busid}' after '-b', got {cmd[b_idx + 1]!r}"
    )
