# Feature: ha-usbip-esp32-client, Property 8: Flapping state transitions emit notifications only on escalation
"""Property tests verifying flapping state transition behavior.

For any sequence of flapping evaluations for a device, a notification and event
SHALL be emitted only when the state transitions upward (NONE→WARNING,
NONE→CRITICAL, WARNING→CRITICAL). Repeated evaluations at the same level or
downward transitions (via clearing) SHALL NOT produce additional notifications.

**Validates: Requirements 14.6**
"""

from datetime import timedelta
from dataclasses import dataclass
from unittest.mock import patch
from typing import List

from hypothesis import given, settings, assume, note
from hypothesis import strategies as st

from usbip_addon.flapping import FlappingDetector, FlappingState


# --- Mock Config ---


@dataclass
class MockConfig:
    """Minimal config for FlappingDetector tests."""

    flap_warning_threshold: int
    flap_critical_threshold: int
    flap_window_seconds: int
    flap_clear_seconds: int


# --- Strategies ---

# Thresholds: warning must be < critical, both positive
threshold_strategy = st.tuples(
    st.integers(min_value=1, max_value=10),
    st.integers(min_value=2, max_value=20),
).filter(lambda t: t[0] < t[1])

# Window sizes (reasonable range for testing)
window_strategy = st.integers(min_value=10, max_value=600)

# Clear period
clear_strategy = st.integers(min_value=10, max_value=900)

# Number of recovery events to record before each evaluation
recovery_count_strategy = st.integers(min_value=0, max_value=25)

# A sequence of recovery counts representing multiple evaluation rounds
evaluation_sequence_strategy = st.lists(
    recovery_count_strategy,
    min_size=2,
    max_size=10,
)

# Whether to clear state between evaluations
clear_between_strategy = st.lists(
    st.booleans(),
    min_size=1,
    max_size=10,
)


def determine_expected_state(count: int, warning_threshold: int, critical_threshold: int) -> FlappingState:
    """Determine what flapping state a count maps to."""
    if count >= critical_threshold:
        return FlappingState.CRITICAL
    elif count >= warning_threshold:
        return FlappingState.WARNING
    else:
        return FlappingState.NONE


STATE_ORDER = {
    FlappingState.NONE: 0,
    FlappingState.WARNING: 1,
    FlappingState.CRITICAL: 2,
}


def is_upward(old: FlappingState, new: FlappingState) -> bool:
    """Check if a transition is upward."""
    return STATE_ORDER[new] > STATE_ORDER[old]


# --- Property 8: Flapping state transitions emit notifications only on escalation ---


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(
    thresholds=threshold_strategy,
    window=window_strategy,
    clear_seconds=clear_strategy,
    recovery_counts=evaluation_sequence_strategy,
)
def test_evaluate_returns_non_none_only_on_upward_transitions(
    thresholds: tuple,
    window: int,
    clear_seconds: int,
    recovery_counts: List[int],
):
    """For any sequence of evaluations, evaluate() returns non-None ONLY when
    an upward state transition occurs. Repeated evaluations at the same level
    return None.

    **Validates: Requirements 14.6**
    """
    warning_threshold, critical_threshold = thresholds
    config = MockConfig(
        flap_warning_threshold=warning_threshold,
        flap_critical_threshold=critical_threshold,
        flap_window_seconds=window,
        flap_clear_seconds=clear_seconds,
    )
    detector = FlappingDetector(config)
    device_key = "test_device"

    # We directly manipulate recovery_timestamps to control the count
    # visible in the window, then call evaluate.
    current_state = FlappingState.NONE
    base_time = 1000.0

    for step_idx, count in enumerate(recovery_counts):
        # Set up timestamps all within the window by placing them at base_time
        tracker = detector._get_tracker(device_key)
        # Clear old timestamps and set exact count within window
        tracker.recovery_timestamps.clear()
        # All timestamps are "now" (within window)
        now = base_time + step_idx * 10
        for i in range(count):
            tracker.recovery_timestamps.append(now - i * 0.1)

        # Patch time.monotonic to return consistent "now"
        with patch("time.monotonic", return_value=now):
            result = detector.evaluate(device_key)

        # Determine expected new state from count
        new_state = determine_expected_state(count, warning_threshold, critical_threshold)

        if is_upward(current_state, new_state):
            # Should emit (return non-None)
            assert result is not None, (
                f"Step {step_idx}: Expected upward transition {current_state.value}→"
                f"{new_state.value} (count={count}, warn={warning_threshold}, "
                f"crit={critical_threshold}) but got None"
            )
            assert result == new_state, (
                f"Step {step_idx}: Expected {new_state.value} but got {result.value}"
            )
            current_state = new_state
        else:
            # No upward transition: should return None
            assert result is None, (
                f"Step {step_idx}: Expected None for non-upward transition "
                f"{current_state.value}→{new_state.value} (count={count}, "
                f"warn={warning_threshold}, crit={critical_threshold}) "
                f"but got {result}"
            )
            # State doesn't change on non-upward
            # (current_state remains as-is)


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(
    thresholds=threshold_strategy,
    window=window_strategy,
    clear_seconds=clear_strategy,
    repeat_count=st.integers(min_value=2, max_value=8),
)
def test_repeated_evaluations_at_same_level_return_none(
    thresholds: tuple,
    window: int,
    clear_seconds: int,
    repeat_count: int,
):
    """Repeated evaluations at the same level SHALL NOT produce additional
    notifications (evaluate returns None after the first transition).

    **Validates: Requirements 14.6**
    """
    warning_threshold, critical_threshold = thresholds
    config = MockConfig(
        flap_warning_threshold=warning_threshold,
        flap_critical_threshold=critical_threshold,
        flap_window_seconds=window,
        flap_clear_seconds=clear_seconds,
    )
    detector = FlappingDetector(config)
    device_key = "test_device"

    # Set up enough recoveries to reach WARNING level
    count = warning_threshold  # exactly at warning threshold
    now = 1000.0
    tracker = detector._get_tracker(device_key)
    for i in range(count):
        tracker.recovery_timestamps.append(now - i * 0.1)

    # First evaluation should trigger the WARNING transition
    with patch("time.monotonic", return_value=now):
        first_result = detector.evaluate(device_key)

    assert first_result == FlappingState.WARNING, (
        f"First evaluation with count={count} (threshold={warning_threshold}) "
        f"should return WARNING, got {first_result}"
    )

    # Subsequent evaluations at the same level should return None
    for repeat_idx in range(repeat_count):
        with patch("time.monotonic", return_value=now + repeat_idx + 1):
            # Timestamps are still within window (window is large)
            result = detector.evaluate(device_key)

        assert result is None, (
            f"Repeat {repeat_idx + 1}: Expected None for repeated evaluation "
            f"at WARNING level, but got {result}"
        )


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(
    thresholds=threshold_strategy,
    window=window_strategy,
    clear_seconds=clear_strategy,
)
def test_after_clearing_same_level_is_upward_again(
    thresholds: tuple,
    window: int,
    clear_seconds: int,
):
    """After clearing, a new evaluation returning the same level is treated
    as an upward transition again (NONE→WARNING emits notification).

    **Validates: Requirements 14.6**
    """
    warning_threshold, critical_threshold = thresholds
    config = MockConfig(
        flap_warning_threshold=warning_threshold,
        flap_critical_threshold=critical_threshold,
        flap_window_seconds=window,
        flap_clear_seconds=clear_seconds,
    )
    detector = FlappingDetector(config)
    device_key = "test_device"

    now = 1000.0
    tracker = detector._get_tracker(device_key)

    # Phase 1: Reach WARNING state
    for i in range(warning_threshold):
        tracker.recovery_timestamps.append(now - i * 0.1)
    tracker.last_recovery = now

    with patch("time.monotonic", return_value=now):
        result = detector.evaluate(device_key)
    assert result == FlappingState.WARNING

    # Phase 2: Clear the state (simulate enough time passing)
    clear_time = now + clear_seconds + 1
    with patch("time.monotonic", return_value=clear_time):
        cleared = detector.check_clear(device_key)
    assert cleared is True
    assert detector.get_state(device_key) == FlappingState.NONE

    # Phase 3: Add new recoveries and evaluate again
    # After clearing, timestamps were purged. Add new ones.
    new_now = clear_time + 10
    tracker = detector._get_tracker(device_key)
    for i in range(warning_threshold):
        tracker.recovery_timestamps.append(new_now - i * 0.1)
    tracker.last_recovery = new_now

    with patch("time.monotonic", return_value=new_now):
        result_after_clear = detector.evaluate(device_key)

    # Should emit WARNING again since we went from NONE→WARNING
    assert result_after_clear == FlappingState.WARNING, (
        f"After clearing and re-accumulating {warning_threshold} recoveries, "
        f"expected WARNING (upward from NONE), got {result_after_clear}"
    )


# --- Property 9: Flapping clearance after stability period ---
# **Validates: Requirements 14.5**


# flap_clear_seconds in valid config range (60-7200)
flap_clear_seconds_strategy = st.integers(min_value=60, max_value=7200)

# Non-NONE flapping states
non_none_state_strategy = st.sampled_from([FlappingState.WARNING, FlappingState.CRITICAL])

# Elapsed time factor >= 1.0 to ensure elapsed >= flap_clear_seconds
clearance_factor_strategy = st.floats(min_value=1.0, max_value=10.0)

# Elapsed time factor in [0.0, 1.0) to ensure elapsed < flap_clear_seconds
no_clearance_factor_strategy = st.floats(
    min_value=0.0, max_value=0.999, allow_nan=False, allow_infinity=False
)


@settings(max_examples=100, deadline=timedelta(seconds=10))
@given(
    flap_clear_secs=flap_clear_seconds_strategy,
    initial_state=non_none_state_strategy,
    factor=clearance_factor_strategy,
)
def test_clearance_when_elapsed_ge_threshold(
    flap_clear_secs: int,
    initial_state: FlappingState,
    factor: float,
):
    """For any device with a non-NONE flapping state, if the elapsed time
    since the last recovery event is >= flap_clear_seconds, check_clear()
    SHALL return True and state SHALL become NONE.

    **Validates: Requirements 14.5**
    """
    from usbip_addon.flapping import DeviceFlappingTracker

    config = MockConfig(
        flap_warning_threshold=3,
        flap_critical_threshold=5,
        flap_window_seconds=600,
        flap_clear_seconds=flap_clear_secs,
    )
    detector = FlappingDetector(config)
    device_key = "test_clearance_device"

    # Set up tracker directly with the desired state and a last_recovery time
    tracker = DeviceFlappingTracker()
    tracker.state = initial_state
    fake_last_recovery = 1000.0
    tracker.last_recovery = fake_last_recovery
    tracker.recovery_timestamps = [fake_last_recovery]
    detector._trackers[device_key] = tracker

    # elapsed = factor * flap_clear_secs >= flap_clear_secs (since factor >= 1.0)
    elapsed = factor * flap_clear_secs
    fake_now = fake_last_recovery + elapsed

    with patch("usbip_addon.flapping.time.monotonic", return_value=fake_now):
        result = detector.check_clear(device_key)

    assert result is True, (
        f"Expected check_clear to return True when elapsed ({elapsed:.1f}s) "
        f">= flap_clear_seconds ({flap_clear_secs}s), but got False"
    )
    assert detector.get_state(device_key) == FlappingState.NONE, (
        f"Expected state to be NONE after clearance, but got "
        f"{detector.get_state(device_key)}"
    )


@settings(max_examples=100, deadline=timedelta(seconds=10))
@given(
    flap_clear_secs=flap_clear_seconds_strategy,
    initial_state=non_none_state_strategy,
    factor=no_clearance_factor_strategy,
)
def test_no_clearance_when_elapsed_lt_threshold(
    flap_clear_secs: int,
    initial_state: FlappingState,
    factor: float,
):
    """For any device with a non-NONE flapping state, if the elapsed time
    since the last recovery event is < flap_clear_seconds, check_clear()
    SHALL return False and state SHALL remain unchanged.

    **Validates: Requirements 14.5**
    """
    from usbip_addon.flapping import DeviceFlappingTracker

    config = MockConfig(
        flap_warning_threshold=3,
        flap_critical_threshold=5,
        flap_window_seconds=600,
        flap_clear_seconds=flap_clear_secs,
    )
    detector = FlappingDetector(config)
    device_key = "test_no_clearance_device"

    tracker = DeviceFlappingTracker()
    tracker.state = initial_state
    fake_last_recovery = 1000.0
    tracker.last_recovery = fake_last_recovery
    tracker.recovery_timestamps = [fake_last_recovery]
    detector._trackers[device_key] = tracker

    # elapsed = factor * flap_clear_secs < flap_clear_secs (since factor < 1.0)
    elapsed = factor * flap_clear_secs
    fake_now = fake_last_recovery + elapsed

    with patch("usbip_addon.flapping.time.monotonic", return_value=fake_now):
        result = detector.check_clear(device_key)

    assert result is False, (
        f"Expected check_clear to return False when elapsed ({elapsed:.1f}s) "
        f"< flap_clear_seconds ({flap_clear_secs}s), but got True"
    )
    assert detector.get_state(device_key) == initial_state, (
        f"Expected state to remain {initial_state}, but got "
        f"{detector.get_state(device_key)}"
    )


@settings(max_examples=100, deadline=timedelta(seconds=10))
@given(
    flap_clear_secs=flap_clear_seconds_strategy,
)
def test_no_clearance_when_state_already_none(
    flap_clear_secs: int,
):
    """For any device with state already NONE, check_clear() SHALL return
    False regardless of elapsed time.

    **Validates: Requirements 14.5**
    """
    from usbip_addon.flapping import DeviceFlappingTracker

    config = MockConfig(
        flap_warning_threshold=3,
        flap_critical_threshold=5,
        flap_window_seconds=600,
        flap_clear_seconds=flap_clear_secs,
    )
    detector = FlappingDetector(config)
    device_key = "test_none_clearance_device"

    # Set up tracker with NONE state and a last_recovery that would
    # normally trigger clearance (elapsed well above threshold)
    tracker = DeviceFlappingTracker()
    tracker.state = FlappingState.NONE
    fake_last_recovery = 1000.0
    tracker.last_recovery = fake_last_recovery
    tracker.recovery_timestamps = [fake_last_recovery]
    detector._trackers[device_key] = tracker

    # Even with elapsed far exceeding threshold, should return False
    fake_now = fake_last_recovery + flap_clear_secs * 2.0

    with patch("usbip_addon.flapping.time.monotonic", return_value=fake_now):
        result = detector.check_clear(device_key)

    assert result is False, (
        f"Expected check_clear to return False when state is already NONE, "
        f"but got True"
    )
    assert detector.get_state(device_key) == FlappingState.NONE
