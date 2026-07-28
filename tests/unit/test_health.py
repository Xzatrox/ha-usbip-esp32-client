"""Unit tests for the health check module.

Tests verify TCP probe behavior for server reachability checking.
"""

import socket
import threading
import time

from usbip_addon.health import HealthChecker, HealthResult


class TestHealthResult:
    """Test the HealthResult dataclass."""

    def test_successful_result(self):
        result = HealthResult(reachable=True, latency_ms=1.5, error=None)
        assert result.reachable is True
        assert result.latency_ms == 1.5
        assert result.error is None

    def test_failed_result(self):
        result = HealthResult(reachable=False, latency_ms=None, error="Connection refused")
        assert result.reachable is False
        assert result.latency_ms is None
        assert result.error == "Connection refused"


class TestHealthChecker:
    """Test the HealthChecker TCP probe."""

    def _start_test_server(self):
        """Start a temporary TCP server for testing and return (port, server_socket)."""
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]
        return port, server_sock

    def test_successful_connection(self):
        """Health check should return reachable=True when server accepts connection."""
        port, server_sock = self._start_test_server()
        try:
            checker = HealthChecker()
            result = checker.check("127.0.0.1", port=port, timeout=2.0)

            assert result.reachable is True
            assert result.latency_ms is not None
            assert result.latency_ms >= 0
            assert result.error is None
        finally:
            server_sock.close()

    def test_connection_refused(self):
        """Health check should return reachable=False when connection is refused."""
        # Use a port that's definitely not listening
        # Bind and immediately close to get a definitely-unused port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        checker = HealthChecker()
        result = checker.check("127.0.0.1", port=port, timeout=2.0)

        assert result.reachable is False
        assert result.latency_ms is None
        assert result.error is not None
        # On different OSes, connection to a closed port may be "refused" or "timed out"
        assert ("refused" in result.error.lower() or
                "timed out" in result.error.lower() or
                "error" in result.error.lower())

    def test_timeout(self):
        """Health check should return reachable=False on timeout."""
        # Use a non-routable IP to trigger timeout
        checker = HealthChecker()
        result = checker.check("192.0.2.1", port=3240, timeout=0.5)

        assert result.reachable is False
        assert result.latency_ms is None
        assert result.error is not None
        assert "timed out" in result.error.lower() or "error" in result.error.lower()

    def test_socket_closed_immediately_on_success(self):
        """Socket should be closed immediately after successful connection."""
        port, server_sock = self._start_test_server()

        # Accept connections in a thread to verify the client closes quickly
        accepted_connections = []

        def accept_connection():
            try:
                conn, addr = server_sock.accept()
                # Wait briefly and check if client closed
                time.sleep(0.1)
                try:
                    # If client closed, recv should return empty
                    data = conn.recv(1)
                    accepted_connections.append(("open", data))
                except (ConnectionResetError, OSError):
                    accepted_connections.append(("closed", None))
                finally:
                    conn.close()
            except OSError:
                pass

        thread = threading.Thread(target=accept_connection, daemon=True)
        thread.start()

        try:
            checker = HealthChecker()
            result = checker.check("127.0.0.1", port=port, timeout=2.0)

            assert result.reachable is True
            thread.join(timeout=2.0)

            # Client should have closed the connection
            assert len(accepted_connections) == 1
            status, data = accepted_connections[0]
            # Either "closed" (got error) or "open" with empty data (graceful close)
            if status == "open":
                assert data == b""  # Empty recv = peer closed
        finally:
            server_sock.close()

    def test_default_port(self):
        """Default port should be 3240."""
        checker = HealthChecker()
        # We can't easily test the default is used in connect,
        # but we verify the result structure with default params
        result = checker.check("192.0.2.1", timeout=0.3)
        assert result.reachable is False
        # The important thing is that no exception was raised with defaults

    def test_default_timeout(self):
        """Default timeout should be 2.0 seconds."""
        port, server_sock = self._start_test_server()
        try:
            checker = HealthChecker()
            # Should work with just server and port
            result = checker.check("127.0.0.1", port=port)
            assert result.reachable is True
        finally:
            server_sock.close()

    def test_latency_measurement(self):
        """Latency should be a non-negative number in milliseconds."""
        port, server_sock = self._start_test_server()
        try:
            checker = HealthChecker()
            result = checker.check("127.0.0.1", port=port, timeout=2.0)

            assert result.reachable is True
            assert result.latency_ms is not None
            assert isinstance(result.latency_ms, float)
            assert result.latency_ms >= 0
            # Localhost connection should be very fast (< 100ms)
            assert result.latency_ms < 100
        finally:
            server_sock.close()
