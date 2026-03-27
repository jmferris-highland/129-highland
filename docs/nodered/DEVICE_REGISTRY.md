# Node-RED — Device Registry & Command Dispatcher

## Device Registry

### Purpose

Centralized knowledge about devices — protocol, topic structure, capabilities, and metadata. Populated automatically from Home Assistant's internal registries on startup and refresh. HA is the authoritative source for device identity, area assignment, and friendly names. Battery chemistry is the only data that must be manually maintained, stored in the `models` block keyed by model ID.

### Storage

Stored as `/home/nodered/config/device_registry.json`, written by the `Utility: Device Registry` flow on every refresh. Loaded into global context at startup by Config Loader as an initial seed; the Device Registry flow then overwrites global context with a fresh HA pull shortly after.

**Access:** `global.get('config.deviceRegistry')`

> **Volume mount:** `/home/nodered/config` is mounted into the Node-RED container at `/config` **without** the `:ro` flag — the Device Registry flow writes back to this directory. The directory requires `chmod 775` on the host to allow container writes.

### Registry Structure

```json
{
  "devices": {
    "office_desk_presence": {
      "ha_device_id": "b1f32de2ab0e6d26c7b3654b4bebb0f2",
      "friendly_name": "office_desk_presence",
      "manufacturer": "Aqara",
      "model": "Presence sensor FP300",
      "model_id": "PS-S04D",
      "area_id": "office",
      "floor_id": "second_floor",
      "protocol": "zigbee",
      "battery_pct_entity": "sensor.office_desk_presence_battery"
    },
    "kitchen_diswasher_vibration": {
      "ha_device_id": "fa91d003c7934fce2c4e11b5a9b6b3f3",
      "friendly_name": "kitchen_diswasher_vibration",
      "manufacturer": "Aqara",
      "model": "Vibration sensor",
      "model_id": "DJT11LM",
      "area_id": "kitchen",
      "floor_id": "first_floor",
      "protocol": "zigbee",
      "battery_pct_entity": "sensor.kitchen_diswasher_vibration_battery"
    }
  },
  "areas": {
    "office": { "name": "Office", "floor_id": "second_floor" },
    "kitchen": { "name": "Kitchen", "floor_id": "first_floor" }
  },
  "models": {
    "PS-S04D": { "battery": { "type": "CR2450", "quantity": 2 } },
    "DJT11LM": { "battery": { "type": "CR2032", "quantity": 1 } }
  },
  "meta": {
    "last_refreshed": "2026-03-27T03:15:00.000Z",
    "device_count": 2,
    "source": "ha_registry"
  }
}
```

**Device fields:**

| Field | Source | Purpose |
|-------|--------|---------|
| `ha_device_id` | HA device registry | Internal HA identifier for cross-referencing |
| `friendly_name` | HA (`name_by_user` ?? `name`) | User-facing name for notifications |
| `manufacturer` | HA device registry | Device manufacturer |
| `model` | HA device registry | Human-readable model name |
| `model_id` | HA device registry | Machine-readable model ID — key into `models` block |
| `area_id` | HA device registry | Area assignment (matches `areas` map key) |
| `floor_id` | Derived from area | Floor assignment |
| `protocol` | Derived from HA identifiers | `zigbee` or `zwave` |
| `battery_pct_entity` | HA entity registry | Entity ID for battery percentage sensor; `null` if mains-powered |

**Battery lookup at runtime:**
```javascript
const device = global.get('config.deviceRegistry')?.devices?.['office_desk_presence'];
const battery = global.get('config.deviceRegistry')?.models?.[device.model_id]?.battery;
// { type: "CR2450", quantity: 2 }
```

Battery chemistry is never stored on the device entry — always looked up via `model_id` from the `models` block. This ensures a single point of truth: fixing a model spec fixes it for all devices of that model.

### Population

The `Utility: Device Registry` flow builds the registry automatically by calling three HA WebSocket APIs in sequence:

1. `config/area_registry/list` → builds the `areas` map
2. `config/device_registry/list` → provides device identity, manufacturer, model, area assignment
3. `config/entity_registry/list` → used to find battery percentage entity per device

**Device filtering:** Only real physical devices are included. Infrastructure is excluded by requiring both `entry_type === null` and `via_device_id !== null`. Protocol is inferred from HA identifiers: `mqtt` platform → `zigbee`, `zwave_js` platform → `zwave`. Devices on other platforms (Cast, mobile app, WebOS, etc.) are silently excluded.

**Friendly name:** `name_by_user` wins if set in HA; otherwise falls back to `name` (which for Z2M devices is the Z2M friendly name — our device key convention).

**Models preservation:** The `models` block is never overwritten by a refresh. On each run, `Assemble Results` reads the existing `models` from the file on disk before building the new registry, then carries it forward into the written output. Manual model entries survive refreshes.

### Utility: Device Registry Flow

**Tab:** `Utility: Device Registry`

**Triggers:**
- On Startup inject (fires once, 100ms delay)
- Manual inject (Test Cases group)
- MQTT in on `highland/command/config/reload/device_registry`

**Groups:**

| Group | Purpose |
|-------|---------|
| Sinks | Three trigger entry points → Connection Gate (HA, 5s retention) → link to pipeline |
| Registry Pipeline | Read existing registry from disk → HA fetch (areas, devices, entities) → Assemble Results → fan out to Persist and Publish |
| Test Cases | Manual inject wired directly to pipeline entry (bypasses Connection Gate) |
| Error Handling | Flow-wide catch → debug |

**Connection Gate:** Uses `RETENTION_MS=5000` rather than 0. This is intentional — on startup, the HA connection state may not yet be confirmed by `Utility: Connections`. The 5-second retention window ensures the gate doesn't drop the startup trigger before the connection is verified.

**Persist:** Serializes `msg.payload` to JSON and writes to `/config/device_registry.json`.

**Publish:** Stores `msg.payload` in `global.config.deviceRegistry`. Publishes a status summary to `highland/status/config/loaded` (retained).

### Maintenance

**Adding a new device:** Pair it in Z2M or Z-Wave, assign it to an area in HA, then trigger a registry refresh (manual inject or `highland/command/config/reload/device_registry`). The device appears automatically.

**Adding a new model's battery spec:** Edit `device_registry.json` directly on the host, add an entry to the `models` block, then reload. The next refresh will preserve your addition.

**Device removal:** Remove from Z2M/Z-Wave, then refresh. The device will disappear from the `devices` block automatically since the registry is rebuilt from scratch on every refresh.

---

## Command Dispatcher

### Purpose

Translate high-level commands ("turn on garage_carriage_left") into protocol-specific MQTT messages. Flows say *what* they want; the dispatcher knows *how*.

### Subflow Interface

**Input:**
```json
{ "entity": "garage_carriage_left", "action": "on" }
```
```json
{ "entity": "garage_carriage_left", "action": "brightness", "value": 50 }
```
```json
{ "entity": "some_device", "action": "raw", "payload": { "custom": "data" } }
```

**Behavior:**
1. Lookup entity in Device Registry (`global.config.deviceRegistry.devices`)
2. Validate action against capabilities (warn on unsupported — don't block)
3. Format payload based on protocol + action
4. Publish to appropriate topic

### Common Actions (v1)

| Action | Applies To |
|--------|------------|
| `on` | lights, switches |
| `off` | lights, switches |
| `toggle` | lights, switches |
| `brightness` | dimmable lights (value: 0–255) |
| `lock` | locks |
| `unlock` | locks |
| `raw` | any — passthrough for unsupported actions |

### Protocol Translation

**Zigbee (Z2M):**
```
Topic: zigbee2mqtt/{device}/set
Payload: {"state": "ON"} / {"brightness": 255}
```

**Z-Wave (Z-Wave JS UI MQTT gateway):**
```
Topic: zwave/{node}/set
Payload: protocol-specific
```

### Extending Actions

| Scenario | Approach |
|----------|----------|
| New device, existing capability | Refresh registry — device appears automatically |
| One-off command | Use `raw` passthrough |
| Repeated new capability | Add to common actions |

---

## ACK Tracker Utility Flow

### Purpose

Centralized tracking of acknowledgment requests. Flows that need confirmation of actions register their expectations; the tracker collects ACKs and reports results on timeout. Keeps ACK bookkeeping out of individual flows.

### Topics

| Topic | Purpose | Publisher |
|-------|---------|-----------|
| `highland/ack/register` | Register expectation for ACKs | Requesting flow |
| `highland/ack` | ACK responses | Responding flows |
| `highland/ack/result` | Outcome after timeout | ACK Tracker |

### Payloads

**Registration:**
```json
{
  "correlation_id": "abc123",
  "expected_sources": ["foyer_entry_door", "garage_entry_door"],
  "timeout_seconds": 30,
  "source": "security"
}
```

**ACK response:**
```json
{
  "ack_correlation_id": "abc123",
  "source": "foyer_entry_door",
  "timestamp": "..."
}
```

**Result (after timeout):**
```json
{
  "correlation_id": "abc123",
  "expected": 2,
  "received": 1,
  "sources": ["foyer_entry_door"],
  "missing": ["garage_entry_door"],
  "success": false
}
```

### Separation of Concerns

| Component | Responsibility |
|-----------|----------------|
| **ACK Tracker** | Count ACKs, track by correlation_id, report results (raw device keys only) |
| **Requesting flow** | Register expectations, handle success/failure, decide escalation |
| **Notification flow** | Resolve friendly names from Device Registry, format user-facing messages |

### Friendly Name Resolution

```
missing: ["garage_entry_door"]
    │
    ▼
global.config.deviceRegistry.devices['garage_entry_door'].friendly_name
    │
    ▼
"Garage Door Lock"
    │
    ▼
Notification: "Lockdown failed: Garage Door Lock did not respond"
```

---

*Last Updated: 2026-03-27*
