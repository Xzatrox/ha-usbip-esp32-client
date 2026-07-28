"""Event log module for the USB/IP ESP32 Client add-on.

Manages an append-only JSONL event file with 200-event rotation.
Events are written atomically as single short append operations,
making concurrent writes from different services safe.

Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import List

from usbip_addon.logging_config import get_logger

logger = get_logger("event_log")


class EventLog:
    """Append-only JSONL event logger with 200-event rotation.

    Writes events to a JSONL file at /tmp/usbip_events.jsonl. Each event
    is a single JSON line with fields: ts, type, device, server, detail.

    The file is truncated to the 200 most recent events when the limit
    is exceeded. Existing events are preserved across add-on restarts
    (Req 15.6).

    Concurrent writes are safe because each write is a single short
    append operation (Req 15.5).
    """

    PATH = "/tmp/usbip_events.jsonl"
    MAX_EVENTS = 200

    VALID_TYPES = {
        "attach_ok",
        "attach_fail",
        "detach_ok",
        "detach_fail",
        "device_lost",
        "device_recovered",
        "reattach_attempt",
        "reattach_ok",
        "reattach_fail",
        "flap_warning",
        "flap_critical",
        "flap_cleared",
        "discover",
    }

    def record(self, event_type: str, device: str, server: str, detail: str) -> None:
        """Append an event to the log file, truncating if over MAX_EVENTS.

        Creates a JSON event entry with an ISO 8601 UTC timestamp and
        appends it as a single line to the JSONL file.

        Args:
            event_type: Event type string, must be one of VALID_TYPES.
            device: Device friendly name.
            server: Server IP address string.
            detail: Human-readable detail text.

        Raises:
            ValueError: If event_type is not in VALID_TYPES.
        """
        if event_type not in self.VALID_TYPES:
            raise ValueError(
                f"Invalid event type '{event_type}'. "
                f"Must be one of: {sorted(self.VALID_TYPES)}"
            )

        event = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z",
            "type": event_type,
            "device": device,
            "server": server,
            "detail": detail,
        }

        line = json.dumps(event, separators=(",", ":")) + "\n"

        try:
            # Atomic append: single short write operation (Req 15.5)
            with open(self.PATH, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            logger.warning("Failed to write event to log: %s", e)
            return

        # Truncate if over limit
        self._truncate_if_needed()

    def read_events(self, limit: int = 200) -> List[dict]:
        """Read events from the log file in reverse chronological order.

        Args:
            limit: Maximum number of events to return (default 200).

        Returns:
            List of event dicts, most recent first.
        """
        if not os.path.exists(self.PATH):
            return []

        events: List[dict] = []
        try:
            with open(self.PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        # Skip malformed lines
                        continue
        except OSError as e:
            logger.warning("Failed to read event log: %s", e)
            return []

        # Return in reverse chronological order (most recent first)
        events.reverse()

        # Apply limit
        if limit < len(events):
            events = events[:limit]

        return events

    def _truncate_if_needed(self) -> None:
        """Keep only the most recent MAX_EVENTS entries.

        Reads the current file, keeps the last MAX_EVENTS lines,
        and rewrites the file atomically using a temp file + rename.

        Requirements: 15.4
        """
        if not os.path.exists(self.PATH):
            return

        try:
            with open(self.PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            logger.warning("Failed to read event log for truncation: %s", e)
            return

        # Filter out empty lines
        lines = [line for line in lines if line.strip()]

        if len(lines) <= self.MAX_EVENTS:
            return

        # Keep only the most recent MAX_EVENTS
        lines = lines[-self.MAX_EVENTS:]

        # Write atomically via temp file + rename (same directory for atomic rename)
        dir_name = os.path.dirname(self.PATH)
        try:
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.writelines(lines)
                os.replace(tmp_path, self.PATH)
            except OSError:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.warning("Failed to truncate event log: %s", e)
