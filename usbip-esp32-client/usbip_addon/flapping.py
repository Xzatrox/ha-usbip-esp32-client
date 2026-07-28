"""Flapping detection module for the USB/IP ESP32 Client add-on.

Implements a per-device state machine that tracks recovery events and
detects flapping patterns (repeated disconnect/reconnect cycles) indicating
WiFi instability or hardware issues.

Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from usbip_addon.logging_config import get_logger

logger = get_logger("flapping")


class FlappingState(Enum):
    """Flapping severity levels."""

    NONE = "none"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class DeviceFlappingTracker:
    """Per-device state for flapping detection.

    Attributes:
        recovery_timestamps: List of monotonic timestamps when recovery events occurred.
        state: Current flapping state level.
        last_recovery: Monotonic timestamp of the most recent recovery event, or None.
    """

    recovery_timestamps: List[float] = field(default_factory=list)
    state: FlappingState = FlappingState.NONE
    last_recovery: Optional[float] = None


class FlappingDetector:
    """Per-device flapping state machine.

    Tracks recovery events per device using monotonic timestamps and
    evaluates flapping severity based on configurable thresholds and
    time windows.

    The detector only emits state transitions on upward escalation:
    NONE→WARNING, NONE→CRITICAL, WARNING→CRITICAL. Repeated evaluations
    at the same level do not produce additional transitions.

    Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6
    """

    def __init__(self, config):
        """Initialize the flapping detector.

        Args:
            config: AddonConfig instance providing flapping thresholds
                    (flap_warning_threshold, flap_critical_threshold,
                    flap_window_seconds, flap_clear_seconds).
        """
        self._config = config
        self._trackers: Dict[str, DeviceFlappingTracker] = {}

    def _get_tracker(self, device_key: str) -> DeviceFlappingTracker:
        """Get or create a tracker for the given device key."""
        if device_key not in self._trackers:
            self._trackers[device_key] = DeviceFlappingTracker()
        return self._trackers[device_key]

    def record_recovery(self, device_key: str) -> None:
        """Record a recovery event timestamp.

        Records the current monotonic time as a recovery event for the
        specified device. This should be called whenever the monitor
        service successfully reattaches a device.

        Args:
            device_key: Unique device identifier (e.g., DeviceEntry.key).

        Requirements: 14.1
        """
        now = time.monotonic()
        tracker = self._get_tracker(device_key)
        tracker.recovery_timestamps.append(now)
        tracker.last_recovery = now

    def evaluate(self, device_key: str) -> Optional[FlappingState]:
        """Evaluate flapping state for a device.

        Counts recovery events within the configured flap_window_seconds
        and determines the flapping level based on threshold counts.
        Returns the new state only on upward transitions.

        Args:
            device_key: Unique device identifier.

        Returns:
            The new FlappingState if an upward transition occurred
            (NONE→WARNING, NONE→CRITICAL, WARNING→CRITICAL), or None
            if no upward transition happened.

        Requirements: 14.2, 14.3, 14.4, 14.6
        """
        tracker = self._get_tracker(device_key)

        now = time.monotonic()
        window = self._config.flap_window_seconds
        cutoff = now - window

        # Count recovery events within the window (Req 14.2)
        count = sum(1 for ts in tracker.recovery_timestamps if ts >= cutoff)

        # Determine the new level based on thresholds (Req 14.3, 14.4)
        critical_threshold = self._config.flap_critical_threshold
        warning_threshold = self._config.flap_warning_threshold

        if count >= critical_threshold:
            new_state = FlappingState.CRITICAL
        elif count >= warning_threshold:
            new_state = FlappingState.WARNING
        else:
            new_state = FlappingState.NONE

        # Only emit on upward transition (Req 14.6)
        old_state = tracker.state
        if self._is_upward_transition(old_state, new_state):
            tracker.state = new_state
            return new_state

        # No upward transition occurred
        return None

    def check_clear(self, device_key: str) -> bool:
        """Check if device has been stable long enough to clear flapping state.

        If the elapsed time since the last recovery event is greater than
        or equal to flap_clear_seconds, the device's flapping state is
        reset to NONE.

        Args:
            device_key: Unique device identifier.

        Returns:
            True if the flapping state was cleared (transitioned to NONE),
            False otherwise.

        Requirements: 14.5
        """
        tracker = self._get_tracker(device_key)

        # Nothing to clear if already NONE
        if tracker.state == FlappingState.NONE:
            return False

        # Can't clear if there's no recovery recorded
        if tracker.last_recovery is None:
            return False

        now = time.monotonic()
        elapsed = now - tracker.last_recovery
        clear_seconds = self._config.flap_clear_seconds

        if elapsed >= clear_seconds:
            tracker.state = FlappingState.NONE
            # Prune old timestamps that are outside any reasonable window
            tracker.recovery_timestamps.clear()
            return True

        return False

    def get_state(self, device_key: str) -> FlappingState:
        """Get the current flapping state for a device.

        Args:
            device_key: Unique device identifier.

        Returns:
            The current FlappingState for the device.
        """
        tracker = self._get_tracker(device_key)
        return tracker.state

    @staticmethod
    def _is_upward_transition(
        old_state: FlappingState, new_state: FlappingState
    ) -> bool:
        """Determine if a state change is an upward transition.

        Upward transitions are:
        - NONE → WARNING
        - NONE → CRITICAL
        - WARNING → CRITICAL

        Args:
            old_state: The current state.
            new_state: The proposed new state.

        Returns:
            True if the transition is upward, False otherwise.
        """
        state_order = {
            FlappingState.NONE: 0,
            FlappingState.WARNING: 1,
            FlappingState.CRITICAL: 2,
        }
        return state_order[new_state] > state_order[old_state]
