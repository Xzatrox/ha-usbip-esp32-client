# Implementation Plan: ha-usbip-esp32-client

## Overview

This plan implements the Home Assistant USB/IP ESP32 Client add-on as a Python-based s6-overlay service architecture. Tasks are ordered to build foundational shared modules first, then services, then the WebUI, with property and unit tests interleaved close to their implementations.

## Tasks

- [x] 1. Set up project structure, configuration, and shared utilities
  - [x] 1.1 Create add-on manifest and container configuration files
    - Create `config.yaml` with all declared options, schema, privileged capabilities (NET_ADMIN, SYS_ADMIN, SYS_MODULE, SYS_RAWIO), devices (/dev/vhci), kernel_modules (vhci-hcd), arch (aarch64, amd64), apparmor: true, hassio_api: true, homeassistant_api: true, ingress: true, ingress_port: 8099
    - Create `Dockerfile` with Alpine base, apk packages (python3, py3-pip, py3-flask, linux-tools-usbip, kmod), COPY rootfs and usbip_addon
    - Create `apparmor.txt` permitting modprobe, mount, and usbip binary execution
    - Create `usbip_addon/__init__.py` package file
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_

  - [x] 1.2 Implement configuration module (`usbip_addon/config.py`)
    - Implement `AddonConfig` class reading from Supervisor API at `http://supervisor/addons/self/info`
    - Use `SUPERVISOR_TOKEN` env var for auth header
    - Implement retry logic (3 retries, 5s delay) for startup API reads
    - Parse and validate `DeviceEntry` dataclass (server, name, port=3240, busid="1-1")
    - Implement `DeviceEntry.key` property returning `server:port:busid`
    - Implement `DeviceEntry.validate()` returning error message or None
    - Expose all config properties: monitor_interval, reattach_retries, attach_delay, log_level, notifications_enabled, flap thresholds
    - Reject duplicate server IPs, log warnings for duplicates
    - Reject device lists exceeding 8 entries
    - Apply defaults for missing optional fields
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 9.1, 9.5, 18.1, 18.2, 18.3, 18.4_

  - [x] 1.3 Write property tests for configuration validation (Property 4)
    - **Property 4: Configuration and DeviceEntry validation**
    - Use Hypothesis to generate config values within and outside valid ranges
    - Verify acceptance of valid ranges and rejection of invalid values
    - Verify max 8 device entries enforced
    - **Validates: Requirements 6.3, 6.4, 9.1, 18.2**

  - [x] 1.4 Write property test for duplicate server IP detection (Property 5)
    - **Property 5: Duplicate server IP detection**
    - Use Hypothesis to generate device lists with and without duplicate IPs
    - Verify first occurrence preserved, duplicates rejected
    - **Validates: Requirements 9.5**

  - [x] 1.5 Implement logging configuration module (`usbip_addon/logging_config.py`)
    - Configure Python logging with format: `<ISO-8601-timestamp> <LEVEL> <logger_name> <message>`
    - Support configurable log levels (debug, info, warning, error)
    - Output to stdout/stderr for s6-overlay capture
    - Provide logger factory for each service/script name
    - _Requirements: 8.1, 8.2, 8.4, 8.6_

  - [x] 1.6 Write property test for log format structure (Property 15)
    - **Property 15: Log format structure**
    - Use Hypothesis to generate log messages at all levels
    - Verify output matches `<ISO-8601-timestamp> <LEVEL> <logger_name> <message>` pattern
    - **Validates: Requirements 8.2**

  - [x] 1.7 Implement server lock module (`usbip_addon/server_lock.py`)
    - Implement `ServerLockManager` with per-server threading locks
    - Use meta-lock for thread-safe lock creation
    - Provide context manager interface (`lock(server)`)
    - _Requirements: 17.2, 17.3_

- [x] 2. Implement USB/IP client and health modules
  - [x] 2.1 Implement USB/IP client module (`usbip_addon/usbip_client.py`)
    - Implement `UsbipClient` class wrapping CLI commands via subprocess
    - Implement `attach(server, busid, port)` → `AttachResult(success, port, stderr)`
    - Implement `detach(port)` → bool
    - Implement `detach_remote(server, busid)` → bool (pre-detach)
    - Implement `list_ports()` → `List[PortEntry]` by parsing `usbip port` output
    - Implement `remount_sysfs()` → bool with 0.5s sleep after success
    - Handle custom port via `--tcp-port` flag when specified
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 7.1, 7.2_

  - [x] 2.2 Write property test for command parameter mapping (Property 1)
    - **Property 1: DeviceEntry to usbip command parameter mapping**
    - Use Hypothesis to generate valid DeviceEntry instances
    - Verify generated commands include correct --remote, --busid, --tcp-port flags
    - Verify pre-detach uses -r and -b flags correctly
    - **Validates: Requirements 3.1, 3.2**

  - [x] 2.3 Write property test for usbip port output parsing (Property 2)
    - **Property 2: usbip port output parsing**
    - Use Hypothesis to generate synthetic usbip port output with varying port/server/busid values
    - Verify parser extracts all port numbers and associated server/busid pairs
    - **Validates: Requirements 3.4, 4.1**

  - [x] 2.4 Implement health check module (`usbip_addon/health.py`)
    - Implement `HealthChecker` with TCP socket probe
    - Connect to server:port with 2-second timeout
    - Return `HealthResult(reachable, latency_ms, error)`
    - Close socket immediately on success
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 2.5 Implement discovery module (`usbip_addon/discovery.py`)
    - Implement `DeviceDiscovery` class
    - Execute `usbip list -r <server>` with optional `--tcp-port`
    - Parse output using regex matching ESP32 format: `<whitespace><busid>: <manufacturer> : <product>`
    - Return `DiscoveryResult(success, devices, error)` with `DiscoveredDevice(busid, manufacturer, product)`
    - Handle 10-second subprocess timeout
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 17.4_

  - [x] 2.6 Write property test for discovery output parsing (Property 6)
    - **Property 6: Discovery output parsing (ESP32 format)**
    - Use Hypothesis to generate valid and invalid usbip list output lines
    - Verify correct busid/manufacturer/product extraction for valid lines
    - Verify no false-positive device entries for invalid lines
    - **Validates: Requirements 13.1, 13.2**

- [x] 3. Implement event log, notifications, and flapping detection
  - [x] 3.1 Implement event log module (`usbip_addon/event_log.py`)
    - Implement `EventLog` class with append-only JSONL at `/tmp/usbip_events.jsonl`
    - Implement `record(event_type, device, server, detail)` with ISO 8601 UTC timestamp
    - Validate event_type against VALID_TYPES set
    - Implement `read_events(limit)` returning events in reverse chronological order
    - Implement `_truncate_if_needed()` keeping most recent 200 events
    - Handle concurrent writes safely via short append operations
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6_

  - [x] 3.2 Write property test for event serialization (Property 11)
    - **Property 11: Event entry serialization**
    - Use Hypothesis to generate valid events with various types, device names, servers, details
    - Verify serialized JSON contains all required fields and is parseable
    - **Validates: Requirements 15.3**

  - [x] 3.3 Write property test for event log rotation (Property 12)
    - **Property 12: Event log rotation preserves most recent events**
    - Use Hypothesis to generate sequences of N > 200 events
    - Verify file contains exactly 200 events (the most recent ones in order)
    - **Validates: Requirements 15.4**

  - [x] 3.4 Implement notifications module (`usbip_addon/notifications.py`)
    - Implement `NotificationManager` with Supervisor API integration
    - Implement `notify_device_lost`, `notify_device_recovered`, `notify_reattach_failed`, `notify_flapping`
    - Apply 300-second per-device cooldown using monotonic timer
    - Respect `notifications_enabled` config flag
    - Handle API failures gracefully (log warning, no retry)
    - Title prefix "USB/IP:" on all notifications
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x] 3.5 Write property test for notification cooldown (Property 10)
    - **Property 10: Notification cooldown enforcement**
    - Use Hypothesis to generate sequences of notification triggers with monotonic timestamps
    - Verify only first and those 300+ seconds after last sent are delivered
    - **Validates: Requirements 10.4**

  - [x] 3.6 Implement flapping detection module (`usbip_addon/flapping.py`)
    - Implement `FlappingDetector` with per-device `DeviceFlappingTracker`
    - Implement `record_recovery(device_key)` recording monotonic timestamps
    - Implement `evaluate(device_key)` counting events in window, returning state transition
    - Implement `check_clear(device_key)` checking stability period elapsed
    - State transitions: NONE→WARNING→CRITICAL, only emit on upward transition
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_

  - [x] 3.7 Write property test for flapping window counting (Property 7)
    - **Property 7: Flapping window counting**
    - Use Hypothesis to generate sets of timestamps and window durations
    - Verify exactly those timestamps within window are counted
    - Verify correct level determination from count
    - **Validates: Requirements 14.2, 14.3, 14.4**

  - [x] 3.8 Write property test for flapping state transitions (Property 8)
    - **Property 8: Flapping state transitions emit notifications only on escalation**
    - Use Hypothesis to generate sequences of evaluations
    - Verify notifications only on upward transitions
    - **Validates: Requirements 14.6**

  - [x] 3.9 Write property test for flapping clearance (Property 9)
    - **Property 9: Flapping clearance after stability period**
    - Use Hypothesis to generate devices with non-NONE states and elapsed times
    - Verify clearance when elapsed >= flap_clear_seconds
    - **Validates: Requirements 14.5**

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement s6-overlay services (attach, monitor, shutdown)
  - [x] 5.1 Implement cont-init.d script (`rootfs/etc/cont-init.d/load_modules.py`)
    - Use `#!/command/with-contenv python3` shebang
    - Execute `modprobe vhci-hcd`
    - Verify module loaded via `/sys/module/vhci_hcd` existence or `lsmod`
    - Exit non-zero on modprobe failure (prevents services from starting)
    - Log warning if modprobe succeeds but verification fails
    - Skip loading if already present, log informational message
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 12.3_

  - [x] 5.2 Implement attach service (`rootfs/etc/services.d/usbip/run`)
    - Use `#!/command/with-contenv python3` shebang
    - Read config from Supervisor API (with retries)
    - Perform discovery on all configured devices
    - For each device: health check → pre-detach (ignore failure) → sysfs remount → attach with retries
    - Apply inter-device attach_delay between devices
    - Record events (attach_ok, attach_fail) in Event_Log
    - Use ServerLockManager for per-server serialization
    - Sleep forever after attachment completes (keep alive for s6)
    - Handle empty device list gracefully
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 5.1, 5.2, 6.1, 6.7, 6.8, 9.2, 12.1, 12.6, 13.3_

  - [x] 5.3 Write property test for missing device detection (Property 3)
    - **Property 3: Missing device detection**
    - Use Hypothesis to generate configured device sets and usbip port output
    - Verify exactly those configured devices not in port output are identified as missing
    - **Validates: Requirements 4.3, 9.3**

  - [x] 5.4 Write property test for independent device failure isolation (Property 13)
    - **Property 13: Independent device failure isolation**
    - Use Hypothesis to generate device sets with various failure subsets
    - Verify remaining devices still attempted independently
    - **Validates: Requirements 9.2**

  - [x] 5.5 Implement monitor service (`rootfs/etc/services.d/monitor/run`)
    - Use `#!/command/with-contenv python3` shebang
    - Wait 15 seconds initial delay
    - Loop every monitor_interval seconds:
      - Re-read config from Supervisor API
      - Apply log_level changes dynamically
      - Run `usbip port` and detect missing devices by matching server + busid
      - For missing devices: health check → sysfs remount → reattach with retries
      - Record events (device_lost, reattach_attempt, reattach_ok, reattach_fail)
      - On successful reattach: record recovery in flapping tracker, send notification
      - On failed reattach (all retries exhausted): send failure notification
      - Evaluate flapping state, send flap notifications on upward transitions
      - Check flap clearance for stable devices
    - Use ServerLockManager for per-server serialization
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 5.3, 6.2, 6.9, 8.3, 8.5, 9.3, 10.1, 10.2, 10.3, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_

  - [x] 5.6 Implement cont-finish.d script (`rootfs/etc/cont-finish.d/detach_devices.py`)
    - Use `#!/command/with-contenv python3` shebang
    - Run `usbip port` to list attached devices
    - Detach each port with 0.5s delay between commands
    - If port listing fails: blind detach ports 0-15
    - If `usbip` binary not found: log warning, exit 0
    - Log summary of detached/failed counts
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 12.4_

  - [x] 5.7 Create service finish scripts (`rootfs/etc/services.d/*/finish`)
    - Create finish scripts for usbip, monitor, and webui services
    - Each logs exit code of terminated service
    - Each returns exit code 0 for s6-overlay automatic restart
    - _Requirements: 12.2, 12.5_

- [x] 6. Implement WebUI service
  - [x] 6.1 Implement Flask application core (`usbip_addon/webui/app.py`)
    - Create Flask app with `X-Ingress-Path` handling via `@app.before_request`
    - Store ingress path in `flask.g` for template URL generation
    - Implement `GET /api/status` returning device status and server health
    - Implement `POST /api/attach` triggering attach by server IP (with server lock)
    - Implement `POST /api/detach` triggering detach by port number
    - Implement `GET /api/discover` running discovery for specified server (with server lock)
    - Implement `GET /api/events` reading Event_Log entries
    - Implement `GET /api/logs` fetching from Supervisor logs API
    - No authentication (relies on HA Ingress)
    - _Requirements: 16.1, 16.2, 16.7, 16.8, 16.9, 16.10_

  - [x] 6.2 Write property test for X-Ingress-Path URL generation (Property 14)
    - **Property 14: X-Ingress-Path URL generation**
    - Use Hypothesis to generate various ingress path header values
    - Verify all generated URLs are prefixed with ingress path
    - **Validates: Requirements 16.9**

  - [x] 6.3 Implement WebUI templates and static assets
    - Create `base.html` template with navigation, ingress path in URLs
    - Create `dashboard.html` showing device status, health, flapping state
    - Create `devices.html` showing device list with attach/detach actions
    - Create `logs.html` with auto-polling log viewer (3s interval)
    - Create `events.html` showing event timeline reverse chronological
    - Create `static/style.css` for styling
    - Create `static/polling.js` for client-side fetch polling
    - _Requirements: 16.3, 16.4, 16.5, 16.6_

  - [x] 6.4 Implement WebUI service entry point (`rootfs/etc/services.d/webui/run`)
    - Use `#!/command/with-contenv python3` shebang
    - Start Flask app on port 8099
    - _Requirements: 12.1, 12.6, 16.1_

- [x] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Integration wiring and final validation
  - [x] 8.1 Create test infrastructure (`tests/conftest.py` and fixtures)
    - Set up pytest with Hypothesis configuration (min 100 examples)
    - Create shared fixtures for mocked subprocess, Supervisor API responses
    - Create test directory structure (property/, unit/, integration/)
    - Add `requirements-test.txt` with pytest, hypothesis, flask test client deps
    - _Requirements: All (testing infrastructure)_

  - [x] 8.2 Write unit tests for s6-overlay scripts
    - Test load_modules: success, already loaded, failure, verify-after-load failure
    - Test attach service: normal flow, empty device list, API retry
    - Test monitor service: initial delay, detection, reattach flow
    - Test shutdown: graceful detach, blind detach, missing binary
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 3.1, 3.3, 4.1, 4.2, 4.3, 7.1, 7.2, 7.3, 7.4_

  - [x] 8.3 Write unit tests for WebUI pages and API
    - Test each page returns 200 with expected elements
    - Test each API endpoint returns correct JSON structure
    - Test ingress path prefix in rendered templates
    - _Requirements: 16.3, 16.4, 16.5, 16.6, 16.7, 16.9_

  - [x] 8.4 Wire all components together and verify end-to-end flow
    - Ensure all s6 scripts import from `usbip_addon` package correctly
    - Verify ServerLockManager is shared singleton across services
    - Verify Event_Log is written by attach, monitor, and webui services
    - Ensure all service scripts have correct shebangs and are executable
    - Create `build.yaml` if needed for HA add-on repository structure
    - _Requirements: 12.1, 12.6, 15.5, 17.3_

- [x] 9. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- All code is Python targeting Alpine Linux container with s6-overlay
- The `usbip` CLI tool is the interface to the kernel USB/IP subsystem — no direct kernel API usage
- Hypothesis library is used for property-based testing with minimum 100 examples per property

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.5", "1.7"] },
    { "id": 1, "tasks": ["1.2", "1.6"] },
    { "id": 2, "tasks": ["1.3", "1.4", "2.1", "2.4", "2.5", "3.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "2.6", "3.2", "3.3", "3.4", "3.6"] },
    { "id": 4, "tasks": ["3.5", "3.7", "3.8", "3.9", "5.1"] },
    { "id": 5, "tasks": ["5.2", "5.5", "5.6", "5.7"] },
    { "id": 6, "tasks": ["5.3", "5.4", "6.1", "6.4"] },
    { "id": 7, "tasks": ["6.2", "6.3"] },
    { "id": 8, "tasks": ["8.1"] },
    { "id": 9, "tasks": ["8.2", "8.3", "8.4"] }
  ]
}
```
