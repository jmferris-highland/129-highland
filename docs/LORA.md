# LoRaWAN Sensors — Design & Architecture

## Overview

LoRaWAN-based sensors for low-power, long-range monitoring of locations without Wi-Fi coverage or convenient power. Primary use cases: mailbox delivery detection, trash/recycling bin monitoring.

**Network:** The Things Network (TTN) via TTN Mapper coverage in the Hudson Valley. Helium is an alternative if TTN coverage proves insufficient.

---

## Hardware Platform

**Selected:** RAKwireless RAK3172-SiP (STM32WLE5 SoC)

**Rationale:**
- Integrated LoRa transceiver + ARM Cortex-M4 MCU in a single package
- Low power consumption suitable for battery operation
- Arduino and STM32CubeIDE support
- Small form factor for mailbox/bin enclosures

**Alternative considered:** LILYGO T3S3 — larger, ESP32-based, more power-hungry. Better for prototyping but not ideal for battery-powered field deployment.

---

## Mailbox Sensor

### Detection Strategy

**Primary:** Reed switch + magnet on mailbox door. Simple, reliable, low power. Door open = magnet moves away = switch state change = wake MCU = transmit event.

**Complementary:** IR break-beam across mail slot. Detects mail insertion without door opening (slot deliveries). Secondary confirmation for door-based events.

**Rejected approaches:**
- Weight/load cell — complexity, calibration drift, false positives from snow/ice accumulation
- Camera/vision — power budget incompatible with battery operation
- Capacitive sensing — environmental sensitivity in outdoor enclosure

### State Machine

The mailbox sensor tracks delivery state across a calendar day. State transitions are event-driven (sensor triggers) or time-driven (midnight rollover, confirmation timeout).

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MAILBOX STATE MACHINE                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌──────────────┐                                                          │
│   │    EMPTY     │◄─────────────────────────────────────────────────────┐   │
│   │              │                                                      │   │
│   │  (midnight   │                                                      │   │
│   │   reset)     │                                                      │   │
│   └──────┬───────┘                                                      │   │
│          │                                                              │   │
│          │ door_open OR slot_trigger                                    │   │
│          ▼                                                              │   │
│   ┌──────────────┐                                                      │   │
│   │   PENDING    │──────────────────────────────────────────────────┐   │   │
│   │              │                                                  │   │   │
│   │  (awaiting   │                                                  │   │   │
│   │  retrieval   │                                                  │   │   │
│   │  confirm)    │                                                  │   │   │
│   └──────┬───────┘                                                  │   │   │
│          │                                                          │   │   │
│          │                    ┌──────────────────┐                  │   │   │
│          │                    │                  │                  │   │   │
│          ├────────────────────┤  door_open       │                  │   │   │
│          │  door_open         │  (retrieval      │                  │   │   │
│          │  (retrieval        │  attempt but     │                  │   │   │
│          │  confirmed)        │  no confirmation)│                  │   │   │
│          │                    │                  │                  │   │   │
│          ▼                    ▼                  │                  │   │   │
│   ┌──────────────┐     ┌──────────────┐          │                  │   │   │
│   │  RETRIEVED   │     │   CHECKED    │──────────┘                  │   │   │
│   │              │     │              │                             │   │   │
│   │  (mail       │     │  (opened     │                             │   │   │
│   │  collected)  │     │  but empty   │                             │   │   │
│   │              │     │  or partial) │                             │   │   │
│   └──────┬───────┘     └──────┬───────┘                             │   │   │
│          │                    │                                     │   │   │
│          │ midnight           │ door_open (with retrieval confirm)  │   │   │
│          │                    │                                     │   │   │
│          │                    ▼                                     │   │   │
│          │             ┌──────────────┐                             │   │   │
│          │             │  RETRIEVED   │                             │   │   │
│          │             └──────┬───────┘                             │   │   │
│          │                    │                                     │   │   │
│          │                    │ midnight                            │   │   │
│          └────────────────────┴─────────────────────────────────────┘   │   │
│                                                                             │
│   ┌──────────────┐                                                          │
│   │  DELIVERY_   │◄── midnight passes while in PENDING                      │
│   │  EXCEPTION   │                                                          │
│   │              │                                                          │
│   │  (delivery   │─── door_open (late retrieval confirm) ──► RETRIEVED      │
│   │  unconfirmed)│                                                          │
│   └──────────────┘                                                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### States

| State | Meaning | Entry Condition |
|-------|---------|-----------------|
| `EMPTY` | No mail expected or present | Midnight reset, or initialization |
| `PENDING` | Delivery detected, awaiting retrieval | Door open or slot trigger (first event of day) |
| `CHECKED` | Mailbox opened but retrieval not confirmed | Door open while PENDING, but no retrieval confirmation |
| `RETRIEVED` | Mail collected | Retrieval confirmed (door open + confirmation mechanism) |
| `DELIVERY_EXCEPTION` | Delivery detected yesterday, not retrieved before midnight | Midnight while in PENDING |

### State Ownership

**Node-RED owns the state machine.** The LoRaWAN sensor transmits raw events (door open, door close, slot trigger); Node-RED interprets them, maintains state, and publishes semantic events.

**MQTT state topic (retained):**
```
highland/state/mailbox/delivery
```

**Payload:**
```json
{
  "timestamp": "2026-03-07T14:30:00Z",
  "state": "PENDING",
  "last_event": "door_open",
  "last_event_time": "2026-03-07T14:28:00Z",
  "delivery_time": "2026-03-07T14:28:00Z",
  "retrieval_time": null
}
```

### DELIVERY_EXCEPTION State

`DELIVERY_EXCEPTION` is a first-class state, not an error condition. It mirrors USPS terminology ("delivery exception") and represents a real operational state: mail was delivered but not retrieved before the calendar day ended.

**Entry condition:** Midnight passes while state is `PENDING`.

**Behavior:**
- Non-terminal — the exception is resolvable
- Notification fires on entry (high priority): "Mail from yesterday was not retrieved"
- State persists until door_open event confirms late retrieval
- On late retrieval: transition to `RETRIEVED`, notification: "Yesterday's mail retrieved"

**Why not just stay in PENDING?** Calendar day boundary is semantically meaningful for mail. A delivery on Monday not retrieved until Tuesday is operationally different from a delivery retrieved same-day. The exception state captures this.

### Midnight Boundary

The calendar day rollover is handled by the Scheduler flow's `midnight` task event (`highland/event/scheduler/midnight`), which fires at 00:00:00 local time. This is the same event used by the Daily Digest and any other flow that needs a true date-rollover trigger.

**Mailbox flow subscribes to `highland/event/scheduler/midnight`:**
- If current state is `PENDING` → transition to `DELIVERY_EXCEPTION`
- If current state is `RETRIEVED` or `EMPTY` → transition to `EMPTY` (new day)
- If current state is `CHECKED` → transition to `EMPTY` (nothing was delivered, just checked)
- If current state is `DELIVERY_EXCEPTION` → remain in `DELIVERY_EXCEPTION` (still unresolved)

### Overnight Period vs Midnight

These are distinct concepts:

| Event | Trigger | Purpose |
|-------|---------|---------|
| `highland/event/scheduler/overnight` | 10:00 PM (configurable) | House enters overnight mode; lighting, security posture changes |
| `highland/event/scheduler/midnight` | 00:00:00 exactly | Calendar day boundary; date-sensitive state machines roll over |

The mailbox state machine cares about the **calendar day boundary** (midnight), not the **overnight period** (10pm). A delivery at 11pm is still "today's mail" until midnight, even though the house is in overnight mode.

### Retrieval Confirmation

**Challenge:** Distinguishing "opened to check" from "opened and retrieved mail."

**Approaches (TBD during implementation):**

1. **Duration heuristic** — Door open > N seconds = retrieval; < N seconds = check. Simple but imperfect.

2. **Weight delta** — Load cell detects weight decrease after door close. More reliable but adds hardware complexity and calibration burden.

3. **Manual confirmation** — Notification action: "Did you get the mail?" User confirms. Highest reliability, lowest automation.

4. **Implicit confirmation** — Assume retrieval on any door_open while PENDING. Accept false positives (CHECKED → RETRIEVED when actually just checking). Simplest, may be good enough.

Initial implementation: **implicit confirmation** (option 4). If false positives prove annoying, layer in duration heuristic (option 1).

### Notifications

| Trigger | Severity | Message |
|---------|----------|---------|
| Delivery detected | `low` | "Mail delivered" |
| Retrieval confirmed | `low` | "Mail retrieved" (optional, may suppress) |
| Midnight while PENDING | `high` | "Mail from yesterday was not retrieved" |
| Late retrieval | `low` | "Yesterday's mail retrieved" |

---

## Trash/Recycling Bin Sensors

### Detection Strategy

**Approach:** Ultrasonic distance sensor mounted inside bin lid, measuring distance to contents.

**Derived metrics:**
- `fill_level_percent` — 0% = empty, 100% = full
- `bin_out` — boolean, derived from accelerometer/tilt detection (bin moved to curb)
- `bin_returned` — boolean, bin returned from curb

### State Machine

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TRASH BIN STATE MACHINE                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌──────────────┐                                                          │
│   │    HOME      │◄─────────────────────────────────────────────────────┐   │
│   │              │                                                      │   │
│   │  (bin at     │                                                      │   │
│   │   house)     │                                                      │   │
│   └──────┬───────┘                                                      │   │
│          │                                                              │   │
│          │ movement detected + orientation change                       │   │
│          ▼                                                              │   │
│   ┌──────────────┐                                                      │   │
│   │   AT_CURB    │                                                      │   │
│   │              │                                                      │   │
│   │  (awaiting   │                                                      │   │
│   │  pickup)     │                                                      │   │
│   └──────┬───────┘                                                      │   │
│          │                                                              │   │
│          │ fill_level drops significantly (emptied)                     │   │
│          ▼                                                              │   │
│   ┌──────────────┐                                                      │   │
│   │   EMPTIED    │                                                      │   │
│   │              │                                                      │   │
│   │  (pickup     │                                                      │   │
│   │  confirmed)  │                                                      │   │
│   └──────┬───────┘                                                      │   │
│          │                                                              │   │
│          │ movement detected (return to house)                          │   │
│          │                                                              │   │
│          └──────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### MQTT Topics

**State (retained):**
```
highland/state/driveway/trash_bin
highland/state/driveway/recycling_bin
```

**Events:**
```
highland/event/driveway/trash_bin/moved_to_curb
highland/event/driveway/trash_bin/emptied
highland/event/driveway/trash_bin/returned
```

### Notifications

| Trigger | Severity | Message |
|---------|----------|---------|
| Collection day reminder (via calendar) | `low` | "Trash day tomorrow — bins are at X%" |
| Bin not at curb by threshold time | `medium` | "Trash day — bins not yet at curb" |
| Pickup confirmed | `low` | "Trash collected" |
| Bin not returned by evening | `low` | "Bins still at curb" |

---

## LoRaWAN Integration

### TTN → Node-RED Path

```
Sensor → LoRaWAN → TTN Network Server → TTN MQTT Integration → Node-RED
```

**TTN MQTT Integration:**
- Node-RED subscribes to TTN MQTT broker
- Topic structure: `v3/{application_id}/devices/{device_id}/up`
- Payload includes decoded sensor data (configured in TTN payload formatter)

### Payload Format

Sensors transmit compact binary payloads to minimize airtime. TTN payload formatter decodes to JSON before MQTT delivery.

**Mailbox sensor payload (example):**
```
Byte 0: event_type (0x01 = door_open, 0x02 = door_close, 0x03 = slot_trigger)
Byte 1: battery_voltage (scaled, e.g., value * 0.02 + 2.0 = voltage)
Byte 2: temperature (signed, °C)
```

**TTN decoder output:**
```json
{
  "event_type": "door_open",
  "battery_voltage": 3.2,
  "temperature": 22
}
```

### Node-RED Processing

1. Subscribe to TTN MQTT topics
2. Parse decoded payload
3. Update state machine based on event_type
4. Publish to `highland/` topics (state and events)
5. Trigger notifications as appropriate

---

## Power Budget

### Mailbox Sensor

**Target:** 1+ year battery life on 2x AA lithium (non-rechargeable, cold-tolerant)

**Assumptions:**
- Average 2 deliveries/day (2 door events + potential slot events)
- Each transmission: ~50mA for ~100ms
- Sleep current: < 5µA (STM32WLE5 STOP2 mode)

**Rough calculation:**
- Active: 2 events × 50mA × 0.1s = 10mAs/day
- Sleep: 24h × 3600s × 5µA = 432mAs/day
- Total: ~442mAs/day = ~161 mAh/year
- 2x AA lithium: ~6000 mAh → ~37 years theoretical

Real-world factors (self-discharge, cold weather, transmit retries) reduce this significantly, but 1+ year is highly achievable.

### Trash Bin Sensor

**Target:** 6+ months battery life, solar-assisted if possible

**Considerations:**
- Ultrasonic ranging is more power-hungry than reed switch
- May require periodic fill-level checks (hourly?) vs. event-driven only
- Solar panel on lid could extend battery life significantly

---

## Enclosures

### Mailbox Sensor

- Mount inside mailbox, protected from weather
- Reed switch on door hinge side
- Magnet on door
- IR break-beam across slot opening (if implemented)
- Antenna routing for LoRaWAN signal

### Trash Bin Sensor

- Weatherproof enclosure inside lid
- Ultrasonic sensor facing down into bin
- Accelerometer for movement/orientation detection
- Consider solar panel integration on lid exterior

---

## Open Questions

- [ ] TTN vs Helium — coverage validation at property location
- [ ] Retrieval confirmation mechanism — start with implicit, evaluate heuristics
- [ ] Bin sensor power budget — solar panel sizing if needed
- [ ] Antenna design for mailbox enclosure — signal strength at ~300ft from house
- [ ] Recycling schedule handling — different pickup day than trash
- [ ] Slot delivery detection — IR break-beam reliability in various lighting conditions

---

## Implementation Sequence

1. **TTN account setup** — Create application, configure MQTT integration
2. **RAK3172 dev kit** — Validate LoRaWAN connectivity from property
3. **Mailbox prototype** — Reed switch + basic state machine in Node-RED
4. **Field deployment** — Enclosure, battery, antenna optimization
5. **Trash bin prototype** — Ultrasonic + accelerometer
6. **Solar evaluation** — If needed for trash bin power budget

---

## Email Notifications via IMAP

### Overview

Some LoRaWAN-related notifications may arrive via email (e.g., TTN service alerts, delivery service integrations). These are ingested via IMAP polling against the household mailbox.

**Mailbox:** `highland@ferris.network` (Dynu-hosted, standard IMAP/SMTP)

### IMAP Polling Pattern

Node-RED polls the IMAP inbox at a configurable interval (e.g., every 5 minutes). Matching emails are processed and then **moved to a processed folder** rather than deleted. A cleanup job purges the processed folder after a retention period (e.g., 14 days).

**Why move rather than delete:**
- Preserves audit trail
- Allows manual review of processed messages
- Recoverable if processing logic has bugs

### Folder Structure

```
INBOX                    ← polling target
└── Processed            ← destination for processed messages
    └── (auto-purged after 14 days)
```

### Node-RED Implementation

**Nodes:** `node-red-node-email` (IMAP support)

**Flow pattern:**
1. IMAP poll node checks INBOX
2. Filter node matches subject/sender patterns
3. Processing node extracts relevant data
4. Move node relocates message to Processed folder
5. Downstream logic (state update, notification, etc.)

**Credentials:** Store in `secrets.json` under `imap` block:
```json
{
  "imap": {
    "host": "mail.dynu.com",
    "port": 993,
    "secure": true,
    "user": "highland@ferris.network",
    "password": "..."
  }
}
```

---

*Last Updated: 2026-03-10*
