# Feature: ha-usbip-esp32-client, Property 15: Log format structure
"""Property test verifying log output format matches the expected pattern:
<ISO-8601-timestamp> <LEVEL> <logger_name> <message>

**Validates: Requirements 8.2**
"""

import io
import logging
import re
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

from usbip_addon.logging_config import configure_logging, get_logger, reset_logging


# ISO 8601 timestamp pattern: YYYY-MM-DDTHH:MM:SS+0000
ISO_8601_PATTERN = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}"

# Full log line pattern: <timestamp> <LEVEL> <logger_name> <message>
# Message is optional (empty messages produce no trailing content)
LOG_LINE_PATTERN = re.compile(
    rf"^({ISO_8601_PATTERN}) (DEBUG|INFO|WARNING|ERROR) (\S+)(?: (.*))?$"
)

# Valid log level names that should appear in output
VALID_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]

# Strategy for logger names: non-empty alphanumeric with underscores/dots (realistic service names)
logger_name_strategy = st.from_regex(r"[a-zA-Z][a-zA-Z0-9_.]{0,30}", fullmatch=True)

# Strategy for log messages: text including unicode and special characters
# Excludes newlines (which would split log lines) and limits to non-whitespace-only
# to avoid ambiguity with trailing space stripping in log capture
message_strategy = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),  # Exclude surrogates
        blacklist_characters=("\n", "\r"),  # No newlines in single log line
    ),
    min_size=0,
    max_size=200,
)

# Strategy for log levels
level_strategy = st.sampled_from(["debug", "info", "warning", "error"])


def _capture_log_output(logger_name: str, level: str, message: str) -> str:
    """Configure logging, emit a message, and capture the formatted output."""
    reset_logging()

    # Capture stdout output
    capture_stream = io.StringIO()
    configure_logging(level="debug")  # Set to debug so all levels are captured

    # Get the root logger and replace its handler stream
    root_logger = logging.getLogger()
    # Find the stdout handler and redirect to our capture stream
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
            handler.stream = capture_stream
            break

    # Get logger and emit message
    logger = logging.getLogger(logger_name)
    log_method = getattr(logger, level)
    log_method(message)

    output = capture_stream.getvalue()
    reset_logging()
    return output


@settings(max_examples=100)
@given(
    logger_name=logger_name_strategy,
    level=level_strategy,
    message=message_strategy,
)
def test_log_format_matches_pattern(logger_name: str, level: str, message: str):
    """For any log message at any level, the formatted output SHALL match
    the pattern: <ISO-8601-timestamp> <LEVEL> <logger_name> <message>

    This validates that:
    1. Output starts with an ISO 8601 timestamp
    2. Followed by a level name (DEBUG, INFO, WARNING, ERROR)
    3. Followed by the logger name
    4. Followed by the message
    5. The format is consistent across all levels and message contents
    """
    output = _capture_log_output(logger_name, level, message)

    # Output should not be empty
    assert output != "", f"Expected log output but got empty string"

    # Get the raw output without stripping (preserve trailing whitespace for message checks)
    # Only strip the final newline that logging adds
    line = output.rstrip("\n")

    # The line may contain internal newlines if the message had them (filtered by strategy)
    # but verify the structural format of the first/only line
    match = LOG_LINE_PATTERN.match(line)
    assert match is not None, (
        f"Log line does not match expected format.\n"
        f"  Expected pattern: <ISO-8601-timestamp> <LEVEL> <logger_name> <message>\n"
        f"  Actual output: {line!r}"
    )

    # Verify the components
    timestamp_str, level_str, name_str, msg_str = match.groups()

    # Level should match what we logged
    assert level_str == level.upper(), (
        f"Expected level {level.upper()}, got {level_str}"
    )

    # Logger name should match what we specified
    assert name_str == logger_name, (
        f"Expected logger name {logger_name!r}, got {name_str!r}"
    )

    # Message should match. The format "%(name)s %(message)s" means:
    # - For non-empty messages: there's a space between name and message in the output
    # - For empty messages: the regex group is None (no space + content after name)
    if message == "":
        # Empty message: the format produces "<name> " but regex won't capture the trailing space
        # msg_str will be None or empty
        assert msg_str is None or msg_str == "", (
            f"Expected empty/no message for empty input, got {msg_str!r}"
        )
    else:
        # Non-empty message should appear as-is after the logger name
        assert msg_str == message, (
            f"Expected message {message!r}, got {msg_str!r}"
        )


@settings(max_examples=100)
@given(level=level_strategy)
def test_log_timestamp_is_valid_iso8601(level: str):
    """Verify the timestamp portion of every log line is valid ISO 8601
    with date, time, and timezone information."""
    output = _capture_log_output("test_service", level, "test message")

    lines = [line for line in output.strip().split("\n") if line]
    assert len(lines) == 1

    line = lines[0]
    match = LOG_LINE_PATTERN.match(line)
    assert match is not None

    timestamp_str = match.group(1)

    # Validate ISO 8601 components
    # Format should be: YYYY-MM-DDTHH:MM:SS+0000
    ts_pattern = re.compile(
        r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})([+-]\d{4})$"
    )
    ts_match = ts_pattern.match(timestamp_str)
    assert ts_match is not None, (
        f"Timestamp {timestamp_str!r} is not valid ISO 8601"
    )

    year, month, day, hour, minute, second, tz = ts_match.groups()

    # Basic range checks
    assert 1 <= int(month) <= 12, f"Invalid month: {month}"
    assert 1 <= int(day) <= 31, f"Invalid day: {day}"
    assert 0 <= int(hour) <= 23, f"Invalid hour: {hour}"
    assert 0 <= int(minute) <= 59, f"Invalid minute: {minute}"
    assert 0 <= int(second) <= 59, f"Invalid second: {second}"


@settings(max_examples=100)
@given(
    logger_name=logger_name_strategy,
    level=level_strategy,
)
def test_log_format_consistent_across_levels(logger_name: str, level: str):
    """Verify the format is consistent regardless of log level.
    All levels should produce the same structural format."""
    message = "consistency check"
    output = _capture_log_output(logger_name, level, message)

    lines = [line for line in output.strip().split("\n") if line]
    assert len(lines) == 1

    line = lines[0]

    # Split by spaces - first part is timestamp, second is level, third is name, rest is message
    parts = line.split(" ", 3)
    assert len(parts) == 4, (
        f"Expected 4 parts (timestamp, level, name, message), got {len(parts)}: {parts}"
    )

    timestamp, level_name, name, msg = parts
    assert level_name in VALID_LEVELS
    assert name == logger_name
    assert msg == message
