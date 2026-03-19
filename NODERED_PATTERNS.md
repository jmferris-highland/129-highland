# Node-RED Patterns & Conventions

## Overview

Design patterns and conventions for Node-RED flows in the Highland home automation system. These patterns prioritize readability, maintainability, and alignment with the event-driven architecture.

---

## Core Principles

1. **Visibility over abstraction** — Keep logic visible in flows; don't hide complexity in subflows unless truly reusable
2. **Horizontal scrolling is the enemy** — Use link nodes and groups to keep flows compact and readable
3. **Pub/sub for inter-flow communication** — Flows talk via MQTT events, not direct dependencies
4. **Centralized error handling** — Flow-wide catch with targeted overrides
5. **Configurable logging** — Per-flow log levels for flexible debugging

---

## Node-RED Environment Configuration

### Using Node.js Modules in Function Nodes

`require()` is not available directly in function nodes in Node-RED 3.x. Modules must be declared in the function node's **Setup tab** and are injected as named variables.

**To use `fs` and `path` (or any other module):**
1. Open the function node
2. Go to the **Setup** tab
3. Add entries under **Modules**:
   - Name: `fs` / Module: `fs`
   - Name: `path` / Module: `path`
4. In the function body, use `fs` and `path` directly — do **not** call `require()`

```javascript
// WRONG — will throw "require is not defined"
const fs = require('fs');

// CORRECT — declared in Setup tab, available as plain variable
const raw = fs.readFileSync(filepath, 'utf8');
```

This applies to any Node.js built-in or npm module used in function nodes.

### Context Storage (settings.js)

Node-RED context is configured with two named stores:

```javascript
contextStorage: {
    default: {
        module: "localfilesystem"
    },
    initializers: {
        module: "memory"
    }
}
```

**`default` (localfilesystem):** Persists to disk. Used for flow state, config cache, and any value that must survive a Node-RED restart. This is the store used when no store name is specified.

**`initializers` (memory):** In-memory only. Used exclusively for runtime utilities populated by `Utility: Initializers` at startup — functions, helpers, and other values that cannot be JSON-serialized and therefore cannot use `localfilesystem`. These are re-populated on every restart.

**Usage convention:**

```javascript
// Utility: Initializers — storing a helper function
global.set('utils.formatStatus', function(text) { ... }, 'initializers');

// Any function node — retrieving it
const formatStatus = global.get('utils.formatStatus', 'initializers');

// Default store — no store name needed
global.set('config', configObject);
const config = global.get('config');
```

The store name in `global.get` / `global.set` is what makes the naming self-documenting — seeing `'initializers'` as the third argument tells you exactly where the value was defined and where to look if it's missing.

### Home Assistant Integration

**Primary method:** `node-red-contrib-home-assistant-websocket`

Provides:
- HA entity state access
- Service calls (notifications, backups, etc.)
- Event subscription
- WebSocket connection to HA

**Configuration:**
- Base URL: `http://{ha_ip}:8123`
- Access Token: Long-lived access token from HA (stored in Node-RED credentials, not in config files)

**Use cases:**
| Action | Method |
|--------|--------|
| Trigger HA backup | Service call: `backup.create` |
| Send notification via Companion App | Service call: `notify.mobile_app_*` |
| Check HA entity state | HA API node or WebSocket |
| React to HA events | HA events node |

*Note: Most device control goes through MQTT directly (Z2M, Z-Wave JS). HA integration is primarily for HA-specific features (backups, notifications, entity state that only exists in HA).*

### Healthchecks.io Pinging

Node-RED pings Healthchecks.io **directly via outbound HTTP** from the Health Monitor flow. This is the correct model because:

- Node-RED can make outbound HTTP calls independently of MQTT state
- Using MQTT as an intermediary (e.g. a watchdog script listening for a heartbeat topic) conflates two failure modes — if MQTT goes down but Node-RED is up, the watchdog sees silence and falsely reports Node-RED as unhealthy
- Direct HTTP from Node-RED proves Node-RED liveness independent of any other service

Each service check that Node-RED is responsible for pings its corresponding Healthchecks.io URL on success. Ping URLs are stored in `config.secrets.healthchecks_io`.

**Node-RED's Healthchecks.io ping** (proving Node-RED itself is alive) is sent on a fixed interval from the Health Monitor flow — not via any external script.

> **Watchdog script:** The original watchdog design (subscribing to a Node-RED MQTT heartbeat) is superseded by direct HTTP pinging. Whether a watchdog script has a remaining role (e.g. monitoring something Node-RED genuinely cannot monitor itself) will be determined as each service check is designed. The watchdog script in the runbook Post-Build section should be considered a placeholder pending that analysis.

---

## Flow Organization

### Flow Types

| Type | Purpose | Examples |
|------|---------|----------|
| **Area flows** | Own devices and automations for a physical area | `Garage`, `Living Room`, `Front Porch` |
| **Utility flows** | Cross-cutting concerns that transcend areas | `Scheduler`, `Security`, `Notifications`, `Logging`, `Backup`, `ACK Tracker`, `Battery Monitor`, `Health Monitor`, `Config Loader`, `Daily Digest` |

### Naming Convention

Flows are named by their area or utility function:
- `Garage`
- `Living Room`
- `Scheduler`
- `Notifications`

*No prefixes or suffixes needed — the flow list in Node-RED is the organizing structure.*

---

## Link Nodes & Groups

### Preferred Over Subflows For:
- Keeping logic visible within a flow
- Breaking up long horizontal chains
- Creating logical sections within a flow

### Pattern: Grouped Logic with Link Nodes

```
┌─────────────────────────────────────────────────────────────────┐
│ Group: Handle Motion Event                                      │
│                                                                 │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────────┐  │
│  │ Link In │───►│ Process │───►│ Decide  │───►│ Link Out    │  │
│  │ motion  │    │ payload │    │ action  │    │ to lights   │  │
│  └─────────┘    └─────────┘    └─────────┘    └─────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Group: Control Lights                                           │
│                                                                 │
│  ┌─────────────┐    ┌─────────┐    ┌─────────┐                 │
│  │ Link In     │───►│ Set     │───►│ MQTT    │                 │
│  │ from motion │    │ payload │    │ Out     │                 │
│  └─────────────┘    └─────────┘    └─────────┘                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Benefits:**
- Each group is a logical unit with a clear purpose
- Link nodes connect groups without spaghetti wires
- Flow reads top-to-bottom or left-to-right in sections
- Minimizes horizontal scrolling

---

## Subflows

### Use Sparingly, For Truly Reusable Components

**Good candidates for subflows:**
- Latches — reusable startup gates (see below)
- Common transformations used identically across many flows

**Not good candidates:**
- Flow-specific logic (keep it visible)
- One-off utilities (just use a function node)
- Anything that hides important business logic

### Latch Pattern

Latches are subflows that gate message flow until some condition is met. The naming convention is `{Condition} Latch` — e.g. `Initializer Latch`, leaving room for future variants like `Network Latch` or `Availability Latch`.

All latches share the same interface:
- **1 input** — any message from any source (inject, MQTT, HTTP, etc.)
- **Output 1 (OK)** — messages pass through once condition is met; buffered messages drain in order
- **Output 2 (TIMEOUT)** — single signal message when condition is never met within retry window

### Initializer Latch

Gates flow execution until `Utility: Initializers` has populated the `initializers` context store. Drop this into any flow's startup sequencing group.

**Environment variables (configurable per instance):**
- `RETRY_INTERVAL_MS` — delay between retries in milliseconds (default: 250)
- `MAX_RETRIES` — maximum retry attempts before timeout (default: 20)

Total timeout at defaults: 250ms × 20 = 5 seconds.

**Internal behavior:**
- Every incoming message is buffered immediately
- On first message, starts polling `global.get('initializers.ready', 'initializers')`
- If flag is `true` → sets `flow.initialized = true`, clears `flow.degraded`, drains buffer via Output 1
- If max retries exceeded → sets `flow.degraded = true`, discards buffer, emits signal via Output 2
- If already initialized → passes message through Output 1 directly (no buffering)
- If already degraded → drops message silently

**Calling flow responsibilities:**
- Wire Output 1 to normal processing logic — messages arrive as if nothing happened
- Wire Output 2 to error handler — log CRITICAL, set node status, etc.
- No gate check function node needed in Sinks — the latch handles everything

---

## Flow Registration

### Purpose

Each area flow self-registers its identity and owned devices. This creates a queryable global registry that enables:
- Targeting messages by area
- Looking up devices by capability
- Knowing which area owns which device

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          On Startup / Deploy                        │
│                                                                     │
│  ┌─────────────────┐                                                │
│  │   Foyer Flow    │──► global.flowRegistry['foyer'] = {...}        │
│  └─────────────────┘                                                │
│                                                                     │
│  ┌─────────────────┐                                                │
│  │   Garage Flow   │──► global.flowRegistry['garage'] = {...}       │
│  └─────────────────┘                                                │
│                                                                     │
│  ┌─────────────────┐                                                │
│  │  Living Room    │──► global.flowRegistry['living_room'] = {...}  │
│  └─────────────────┘                                                │
│                                                                     │
│  Each flow overwrites its own key. No global purge, no timing issues│
└─────────────────────────────────────────────────────────────────────┘
```

### Storage

| Storage | Persistence | Purpose |
|---------|-------------|---------|
| `flow.identity` | Disk | This flow's identity and devices |
| `global.flowRegistry` | Disk | All flows' registrations |
| `global.config.deviceRegistry` | Disk | Device details (single source of truth for capabilities) |

**Note:** Node-RED context storage is configured for disk persistence. Survives restarts.

### Flow Registry Structure

```json
{
  "foyer": {
    "devices": ["foyer_entry_door", "foyer_environment"]
  },
  "garage": {
    "devices": ["garage_entry_door", "garage_carriage_left", "garage_carriage_right", "garage_motion_sensor", "garage_environment"]
  },
  "living_room": {
    "devices": ["living_room_overhead", "living_room_environment"]
  }
}
```

### Registration Boilerplate

Every area flow includes this pattern:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Flow: Foyer                                                        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Group: Flow Registration                                   │   │
│  │                                                             │   │
│  │  ┌──────────────────────┐    ┌─────────────────────────┐   │   │
│  │  │ Inject               │───►│ Register Flow           │   │   │
│  │  │ • On startup         │    │                         │   │   │
│  │  │ • On deploy          │    │ • Set flow.identity     │   │   │
│  │  │ • Manual trigger     │    │ • Update flowRegistry   │   │   │
│  │  └──────────────────────┘    └─────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ... rest of flow logic ...                                         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Register Flow function node:**

```javascript
const flowIdentity = {
  area: 'foyer',
  devices: ['foyer_entry_door', 'foyer_environment']
};

// Set flow-level identity
flow.set('identity', flowIdentity);

// Update global registry (overwrite this flow's section only)
const registry = global.get('flowRegistry') || {};
registry[flowIdentity.area] = {
  devices: flowIdentity.devices
};
global.set('flowRegistry', registry);

node.status({ fill: 'green', shape: 'dot', text: `Registered: ${flowIdentity.devices.length} devices` });

return msg;
```

### Capability Lookup at Runtime

Device capabilities are NOT stored in the flow registry. They live in the Device Registry (single source of truth). Query at runtime:

```javascript
// "Find all areas with locks"
function getAreasByCapability(capability) {
  const flowRegistry = global.get('flowRegistry');
  const deviceRegistry = global.get('config.deviceRegistry');
  const result = {};
  
  for (const [area, areaData] of Object.entries(flowRegistry)) {
    const matchingDevices = areaData.devices.filter(deviceId => {
      const device = deviceRegistry[deviceId];
      return device && device.capabilities.includes(capability);
    });
    
    if (matchingDevices.length > 0) {
      result[area] = matchingDevices;
    }
  }
  
  return result;
}

// Usage:
getAreasByCapability('lock');
// → { "foyer": ["foyer_entry_door"], "garage": ["garage_entry_door"] }
```

### Message Targeting Pattern

**Security flow wants to lock all locks:**

```javascript
// 1. Find all areas with lock capability
const locksByArea = getAreasByCapability('lock');
// → { "foyer": ["foyer_entry_door"], "garage": ["garage_entry_door"] }

// 2. Target areas in message
const targetAreas = Object.keys(locksByArea); // ["foyer", "garage"]

// 3. Register expected ACKs at device level
const expectedAcks = Object.values(locksByArea).flat(); 
// → ["foyer_entry_door", "garage_entry_door"]

// 4. Publish lockdown with area targets
msg.payload = {
  message_id: 'lock_123',
  source: 'security',
  recipients: targetAreas,
  request_ack: true
};
// Publish to: highland/event/security/lockdown

// 5. Register with ACK Tracker
msg.ackRegistration = {
  correlation_id: 'lock_123',
  expected_sources: expectedAcks,
  timeout_seconds: 30
};
// Publish to: highland/ack/register
```

**Area flow receives and responds:**

```javascript
// Foyer flow receives lockdown message
// recipients: ["foyer", "garage"]
// flow.identity.area: "foyer" → matches, process message

// Command the lock
// ... (via Command Dispatcher)

// Send ACK at device level
msg.payload = {
  ack_correlation_id: 'lock_123',
  source: 'foyer_entry_door',  // Device, not area
  timestamp: new Date().toISOString()
};
// Publish to: highland/ack
```

### Staleness Handling

**On device removal:**
1. Update the flow's registration boilerplate to remove device
2. Deploy flow
3. Flow overwrites its registry entry, device is gone

**On flow removal:**
1. Flow's registry entry persists (stale)
2. Acceptable: stale entry causes no harm (messages to deleted flow just aren't received)
3. Optional: Manual cleanup or periodic audit

*Details TBD during implementation.*

---

## Startup Sequencing

### The Problem

On Node-RED startup or deploy, three things can conflict:

1. MQTT subscriptions are established and the broker **immediately** delivers all retained messages for those topics
2. The flow's own initialization (Config Loader, flow registration, context restoration from disk) is still in progress
3. `Utility: Initializers` may not have finished populating the `initializers` context store yet

A retained message can arrive and trigger handler logic before `global.config` is loaded, before flow context is restored, and before utility functions like `utils.formatStatus` are available. There is no guaranteed ordering between inject nodes across flows — Node-RED makes no startup ordering guarantees.

### The Two-Condition Gate

The correct solution combines the echo probe pattern with an Initializers readiness check. A flow's gate opens only when **both** conditions are true:

1. **Echo probe returned** — guarantees all retained MQTT messages for this session have been processed
2. **Initializers ready** — guarantees utility functions are available in the `initializers` context store

```
On startup:
  1. Set flow.initialized = false  (gate closed)
  2. Subscribe to all topics (including retained state topics)
  3. Subscribe to highland/status/initializers/ready (non-retained)
  4. Publish probe to highland/command/nodered/init_probe/{flow_name} (non-retained)
  5. Retained messages begin arriving → buffer or discard (gate is closed)
  6. Own probe returns → retained messages are done
     → Check global.get('initializers.ready', 'initializers')
     → If true: both conditions met → open gate immediately
     → If undefined: wait for highland/status/initializers/ready message
  7. highland/status/initializers/ready arrives (non-retained)
     → If probe already returned: both conditions met → open gate
  8. Gate opens → process buffered state → normal operation begins
```

### Why Non-Retained for the Ready Signal

Using a retained message for `highland/status/initializers/ready` introduces a stale session problem — a flow restarting at 09:00:02a would see the retained message from the previous session's 08:00:05a initialization and incorrectly open its gate before the current session's utilities are ready.

Using a **non-retained** message combined with a **global flag** in the `initializers` store solves this cleanly:

- If Initializers runs first → sets `global('initializers.ready', true, 'initializers')` → dependent flows check the flag when their probe returns and open immediately
- If a dependent flow starts first → probe returns, flag is `undefined` → flow waits for the non-retained ready message
- On restart → the `initializers` store clears (memory backend resets) → flag is gone → stale ready state is structurally impossible

### Initializers Startup Sequence

```javascript
// Step 1: Mark not ready
global.set('initializers.ready', false, 'initializers');

// Step 2: Populate all utility functions
global.set('utils.formatStatus', function(text) { ... }, 'initializers');
// ... other utilities ...

// Step 3: Mark ready and signal dependent flows (non-retained)
global.set('initializers.ready', true, 'initializers');
msg.topic   = 'highland/status/initializers/ready';
msg.payload = { timestamp: new Date().toISOString() };
return msg;
// → publish non-retained to highland/status/initializers/ready
```

### Gate Pattern in Sinks Groups

The gate check belongs in the Sinks group — at the point of ingress — before messages reach any processing logic:

```javascript
if (!flow.get('initialized')) {
    const buffer = flow.get('state_buffer') || [];
    buffer.push(msg);
    flow.set('state_buffer', buffer);
    return null;
}
return msg;
```

### State vs Event Handling During Init

**Retained state messages** (from `highland/state/#`) — buffer these. They represent current truth and will be needed once the gate opens.

**Point-in-time events** (from `highland/event/#`) — discard these. If a real-time event fires during the brief init window it is genuinely gone and cannot be recovered. This is acceptable — the window is very short and events are by definition momentary.

### Processing the Buffer

Once the gate opens, process buffered state in order:

```javascript
const buffer = flow.get('state_buffer') || [];
flow.set('state_buffer', []);
for (const bufferedMsg of buffer) {
    node.send(bufferedMsg);
}
```

### Reacting to State vs Reacting to Events

**Two entry points, one handler:**

```
highland/state/scheduler/period  ──┬  (retained — arrives during init, buffered,
  (startup recovery path)          │   processed after gate opens)
                                   ├──► mutate flow.current_period ──► period logic
highland/event/scheduler/evening ──┘  (real-time — arrives during normal operation,
  (real-time transition path)         gate already open)
```

### Probe Topic Convention

```
highland/command/nodered/init_probe/{flow_name}
```

Each flow uses its own probe topic to avoid cross-flow interference.

### Notes

- The init window is typically well under one second. The gate is a safety net, not a performance concern.
- This pattern applies to every flow that subscribes to retained state topics OR uses utilities from the `initializers` store.
- Config Loader and Initializers do not use the two-condition gate — they are the things being waited for, not the things waiting.
- If the MQTT broker is unavailable on startup, the probe never returns. Flows should have a startup timeout (e.g., 10 seconds) after which they log an error and enter a degraded state.

### Bootstrapping Limitation

There is an inherent bootstrapping limitation in any event-driven system: **you cannot use infrastructure to report infrastructure failures.**

If both Initializers and MQTT are simultaneously unavailable, Node-RED has no self-reporting mechanism. This is not a design flaw — it is a physical reality. You cannot publish to a broker that isn't there.

**Accepted fallbacks when MQTT is unavailable:**
- **Node-RED debug sidebar** — node status (red ring, "Degraded") is visible in the editor regardless of MQTT state
- **Node-RED console log** — `node.error()` and `node.warn()` write to Node-RED's own log, visible via `docker compose logs nodered`
- **Healthchecks.io** — the Health Monitor pings Healthchecks.io via direct HTTP, independently of MQTT. If Node-RED is alive but MQTT is down, Healthchecks.io still receives pings and you know Node-RED itself is running

The correct mitigation is not to engineer around this limitation but to **monitor MQTT health** so you know when this condition exists. Once MQTT health monitoring is in place, a simultaneous MQTT outage and Initializers failure becomes a known, observable state rather than a silent one.

### Degraded State and Recovery

When the `Initializer Latch` subflow times out, the calling flow sets `flow.set('degraded', true)`. The gate check in Sinks handles three states:

```javascript
// Check degraded first — permanent failure, drop message
if (flow.get('degraded')) {
    node.warn('Flow is degraded — dropping message');
    return null;
}

// Check initialized — temporary, waiting
if (!flow.get('initialized')) {
    const buffer = flow.get('state_buffer') || [];
    buffer.push(msg);
    flow.set('state_buffer', buffer);
    return null;
}

// Gate open — proceed
return msg;
```

Degraded is checked before initialized because a degraded flow also has `initialized = false` — without this ordering, messages would buffer forever with no way out.

When entering the degraded state, the failure handler should also clear the buffer since those messages will never be processed:

```javascript
flow.set('degraded', true);
flow.set('state_buffer', []);  // No point buffering — gate will never open
node.status({ fill: 'red', shape: 'ring', text: 'Degraded: init timeout' });
// Publish CRITICAL log entry to highland/event/log
```

**Recovery procedure:**

The root cause of a degraded flow is always in `Utility: Initializers` — a bug preventing one or more utilities from being registered, or preventing `initializers.ready` from being set to `true`. The degraded state in dependent flows is a symptom, not the cause.

1. Identify and fix the issue in `Utility: Initializers`
2. Deploy `Utility: Initializers`
3. Redeploy the affected flow(s)

Step 3 is required because `flow.get('degraded')` persists in context storage across restarts. Redeploying resets flow context and re-runs the startup inject, giving the two-condition gate a fresh start against the now-healthy Initializers.

### Two-Tier Approach

1. **Targeted handlers** — Catch errors in specific groups where you need custom handling
2. **Flow-wide catch-all** — Single Error node per flow catches anything unhandled

```
┌─────────────────────────────────────────────────────────────────┐
│ Flow: Garage                                                    │
│                                                                 │
│  ┌─────────────────────────────────────┐                       │
│  │ Group: Critical Operation           │                       │
│  │                                     │                       │
│  │  [nodes] ───► [targeted error] ─────┤──► (custom handling)  │
│  │                                     │                       │
│  └─────────────────────────────────────┘                       │
│                                                                 │
│  ┌─────────────────────────────────────┐                       │
│  │ Group: Normal Operation             │                       │
│  │                                     │                       │
│  │  [nodes] ──────────────────────►│ (errors bubble up)    │
│  │                                     │                       │
│  └─────────────────────────────────────┘                       │
│                                                                 │
│  ┌───────────────────────────────────────────────────────┐   │
│  │ Flow-wide Error Node                                    │   │
│  │ Catches all unhandled errors → dispatches to logging    │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Logging Framework

### Concept

Logging answers: *"How important is this for troubleshooting/audit?"*

Logging is separate from notifications. They intersect (a CRITICAL log may auto-generate a notification), but serve different purposes.

### Log Storage

**Format:** JSONL (JSON Lines) — one JSON object per line

**Location:** `/var/log/highland/` (or equivalent on Node-RED host)

**Rotation:** Daily files

```
/var/log/highland/
├── highland-2025-02-22.jsonl
├── highland-2025-02-23.jsonl
└── highland-2025-02-24.jsonl  (current)
```

**Retention:** Keep N days, delete older (scheduled cleanup task)

### Unified Log

A single daily log file contains entries from ALL systems — Node-RED, Z2M, Z-Wave JS, HA, watchdog, etc. This provides a unified view similar to Windows Event Viewer.

**Log entry structure:**

| Field | Purpose | Examples |
|-------|---------|----------|
| `timestamp` | When it happened | `2025-02-24T10:00:00Z` |
| `system` | Which system generated the log | `node_red`, `ha`, `z2m`, `zwave_js`, `watchdog` |
| `source` | Component within that system | `garage`, `scheduler`, `coordinator` |
| `level` | Severity | `VERBOSE`, `DEBUG`, `INFO`, `WARN`, `ERROR`, `CRITICAL` |
| `message` | Human-readable description | `Failed to turn on carriage lights` |
| `context` | Structured additional data | `{"device": "...", "error": "..."}` |

**Example entries:**

```json
{"timestamp":"2025-02-24T10:00:00Z","system":"node_red","source":"garage","level":"ERROR","message":"Failed to turn on carriage lights","context":{"device":"light.garage_carriage","error":"MQTT timeout"}}
{"timestamp":"2025-02-24T10:00:05Z","system":"z2m","source":"coordinator","level":"WARN","message":"Device interview failed","context":{"device":"garage_motion_sensor"}}
{"timestamp":"2025-02-24T10:00:10Z","system":"ha","source":"recorder","level":"INFO","message":"Database purge completed","context":{"rows_deleted":15000}}
{"timestamp":"2025-02-24T10:00:15Z","system":"watchdog","source":"node_red_monitor","level":"INFO","message":"Heartbeat received","context":{}}
```

### Log Levels

| Level | Value | When to Use |
|-------|-------|-------------|
| `VERBOSE` | 0 | Granular trace; active debugging only |
| `DEBUG` | 1 | Detailed info useful for troubleshooting |
| `INFO` | 2 | Normal operational events worth recording |
| `WARN` | 3 | Something unexpected but not broken |
| `ERROR` | 4 | Something failed but flow continues |
| `CRITICAL` | 5 | Catastrophic failure; intervention needed |

### Per-Flow Log Level Threshold

Each flow has a configured minimum log level (stored in flow context):

```javascript
// Flow context
flow.set('logLevel', 'WARN');  // This flow only emits WARN and above
```

When a flow emits a log message:
- If message level >= flow threshold → emit to logging utility
- If message level < flow threshold → suppress

**Use case:** Set a flow to `DEBUG` while developing, `WARN` in steady state.

### Log Event Topic

Single topic — all systems publish here:

```
highland/event/log
```

### Log Event Payload (MQTT)

```json
{
  "timestamp": "2025-02-24T14:30:00Z",
  "system": "node_red",
  "source": "garage",
  "level": "ERROR",
  "message": "Failed to turn on carriage lights",
  "context": {
    "device": "light.garage_carriage",
    "error": "MQTT timeout"
  }
}
```

### How Systems Log

| System | Mechanism |
|--------|-----------|
| **Node-RED** | Flows publish to `highland/event/log`; Logging utility writes to file |
| **Z2M / Z-Wave JS** | Publish to `highland/event/log` (if configurable), or sidecar script |
| **Home Assistant** | Publish to `highland/event/log` via automation, or sidecar script |
| **Watchdog** | Publish to `highland/event/log` |

Node-RED's Logging utility flow subscribes to `highland/event/log` and writes ALL entries to the unified JSONL file, regardless of `system`.

### Logging Utility Flow

Centralized flow that:
1. Subscribes to `highland/event/log`
2. Appends to today's JSONL file
3. If level = `CRITICAL` → auto-dispatch to Notification Utility

```
highland/event/log ──► Logging Utility ──► Append to JSONL
                              │
                              │ (if CRITICAL)
                              │
                       highland/event/notify
```

### Querying Logs

JSONL + `jq` provides powerful ad-hoc querying:

```bash
# All errors from any system
jq 'select(.level == "ERROR")' highland-2025-02-24.jsonl

# All Node-RED entries
jq 'select(.system == "node_red")' highland-2025-02-24.jsonl

# All entries from garage (regardless of system)
jq 'select(.source == "garage")' highland-2025-02-24.jsonl

# Z2M warnings and above
jq 'select(.system == "z2m" and (.level == "WARN" or .level == "ERROR" or .level == "CRITICAL"))' highland-2025-02-24.jsonl

# Last 10 entries
tail -10 highland-2025-02-24.jsonl | jq '.'
```

### Future: Log Shipping (Deferred)

When NAS is available or if cloud aggregation is desired:
- Ship JSONL files to central location
- JSONL is compatible with most aggregators (Loki, Elastic, Datadog)
- Could also stream via MQTT to external subscriber

*Details TBD when infrastructure supports it.*

### Auto-Notify Behavior

**Only CRITICAL logs auto-notify.** ERROR and below do not.

| Level | Auto-Notify | Rationale |
|-------|-------------|-----------|
| CRITICAL | Yes | System health, potential data loss, immediate intervention |
| ERROR | No | Something failed but system continues; log and move on |
| WARN and below | No | Informational |

**CRITICAL examples:**
- Database size threshold exceeded
- Disk usage critical
- Sustained abnormal CPU spikes
- Core service unresponsive

**ERROR examples (no auto-notify):**
- API timeout (data stale but system functional)
- Device command failed (retry later)
- Automation couldn't complete non-critical path

### Escalation is Flow Responsibility

If a flow wants to notify after repeated ERRORs, *that flow* decides:

```
┌─────────────────────────────────────────────────────────────┐
│  Example: Weather Flow                                      │
│                                                             │
│  API call fails → log ERROR                                 │
│       │                                                     │
│       ┃                                                     │
│  Increment failure counter (flow context)                   │
│       │                                                     │
│       ┃                                                     │
│  Counter > threshold? ──► YES ──► Publish to notify         │
│       │                           (deliberate choice)       │
│       ┃                                                     │
│      NO → continue, try again next cycle                    │
└─────────────────────────────────────────────────────────────┘
```

The logging framework doesn't escalate. Flows own their escalation logic.

---

## Device Registry

### Purpose

Centralized knowledge about devices — protocol, topic structure, capabilities, and metadata. Abstracts the differences between Z2M and Z-Wave JS UI so flows don't need to know protocol details.

### Registry Structure

```json
{
  "garage_carriage_left": {
    "friendly_name": "Garage Carriage Light (Left)",
    "protocol": "zigbee",
    "topic": "zigbee2mqtt/garage_carriage_left",
    "area": "garage",
    "capabilities": ["on_off", "brightness"],
    "battery": null
  },
  "garage_motion_sensor": {
    "friendly_name": "Garage Motion Sensor",
    "protocol": "zigbee",
    "topic": "zigbee2mqtt/garage_motion_sensor",
    "area": "garage",
    "capabilities": ["motion", "battery"],
    "battery": {
      "type": "CR2032",
      "quantity": 1
    }
  },
  "foyer_entry_door": {
    "friendly_name": "Front Door Lock",
    "protocol": "zwave",
    "topic": "zwave/foyer_entry_door",
    "area": "foyer",
    "capabilities": ["lock", "battery"],
    "battery": {
      "type": "AA",
      "quantity": 4
    }
  }
}
```

**Fields:**

| Field | Purpose |
|-------|---------|
| `friendly_name` | User-facing name for notifications, dashboards |
| `protocol` | `zigbee` or `zwave` — determines command formatting |
| `topic` | Base MQTT topic for this device |
| `area` | Physical area (for grouping, context) |
| `capabilities` | What actions this device supports |
| `battery` | Battery metadata (null if mains-powered) |

**Note:** The registry key (e.g., `foyer_entry_door`) is used for internal references and ACK correlation. `friendly_name` is used for user-facing output.

### Storage

Device registry is stored as an external JSON file, loaded into global context at startup. See **Configuration Management** section for full details.

**File:** `/home/nodered/config/device_registry.json`

**Access:** `global.get('config.deviceRegistry')`

### Population

**Manual with validation:**
- Maintain the JSON file directly (IDE, version control)
- Validation flow checks actual devices against registry
- Reports discrepancies (log/notify), does not block commands

---

## Configuration Management

### Overview

Centralized configuration using external JSON files. Separation of version-controllable config from secrets.

### File Structure

```
/home/nodered/config/
├── device_registry.json        ← git: yes
├── flow_registry.json          ← git: yes (area→device mappings, if persisted)
├── notifications.json          ← git: yes (recipient mappings, channels)
├── thresholds.json             ← git: yes (battery, health, etc.)
├── healthchecks.json           ← git: yes (service config)
├── secrets.json                ← git: NO (.gitignore)
└── README.md                   ← git: yes (documents config structure)
```

*Note: Scheduler configuration (periods, sunrise/sunset) lives in schedex nodes within the Scheduler flow, not external config.*

### Config Categories

| Category | Examples | Version Control |
|----------|----------|-----------------|
| **Structural** | Device registry, flow registry, notification recipients | Yes |
| **Tunable** | Thresholds, scheduler times, timeouts | Yes |
| **Secrets** | API keys, credentials, tokens, passwords | **No** |

### Example: secrets.json

```json
{
  "mqtt": {
    "username": "highland",
    "password": "..."
  },
  "smtp": {
    "host": "smtp.example.com",
    "port": 587,
    "secure": false,
    "user": "...",
    "password": "..."
  },
  "weather_api_key": "abc123...",
  "google_calendar_api_key": "...",
  "healthchecks_io": {
    "mqtt": "https://hc-ping.com/uuid-1",
    "z2m": "https://hc-ping.com/uuid-2",
    "zwave": "https://hc-ping.com/uuid-3",
    "ha": "https://hc-ping.com/uuid-4",
    "node_red": "https://hc-ping.com/uuid-5"
  },
  "ai_providers": {
    "openai_api_key": "sk-...",
    "anthropic_api_key": "sk-ant-..."
  }
}
```

### Example: thresholds.json

```json
{
  "battery": {
    "warning": 35,
    "critical": 15
  },
  "health": {
    "disk_warning": 70,
    "disk_critical": 90,
    "cpu_warning": 80,
    "cpu_critical": 95,
    "memory_warning": 80,
    "memory_critical": 95,
    "devices_offline_critical_percent": 20
  },
  "ack": {
    "default_timeout_seconds": 30
  }
}
```

### Example: notifications.json

```json
{
  "recipients": {
    "mobile_joseph": {
      "type": "ha_companion",
      "service": "notify.mobile_app_joseph_phone",
      "admin": true
    },
    "mobile_spouse": {
      "type": "ha_companion",
      "service": "notify.mobile_app_spouse_phone",
      "admin": false
    }
  },
  "channels": {
    "highland_low": { "importance": "low", "dnd_override": false },
    "highland_default": { "importance": "default", "dnd_override": false },
    "highland_high": { "importance": "high", "dnd_override": true },
    "highland_critical": { "importance": "high", "dnd_override": true }
  },
  "daily_digest": {
    "recipients": ["joseph@example.com"],
    "enabled": true
  },
  "defaults": {
    "admin_only": ["mobile_joseph"],
    "all": ["mobile_joseph", "mobile_spouse"]
  }
}
```

**Recipient targeting:**
- `admin: true` — Receives administrative notifications (system health, backups, etc.)
- `admin: false` — Receives household notifications only (security, weather, etc.)
- `defaults.admin_only` — Default recipient list for admin-type notifications
- `defaults.all` — Default recipient list for household notifications

### Config Loader Utility Flow

Loads all config files into global context at startup.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Config Loader (Utility Flow)                                       │
│                                                                     │
│  Triggers:                                                          │
│    • Node-RED startup                                               │
│    • Node-RED deploy                                                │
│    • Manual inject                                                  │
│    • MQTT: highland/command/config/reload                           │
│    • MQTT: highland/command/config/reload/{config_name}             │
│                                                                     │
│  Actions:                                                           │
│    1. Read each JSON file from /home/nodered/config/                │
│    2. Validate JSON structure                                       │
│    3. Store in global.config namespace:                             │
│         global.config.deviceRegistry                                │
│         global.config.flowRegistry                                  │
│         global.config.notifications                                 │
│         global.config.thresholds                                    │
│         global.config.healthchecks                                  │
│         global.config.secrets                                       │
│    4. Log: "Config loaded: {list}"                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Accessing Config in Flows

```javascript
// Device info
const device = global.get('config.deviceRegistry.foyer_entry_door');
const friendlyName = device.friendly_name;

// Thresholds
const batteryWarn = global.get('config.thresholds.battery.warning');
const batteryCrit = global.get('config.thresholds.battery.critical');

// Secrets
const apiKey = global.get('config.secrets.weather_api_key');
const mqttUser = global.get('config.secrets.mqtt.username');

// Notification recipients
const josephDevice = global.get('config.notifications.recipients.mobile_joseph');
const adminRecipients = global.get('config.notifications.defaults.admin_only');
```

### Structural Validation

On load, validate each config file:
- JSON parses correctly
- Required fields present for each entry type
- Log errors, don't crash Node-RED

### Discovery Validation

Periodic or on-demand check comparing device registry against actual Z2M/Z-Wave device lists:
- Devices in Z2M/Z-Wave but not in registry → log/notify (unregistered)
- Devices in registry but not in Z2M/Z-Wave → log/notify (stale or offline)
- Does **not** block commands to unregistered devices

---

## Command Dispatcher

### Purpose

Translate high-level commands ("turn on garage_carriage_left") into protocol-specific MQTT messages. Flows say *what* they want; the dispatcher knows *how*.

### Common Actions (v1)

| Action | Applies To | Notes |
|--------|------------|-------|
| `on` | lights, switches | Turn on |
| `off` | lights, switches | Turn off |
| `toggle` | lights, switches | Toggle state |
| `brightness` | dimmable lights | Set brightness (0-255 or 0-100, normalized) |
| `lock` | locks | Engage lock |
| `unlock` | locks | Disengage lock |
| `raw` | any | Passthrough for unsupported actions |

### Subflow Interface

**Input:**
```json
{
  "entity": "garage_carriage_left",
  "action": "on"
}
```

```json
{
  "entity": "garage_carriage_left",
  "action": "brightness",
  "value": 50
}
```

```json
{
  "entity": "some_device",
  "action": "raw",
  "payload": { "custom": "data" }
}
```

**Behavior:**
1. Lookup entity in Device Registry
2. Validate action against capabilities (optional, could warn/error)
3. Format payload based on protocol + action
4. Publish to appropriate topic

### Protocol Translation

**Zigbee (Z2M):**
```
Topic: zigbee2mqtt/{device}/set
Payload: {"state": "ON"} / {"brightness": 255}
```

**Z-Wave (Z-Wave JS UI MQTT gateway):**
```
Topic: zwave/{node}/set (configurable)
Payload: Protocol-specific, may differ
```

The dispatcher handles this translation internally.

### Extending Actions

| Scenario | Approach |
|----------|----------|
| New device, existing capability | Add to registry only |
| One-off command | Use `raw` passthrough |
| Repeated new capability | Add to common actions |

### Usage in Flows

```
┌──────────────────┐    ┌────────────────────┐
│ Evening period   │───►│ Command Dispatcher │
│ event arrives    │    │                    │
│                  │    │ entity: garage_    │
│                  │    │   carriage_left    │
│                  │    │ action: on         │
└──────────────────┘    └────────────────────┘
```

Flow doesn't know or care about Zigbee topics or payload formats.

---

## ACK Tracker Utility Flow

### Purpose

Centralized tracking of acknowledgment requests. Flows that need confirmation of actions register their expectations, the tracker collects ACKs, and reports results on timeout. Keeps ACK bookkeeping out of individual flows.

### Topics

| Topic | Purpose | Publisher |
|-------|---------|-----------|
| `highland/ack/register` | Register expectation for ACKs | Requesting flow |
| `highland/ack` | ACK responses | Responding flows |
| `highland/ack/result` | Outcome after timeout | ACK Tracker |

### Payloads

**Registration (Flow A → Tracker):**
```json
{
  "correlation_id": "abc123",
  "expected_sources": ["foyer_entry_door", "garage_entry_door"],
  "timeout_seconds": 30,
  "source": "security"
}
```

**ACK (Flow B → Tracker):**
```json
{
  "ack_correlation_id": "abc123",
  "source": "foyer_entry_door",
  "timestamp": "2025-02-24T22:00:05Z"
}
```

**Result (Tracker → Flow A):**
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

---

## Battery Monitor Utility Flow

| State | Threshold | Notification |
|-------|-----------|--------------|
| `normal` | > 35% | None |
| `low` | 35–15% | Normal priority, once |
| `critical` | < 15% | High priority, repeats 24h |

---

## Notification Framework

### Concept

Notifications answer: *"How urgently does a human need to know about this?"* Separate from logging, though CRITICAL logs auto-forward to `highland/event/notify`.

### Notification Topic

```
highland/event/notify
```

### Notification Payload (Internal)

```json
{
  "timestamp": "2025-02-24T14:30:00Z",
  "source": "security",
  "severity": "high",
  "title": "Lock Failed to Engage",
  "message": "Front Door Lock did not respond within 30 seconds",
  "recipients": ["mobile_joseph", "mobile_spouse"],
  "dnd_override": true,
  "media": { "image": "http://camera.local/snapshot.jpg" },
  "actionable": true,
  "actions": [
    { "id": "retry", "label": "Retry Lock" },
    { "id": "dismiss", "label": "Dismiss" }
  ],
  "sticky": true,
  "group": "security_alerts",
  "correlation_id": "lockdown_20250224_2200"
}
```

### Severity Levels

| Severity | DND Override | Use Case |
|----------|--------------|----------|
| `low` | No | Informational; can wait |
| `medium` | No | Worth knowing soon, not urgent |
| `high` | Yes | Needs attention now |
| `critical` | Yes | Emergency |

### Delivery Channels

**Primary: HA Companion App (Android)**
- Rich notifications — images, actionable responses, persistent, DND channels
- Deep-links into HA dashboards on tap
- HA-dependent — unavailable when `connections.home_assistant` is false

**Secondary: Pushover**
- Node-RED calls directly via HTTP — no HA dependency
- Simple, reliable fallback for safety-critical notifications
- Used only when HA is unavailable AND severity is `high` or `critical`

**Routing logic:**
```javascript
const haAvailable = global.get('connections.home_assistant') !== false;

if (haAvailable) {
    // route to HA Companion
} else if (['high', 'critical'].includes(msg.payload.severity)) {
    // route to Pushover
} else {
    // drop low/medium — stale by the time HA recovers
}
```

**No dual delivery** — explicit failover only. HA Companion or Pushover, never both for the same event.

### Action Responses

When user taps a notification action, HA fires an event. The Notification Utility normalizes and publishes:

```
Topic: highland/event/notify/action_response
Payload: {
  "timestamp": "...",
  "source": "notification",
  "action": "retry",
  "correlation_id": "lockdown_20250224_2200",
  "device": "mobile_joseph"
}
```

### Future Channels (Deferred)

Telegram is a strong candidate for future consideration — rich features, HA-independent, two-way interaction. Deferred pending evaluation of DND override limitations and webhook complexity for action responses.

---

## Utility: Connections

### Purpose

Tracks the live state of external service connections and exposes that state via global context for any flow that needs to make runtime decisions based on it. Distinct from `Utility: Health Checks` — Health Checks *reports* infrastructure health outward (to Healthchecks.io, to logs); Connections *exposes* connection state inward to other flows.

### Home Assistant Connection (`connections.home_assistant`)

Uses a persistent SSE stream to HA's `/api/stream` endpoint. The stream stays open indefinitely — when HA goes down the connection closes immediately, giving true real-time detection rather than polling-based inference.

**Why SSE over polling:**
- Connection closure fires instantly — no detection lag
- HA's `/api/stream` sends a `ping` event every 30 seconds when idle — proof of life without any Node-RED-side polling
- Less resource intensive than periodic HTTP requests

**Why not `events: all` or `events: state` on `sensor.time`:**
- `events: all` only delivers events while connected — disconnection produces silence, not a signal
- `sensor.time` updates on a fixed 1-minute cadence, inflexible and coarse

**Signal mapping:**
- `msg.event === 'message'` → HA is reachable → `connections.home_assistant = true`
- `msg.event === 'error'` → HA is unreachable → `connections.home_assistant = false`

**Flow structure (`Utility: Connections`, group: Home Assistant Persistent Stream):**

```
On Startup inject → Initializer Latch → Configure Stream → SSE Client (HA Stream)
                          ↓ (timeout)          ↓
                     Log CRITICAL        Link Out (Process Event)
                                               ↓
                                         Link In (Process Event)
                                               ↓
                                    Event Delta Evaluation
                                               ↓
                                         Record Event
                                               ↓
                                       Loggable? (switch)
                                               ↓ (log_event == true)
                                    Prepare Log Message → MQTT out → highland/event/log
```

**Event Delta Evaluation** — tracks `flow.ha_stream_last_event_type` and sets `msg.log_event = true` only when `msg.event` changes. Prevents logging every 30-second ping — only state transitions are logged.

**Record Event** — sets `global.connections.home_assistant` and updates node status on every event. Node status is the primary visibility mechanism.

**Loggable? switch** — gates the log path on `msg.log_event === true`.

**Log levels:**
- Connection established → `INFO`
- Connection lost → `WARN` (expected during HA restarts)

**Reconnection:** The `node-red-contrib-sse-client` node reconnects automatically on connection closure. The `Restart connection after timeout` checkbox controls idle timeout recovery only.

**Required palette node:** `node-red-contrib-sse-client` (v0.2.4+)

**`CONTEXT_PREFIX` for Initializer Latch:** `ha_stream_`

### Usage in Other Flows

```javascript
const haAvailable = global.get('connections.home_assistant') !== false;
```

The `!== false` guard handles the startup case where the flag hasn't been set yet — defaults to assuming HA is available rather than silently dropping notifications during the brief init window.

### Future Additions

- `connections.mqtt` — if needed
- `connections.telegram` — if Telegram is added as a notification channel

---

## Health Monitoring

### Overview

**Philosophy:** Treat this as a line-of-business application. Degradation detection is as important as outage detection.

### Single Point of Failure Problem

Node-RED alone as the health reporter creates ambiguity. If Node-RED goes down, all service checks stop pinging simultaneously — making it impossible to distinguish "Node-RED is down" from "everything is down."

**Solution: each service self-reports its own liveness independently of Node-RED.** Node-RED's edge checks prove the *connection* is healthy; each service's own ping proves the *service* is healthy.

**Healthchecks.io naming convention:**
- `{Service}` — the service's own self-report, independent of Node-RED
- `Node-RED / {Service} Edge` — Node-RED's check proving the connection to that service is healthy

**Failure signature matrix:**

| Failure | Service check | Edge check | Node-RED check |
|---------|---------------|------------|----------------|
| Node-RED down | ✅ pinging | ❌ silent | ❌ silent |
| Service down | ❌ silent | ❌ silent | ✅ pinging |
| Network path broken (both up) | ✅ pinging | ❌ silent | ✅ pinging |

Three distinct signatures — completely unambiguous diagnosis.

**Current implementation status:**
- `Node-RED` ✅ | `Home Assistant` ✅ | `Communications Hub` ✅
- `Node-RED / Home Assistant Edge` ✅ | `Node-RED / MQTT Edge` ✅
- `Node-RED / Zigbee Edge` ✅ | `Node-RED / Z-Wave Edge` ✅
- `Home Assistant / Zigbee Edge` ✅ | `Home Assistant / Z-Wave Edge` ✅

### Status Values

| Status | Meaning |
|--------|---------|
| `healthy` | Responding AND all metrics within acceptable ranges |
| `degraded` | Responding BUT one or more metrics in warning territory |
| `unhealthy` | Not responding OR critical threshold exceeded |

### Check Frequency

| Service | Frequency | Grace Period |
|---------|-----------|-------------|
| Node-RED | 1 min | 3 min |
| Home Assistant | 1 min | 3 min |
| Communications Hub | 1 min | 3 min |
| MQTT Edge | 1 min | 3 min |
| Zigbee Edge | 1 min | 3 min |
| Z-Wave Edge | 1 min | 3 min |

> Healthchecks.io only supports whole-minute grace periods. 3 minutes is the practical minimum.

### Watchdog Script

The original watchdog design (cron script subscribing to Node-RED's MQTT heartbeat) has been superseded by direct HTTP pinging from Node-RED. Whether a watchdog script has a remaining role will be evaluated per-service. TBD.

---

## Daily Digest

**Trigger:** Midnight + 5 second delay
**Content:** Calendar (next 24–48h), weather, battery status, system health
**Implementation:** Markdown → HTML → SMTP email

---

## Open Questions

- [x] ~~Pub/sub subflow implementation details~~ → **Flow Registration pattern; area-level targeting, device-level ACKs**
- [x] ~~Logging persistence destination~~ → **JSONL files, daily rotation, unified log with `system` field**
- [x] ~~Mobile notification channel selection~~ → **HA Companion App primary; Pushover secondary for high/critical when HA unavailable; no dual delivery**
- [x] ~~Should ERROR-level logs also auto-notify, or only CRITICAL?~~ → **CRITICAL only; escalation is flow responsibility**
- [x] ~~Device Registry storage location~~ → **External JSON file, loaded to global.config.deviceRegistry**
- [x] ~~Device Registry population~~ → **Manual with discovery validation (log/notify discrepancies, don't block)**
- [x] ~~Where to surface "devices needing batteries" data~~ → **Daily Digest email + immediate notifications for critical; dashboard deferred**
- [x] ~~ACK pattern design~~ → **Centralized ACK Tracker utility flow**
- [x] ~~Health monitoring approach~~ → **Each service self-reports + Node-RED edge checks + HA edge checks + Healthchecks.io**
- [x] ~~Startup sequencing / race conditions~~ → **Initializer Latch subflow**
- [x] ~~HA connection state detection~~ → **SSE stream to `/api/stream`; `connections.home_assistant` global flag; `Utility: Connections` flow**
- [x] ~~Notification routing when HA is down~~ → **`connections.home_assistant` flag drives failover; high/critical → Pushover; low/medium → drop**
- [ ] **MQTT latch** — gate for flows that depend on MQTT being available
- [ ] **Utility: Notifications** — build out flow with HA Companion primary + Pushover secondary routing
- [ ] **Utility: Scheduler** — period transitions and task events

---

*Last Updated: 2026-03-19*
