# Feature: ha-usbip-esp32-client, Property 12: Event log rotation preserves most recent events
"""Property tests verifying event log rotation logic.

For any sequence of N events written to the Event_Log where N > 200,
the file SHALL contain exactly 200 events, and those events SHALL be
the 200 most recently written ones in their original chronological order.

**Validates: Requirements 15.4**
"""

import json
import os
import tempfile
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from usbip_addon.event_log import EventLog


# --- Strategies ---

# Valid event types from EventLog.VALID_TYPES
valid_event_type_strategy = st.sampled_from(sorted(EventLog.VALID_TYPES))

# Simple device names (avoid slow text generation)
device_name_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789 -_",
    min_size=1,
    max_size=15,
).filter(lambda s: s.strip() != "")

# Simple server IP addresses
server_strategy = st.tuples(
    st.integers(min_value=1, max_value=254),
    st.integers(min_value=0, max_value=254),
    st.integers(min_value=0, max_value=254),
    st.integers(min_value=1, max_value=254),
).map(lambda t: f"{t[0]}.{t[1]}.{t[2]}.{t[3]}")


# --- Property 12: Event log rotation preserves most recent events ---

@settings(max_examples=100, deadline=timedelta(seconds=60))
@given(
    num_events=st.integers(min_value=201, max_value=220),
    event_type=valid_event_type_strategy,
    device=device_name_strategy,
    server=server_strategy,
)
def test_rotation_preserves_exactly_200_most_recent_events(
    num_events: int, event_type: str, device: str, server: str
):
    """For any sequence of N > 200 events written to the Event_Log,
    the file SHALL contain exactly 200 events, and those events SHALL
    be the 200 most recently written ones in their original chronological order.

    Each event gets a unique detail string containing its index so we can
    verify which events were kept after rotation.

    **Validates: Requirements 15.4**
    """
    # Use a unique temp file to avoid conflicts between test runs
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(tmp_fd)

    try:
        # Patch the EventLog PATH to use our temp file
        event_log = EventLog()
        original_path = EventLog.PATH
        EventLog.PATH = tmp_path

        # Remove the temp file so we start fresh (record() creates it)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

        # Write N events, each with a unique detail containing the index
        for i in range(num_events):
            event_log.record(event_type, device, server, f"event_index_{i}")

        # Read the file directly (not via read_events, which reverses order)
        assert os.path.exists(tmp_path), "Event log file should exist"

        with open(tmp_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        # 1. Verify exactly 200 events remain
        assert len(lines) == EventLog.MAX_EVENTS, (
            f"Expected exactly {EventLog.MAX_EVENTS} events after writing "
            f"{num_events}, got {len(lines)}"
        )

        # 2. Verify the remaining events are the most recent 200
        # The last N-200 events should have been discarded (indices 0 to N-201)
        # The kept events should be indices (N-200) through (N-1)
        expected_start_index = num_events - EventLog.MAX_EVENTS

        for line_idx, line in enumerate(lines):
            event = json.loads(line)
            expected_detail = f"event_index_{expected_start_index + line_idx}"
            assert event["detail"] == expected_detail, (
                f"Event at position {line_idx} has detail={event['detail']!r}, "
                f"expected {expected_detail!r}. "
                f"This means the rotation did not preserve the most recent events "
                f"in chronological order."
            )

        # 3. Verify chronological order (events appear in order of writing)
        # This is implicitly verified above since we check sequential indices,
        # but let's also verify via detail field ordering
        details = [json.loads(line)["detail"] for line in lines]
        indices = [int(d.split("_")[-1]) for d in details]
        assert indices == sorted(indices), (
            f"Events are not in chronological order. "
            f"Got indices: {indices[:5]}...{indices[-5:]}"
        )

    finally:
        # Restore original PATH and clean up
        EventLog.PATH = original_path
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
