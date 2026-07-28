"""Flask application for the USB/IP ESP32 Client WebUI.

Provides a web interface and REST API for device status monitoring,
management actions (attach/detach/discover), event history, and log viewing.
Accessible via Home Assistant Ingress with no additional authentication.

Requirements: 16.1, 16.2, 16.7, 16.8, 16.9, 16.10
"""

import os
from typing import Optional

from flask import Flask, g, jsonify, render_template, request

from usbip_addon.config import AddonConfig
from usbip_addon.discovery import DeviceDiscovery
from usbip_addon.event_log import EventLog
from usbip_addon.health import HealthChecker
from usbip_addon.logging_config import get_logger
from usbip_addon.server_lock import ServerLockManager
from usbip_addon.usbip_client import UsbipClient

logger = get_logger("webui")

# Shared instances used by the Flask app
_config: Optional[AddonConfig] = None
_usbip_client: Optional[UsbipClient] = None
_health_checker: Optional[HealthChecker] = None
_discovery: Optional[DeviceDiscovery] = None
_event_log: Optional[EventLog] = None
_server_locks: Optional[ServerLockManager] = None


def create_app(
    config: Optional[AddonConfig] = None,
    usbip_client: Optional[UsbipClient] = None,
    health_checker: Optional[HealthChecker] = None,
    discovery: Optional[DeviceDiscovery] = None,
    event_log: Optional[EventLog] = None,
    server_locks: Optional[ServerLockManager] = None,
) -> Flask:
    """Create and configure the Flask application.

    Args:
        config: AddonConfig instance (created if not provided).
        usbip_client: UsbipClient instance (created if not provided).
        health_checker: HealthChecker instance (created if not provided).
        discovery: DeviceDiscovery instance (created if not provided).
        event_log: EventLog instance (created if not provided).
        server_locks: ServerLockManager instance (created if not provided).

    Returns:
        Configured Flask application.
    """
    global _config, _usbip_client, _health_checker, _discovery, _event_log, _server_locks

    _config = config or AddonConfig()
    _usbip_client = usbip_client or UsbipClient()
    _health_checker = health_checker or HealthChecker()
    _discovery = discovery or DeviceDiscovery()
    _event_log = event_log or EventLog()
    _server_locks = server_locks or ServerLockManager()

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )

    _register_hooks(app)
    _register_pages(app)
    _register_api(app)

    return app


def _register_hooks(app: Flask) -> None:
    """Register before_request hooks for Ingress path handling."""

    @app.before_request
    def set_ingress_path():
        """Extract X-Ingress-Path header for URL generation (Req 16.9)."""
        g.ingress_path = request.headers.get("X-Ingress-Path", "")


def _register_pages(app: Flask) -> None:
    """Register page routes serving HTML templates."""

    @app.route("/")
    def dashboard():
        """Dashboard page showing device status, health, and flapping."""
        return render_template("dashboard.html", ingress_path=g.ingress_path)

    @app.route("/devices")
    def devices_page():
        """Device list page with attach/detach actions."""
        return render_template("devices.html", ingress_path=g.ingress_path)

    @app.route("/logs")
    def logs_page():
        """Log viewer page with auto-polling."""
        return render_template("logs.html", ingress_path=g.ingress_path)

    @app.route("/events")
    def events_page():
        """Event timeline page in reverse chronological order."""
        return render_template("events.html", ingress_path=g.ingress_path)


def _register_api(app: Flask) -> None:
    """Register API endpoint routes returning JSON."""

    @app.route("/api/status")
    def api_status():
        """GET /api/status - device status and server health.

        Returns JSON with:
        - devices: list of configured devices with attachment state
        - health: per-server health check results with latency
        - ports: currently attached port entries
        """
        try:
            _config.read_config()
        except RuntimeError:
            pass  # Use cached config if API is unavailable

        devices_config = _config.devices
        ports = _usbip_client.list_ports()

        # Build device status list
        device_statuses = []
        for device in devices_config:
            # Check if device is attached by matching server + busid in port list
            attached_port = None
            for port_entry in ports:
                if port_entry.server == device.server and port_entry.busid == device.busid:
                    attached_port = port_entry.port
                    break

            # Perform health check
            health = _health_checker.check(device.server, device.port)

            device_statuses.append({
                "name": device.name,
                "server": device.server,
                "port": device.port,
                "busid": device.busid,
                "attached": attached_port is not None,
                "attached_port": attached_port,
                "health": {
                    "reachable": health.reachable,
                    "latency_ms": health.latency_ms,
                    "error": health.error,
                },
            })

        # Build port list
        port_list = [
            {
                "port": p.port,
                "server": p.server,
                "busid": p.busid,
                "device_info": p.device_info,
            }
            for p in ports
        ]

        return jsonify({
            "devices": device_statuses,
            "ports": port_list,
        })

    @app.route("/api/attach", methods=["POST"])
    def api_attach():
        """POST /api/attach - attach a device by server IP.

        Request body: {"server": "<ip_address>"}

        Performs health check, pre-detach, sysfs remount, and attach
        with server lock to prevent concurrent ESP32 access.
        """
        data = request.get_json(silent=True)
        if not data or "server" not in data:
            return jsonify({"error": "Missing 'server' field in request body"}), 400

        target_server = data["server"]

        # Find matching device config
        try:
            _config.read_config()
        except RuntimeError:
            pass

        devices_config = _config.devices
        device = None
        for d in devices_config:
            if d.server == target_server:
                device = d
                break

        if device is None:
            return jsonify({
                "error": f"No configured device found for server '{target_server}'"
            }), 404

        # Perform attach with server lock
        with _server_locks.lock(device.server):
            # Health check
            health = _health_checker.check(device.server, device.port)
            if not health.reachable:
                _event_log.record(
                    "attach_fail", device.name, device.server,
                    f"Health check failed: {health.error}"
                )
                return jsonify({
                    "success": False,
                    "error": f"Server unreachable: {health.error}",
                }), 503

            # Pre-detach (ignore failure)
            _usbip_client.detach_remote(device.server, device.busid)

            # Sysfs remount
            _usbip_client.remount_sysfs()

            # Attach
            result = _usbip_client.attach(
                server=device.server,
                busid=device.busid,
                port=device.port if device.port != 3240 else None,
            )

        if result.success:
            _event_log.record(
                "attach_ok", device.name, device.server,
                f"Attached to port {result.port}"
            )
            return jsonify({
                "success": True,
                "port": result.port,
                "device": device.name,
                "server": device.server,
            })
        else:
            _event_log.record(
                "attach_fail", device.name, device.server,
                f"Attach failed: {result.stderr}"
            )
            return jsonify({
                "success": False,
                "error": result.stderr,
            }), 500

    @app.route("/api/detach", methods=["POST"])
    def api_detach():
        """POST /api/detach - detach a device by port number.

        Request body: {"port": <port_number>}
        """
        data = request.get_json(silent=True)
        if not data or "port" not in data:
            return jsonify({"error": "Missing 'port' field in request body"}), 400

        try:
            port_num = int(data["port"])
        except (TypeError, ValueError):
            return jsonify({"error": "'port' must be an integer"}), 400

        success = _usbip_client.detach(port_num)

        if success:
            _event_log.record(
                "detach_ok", "", "",
                f"Detached port {port_num}"
            )
            return jsonify({
                "success": True,
                "port": port_num,
            })
        else:
            _event_log.record(
                "detach_fail", "", "",
                f"Failed to detach port {port_num}"
            )
            return jsonify({
                "success": False,
                "error": f"Failed to detach port {port_num}",
            }), 500

    @app.route("/api/discover")
    def api_discover():
        """GET /api/discover - run discovery for a specified server.

        Query param: ?server=<ip_address>

        Uses server lock to prevent concurrent ESP32 access.
        """
        server = request.args.get("server")
        if not server:
            return jsonify({"error": "Missing 'server' query parameter"}), 400

        # Find matching device config for port info
        try:
            _config.read_config()
        except RuntimeError:
            pass

        devices_config = _config.devices
        device_port = None
        for d in devices_config:
            if d.server == server:
                device_port = d.port if d.port != 3240 else None
                break

        # Run discovery with server lock
        with _server_locks.lock(server):
            result = _discovery.discover(server, port=device_port)

        if result.success:
            devices_found = [
                {
                    "busid": dev.busid,
                    "manufacturer": dev.manufacturer,
                    "product": dev.product,
                }
                for dev in result.devices
            ]
            _event_log.record(
                "discover", "", server,
                f"Found {len(result.devices)} device(s)"
            )
            return jsonify({
                "success": True,
                "server": server,
                "devices": devices_found,
            })
        else:
            _event_log.record(
                "discover", "", server,
                f"Discovery failed: {result.error}"
            )
            return jsonify({
                "success": False,
                "server": server,
                "error": result.error,
            }), 500

    @app.route("/api/events")
    def api_events():
        """GET /api/events - read Event_Log entries.

        Query param: ?limit=N (default 200)

        Returns events in reverse chronological order (most recent first).
        """
        try:
            limit = int(request.args.get("limit", 200))
        except (TypeError, ValueError):
            limit = 200

        # Clamp limit to reasonable range
        limit = max(1, min(limit, 200))

        events = _event_log.read_events(limit=limit)

        return jsonify({
            "events": events,
            "count": len(events),
        })

    @app.route("/api/logs")
    def api_logs():
        """GET /api/logs - fetch logs from Supervisor logs API.

        Fetches recent log output from /addons/self/logs endpoint.
        """
        try:
            from urllib.request import Request, urlopen
            from urllib.error import URLError, HTTPError

            token = os.environ.get("SUPERVISOR_TOKEN", "")
            url = f"{AddonConfig.SUPERVISOR_URL}/addons/self/logs"

            req = Request(url)
            req.add_header("Authorization", f"Bearer {token}")

            with urlopen(req, timeout=5) as response:
                logs_text = response.read().decode("utf-8", errors="replace")

            # Return last 200 lines
            lines = logs_text.splitlines()
            lines = lines[-200:] if len(lines) > 200 else lines

            return jsonify({
                "logs": lines,
                "count": len(lines),
            })

        except (URLError, HTTPError, OSError) as e:
            logger.warning("Failed to fetch logs from Supervisor API: %s", e)
            return jsonify({
                "logs": [],
                "count": 0,
                "error": str(e),
            }), 502


# ---------------------------------------------------------------------------
# Module-level app instance for s6 service entry point import
# ---------------------------------------------------------------------------
# The webui/run service script imports `app` directly from this module.
# This creates the app with default instances (created on first use).
app = create_app()
