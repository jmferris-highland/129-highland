# Node-RED — Notification Framework

## Concept

Notifications answer: *"How urgently does a human need to know about this?"*

Notifications are separate from logging. A CRITICAL log may auto-generate a notification, but many notifications have nothing to do with errors — weather alerts, reminders, security events.

---

## Notification Topic

**Single topic — all details in payload:**

```
highland/event/notify
```

See `standards/MQTT_TOPICS.md` for full payload schema.

---

## Severity Levels

| Severity | DND Override | Use Case |
|----------|--------------|----------|
| `low` | No | Informational; can wait (fog advisory, routine status) |
| `medium` | No | Worth knowing soon, but not urgent |
| `high` | Yes | Needs attention now (lock failure, unexpected motion) |
| `critical` | Yes | Emergency (fire, flood, intrusion) |

---

## Notification Payload Fields

| Field | Required | Description |
|-------|----------|-------------|
| `notification_id` | Opt-in | Subscription ID from `notifications.json` — resolves `targets` and `severity` automatically. Preferred for new producers. |
| `targets` | Required if no `notification_id` | Namespaced target array: `["people.joseph.ha_companion"]` |
| `severity` | Required if no `notification_id` | `low`, `medium`, `high`, `critical` |
| `title` | Yes | Short summary |
| `message` | Yes | Full detail |
| `icon` | No | MDI icon string for TV/rich delivery channels (e.g. `mdi:motion-sensor`) |
| `media` | No | Image and/or video URLs |
| `actionable` | No | Can recipient respond? Default = false |
| `actions` | No | Available response actions |
| `sticky` | No | Notification persists until dismissed; default = false |
| `group` | No | Group related notifications together |
| `correlation_id` | No | For linking response back to originating event; also used as notification tag for clearing |

**Two patterns for specifying delivery:**

1. **`notification_id` (preferred for new producers)** — specify a subscription key from `notifications.json`. The `Resolve Notification ID` node expands it to `targets` and `severity` before validation. `title` and `message` remain in the payload since they are typically dynamic.

2. **Explicit `targets` + `severity` (backward compatible)** — hardcode delivery details directly in the payload. All existing producers use this pattern and continue to work unchanged.

```json
// Pattern 1 — notification_id
{
  "notification_id": "dishwasher.cycle_finished",
  "timestamp": "...",
  "source": "dishwasher_attention",
  "title": "Dishwasher finished",
  "message": "Dishes are clean.",
  "correlation_id": "dishwasher_unload_pending",
  "icon": "mdi:dishwasher"
}

// Pattern 2 — explicit targets
{
  "targets": ["people.joseph.ha_companion"],
  "severity": "low",
  "title": "Dishwasher finished",
  "message": "Dishes are clean."
}
```

---

## Target Addressing

Targets use a `namespace.key.channel` format:

| Example target | Resolves to |
|----------------|-------------|
| `people.joseph.ha_companion` | Joseph's phone via HA Companion |
| `people.*.ha_companion` | All people's HA Companion |
| `areas.living_room.tv` | Living room TV |
| `areas.*.tv` | All area TVs |

The `*` wildcard expands all keys in a namespace section.

---

## HA Companion App (Android)

Initial notification delivery channel. Configured via `notifications.json`.

### Android Notification Channels

Pre-configured in HA Companion App for user control over sound/vibration/DND:

| Channel ID | Purpose | DND Override |
|------------|---------|--------------|
| `highland_low` | Informational | No |
| `highland_default` | Standard alerts | No |
| `highland_high` | Urgent alerts | Yes |
| `highland_critical` | Emergency | Yes |

### Severity → HA Companion Mapping

| Severity | HA Priority | Channel | Persistent |
|--------------|-------------|---------|------------|
| `low` | `low` | `highland_low` | No |
| `medium` | `default` | `highland_default` | No |
| `high` | `high` | `highland_high` | No (unless `sticky: true`) |
| `critical` | `high` | `highland_critical` | Yes |

### Clearing Notifications

To programmatically dismiss a notification (e.g., lock succeeded on retry):

```yaml
service: notify.mobile_app_joseph_phone
data:
  message: "clear_notification"
  data:
    tag: "lockdown_20260224_2200"
```

Same `tag` as the original notification, message `"clear_notification"`. Use `highland/command/notify/clear` on the bus — the Notification Utility handles the HA service call.

---

## Action Responses

When a user taps a notification action, HA fires an event. The Notification Utility captures this and publishes to MQTT:

**Published to MQTT:**
```json
{
  "timestamp": "...",
  "source": "notification",
  "action": "retry",
  "correlation_id": "lockdown_20260224_2200",
  "device": "mobile_joseph"
}
```

Topic: `highland/event/notify/action_response`

Originating flows subscribe to `highland/event/notify/action_response`, filter by `correlation_id`, and handle accordingly.

---

## Utility: Notifications Flow

Centralized delivery flow. All notification traffic enters via MQTT and is dispatched here — no other flow calls HA notification services directly.

### Topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `highland/event/notify` | Inbound | Deliver a notification |
| `highland/command/notify/clear` | Inbound | Dismiss a previously delivered notification |
| `highland/event/log` | Outbound | Log delivery outcomes |

### Groups

**Receive Notification** — MQTT in → Resolve Notification ID → Initializer Latch → Validate Payload → Build Targets → `link call` (Deliver, dynamic) → Log Event link out

**HA Companion Delivery** — Link In → Connection Gate → Build Service Call → HA service call node → `link out` (return mode)

**Clear Notification** — MQTT in (`highland/command/notify/clear`) → Resolve Clear Targets → Initializer Latch → Build Clear Call → `link call` (Deliver, dynamic) → Log Event link out

**State Change Logging** — Log Event link in → MQTT Available? switch → Format Log Message → MQTT out / Log to Console

**Test Cases** — Persistent sanity tests; intentionally preserved.

### Resolve Notification ID

Optional pre-processing node inserted before the Initializer Latch in the Receive Notification group. Supports the `notification_id` pattern:

- `notification_id` present → look up in `notifications.json` `subscriptions` section, merge `targets` and `severity` into payload, remove `notification_id`, pass through
- `notification_id` absent → pass through unchanged (full backward compatibility)
- `notification_id` present but not found → `node.warn()` and drop

### Resolve Clear Targets

Parallel node in the Clear Notification group. Same lookup logic as Resolve Notification ID but merges only `targets` (severity is irrelevant for clears). Allows clear payloads to reference a `notification_id` instead of hardcoding target arrays.

### Subscriptions (`notifications.json`)

The `subscriptions` section maps notification IDs to delivery config:

```json
"subscriptions": {
  "dishwasher.cycle_finished": {
    "targets": ["people.joseph.ha_companion", "areas.*.tv"],
    "severity": "low"
  },
  "dishwasher.cycle_finished_reminder": {
    "targets": ["people.joseph.ha_companion", "areas.*.tv"],
    "severity": "low"
  }
}
```

Naming convention: `{device}.{event}` for primary notifications, `{device}.{event}_reminder` for follow-up nags.

### Build Targets (Fan Out)

Resolves a `targets` array into individual delivery messages. Resolution logic:

1. Split target into `[namespace, key, channel]`
2. Look up `notifications[namespace]` — WARN and skip if unknown
3. Expand `*` to all keys in the namespace section
4. Look up `entry.channels[channel]` — WARN and skip if missing
5. Emit one message per resolved address

`resolveLinkTarget()` maps channel names to their `Link In` node names:

```javascript
function resolveLinkTarget(channel) {
    switch (channel) {
        case 'ha_companion': return 'Home Assistant Companion';
        case 'tv':           return 'Television Delivery';
        default: throw new Error(`Unable to resolve channel: ${channel}`);
    }
}
```

Adding a new channel: add a case here and a new delivery group with a matching `Link In` name.

### `link call` Node (Deliver)

Reads `msg.target` dynamically and routes to the matching `Link In` node name. Set to **dynamic** link type, 30-second timeout. Output wires to Log Event link out — logging happens once on the return path after delivery completes. Timeouts handled by a catch node scoped to the `link call`.

### Connection Gate (HA Companion Delivery)

`CONNECTION_TYPE = home_assistant`, `RETENTION_MS = 0`. Output 2 unwired — if HA is down the message drops. Resiliency is the caller's responsibility via channel selection. See `nodered/SUBFLOWS.md`.

### Return Path

The last node in each delivery group is a `link out` set to **return** mode. This returns the message to the `link call` that dispatched it, completing the call/return cycle and triggering downstream logging.

### MQTT Availability Fallback

When MQTT goes down, the normal log path (`highland/event/log` via MQTT out) is unavailable. The State Change Logging group handles this:

```
Log Event link in → MQTT Available? switch (global.connections.mqtt == 'up')
    ↓ up                                   ↓ else
Format Log Message → MQTT out         Log to Console (node.error/warn)
```

`Log to Console` uses `node.error()` / `node.warn()` which write to Node-RED's internal log regardless of MQTT state — visible via `docker compose logs nodered`.

---

## Channel Selection Philosophy

**Multi-channel is intent, not failover.** Specifying `["ha_companion", "pushover"]` means deliver via both regardless of availability. Specifying `["ha_companion"]` means HA Companion only — the caller has made a conscious decision that delivery is best-effort if HA is down.

**Resiliency is the caller's responsibility.** If a notification requires guaranteed delivery, the caller specifies multiple channels. The Notification Utility delivers what it can via the channels specified.

**Missing channel address → log WARN, skip, continue.** Deliver as much as possible.

---

## Television Delivery (TvOverlay)

Delivers notifications to Android TV devices via the [TvOverlay](https://github.com/gugutab/TvOverlay) app REST API. Phase 1 covers Android TV (FIOS STB) only. WebOS fallback is a future phase.

### State Check

Before delivery, the STB media player state is checked via `api-current-state`. Delivery is skipped if state is `off`, `unavailable`, or `unknown`. Any other state (`on`, `playing`, `paused`, `idle`) proceeds.

### Payload Mapping

| Highland field | TvOverlay field | Notes |
|----------------|-----------------|-------|
| `title` | `title` | Direct map |
| `message` | `message` | Direct map |
| `icon` | `smallIcon` | MDI string e.g. `mdi:motion-sensor`; omitted if not set |
| `media.image` | `image` | URL; omitted if not set |
| `media.video` | `video` | RTSP/HLS URL; omitted if not set |
| _(hardcoded)_ | `source` | Always `"Highland"` |

### Config (`notifications.json`)

```json
"living_room": {
    "channels": {
        "tv": {
            "media_player": "media_player.living_room_television",
            "android_tv": {
                "host": "STB_IP_ADDRESS",
                "port": 7143,
                "media_player": "media_player.living_room_android_tv"
            }
        }
    }
}
```

`media_player` at the channel level is the TV itself (reserved for future source detection). `android_tv.media_player` is the STB entity used for state gating. `host` and `port` are the TvOverlay REST endpoint — internal IPs, not secrets.

### Flow Groups

TV delivery is split across two groups, preserving the branching structure needed for future WebOS fallback.

**Television Routing group** — Entry point for all TV channel notifications. Checks the television entity state and routes to the appropriate delivery group.

1. **Link In** (`Television Routing`) — entry from dynamic link call in Receive Notification
2. **Set Entity ID** — sets `msg.payload.entityId` from `_delivery.address.media_player` (the TV itself)
3. **Get TV State** — `api-current-state` node; full entity written to `msg.data`
4. **Resolve Endpoint** — checks `msg.data.state`; drops if `off`/`unavailable`/`unknown`; routes to `Android TV Delivery` (phase 1 — no source detection yet); handles clear path with immediate return
5. **TV Dispatch** — `link call` (dynamic), 30s timeout; routes to delivery group by `msg.target`
6. **Link Out** (return mode) — Television Routing Return

**Android TV Delivery group** — Checks the STB state before committing to TvOverlay delivery.

1. **Link In** (`Android TV Delivery`) — entry from TV Dispatch
2. **Set Entity ID** — sets `msg.payload.entityId` from `_delivery.address.android_tv.media_player` (the STB)
3. **Get Android TV State** — `api-current-state` node; full entity written to `msg.data`
4. **Evaluate STB State** — Output 1: STB active → proceed; Output 2: `off`/`unavailable`/`unknown` → Link Out return (avoids link call timeout)
5. **Build Android TV Call** — builds TvOverlay POST body from notification payload; sets `msg.url`
6. **Notify Android TV** — HTTP request POST to TvOverlay `/notify`, returns JSON object
7. **Link Out** (return mode) — Android TV Return

**WebOS Delivery group** — Placeholder for phase 2. Uses HA `api-call-service` with a simple title/message payload. Not yet routed to in phase 1.

### Future Enhancements

- **Clear support** — TvOverlay's REST API supports dismissing notifications by ID (`DELETE /notify/{id}`). Currently skipped in the Television Routing group (`Resolve Endpoint` clear path returns immediately). Revisit when a concrete use case emerges.
- **Notification timeout** — TvOverlay defaults to 5 seconds display duration. The `duration` field on the `/notify` payload accepts a per-message override in seconds, and omitting it defers to the app's configured default. Persistent notifications are also supported. The current implementation uses the app default; add `duration` to `Build Android TV Call` if a specific timeout or persistence is needed per notification type.
- **Tagging / in-place update** — TvOverlay supports updating an already-displayed notification by reusing its `id`. Not yet wired up, but the likely first use case is progressive notifications during video analysis (e.g. updating a motion alert as the analysis pipeline refines its result). When that lands, `correlation_id` from the Highland payload is the natural source for the TvOverlay `id`.

### Future: WebOS Fallback

When TV is on but STB is off (or source is Xbox/PlayStation), fall back to native WebOS notification via `notify.living_room_lg_tv`. Source detection uses `media_player.living_room_television` current source matched against the `sources` array in config.

---

## Future Channels (Deferred)

| Channel | Notes |
|---------|-------|
| `telegram` | Two-way interaction possible |
| `signal` | Privacy-focused |
| `tts` | Text-to-speech on smart speakers — see `highland/event/speak` |
| `email` | SMTP integration |

Adding a new channel: add a case to `resolveLinkTarget()`, build a new delivery group with a matching `Link In` name, wire the return `link out`.

---

*Last Updated: 2026-04-07*
