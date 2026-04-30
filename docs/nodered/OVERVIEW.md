# Node-RED — Overview & Conventions

## Core Principles

1. **Visibility over abstraction** — Keep logic visible in flows; don't hide complexity in subflows unless truly reusable
2. **Horizontal scrolling is the enemy** — Use link nodes and groups to keep flows compact and readable
3. **Pub/sub for inter-flow communication** — Flows talk via MQTT events, not direct dependencies
4. **Centralized error handling** — Flow-wide catch with targeted overrides where needed
5. **Configurable logging** — Per-flow log levels for flexible debugging

---

## Flow Types

| Type | Purpose | Examples |
|------|---------|----------|
| **Area flows** | Own devices and automations for a physical area | `Garage`, `Living Room`, `Front Porch` |
| **Utility flows** | Cross-cutting concerns that transcend areas | `Scheduler`, `Security`, `Notifications`, `Logging`, `Backup`, `ACK Tracker`, `Battery Monitor`, `Health Monitor`, `Config Loader`, `Daily Digest` |

Area flows are named after their physical area. Utility flows are named after their functional concept.

---

## Tab Naming Convention

Flow tabs use a prefix to identify type at a glance:

- **Area flows:** `Area: Garage`, `Area: Living Room`, `Area: Front Porch`
- **Utility flows:** `Utility: Scheduler`, `Utility: Notifications`, `Utility: Config Loader`

---

## Groups

Groups are the primary organizing unit within a flow. Every logical section of a flow lives in a named group with a descriptive label. Examples: `Sinks`, `Flow Registration`, `Handle Motion Event`, `Control Lights`, `Error Handling`.

**Rules:**
- Groups must have descriptive names — the name should tell you what the group does without reading the nodes
- Flows should be as linear as possible within groups (left to right, top to bottom)
- Excessive branching within a group is a signal that the logic needs additional groups
- No node should have more than two outputs (more outputs increase node height and visual complexity)

---

## Link Nodes

Link nodes connect groups to avoid cross-boundary wires. They are preferred over direct wiring whenever connecting across group boundaries.

**Pattern: Grouped Logic with Link Nodes**

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

Each group is a logical unit with a clear purpose. Link nodes connect groups without spaghetti wires. Flows read top-to-bottom or left-to-right in sections.

### Link Call as Callable Subroutine

Node-RED's `link call` node (paired with a matching `link in` and a `link out` in return mode) provides a callable-subroutine pattern. Use this when:

- A flow needs to dispatch to one of several similar processors based on some discriminator (source, type, label, etc.)
- Each processor is functionally self-contained with a clear input shape and an implicit "return when done" exit
- Fanning out to multiple outputs would create a wide dispatcher with one branch per processor type

The pattern looks like:

```
Dispatcher Side                       Processor Side

  ┌─────────────┐                       ┌─────────────────┐
  │ Decide      │                       │ Link In         │
  │ which       │                       │ named e.g.      │
  │ processor   │                       │ "USPS Parser"   │
  └─────┬───────┘                       └─────┬───────────┘
        │                                     │
        ▼                                     ▼
  ┌─────────────┐  ─── invokes ──▶      ┌─────────────────┐
  │ Link Call   │                       │ Process         │
  │ to named    │                       │ message         │
  │ link in     │                       └─────┬───────────┘
  └─────┬───────┘                             │
        │                                     ▼
        │                                ┌─────────────────┐
        │   ◀── returns when done ───   │ Link Out        │
        ▼                                │ (mode: return)  │
  ┌─────────────┐                        └─────────────────┘
  │ Continue    │
  │ pipeline    │
  └─────────────┘
```

**Key properties:**

- The `link out` in return mode hands flow control back to whatever's wired downstream of the `link call`, not to a fixed destination. Multiple dispatchers can call the same processor and each receives its own return.
- Adding a new processor type = add a new `link in` (named for the type) and another `link call` invocation in the dispatcher. No restructuring needed.
- The `msg` object carries hidden `_linkSource` metadata while inside a link-call subroutine. **Function nodes inside the subroutine that build new payloads must not replace `msg` entirely** — doing so strips `_linkSource` and the return won't route. The standard pattern is two outputs: one for the new payload (ACK, side-effect, etc.), one for the original `msg` to flow back through `link out`.

**Example in the codebase:** `Utility: Deliveries` uses link-call to dispatch from a single source-router function to per-carrier parsers. See `subsystems/DELIVERIES.md § Source dispatch via link-call`.

**When NOT to use link-call:**

- The processor naturally fans out to multiple downstream destinations rather than "returning" to a single point
- The processors are conceptually different enough that a unified return contract would be artificial
- The dispatcher is simple enough that a single switch node with two or three outputs is clearer

Reserve link-call for the case where "call this thing, get flow control back when it's done" is genuinely the right mental model.

---

## Node Naming

Node names describe **function**, not topic strings.

- ✓ `Extract Battery Level`
- ✓ `Evaluate State`
- ✓ `Build Notification`
- ✗ `zigbee2mqtt/#`
- ✗ `highland/event/log`

Topic strings belong in node configuration, not node labels. A flow should be readable without knowing the underlying MQTT topics.

---

## Subflows

Use subflows sparingly — only for truly reusable components that are used identically across many flows.

**Good candidates:**
- Latches — reusable startup gates (see `nodered/SUBFLOWS.md`)
- Common transformations used identically across many flows

**Not good candidates:**
- Flow-specific logic (keep it visible)
- One-off utilities (use a function node)
- Anything that hides important business logic

---

## Error Handling

Two-tier approach:

1. **Targeted handlers** — Catch errors in specific groups where custom handling is needed
2. **Flow-wide catch-all** — Single Error node per flow catches anything unhandled

```
┌─────────────────────────────────────────────────────────────────┐
│ Flow: Garage                                                    │
│                                                                 │
│  ┌─────────────────────────────────────┐                       │
│  │ Group: Critical Operation           │                       │
│  │                                     │                       │
│  │  [nodes] ───► [targeted error] ─────┼──► (custom handling)  │
│  └─────────────────────────────────────┘                       │
│                                                                 │
│  ┌─────────────────────────────────────┐                       │
│  │ Group: Normal Operation             │                       │
│  │  [nodes] ──────────────────────────►│ (errors bubble up)   │
│  └─────────────────────────────────────┘                       │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │ Flow-wide Error Node — catches all unhandled errors       │ │
│  └───────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## Flow Registration

Every area flow self-registers its identity and owned devices at startup. This creates a queryable global registry used for capability lookup and message targeting.

### Registration Boilerplate

Every area flow includes a `Flow Registration` group:

```
┌─────────────────────────────────────────────────────────────────┐
│  Group: Flow Registration                                       │
│                                                                 │
│  ┌──────────────────────┐    ┌─────────────────────────┐       │
│  │ Inject               │───►│ Register Flow           │       │
│  │ • On startup         │    │ • Set flow.identity     │       │
│  │ • On deploy          │    │ • Update flowRegistry   │       │
│  │ • Manual trigger     │    └─────────────────────────┘       │
│  └──────────────────────┘                                       │
└─────────────────────────────────────────────────────────────────┘
```

**Register Flow function node:**

```javascript
const flowIdentity = {
  area: 'foyer',
  devices: ['foyer_entry_door', 'foyer_environment']
};

flow.set('identity', flowIdentity);

const registry = global.get('flowRegistry') || {};
registry[flowIdentity.area] = { devices: flowIdentity.devices };
global.set('flowRegistry', registry);

node.status({ fill: 'green', shape: 'dot', text: `Registered: ${flowIdentity.devices.length} devices` });
return msg;
```

### Registry Structure

```json
{
  "foyer": {
    "devices": ["foyer_entry_door", "foyer_environment"]
  },
  "garage": {
    "devices": ["garage_entry_door", "garage_carriage_left", "garage_carriage_right", "garage_motion_sensor"]
  }
}
```

### Storage

| Key | Store | Purpose |
|-----|-------|---------|
| `flow.identity` | disk | This flow's identity and devices |
| `global.flowRegistry` | disk | All flows' registrations |
| `global.config.deviceRegistry` | disk | Device details (single source of truth for capabilities) |

### Capability Lookup at Runtime

Device capabilities live in the Device Registry, not the flow registry. Query at runtime:

```javascript
function getAreasByCapability(capability) {
  const flowRegistry = global.get('flowRegistry');
  const deviceRegistry = global.get('config.deviceRegistry');
  const result = {};

  for (const [area, areaData] of Object.entries(flowRegistry)) {
    const matchingDevices = areaData.devices.filter(deviceId => {
      const device = deviceRegistry[deviceId];
      return device && device.capabilities.includes(capability);
    });
    if (matchingDevices.length > 0) result[area] = matchingDevices;
  }
  return result;
}

// Usage:
getAreasByCapability('lock');
// → { "foyer": ["foyer_entry_door"], "garage": ["garage_entry_door"] }
```

### Staleness Handling

On device removal: update the flow's registration boilerplate and redeploy. The flow overwrites its own registry entry; removed devices disappear immediately. Stale entries from deleted flows are harmless — messages to a deleted flow simply aren't received.

---

## Related Documents

| Document | Content |
|----------|---------|
| `nodered/ENVIRONMENT.md` | Node.js modules, context stores, settings.js |
| `nodered/STARTUP_SEQUENCING.md` | Echo probe, two-condition gate, degraded state |
| `nodered/CONFIG_MANAGEMENT.md` | Config files, Config Loader, secrets |
| `nodered/DEVICE_REGISTRY.md` | Device Registry, Command Dispatcher |
| `nodered/SUBFLOWS.md` | Initializer Latch, Connection Gate |
| `nodered/LOGGING.md` | Logging framework, Utility: Logging |
| `nodered/NOTIFICATIONS.md` | Notification framework, Utility: Notifications |
| `nodered/SCHEDULING.md` | Utility: Scheduling |
| `nodered/HEALTH_MONITORING.md` | Health monitoring, Utility: Connections |
| `nodered/BATTERY_MONITOR.md` | Utility: Battery Monitor |
| `nodered/DAILY_DIGEST.md` | Utility: Daily Digest |

---

*Last Updated: 2026-04-29*
