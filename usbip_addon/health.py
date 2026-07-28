"""Health check module for ESP32 server reachability.

Performs a TCP connection probe to verify that an ESP32 USB/IP server
is reachable on its configured port before attempting device attachment.

The health check:
1. Creates a TCP socket
2. Sets timeout to 2 seconds (configurable)
3. Attempts to connect to server:port
4. On success: records latency, closes socket immediately, returns reachable=True
5. On failure: captures error message, returns reachable=False
"""

import socket
import time
from dataclasses import dataclass
from typing import Optional

from .logging_config import get_logger

logger = get_logger("health")


@dataclass
class HealthResult:
    """Result of a TCP health check probe.

    Attributes:
        reachable: Whether the server responded to the TCP connection.
        latency_ms: Round-trip connection time in milliseconds (None on failure).
        error: Error description string (None on success).
    """

    reachable: bool
    latency_ms: Optional[float]
    error: Optional[str]


class HealthChecker:
    """TCP probe to verify ESP32 server reachability.

    Performs a simple TCP connection attempt to the server's USB/IP port.
    The socket is closed immediately on success to avoid holding the
    ESP32's single TCP connection slot.
    """

    def check(
        self, server: str, port: int = 3240, timeout: float = 2.0
    ) -> HealthResult:
        """Attempt a TCP connection to verify server reachability.

        Creates a TCP socket, sets the timeout, and attempts to connect.
        On success, the socket is closed immediately and latency is recorded.
        On failure, the error message is captured.

        Args:
            server: IP address or hostname of the ESP32 server.
            port: TCP port to probe (default 3240, the USB/IP default).
            timeout: Connection timeout in seconds (default 2.0).

        Returns:
            HealthResult with reachable status, latency, and any error.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        start = time.monotonic()
        try:
            sock.connect((server, port))
            elapsed = time.monotonic() - start
            latency_ms = round(elapsed * 1000, 2)

            # Close socket immediately on success (ESP32 single-connection constraint)
            sock.close()

            logger.debug(
                "Health check passed for %s:%d (latency: %.2fms)",
                server,
                port,
                latency_ms,
            )
            return HealthResult(reachable=True, latency_ms=latency_ms, error=None)

        except socket.timeout:
            elapsed = time.monotonic() - start
            error_msg = f"Connection timed out after {timeout}s"
            logger.warning(
                "Health check failed for %s:%d - %s", server, port, error_msg
            )
            return HealthResult(reachable=False, latency_ms=None, error=error_msg)

        except ConnectionRefusedError:
            error_msg = "Connection refused"
            logger.warning(
                "Health check failed for %s:%d - %s", server, port, error_msg
            )
            return HealthResult(reachable=False, latency_ms=None, error=error_msg)

        except OSError as e:
            error_msg = f"Network error: {e}"
            logger.warning(
                "Health check failed for %s:%d - %s", server, port, error_msg
            )
            return HealthResult(reachable=False, latency_ms=None, error=error_msg)

        finally:
            try:
                sock.close()
            except OSError:
                pass
