# Garage Door Integration — Konnected GDO blaQ

## Overview

The garage door opener is integrated via a **Konnected GDO blaQ** (model GDOv2-Q), a Wi-Fi device that retrofits a Chamberlain/LiftMaster Security+ garage opener with direct local serial control. The device runs ESPHome firmware and exposes a local REST API and Server-Sent Events stream. No cloud dependency, no polling-required state — push updates are native.

**Device docs:** https://konnected.readme.io/reference/gdo-blaq-introduction

---

## Integration Approach

The Blaq is a Wi-Fi device, not Zigbee or Z-Wave, so it sits outside the normal Z2M/ZWaveJS device model. Integration options considered:

| Option | Tradeoff |
|--------|----------|
| **HA native ESPHome integration** | Zero effort, but gates all garage control and state on HA uptime — unacceptable for a safety-relevant actuator |
| **Node-RED bridge (selected)** | NR owns the device relationship; HA gets state and control via MQTT Discovery — consistent with Highland architecture |

**Selected:** Node-RED bridge. NR subscribes to the Blaq's SSE stream, translates state to `highland/state/garage/` retained topics, and translates `highland/command/garage/` commands to Blaq REST calls. HA never speaks directly to the device.

---

## API Surface

All endpoints are on the device's local IP. No authentication required on the local network (ESPHome default).

Base URL: `http://{device_ip}`

### Cover (Garage Door)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/cover/garage_door` | Current door state |
| POST | `/cover/garage_door/open` | Open command |
| POST | `/cover/garage_door/close` | Close command (triggers pre-close warning automatically) |
| POST | `/cover/garage_door/stop` | Stop mid-travel |
| POST | `/cover/garage_door/toggle` | Toggle open/closed |
| POST | `/cover/garage_door/set` | Set position (0–100%) |

**State fields:**
- `state`: `OPEN` | `CLOSED`
- `current_operation`: `IDLE` | `OPENING` | `CLOSING`
- `value`: `1` (open) | `0` (closed)
- `position`: 0–100 (percentage — confirm exact field name from SSE stream at implementation time)

**Safety note:** The device handles the pre-close audible warning automatically when a close command is issued while the door is open. No separate warning trigger is needed from Node-RED.

### Light

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/light/garage_light` | Current light state |
| POST | `/light/garage_light/turn_on` | Turn on |
| POST | `/light/garage_light/turn_off` | Turn off |
| POST | `/light/garage_light/toggle` | Toggle |

### Remote Control Lock

The "lock" represents the Security+ remote control lockout feature — disables all physical remotes and keypads. This is not a door lock.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/lock/lock` | Current lock state |
| POST | `/lock/lock/lock` | Enable lockout (disable remotes) |
| POST | `/lock/lock/unlock` | Disable lockout (enable remotes) |

### Binary Sensors (Read-Only)

| Endpoint | Entity | Notes |
|----------|--------|-------|
| `/binary_sensor/motion` | Motion | Present only if opener has a compatible motion-sensing wall button |
| `/binary_sensor/obstruction` | Obstruction | Safety beam interruption |
| `/binary_sensor/motor` | Motor | Whether the drive motor is actively running |
| `/binary_sensor/synced` | Protocol Sync | Security+ protocol handshake status |
| `/binary_sensor/wall_button` | Wall Button | Momentary press state of the wall button |

### Sensors (Read-Only)

| Endpoint | Entity | Notes |
|----------|--------|-------|
| `/sensor/garage_openings` | Opening Cycles | Cumulative lifetime open/close count |
| `/sensor/wifi_signal_rssi` | Wi-Fi RSSI | Signal strength dBm |
| `/sensor/wifi_signal__` | Wi-Fi Signal % | Signal strength percentage |
| `/sensor/uptime` | Uptime | Seconds since last boot |

### Configuration (Operational)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET/POST | `/select/security__protocol` | Security+ protocol version detection/override |
| GET/POST | `/switch/learn` | "Learn mode" on the opener (for pairing remotes) |

### Utility Buttons

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/button/pre_close_warning/press` | Manually trigger audible pre-close warning |
| POST | `/button/play_sound/press` | Play configured warning sound |
| POST | `/button/restart/press` | Reboot the device |
| POST | `/button/factory_reset/press` | Factory reset (destructive — do not automate) |

---

## Push Updates — Server-Sent Events

**Endpoint:** `GET http://{device_ip}/events`

Long-lived SSE stream. Preferred over polling for all state tracking. The device pushes a `state` event whenever any entity changes. Payload is identical to the corresponding GET endpoint response.

**Example events:**

```json
{"id":"cover-garage_door","value":1,"state":"OPEN","current_operation":"IDLE"}
{"id":"cover-garage_door","value":0,"state":"CLOSED","current_operation":"IDLE"}
{"id":"cover-garage_door","value":0,"state":"CLOSED","current_operation":"CLOSING"}
{"id":"light-garage_light","state":"ON","brightness":255}
{"id":"binary_sensor-obstruction","state":"ON"}
{"id":"binary_sensor-motion","state":"ON"}
{"id":"sensor-garage_openings","value":1247}
```

The `id` field maps to entity IDs as `{component}-{entity_name}`.

**Reconnect:** The device reboots occasionally (firmware updates, power events). The SSE consumer must reconnect with exponential backoff. Connection drops should not produce stale retained state — the bridge should mark state unknown or re-poll on reconnect.

---

## Node-RED Bridge Architecture

### Data Flow

```
Blaq SSE /events
    │
    ▼
[SSE Consumer node]
    │
    ▼
[Parse + Route]
    │
    ├──► highland/state/garage/{entity}   (retained — HA reads these)
    │
    └──► highland/event/garage/{event}    (not retained — point-in-time)


highland/command/garage/{entity}         (HA writes these via MQTT cover/light/lock)
    │
    ▼
[Command Router]
    │
    ▼
[HTTP POST → Blaq REST API]
    │
    ▼
Blaq executes → SSE state update → loop back above


ZEN37 button press (Z-Wave → ZWaveJS UI → MQTT)
    │
    ▼
[Smart Reversing Logic]          (see §Smart Reversing below)
    │
    ▼
[HTTP POST → Blaq REST API]
```

### Startup Sequence

On flow start/deploy:
1. Poll all GET endpoints (door, light, lock, obstruction, motion, motor, synced, openings) to initialize retained state
2. Establish SSE connection to `/events`
3. From this point, all state updates come from SSE — polling is a one-time bootstrap only

This avoids the window where Node-RED is running but `highland/state/garage/` topics are stale or absent.

### SSE Consumer in Node-RED

Node-RED has no native SSE consumer node. Options:

- **`node-red-contrib-eventsource`** — dedicated SSE node, recommended
- Function node using Node.js `http`/`https` module directly — viable but requires manual reconnect logic

Whichever approach is used, reconnect-with-backoff is mandatory. The bridge should publish a health indicator (`highland/status/garage_bridge/heartbeat`) to allow monitoring of SSE connection liveness.

### Command Routing

HA's MQTT cover, light, and lock entities publish commands via `command_template` in their discovery configs, producing the standard Highland JSON command envelope. NR subscribes to command topics and issues the corresponding HTTP POST:

| Command topic | Action field | Blaq endpoint |
|---------------|-------------|---------------|
| `highland/command/garage/door` | `open` | `/cover/garage_door/open` |
| `highland/command/garage/door` | `close` | `/cover/garage_door/close` |
| `highland/command/garage/door` | `stop` | `/cover/garage_door/stop` |
| `highland/command/garage/light` | `turn_on` | `/light/garage_light/turn_on` |
| `highland/command/garage/light` | `turn_off` | `/light/garage_light/turn_off` |
| `highland/command/garage/remote_lock` | `lock` | `/lock/lock/lock` |
| `highland/command/garage/remote_lock` | `unlock` | `/lock/lock/unlock` |
| `highland/command/garage/learn` | `turn_on` | `/switch/learn/turn_on` |
| `highland/command/garage/learn` | `turn_off` | `/switch/learn/turn_off` |

HA UI commands are explicit directional commands — they bypass the Smart Reversing logic entirely.

---

## Smart Reversing

### Problem

The Security+ protocol is fundamentally a toggle system. If the door is stopped mid-travel and a toggle command is issued, the opener would continue in the same direction it was already travelling. This is unintuitive when using a physical toggle button — the user's intent when stopping a door mid-travel and pressing again is almost always to reverse direction.

### Solution

The Garage Bridge flow tracks the last direction of travel in flow context. When a physical toggle command arrives via the ZEN37, the bridge uses current door state and last known direction to determine the correct directional REST call, rather than issuing a raw toggle.

### State Machine

**Tracked variable:** `last_direction` — `"OPENING"` | `"CLOSING"` | `"UNKNOWN"`

**Update rule:** Set to `"OPENING"` or `"CLOSING"` on every SSE event where `current_operation` transitions to that value. Updated continuously as the door travels, including during reversals — no special reset needed.

**Default / reset value:** `"UNKNOWN"` — set on flow start/deploy before the first SSE update arrives.

**Decision logic on ZEN37 bay 1 large button press:**

| Door state | `current_operation` | `last_direction` | Action |
|------------|-------------------|-----------------|--------|
| `CLOSED` | `IDLE` | any | `open` |
| `OPEN` | `IDLE` | any | `close` |
| any | `OPENING` or `CLOSING` | any | `stop` |
| mid-travel | `IDLE` | `"OPENING"` | `close` |
| mid-travel | `IDLE` | `"CLOSING"` | `open` |
| mid-travel | `IDLE` | `"UNKNOWN"` | `close` ← fail-safe |

**Mid-travel definition:** position is strictly between 0 and 100, and `current_operation` is `IDLE`. Fully open (position 100) and fully closed (position 0) are not mid-travel regardless of `current_operation`.

**Fail-safe rationale:** When `last_direction` is unknown (e.g., flow just restarted and door is already mid-travel), the bridge defaults to closing. An open or partially-open garage door is the higher-risk state; closing is the safer assumption.

**Reversal is transparent to the state machine.** After a reversal command, the door simply starts moving in the new direction and `last_direction` updates naturally from the SSE stream. The logic applies identically on each subsequent toggle — the door can be stopped and reversed any number of times.

---

## ZEN37 Wall Remote

### Overview

The **Zooz ZEN37** is a 4-button Z-Wave wall remote with 2 large buttons and 2 small buttons. It is used as the primary physical toggle control for the garage and is positioned to grow with the second bay.

### Button Mapping

| Button | Action | Function |
|--------|--------|----------|
| Large button 1 | Press | Bay 1 door — Smart Reversing toggle |
| Small button 1 | Press | Bay 1 light — toggle |
| Large button 2 | Press | Bay 2 door — reserved (future second opener) |
| Small button 2 | Press | Bay 2 light — reserved (future second opener) |

### Command Path

ZEN37 events arrive via Z-Wave JS UI → MQTT. The Garage Bridge flow subscribes to the relevant Z-Wave scene/button topics for large button 1 and small button 1. Bay 2 button subscriptions are deferred until the second opener is installed.

**Important:** ZEN37 toggles route through Smart Reversing logic. HA UI commands on `highland/command/garage/door` do not — HA's cover entity provides explicit directional control at all times and has no need for toggle interpretation.

---

## MQTT Topics

See **MQTT_TOPICS.md §Garage Door** for the canonical topic registry, payloads, and retention rules.

Summary:

| Topic | Retained | Purpose |
|-------|----------|---------|
| `highland/state/garage/door` | Yes | Door cover state |
| `highland/state/garage/light` | Yes | Garage light state |
| `highland/state/garage/remote_lock` | Yes | Remote lockout state |
| `highland/state/garage/obstruction` | Yes | Safety beam state |
| `highland/state/garage/motion` | Yes | Motion sensor state (conditional) |
| `highland/state/garage/motor` | Yes | Motor running state |
| `highland/state/garage/synced` | Yes | Protocol sync state |
| `highland/state/garage/openings` | Yes | Cumulative cycle count |
| `highland/state/garage/learn` | Yes | Learn mode state |
| `highland/event/garage/door_opened` | No | Door reached open state |
| `highland/event/garage/door_closed` | No | Door reached closed state |
| `highland/event/garage/obstruction_detected` | No | Safety beam interrupted |
| `highland/event/garage/obstruction_cleared` | No | Safety beam restored |
| `highland/event/garage/motion_detected` | No | Motion sensor triggered |
| `highland/command/garage/door` | No | Door control (from HA) |
| `highland/command/garage/light` | No | Light control (from HA) |
| `highland/command/garage/remote_lock` | No | Remote lockout control (from HA) |
| `highland/command/garage/learn` | No | Learn mode control (from HA) |

---

## Home Assistant Entity Inventory

All entities registered via MQTT Discovery by the Garage Bridge flow on startup.

| Entity | Type | Discovery Component | Source topic |
|--------|------|---------------------|-------------|
| Garage Door | cover | `cover` | `highland/state/garage/door` |
| Garage Light | light | `light` | `highland/state/garage/light` |
| Remote Control Lock | lock | `lock` | `highland/state/garage/remote_lock` |
| Obstruction | binary_sensor | `binary_sensor` | `highland/state/garage/obstruction` |
| Motion | binary_sensor | `binary_sensor` | `highland/state/garage/motion` |
| Motor Running | binary_sensor | `binary_sensor` | `highland/state/garage/motor` |
| Protocol Synced | binary_sensor | `binary_sensor` | `highland/state/garage/synced` |
| Opening Cycles | sensor | `sensor` | `highland/state/garage/openings` |
| Learn Mode | switch | `switch` | `highland/state/garage/learn` |

All entities grouped under a single HA device: **"Garage Door Opener"** (`identifiers: ["highland_garage"]`).

**Motion entity note:** The motion binary_sensor is registered unconditionally. Whether a compatible wall button is present determines if the sensor ever fires. If no wall button is installed, the entity will remain in an unknown/unavailable state — acceptable.

---

## Implementation Notes

- **No authentication** on the Blaq's local API (ESPHome default). Device should be on an IoT VLAN (future) or at minimum assigned a static IP via DHCP reservation.
- **Device IP** stored in `global.config.secrets` under `garage.blaq_ip`, following the standard secrets pattern.
- **`wall_button` binary sensor** is a momentary press — it will bounce ON briefly and return to OFF. Expose as a sensor for completeness but do not build automations that depend on catching its edge reliably.
- **`set` position command** (0–100%) is available on the cover but is not wired to an HA entity in the initial implementation — standard open/close/stop covers the feature parity requirement.
- **Pre-close warning** is device-managed. The Blaq will play the audible warning automatically before closing. Do not issue a separate warning before calling the close endpoint.
- **Factory reset button** is intentionally excluded from the command routing table and from HA entities.
- **Door position field** — the Blaq reports travel position (0–100) in addition to `state` and `current_operation`. Exact SSE field name to be confirmed at implementation time. Mid-travel detection in Smart Reversing uses this value: any position strictly between 0 and 100 where `current_operation` is `IDLE` is considered mid-travel stopped.

---

## Open Items

| Item | Notes |
|------|-------|
| Static IP / DHCP reservation | Assign before go-live |
| SSE consumer node selection | `node-red-contrib-eventsource` vs. custom function — decide at implementation |
| Motion entity availability | Verify if existing opener has a compatible motion-sensing wall button |
| `wall_button` entity | Expose in HA for parity; monitor for useful automation applications |
| Door position SSE field name | Confirm exact field name from live SSE stream at implementation time |
| ZEN37 Z-Wave scene topics | Confirm button 1 and button 2 scene/event topic paths from Z-Wave JS UI at implementation time |

---

*Last Updated: 2026-03-11*
