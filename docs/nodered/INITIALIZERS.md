# Node-RED — Utility: Initializers

## Purpose

Registers global helper functions into the `initializers` context store and signals readiness to the rest of the system. All flows that depend on these helpers wait for the ready signal before processing messages.

---

## How It Works

On Node-RED startup, a single inject node fires once. Two function nodes run in sequence:

1. **Register Functions** — registers each helper into `global` under the `initializers` store, then sets its own `node.status()` showing how many functions were registered
2. **Emit Ready State** — sets `global.get('initializers.ready', 'initializers')` to `true`, signalling all waiting flows that helpers are available

This is a fire-once, no-output flow. Nothing is published to MQTT. Readiness is communicated entirely via context.

---

## Registered Helpers

### `utils.formatStatus`

**Store:** `initializers`

**Signature:** `formatStatus(text: string) → string`

**Usage:**
```javascript
const formatStatus = global.get('utils.formatStatus', 'initializers');
node.status({ fill: 'green', shape: 'dot', text: formatStatus('Completed') });
```

**Returns:** The input text with a human-readable timestamp appended.

**Example output:** `"Completed at: Mar 27, 3:15 AM"`

**Timestamp format:** Month (short), day, hour, minute, 12-hour clock — `en-US` locale.

**Purpose:** Standardizes `node.status()` text across all flows. Any node that sets a status indicator uses this helper so timestamps are consistently formatted throughout the editor.

---

## Accessing Helpers

Always retrieve from the `initializers` store explicitly — never from `default`:

```javascript
const formatStatus = global.get('utils.formatStatus', 'initializers');
```

Omitting the store name reads from `default`, which will return `undefined`.

---

## Readiness Signal

The ready flag lives at:

```javascript
global.get('initializers.ready', 'initializers')  // true when ready
```

This is what the Initializer Latch subflow polls. See `nodered/SUBFLOWS.md` for the latch behavior.

---

## Adding New Helpers

Add a new `global.set()` call inside `Register Functions` before the `node.status()` call:

```javascript
global.set('utils.myHelper', function(arg) {
    // implementation
}, 'initializers');
```

The store argument (`'initializers'`) is required on every `global.set()` call — helpers must land in the `initializers` store, not `default`. The status line in `Register Functions` automatically reflects the updated count via `global.keys('initializers').length`.

---

## Flow Groups

**Initializers** — Single group containing:
- `On Startup` inject (fires once, 100ms delay)
- `Register Functions` function node
- `Emit Ready State` function node (no outputs — terminal)

---

## Notes

- `Utility: Logging` does **not** wait for initializers — it inlines any helpers it needs directly so logging remains functional even if this flow fails. If initializers failed, you want logging to still work.
- The `initializers` context store is memory-backed (`module: "memory"` in settings.js) — non-serializable values like functions can be stored here safely. Functions cannot be serialized to disk and must not be stored in the `default` (localfilesystem) store.
- On Node-RED restart, the inject node re-fires and all helpers are re-registered. Any flow waiting via the Initializer Latch will receive the ready signal within its retry window.

---

*Last Updated: 2026-03-27*
