"""Structured logging configuration for the USB/IP add-on.

Provides ISO 8601 formatted logging to stdout/stderr for s6-overlay capture.
All services and scripts use this module to obtain configured loggers.

Format: <ISO-8601-timestamp> <LEVEL> <logger_name> <message>
"""

import logging
import sys
from datetime import timezone


# Valid log levels mapped from configuration strings to logging constants
LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

# Log format: ISO 8601 timestamp, level, logger name, message
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# ISO 8601 date format with timezone
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_configured = False


class _Iso8601Formatter(logging.Formatter):
    """Custom formatter that produces ISO 8601 timestamps with timezone."""

    def __init__(self):
        super().__init__(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    def formatTime(self, record, datefmt=None):
        """Format the time as ISO 8601 with UTC timezone offset."""
        from datetime import datetime

        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.strftime(LOG_DATE_FORMAT)


def configure_logging(level: str = "info") -> None:
    """Configure the root logger with structured output to stdout.

    Sets up the logging system with:
    - ISO 8601 timestamps
    - Output to stdout (for s6-overlay capture)
    - Configurable log level

    Args:
        level: Log level string - one of "debug", "info", "warning", "error".
               Defaults to "info" if an invalid value is provided.
    """
    global _configured

    # Resolve level string to logging constant, default to INFO
    log_level = LOG_LEVELS.get(level.lower(), logging.INFO)

    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    if not _configured:
        # Remove any existing handlers to avoid duplicates
        root_logger.handlers.clear()

        # Create stdout handler for s6-overlay capture
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.DEBUG)  # Let root logger level filter

        # Create stderr handler for ERROR and above
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.ERROR)

        # Apply ISO 8601 formatter
        formatter = _Iso8601Formatter()
        stdout_handler.setFormatter(formatter)
        stderr_handler.setFormatter(formatter)

        # Add handlers
        root_logger.addHandler(stdout_handler)
        root_logger.addHandler(stderr_handler)

        _configured = True
    else:
        # If already configured, just update the level on existing handlers
        root_logger.setLevel(log_level)


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger for a service or script.

    If configure_logging() has not been called yet, it will be called
    with the default level ("info").

    Args:
        name: Logger name identifying the originating service or script.
              Examples: "load_modules", "usbip_run", "monitor", "webui", "discovery"

    Returns:
        A configured logging.Logger instance.
    """
    if not _configured:
        configure_logging()

    return logging.getLogger(name)


def reset_logging() -> None:
    """Reset the logging configuration state.

    Primarily used for testing purposes to allow reconfiguration.
    """
    global _configured
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    _configured = False
