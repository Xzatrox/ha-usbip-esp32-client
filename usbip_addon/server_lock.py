"""Per-server threading locks for ESP32 single-connection constraint.

ESP32 servers support only one TCP client connection at a time. This module
provides a ServerLockManager that serializes all operations (discovery, health
check, attachment) targeting the same server, preventing concurrent access
from the Monitor Service and WebUI Service.
"""

import threading
from contextlib import contextmanager
from typing import Dict


class ServerLockManager:
    """Per-server threading locks for ESP32 single-connection constraint.

    Uses a meta-lock to protect thread-safe creation of per-server locks.
    Provides both explicit acquire/release and a context manager interface.
    """

    def __init__(self):
        self._locks: Dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()

    def _get_lock(self, server: str) -> threading.Lock:
        """Get or create a lock for the given server address.

        Uses the meta-lock to ensure thread-safe lock creation when
        multiple threads request a lock for the same server simultaneously.
        """
        if server in self._locks:
            return self._locks[server]
        with self._meta_lock:
            # Double-check after acquiring meta-lock
            if server not in self._locks:
                self._locks[server] = threading.Lock()
            return self._locks[server]

    def acquire(self, server: str) -> None:
        """Acquire lock for server (blocking).

        Blocks until the lock for the specified server is available.
        """
        lock = self._get_lock(server)
        lock.acquire()

    def release(self, server: str) -> None:
        """Release lock for server.

        Raises RuntimeError if the lock is not held or does not exist.
        """
        lock = self._get_lock(server)
        lock.release()

    @contextmanager
    def lock(self, server: str):
        """Context manager for server lock.

        Usage:
            with server_locks.lock("192.168.1.100"):
                # Only one thread at a time can reach here for this server
                perform_operation(server)
        """
        self.acquire(server)
        try:
            yield
        finally:
            self.release(server)
