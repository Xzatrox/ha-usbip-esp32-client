# Feature: ha-usbip-esp32-client, Property 10: Notification cooldown enforcement
"""Property tests verifying notification cooldown logic.

For any sequence of notification triggers for the same device at monotonic
timestamps, only the first notification and those triggered at least 300
seconds after the previous sent notification SHALL be delivered. All others
SHALL be silently discarded.

**Validates: Requirements 10.4**
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from usbip_addon.notifications import NotificationManager


# --- Strategies ---

# Generate sorted (non-decreasing) lists of monotonic timestamps.
# Start from 0.0 and allow large gaps (to test both within and outside cooldown).
monotonic_timestamps_strategy = st.lists(
    st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
    min_size=1,
    max_size=50,
).map(sorted)

# Simple device names
device_name_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_",
    min_size=1,
    max_size=10,
).filter(lambda s: s.strip() != "")

# Simple server IP addresses
server_strategy = st.tuples(
    st.integers(min_value=1, max_value=254),
    st.integers(min_value=0, max_value=254),
    st.integers(min_value=0, max_value=254),
    st.integers(min_value=1, max_value=254),
).map(lambda t: f"{t[0]}.{t[1]}.{t[2]}.{t[3]}")


def _create_manager() -> NotificationManager:
    """Create a NotificationManager with a mocked config (notifications enabled)."""
    config = MagicMock()
    config.notifications_enabled = True
    config.token = "test-token"
    return NotificationManager(config)


def _compute_expected_deliveries(timestamps: list[float], cooldown: float) -> list[bool]:
    """Compute which timestamps should result in a delivered notification.

    The first trigger is always delivered. Subsequent triggers are delivered
    only if they occur at least `cooldown` seconds after the last delivered
    notification.

    Returns a list of booleans parallel to timestamps: True = delivered.
    """
    delivered = []
    last_sent_time = None

    for ts in timestamps:
        if last_sent_time is None:
            # First notification is always delivered
            delivered.append(True)
            last_sent_time = ts
        elif (ts - last_sent_time) >= cooldown:
            # Enough time has elapsed since last sent
            delivered.append(True)
            last_sent_time = ts
        else:
            # Within cooldown window — suppressed
            delivered.append(False)

    return delivered


# --- Property 10: Notification cooldown enforcement ---


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(
    timestamps=monotonic_timestamps_strategy,
    device=device_name_strategy,
    server=server_strategy,
)
def test_cooldown_enforcement(timestamps: list[float], device: str, server: str):
    """For any sequence of notification triggers for the same device at
    monotonic timestamps, only the first notification and those triggered
    at least 300 seconds after the previous sent notification SHALL be
    delivered. All others SHALL be silently discarded.

    **Validates: Requirements 10.4**
    """
    manager = _create_manager()
    cooldown = NotificationManager.COOLDOWN_SECONDS

    # Compute expected delivery pattern
    expected = _compute_expected_deliveries(timestamps, cooldown)

    # Track actual API calls
    actual_call_count = 0
    call_indices = []

    # We mock both time.monotonic (to control the clock) and urlopen (to
    # intercept the API call without network access).
    time_mock_values = iter(timestamps)

    def mock_monotonic():
        return next(time_mock_values)

    def mock_urlopen(*args, **kwargs):
        nonlocal actual_call_count
        actual_call_count += 1
        response = MagicMock()
        response.close = MagicMock()
        return response

    with patch("usbip_addon.notifications.time.monotonic", side_effect=mock_monotonic):
        with patch("usbip_addon.notifications.urlopen", side_effect=mock_urlopen):
            for i, ts in enumerate(timestamps):
                manager.notify_device_lost(device, server)
                if expected[i]:
                    call_indices.append(i)

    # 1. The first trigger is always delivered
    assert expected[0] is True, "First notification should always be delivered"

    # 2. Verify total delivered count matches expected
    expected_count = sum(expected)
    assert actual_call_count == expected_count, (
        f"Expected {expected_count} notifications to be delivered, "
        f"but {actual_call_count} were actually sent. "
        f"Timestamps: {timestamps[:10]}..."
    )


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(
    timestamps=monotonic_timestamps_strategy,
    device=device_name_strategy,
    server=server_strategy,
)
def test_within_cooldown_suppressed(timestamps: list[float], device: str, server: str):
    """Notifications triggered within 300 seconds of the last sent
    notification SHALL be silently discarded.

    **Validates: Requirements 10.4**
    """
    assume(len(timestamps) >= 2)

    manager = _create_manager()
    cooldown = NotificationManager.COOLDOWN_SECONDS

    # Track which notifications are delivered
    delivered_timestamps = []

    time_iter = iter(timestamps)

    def mock_monotonic():
        return next(time_iter)

    def mock_urlopen(*args, **kwargs):
        response = MagicMock()
        response.close = MagicMock()
        return response

    with patch("usbip_addon.notifications.time.monotonic", side_effect=mock_monotonic):
        with patch("usbip_addon.notifications.urlopen", side_effect=mock_urlopen):
            for ts in timestamps:
                # Check if this will be delivered by looking at internal state
                device_key = f"{device}:{server}"
                last = manager._last_sent.get(device_key)
                will_deliver = (last is None) or (ts - last) >= cooldown

                manager.notify_device_lost(device, server)

                if will_deliver:
                    delivered_timestamps.append(ts)

    # Verify that all consecutive delivered notifications respect the cooldown
    for i in range(1, len(delivered_timestamps)):
        gap = delivered_timestamps[i] - delivered_timestamps[i - 1]
        assert gap >= cooldown, (
            f"Notification at t={delivered_timestamps[i]} was delivered only "
            f"{gap}s after previous at t={delivered_timestamps[i-1]}, "
            f"which is less than the {cooldown}s cooldown."
        )


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(
    timestamps=st.lists(
        st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
        min_size=2,
        max_size=50,
    ).map(sorted),
    device=device_name_strategy,
    server=server_strategy,
)
def test_after_cooldown_delivered(timestamps: list[float], device: str, server: str):
    """Notifications triggered at least 300 seconds after the last sent
    notification SHALL be delivered.

    **Validates: Requirements 10.4**
    """
    manager = _create_manager()
    cooldown = NotificationManager.COOLDOWN_SECONDS

    time_iter = iter(timestamps)
    call_count = 0

    def mock_monotonic():
        return next(time_iter)

    def mock_urlopen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        response = MagicMock()
        response.close = MagicMock()
        return response

    with patch("usbip_addon.notifications.time.monotonic", side_effect=mock_monotonic):
        with patch("usbip_addon.notifications.urlopen", side_effect=mock_urlopen):
            for ts in timestamps:
                manager.notify_device_lost(device, server)

    # Independently compute expected delivery count
    expected = _compute_expected_deliveries(timestamps, cooldown)
    expected_count = sum(expected)

    assert call_count == expected_count, (
        f"Expected {expected_count} deliveries for timestamps "
        f"{timestamps[:10]}... but got {call_count}"
    )

    # Every timestamp that is 300+ seconds after the last delivery should
    # result in a delivery. Verify by checking the expected pattern:
    last_sent = None
    for i, ts in enumerate(timestamps):
        if last_sent is None:
            assert expected[i] is True
            last_sent = ts
        elif (ts - last_sent) >= cooldown:
            assert expected[i] is True, (
                f"Timestamp {ts} is {ts - last_sent}s after last sent at "
                f"{last_sent}, should have been delivered"
            )
            last_sent = ts
