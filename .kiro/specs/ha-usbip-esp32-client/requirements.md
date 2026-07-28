# Requirements Document

## Introduction

A Home Assistant add-on that acts as a USB/IP client specifically for ESP32 devices running the ESPHome USB/IP server component. The add-on attaches remote USB devices exposed by one or more ESP32-S3 servers over WiFi, making them available as local USB devices within the Home Assistant host. It supports configurable server IP, port, and busid per device entry, using the standard USB/IP protocol (default port 3240, default busid "1-1"). The add-on includes device discovery, connection monitoring with flapping detection, a JSONL event log, and a lightweight WebUI accessible via Home Assistant Ingress for status monitoring, device management, and event history.

## Glossary

- **Add_On**: The Home Assistant Supervisor add-on container running Alpine Linux that hosts all services described in this document
- **ESP32_Server**: An ESP32-S3 device running the ESPHome USB/IP server component, listening on TCP port 3240 and currently exposing one USB device with busid "1-1"; the ESP32_Server supports only one TCP client connection at a time
- **VHCI_Module**: The Linux kernel module `vhci-hcd` that provides virtual USB host controller interfaces, enabling USB/IP device attachment on the host
- **Monitor_Service**: A long-running s6-overlay service within the Add_On that periodically checks device connectivity, triggers reattachment when devices are lost, and evaluates flapping state
- **Attach_Service**: A long-running s6-overlay service within the Add_On that performs the initial USB/IP device attachment at startup and remains alive for s6 lifecycle management
- **WebUI_Service**: A long-running s6-overlay service within the Add_On running a Flask application on ingress port 8099, providing device status, management actions, log viewing, and event history via Home Assistant Ingress
- **Supervisor_API**: The Home Assistant Supervisor HTTP API used to read add-on configuration, fetch logs, and send persistent notifications
- **Device_Entry**: A single configured ESP32 server with its IP address, friendly name, optional port override, and optional busid (defaults to "1-1")
- **Health_Check**: A TCP connection probe to an ESP32_Server on its USB/IP port to verify network reachability before attempting attachment
- **Discovery**: The process of running `usbip list -r <server_ip>` against an ESP32_Server and parsing the output to confirm the server is responsive and has a device available
- **Event_Log**: A JSONL-formatted file (`/tmp/usbip_events.jsonl`) recording timestamped entries for all significant operations, limited to the 200 most recent events
- **Flapping**: A pattern where an ESP32_Server device repeatedly disconnects and reconnects within a short time window, indicating WiFi instability or hardware issues
- **Ingress**: Home Assistant's reverse proxy mechanism that provides authenticated access to add-on web interfaces through the HA side panel without exposing additional ports

## Requirements

### Requirement 1: Kernel Module Loading

**User Story:** As a Home Assistant user, I want the add-on to automatically load the vhci-hcd kernel module at startup, so that USB/IP device attachment is possible without manual host configuration.

#### Acceptance Criteria

1. WHEN the Add_On starts, THE Add_On SHALL execute `/sbin/modprobe vhci-hcd` to load the VHCI_Module and verify the module is present by confirming that `/sys/module/vhci_hcd` exists or that the module appears in `lsmod` output
2. IF the modprobe command returns a non-zero exit code, THEN THE Add_On SHALL log an error message containing the module name "vhci-hcd" and the stderr output from the failed command, and exit with a non-zero status to prevent dependent services from starting
3. WHEN the VHCI_Module is already loaded on the host (i.e., `/sys/module/vhci_hcd` already exists), THE Add_On SHALL skip loading, log an informational message, and continue startup without producing any error or warning log entries
4. IF modprobe succeeds (exit code 0) but the VHCI_Module cannot be verified in `lsmod` output or `/sys/module/vhci_hcd`, THEN THE Add_On SHALL log a warning and continue startup

### Requirement 2: Sysfs Remount

**User Story:** As a Home Assistant user, I want the add-on to handle sysfs container isolation automatically, so that USB/IP attachment works without manual intervention.

#### Acceptance Criteria

1. WHEN the Add_On prepares to attach a device, THE Add_On SHALL execute `mount -o remount -t sysfs sysfs /sys` to remount sysfs with read-write access inside the container, and wait 0.5 seconds after a successful remount before proceeding
2. IF the sysfs remount command returns a non-zero exit code, THEN THE Add_On SHALL log a warning containing the command stderr output and attempt the attach operation regardless
3. THE Add_On SHALL execute the sysfs remount before every batch of attach operations, including those triggered by the Monitor_Service during reattachment cycles
4. THE Add_On SHALL NOT execute concurrent sysfs remount operations; if the Attach_Service and Monitor_Service both require a remount, each SHALL execute its own remount sequentially within its own service context

### Requirement 3: Device Attachment

**User Story:** As a Home Assistant user, I want the add-on to attach USB devices from my ESP32 servers, so that they appear as local USB devices on my Home Assistant host.

#### Acceptance Criteria

1. WHEN the Add_On starts and the VHCI_Module is loaded, THE Attach_Service SHALL attempt a pre-detach by running `usbip detach -r <server_ip> -b <busid>` for each configured Device_Entry (ignoring any failure), then execute `usbip attach --remote <server_ip> --busid <busid>` for each Device_Entry, using the configured busid or "1-1" if not specified
2. WHEN attaching a Device_Entry, THE Add_On SHALL use the configured server IP address and the Device_Entry's busid (defaulting to "1-1") as the remote device identifier, and the configured custom port if one is specified
3. IF an attach attempt fails (non-zero return code from `usbip attach`), THEN THE Add_On SHALL retry the attach up to the configured maximum retry count (default 3, range 0-10) with the configured delay (default 2 seconds, range 0-30) between attempts, and if all attempts are exhausted SHALL log the failure with the device name, server address, and usbip stderr output, and continue to the next Device_Entry
4. WHEN a Device_Entry is successfully attached (return code 0 from `usbip attach`), THE Add_On SHALL determine the assigned local port number by parsing the output of `usbip port` and log the device name, server address, and assigned local port number at info level
5. WHEN multiple Device_Entry items are configured, THE Add_On SHALL attach them sequentially with an inter-device delay of 2 seconds (configurable via `attach_delay` option)

### Requirement 4: Connection Monitoring

**User Story:** As a Home Assistant user, I want the add-on to detect when an ESP32 USB device disconnects and automatically reattach it, so that my devices recover from WiFi interruptions without manual intervention.

#### Acceptance Criteria

1. WHILE the Add_On is running, THE Monitor_Service SHALL check the attachment status of all configured devices at the configured monitoring interval (default 30 seconds) by running `usbip port` and matching each Device_Entry against attached devices by server IP and the Device_Entry's configured busid (default "1-1")
2. WHEN the Monitor_Service starts, THE Monitor_Service SHALL wait 15 seconds before performing the first attachment status check to allow initial attachment to complete
3. WHEN the Monitor_Service detects a device is no longer attached, THE Monitor_Service SHALL perform a sysfs remount and then attempt to reattach the device using the configured maximum retry count and retry delay
4. IF the Monitor_Service fails to reattach a device after exhausting all configured retry attempts, THEN THE Monitor_Service SHALL log the failure with the device name, server address, and number of attempts made, and continue monitoring for the device on subsequent cycles
5. WHEN the Monitor_Service successfully reattaches a device, THE Monitor_Service SHALL log the recovery event with the device name and server address at info level

### Requirement 5: Health Check

**User Story:** As a Home Assistant user, I want the add-on to verify ESP32 server reachability before attempting attachment, so that I get clear diagnostics when a server is offline.

#### Acceptance Criteria

1. WHEN the Add_On prepares to attach a Device_Entry, THE Add_On SHALL perform a TCP connection probe to the ESP32_Server on the configured port (default 3240) with a timeout of 2 seconds, and close the socket immediately upon successful connection
2. IF the Health_Check fails (connection refused, timeout, or network error), THEN THE Add_On SHALL skip the attach attempt for that Device_Entry, log the failure with the server address, port, and device name, and continue to the next device
3. WHEN the Monitor_Service prepares to reattach a lost device, THE Monitor_Service SHALL perform a Health_Check before each reattach attempt; IF the Health_Check fails, THEN THE Monitor_Service SHALL count the attempt against the configured retry limit, log the failure, and wait the configured retry delay before the next attempt

### Requirement 6: Configuration

**User Story:** As a Home Assistant user, I want to configure my ESP32 servers and monitoring parameters through the standard add-on configuration panel, so that I do not need to edit files manually.

#### Acceptance Criteria

1. WHEN the Add_On starts, THE Add_On SHALL read its configuration from the Supervisor_API by making a GET request to `/addons/self/info` with the SUPERVISOR_TOKEN environment variable for authentication
2. WHILE the Monitor_Service is running, THE Monitor_Service SHALL re-read configuration from the Supervisor_API at the beginning of each monitoring cycle so that configuration changes take effect without restarting the add-on
3. THE Add_On SHALL support configuration of the following parameters: log_level (list: debug, info, warning, error), monitor_interval (integer, range 10-300 seconds), reattach_retries (integer, range 0-10), attach_delay (integer, range 0-30 seconds), notifications_enabled (boolean), flap_warning_threshold (integer, default 3), flap_critical_threshold (integer, default 5), flap_window_seconds (integer, default 600), flap_clear_seconds (integer, default 900), and a list of Device_Entry items
4. THE Add_On SHALL support each Device_Entry containing: a `server` field (string, required, IP address of the ESP32_Server), a `name` field (string, required, friendly display name), a `port` field (integer, optional, range 1-65535, default 3240), and a `busid` field (string, optional, default "1-1")
5. THE Add_On SHALL use the following default values: log_level "info", monitor_interval 30, reattach_retries 3, attach_delay 2, notifications_enabled true, flap_warning_threshold 3, flap_critical_threshold 5, flap_window_seconds 600, flap_clear_seconds 900
6. WHERE a Device_Entry specifies a custom port, THE Add_On SHALL use that port for all operations on that device including Health_Check, Discovery, and `usbip attach` operations instead of the default port 3240
7. IF the Supervisor_API is unreachable during startup, THEN THE Add_On SHALL retry the configuration read up to 3 times with 5-second intervals before aborting startup with a non-zero exit code
8. IF the devices list is empty, THEN THE Add_On SHALL log a warning at startup and enter the monitor loop without attempting any attach operations
9. WHEN the log_level configuration changes, THE Monitor_Service SHALL apply the new log level at the beginning of the next monitoring cycle without requiring an add-on restart; other services (Attach_Service, WebUI_Service) SHALL require an add-on restart for log level changes to take effect

### Requirement 7: Lifecycle Management

**User Story:** As a Home Assistant user, I want the add-on to cleanly detach all devices on shutdown, so that kernel resources are released and no stale ports remain.

#### Acceptance Criteria

1. WHEN the Add_On receives a shutdown signal, THE Add_On SHALL run `usbip port` to list currently attached devices and execute `usbip detach -p <port>` for each attached port, with a 0.5-second delay between each detach command
2. IF the `usbip port` command returns a non-zero exit code or produces empty output during shutdown, THEN THE Add_On SHALL perform a blind detach by executing `usbip detach -p <n>` for ports 0 through 15 inclusive
3. WHEN the Add_On completes device detachment, THE Add_On SHALL log the count of successfully detached devices and the count of failed detachments
4. IF the `usbip` binary is not found on the system PATH during shutdown, THEN THE Add_On SHALL log a warning and exit the shutdown script with exit code 0 without attempting detachment

### Requirement 8: Logging

**User Story:** As a Home Assistant user, I want the add-on to produce structured logs viewable in the Supervisor logs panel, so that I can diagnose connection issues.

#### Acceptance Criteria

1. THE Add_On SHALL write all log output to stdout and stderr so that s6-overlay captures it for the Supervisor logs API
2. THE Add_On SHALL format each log line to include a timestamp in ISO 8601 format, log level name, logger name identifying the originating service or script (e.g., "load_modules", "usbip_run", "monitor", "webui", "discovery"), and the log message
3. THE Add_On SHALL log all significant events including: module loading result, sysfs remount result, attach attempts with server address and busid, attach successes with port number, attach failures with error details, device losses detected by monitor, reattach attempts and results, discovery results, flapping state changes, and shutdown detachment summary
4. WHEN a log level is configured, THE Add_On SHALL suppress all log messages below the configured level, where the levels in ascending severity order are: debug, info, warning, error
5. WHEN a device operation fails, THE Add_On SHALL include the server address, device name, and the stderr output from the failed command in the log message
6. THE Add_On SHALL support configuration of log level via the `log_level` option with valid values: debug, info, warning, error; the configured level SHALL apply to all Python services and scripts within the Add_On

### Requirement 9: Multiple Device Support

**User Story:** As a Home Assistant user, I want to connect multiple ESP32 USB/IP servers simultaneously, so that I can use several remote USB devices (e.g., Zigbee stick and Bluetooth dongle) at the same time.

#### Acceptance Criteria

1. THE Add_On SHALL support a configurable list of one or more Device_Entry items, each pointing to a different ESP32_Server, up to a maximum of 8 devices (matching the VHCI high-speed port capacity)
2. WHEN multiple Device_Entry items are configured, THE Add_On SHALL attach each device independently so that a failure on one device does not prevent attachment of others; each device gets its own Health_Check, pre-detach, and attach sequence
3. WHILE multiple devices are attached, THE Monitor_Service SHALL track each device independently by matching server IP and the Device_Entry's configured busid in the `usbip port` output, and reattach only the specific devices that are missing
4. IF more Device_Entry items are configured than available VHCI high-speed ports (8), THEN THE Add_On SHALL log an error for each device beyond the capacity limit and skip those devices during attachment
5. THE Add_On SHALL reject duplicate server IP addresses in the devices list at configuration read time and log a warning identifying the duplicate entry

### Requirement 10: Notifications

**User Story:** As a Home Assistant user, I want to receive Home Assistant persistent notifications when a device is lost or recovered, so that I am aware of connectivity issues without monitoring logs.

#### Acceptance Criteria

1. WHEN the Monitor_Service detects a device loss, THE Add_On SHALL send a persistent notification to Home Assistant via the Supervisor_API endpoint `/core/api/services/persistent_notification/create` containing the device name, server address, and a title prefixed with "USB/IP:"
2. WHEN the Monitor_Service successfully reattaches a device, THE Add_On SHALL send a persistent notification to Home Assistant via the Supervisor_API containing the device name, server address, and a title prefixed with "USB/IP:" indicating the device has recovered
3. IF the Monitor_Service fails to reattach a device after all retry attempts, THEN THE Add_On SHALL send a persistent notification via the Supervisor_API containing the device name, server address, and a title prefixed with "USB/IP:" indicating manual intervention is required
4. THE Add_On SHALL apply a cooldown period of 300 seconds between repeated notifications for the same device, tracked per device using a monotonic timer, silently discarding any notification triggered within the cooldown window
5. IF the Supervisor_API notification request fails (network error or non-2xx response), THEN THE Add_On SHALL log a warning with the device name and failure reason and continue normal operation without retrying the notification
6. WHERE notifications are disabled in the Add_On configuration (`notifications_enabled: false`), THE Add_On SHALL suppress all persistent notifications while continuing to log device loss and recovery events normally

### Requirement 11: Container Privileges

**User Story:** As a Home Assistant user, I want the add-on to declare the correct container privileges, so that kernel module loading and USB/IP operations succeed without manual system configuration.

#### Acceptance Criteria

1. THE Add_On SHALL declare the privileged capabilities NET_ADMIN, SYS_ADMIN, SYS_MODULE, and SYS_RAWIO in its config.yaml `privileged` field as a YAML list of exactly these four capabilities
2. THE Add_On SHALL declare access to the /dev/vhci device in its config.yaml `devices` field as a YAML list containing the string "/dev/vhci"
3. THE Add_On SHALL declare the vhci-hcd kernel module in its config.yaml `kernel_modules` field as a YAML list containing the string "vhci-hcd"
4. THE Add_On SHALL declare support for the aarch64 and amd64 architectures in its config.yaml `arch` field as a YAML list containing exactly these two architecture identifiers
5. THE Add_On SHALL declare `apparmor: true` in its config.yaml and provide a custom AppArmor profile file that permits modprobe, mount, and usbip binary execution
6. THE Add_On SHALL declare `hassio_api: true` and `homeassistant_api: true` in its config.yaml to enable Supervisor API access for configuration reading and Home Assistant notification delivery
7. THE Add_On SHALL declare `ingress: true` and `ingress_port: 8099` in its config.yaml to enable Home Assistant Ingress access for the WebUI_Service

### Requirement 12: Service Architecture

**User Story:** As a Home Assistant user, I want the add-on to use s6-overlay for service supervision, so that services are automatically restarted on failure and follow Home Assistant add-on conventions.

#### Acceptance Criteria

1. THE Add_On SHALL use s6-overlay for process supervision with a minimum of three long-running services: the Attach_Service (`/etc/services.d/usbip/run`) for device attachment, the Monitor_Service (`/etc/services.d/monitor/run`) for connection monitoring, and the WebUI_Service (`/etc/services.d/webui/run`) for the web interface
2. WHEN a long-running service process (Attach_Service, Monitor_Service, or WebUI_Service) exits with a non-zero exit code, THE Add_On SHALL allow s6-overlay to restart the service automatically without halting the container
3. IF a cont-init.d initialization script (e.g., `load_modules.py`) exits with a non-zero exit code, THEN THE Add_On SHALL abort container startup and prevent long-running services from starting
4. THE Add_On SHALL execute cleanup scripts (device detachment) as s6 cont-finish.d scripts (`/etc/cont-finish.d/detach_devices.py`) during container shutdown
5. THE Add_On SHALL configure service finish scripts (`/etc/services.d/*/finish`) to log the exit code of the terminated service and return exit code 0 so that s6-overlay proceeds with automatic service restart
6. ALL service scripts SHALL use the `#!/command/with-contenv python3` shebang to inherit s6 environment variables including SUPERVISOR_TOKEN

### Requirement 13: Device Discovery

**User Story:** As a Home Assistant user, I want the add-on to discover devices on my ESP32 servers, so that I can confirm servers are responsive and have devices available before attachment.

#### Acceptance Criteria

1. WHEN Discovery is triggered for a Device_Entry, THE Add_On SHALL execute `usbip list -r <server_ip>` (with `--tcp-port <port>` if a custom port is configured) and parse the output to determine whether the ESP32_Server has a device available
2. THE Add_On SHALL parse the `usbip list -r` output using a regex pattern that matches the actual output format produced by the usbip CLI when communicating with an ESP32_Server, where each device line has the format `<whitespace><busid>: <manufacturer> : <product_name>` (e.g., `1-1: Realtek Semiconductor Corp. : unknown product`); THE Add_On SHALL NOT use the pattern `^\s*([0-9][0-9.\-]+):\s*(.+)\s+\(([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\)\s*$` as it never matches the actual output
3. WHEN the Add_On starts and the VHCI_Module is loaded, THE Add_On SHALL perform Discovery on all configured Device_Entry items before attempting attachment, logging whether each ESP32_Server is responsive and has a device available
4. WHEN Discovery is triggered via the WebUI API, THE Add_On SHALL execute Discovery for the specified server and return the parsed device information including busid, manufacturer name, and product name
5. IF the `usbip list -r` command times out (10-second timeout) or returns a non-zero exit code, THEN THE Add_On SHALL record the Discovery failure in the Event_Log with the server address and error details, and report the server as offline
6. WHEN Discovery confirms a device is available on an ESP32_Server, THE Add_On SHALL extract the busid from the parsed output and confirm it matches the expected value "1-1"; IF the busid does not match "1-1", THEN THE Add_On SHALL log a warning with the unexpected busid value

### Requirement 14: Flapping Detection

**User Story:** As a Home Assistant user, I want the add-on to detect when a device is repeatedly disconnecting and reconnecting, so that I am alerted to underlying WiFi or hardware stability issues rather than receiving individual disconnect notifications.

#### Acceptance Criteria

1. WHEN the Monitor_Service successfully reattaches a device, THE Monitor_Service SHALL record the recovery event with a monotonic timestamp in the flapping tracker for that Device_Entry
2. WHILE the Monitor_Service is running, THE Monitor_Service SHALL evaluate the flapping state for each Device_Entry at the end of every monitoring cycle by counting recovery events within the configured flap_window_seconds (default 600 seconds)
3. IF the number of recovery events within the flap window reaches the flap_warning_threshold (default 3), THEN THE Monitor_Service SHALL transition the Device_Entry flapping state to "warning" level, log a flapping warning including the device name, server address, and recovery count, record a `flap_warning` event in the Event_Log, and send a persistent notification with title "USB/IP: Flapping Warning"
4. IF the number of recovery events within the flap window reaches the flap_critical_threshold (default 5), THEN THE Monitor_Service SHALL transition the Device_Entry flapping state to "critical" level, log a flapping critical message including the device name, server address, and recovery count, record a `flap_critical` event in the Event_Log, and send a persistent notification with title "USB/IP: Flapping Critical"
5. WHEN a Device_Entry has been stable (no recovery events) for flap_clear_seconds (default 900 seconds) since the last recovery event, THE Monitor_Service SHALL clear the flapping state for that Device_Entry, record a `flap_cleared` event in the Event_Log, and log the clearance at info level
6. THE Monitor_Service SHALL only emit a notification and event when the flapping level transitions upward (none→warning, none→critical, warning→critical); repeated evaluations at the same level SHALL NOT produce additional notifications or events

### Requirement 15: Event Log System

**User Story:** As a Home Assistant user, I want the add-on to maintain a structured event log of all significant operations, so that I can review device history and diagnose intermittent issues through the WebUI.

#### Acceptance Criteria

1. THE Add_On SHALL maintain the Event_Log as a JSONL file at the path `/tmp/usbip_events.jsonl`, where each line is a JSON object representing one event
2. THE Add_On SHALL record events for the following operation types: `attach_ok`, `attach_fail`, `detach_ok`, `detach_fail`, `device_lost`, `device_recovered`, `reattach_attempt`, `reattach_ok`, `reattach_fail`, `flap_warning`, `flap_critical`, `flap_cleared`, and `discover`
3. THE Add_On SHALL structure each event entry with the following fields: `ts` (timestamp in ISO 8601 UTC format), `type` (event type string from the defined list), `device` (device name string), `server` (server IP address string), and `detail` (human-readable detail text string)
4. WHEN the Event_Log exceeds 200 lines, THE Add_On SHALL truncate the file to retain only the 200 most recent events, discarding the oldest entries
5. THE Add_On SHALL write events to the Event_Log atomically by appending a complete JSON line followed by a newline character; concurrent writes from different services SHALL NOT corrupt the file (each write is a single short append operation)
6. WHEN the Add_On starts, THE Add_On SHALL NOT clear or rotate the Event_Log; existing events from a previous run SHALL be preserved until the 200-event limit causes natural rotation

### Requirement 16: WebUI Service

**User Story:** As a Home Assistant user, I want a lightweight web interface accessible from the HA side panel, so that I can view device status, trigger actions, and review logs without using the command line.

#### Acceptance Criteria

1. THE WebUI_Service SHALL run a Flask application listening on port 8099 within the container, accessible via Home Assistant Ingress at the path configured by the Supervisor
2. THE WebUI_Service SHALL NOT use flask-socketio, gevent, or any WebSocket library; live data updates SHALL be implemented using client-side polling with JavaScript `fetch()` at configurable intervals
3. THE WebUI_Service SHALL serve a dashboard page displaying: a list of attached devices with their status (attached/detached), server health for each configured Device_Entry (online/offline with latency in milliseconds), and active flapping warnings
4. THE WebUI_Service SHALL serve a devices page displaying: all configured Device_Entry items with their current attachment state (attached, detached, or error), server IP, port, and friendly name
5. THE WebUI_Service SHALL serve a logs page displaying: recent log output from the Add_On fetched from the Supervisor logs API (`/addons/self/logs`), with automatic polling for new log lines every 3 seconds
6. THE WebUI_Service SHALL serve an events page displaying: the event timeline from the Event_Log in reverse chronological order (most recent first), showing timestamp, event type, device name, server, and detail for each event
7. THE WebUI_Service SHALL provide the following API endpoints returning JSON responses: `GET /api/status` (attached devices and server health), `POST /api/attach` (attach a device by server IP), `POST /api/detach` (detach a device by port number), `GET /api/discover` (run Discovery on a specified server), `GET /api/events` (read Event_Log entries), and `GET /api/logs` (fetch recent logs from Supervisor API)
8. THE WebUI_Service SHALL rely on Home Assistant Ingress for authentication; THE WebUI_Service SHALL NOT implement its own authentication or session management
9. THE WebUI_Service SHALL handle the `X-Ingress-Path` HTTP header to correctly generate internal URLs and links when accessed through the HA Ingress proxy
10. THE WebUI_Service SHALL depend only on the Flask Python package for its web framework; no additional web framework dependencies (no flask-socketio, no gevent, no eventlet) SHALL be required

### Requirement 17: ESP32 Server Constraints

**User Story:** As a developer, I want the add-on to handle the known constraints and capabilities of the ESP32 USB/IP server component, so that the system behaves predictably and supports both current single-device and future multi-device scenarios.

#### Acceptance Criteria

1. THE Add_On SHALL support ESP32_Servers that expose one or more USB devices; WHEN Discovery reports multiple devices on a single ESP32_Server, THE Add_On SHALL allow the user to configure which busid to attach (defaulting to "1-1" if not specified in the Device_Entry)
2. THE Add_On SHALL operate under the constraint that each ESP32_Server supports only one TCP client connection at a time; THE Add_On SHALL NOT hold persistent TCP connections to an ESP32_Server beyond the duration of a single operation (Discovery, health check, or usbip attach handshake)
3. THE Add_On SHALL NOT attempt concurrent operations (Discovery and attachment) against the same ESP32_Server; if the Monitor_Service and WebUI_Service both need to communicate with the same server, the operations SHALL be serialized
4. THE Add_On SHALL handle the `usbip list -r` output format produced when the usbip CLI communicates with an ESP32_Server, where the output does NOT include VID:PID in parentheses (unlike some other USB/IP server implementations); the actual format shows device lines as `<busid>: <manufacturer> : <product>` without parenthesized identifiers
5. IF the ESP32_Server is unresponsive (the TCP connection to port 3240 cannot be established within the Health_Check timeout), THEN THE Add_On SHALL assume the ESP32 device may be rebooting or experiencing WiFi connectivity issues and SHALL retry according to the configured reattach_retries parameter
6. WHERE a Device_Entry does not specify a busid, THE Add_On SHALL default to using "1-1" as the busid for attach operations, matching the current behavior of the ESP32 USB/IP server component which always exposes a single device at busid "1-1"

### Requirement 18: Remote ESP Configuration

**User Story:** As a Home Assistant user, I want to configure each ESP32 server connection through the add-on configuration panel, so that I can manage multiple devices with different network settings.

#### Acceptance Criteria

1. THE Add_On SHALL support each Device_Entry in the configuration containing: a `server` field (string, required) specifying the IP address of the ESP32_Server, a `name` field (string, required) specifying a friendly display name for the device, a `port` field (integer, optional, default 3240) specifying the TCP port number, and a `busid` field (string, optional, default "1-1") specifying the remote device busid to attach
2. THE Add_On SHALL validate each Device_Entry at configuration read time: the `server` field SHALL be a non-empty string, the `name` field SHALL be a non-empty string, the `port` field (if present) SHALL be an integer in the range 1-65535, and the `busid` field (if present) SHALL be a non-empty string matching a valid USB busid format
3. IF a Device_Entry fails validation, THEN THE Add_On SHALL log an error identifying the invalid entry by name or index and skip that entry during attachment, continuing to process remaining valid entries
4. THE Add_On SHALL declare the Device_Entry schema in config.yaml so that the Home Assistant add-on configuration panel renders appropriate input fields for server, name, port, and busid for each device entry
5. THE Add_On SHALL support the full device list being manageable through the HA add-on configuration panel without requiring manual file editing; all Device_Entry fields SHALL be exposed in the config.yaml schema definition

