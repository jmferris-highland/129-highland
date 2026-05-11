# Landroid Integration

Integration of Worx Landroid Vision robotic mower into Highland.

**Status:** 📋 Phase 1 in progress — `landroid_cloud` installed, schedule and rain suppression state machine designed, Node-RED flow implementation pending. Edge cut implementation blocked pending `landroid_cloud` fix for Vision hardware.

---

## Hardware

**Worx Landroid Vision 1-Acre 4WD (WR344)**

- Wire-free boundary detection via camera/AI (no perimeter wire required)
- Built-in WiFi; communicates exclusively via Worx cloud (AWS IoT Core)
- No local API
- Sited: side yard (late afternoon direct sun only)
- Zones mapped: side yard + rear yard combined as single L-shaped zone (0.12 acres); front yard (est. 0.25–0.30 acres) mapping in progress

**Reolink Argus Eco Ultra + Solar Panel** *(to be purchased)*

- 4K, 125° FOV, color night vision
- Solar/battery powered, WiFi
- Strap mounts included — supports tree or pole mounting where no fixed structure is available
- Connects to the RLN16-410 NVR over WiFi; NVR exposes RTSP stream to the Highland video pipeline identically to PoE cameras

---

## Integration Philosophy

Connectivity is **additive nicety, not a requirement** for the mower's core function. If the Highland integration breaks temporarily or permanently, Mowen continues to sit safely on his dock. What is lost is automated scheduling and rain management — not catastrophic, but worth designing for reliability.

Node-RED owns **both** the schedule and rain management. The Worx app schedule is disabled entirely — no conflict, no race conditions, no intercepting a `starting` event the app triggered. This was chosen over a hybrid approach because:

- The firmware's coverage queue naturally distributes work across zones without needing a rigid alternating zone schedule
- Rain-interrupted sessions already break any predictable zone alternation anyway
- Node-RED owning the schedule eliminates the `starting` intercept entirely — we simply don't send `start_mowing` if conditions are bad
- The cleaner design outweighs the loss of the app schedule as a fallback

**Failure mode:** If Node-RED is down, Mowen does not mow. This is the accepted tradeoff.

---

## Mowing Schedule

Node-RED issues commands at `schedule_start` each day (Monday through Saturday). The Worx app schedule is disabled entirely. Sunday is the only fixed day off.

**Start time: 9:00 AM** — late enough for dew to burn off (front yard slope receives early morning sun), early enough to complete before evening moisture accumulates.

**Edge cut days: Wednesday and Sunday.** Community consensus for WR344 is twice weekly. On edge cut days, Node-RED issues `landroid_cloud.ots` with `boundary: true` first and waits for completion before sending `start_mowing`. Sunday edge cuts are followed by a normal mow session — Sunday is not a rest day.

**Zone distribution:** No zone-specific schedule. Mowen's firmware coverage queue naturally prioritizes unmowed and partially-mowed areas across all zones. Observed behavior confirms the firmware distributes coverage intelligently — a rigid alternating zone schedule would be redundant complexity.

**App configuration:** Schedule disabled in Worx app. "Mow When It Is Raining" = ON (disables onboard rain delay). Node-RED owns all start, stop, and rain management decisions.

---

## Observed State Behavior

Captured from live WR344 operation. Use this as the reference for flow design.

### Normal Mow Cycle

```
[NR sends start_mowing] → starting → mowing → returning → docked
```

### Normal Edge Cut Cycle

```
[NR sends ots boundary:true] → starting → edge_cut → returning → docked
```

### Low Battery Mid-Session

The battery recharge signature applies equally to both mowing and edge cut sessions:

**During coverage mowing:**
```
mowing → [error: battery_low] → returning → docked → [error: no_error] → starting → mowing
```

**During edge cut:**
```
edge_cut → [error: battery_low] → returning → docked → [error: no_error] → starting → edge_cut (expected — unconfirmed)
```

- `battery_low` error fires at the 10% discharge threshold
- Error clears approximately 2.5 minutes after docking, once battery climbs back above 10%
- Mower resumes autonomously once sufficiently charged — `starting` fires without any external command
- Whether an interrupted edge cut resumes from where it left off (consistent with coverage mowing behavior) or restarts from scratch is **unconfirmed** — requires empirical observation once edge cut fix lands

### The `returning` Disambiguation Problem

`returning` alone is ambiguous — it means Mowen is heading home but does not indicate why. The reason must be inferred from what preceded it:

| What preceded `returning` | Cause | Response |
|---------------------------|-------|----------|
| `battery_low` error was active (`battery_low_pending` = true) | Battery recharge mid-session | None — Mowen resumes autonomously |
| Node-RED issued `lawn_mower.dock` | Forced dock (rain or end-of-day) | Monitor conditions; resume when appropriate |
| Neither of the above | Natural session completion | Session done for today |

**`battery_low_pending` flag** is the discriminator. It is set when `battery_low` error fires and cleared when the error returns to `no_error`. When `returning` is observed, this flag determines whether the return is a recharge cycle or a completion. This applies identically to both mowing and edge cut sessions.

### Rain Delay (Legacy — pre-Highland)

```
mowing → [error: rain_delay] → returning → rain_delayed
```

This state sequence applied when the Worx app's onboard rain delay feature was active. With "Mow When It Is Raining" enabled in the Worx app (rain delay permanently disabled for Highland operation), `rain_delayed` will no longer appear. Node-RED owns all rain management. Documented here for reference only.

**Why the onboard rain delay was disabled:**
- The onboard rain sensor uses a cross-hatch plastic surface that holds water droplets via nucleation points, causing it to read "wet" for up to 7 hours after rain stops
- The rain delay cannot be toggled programmatically via `landroid_cloud` — only manually in the app
- When in `rain_delayed` state, `lawn_mower.start_mowing` is blocked — the sensor reading wet immediately returns Mowen to the dock
- `number.set_value` for the rain delay entity times out on Vision hardware — write path appears non-functional for this entity
- These constraints make the onboard rain delay incompatible with Tempest-owned rain management

### Schedule Window End Behavior

**Observed:** Mowen self-docks at the end of his previous schedule window even after manual overrides. If forced back out after the window closed, he starts, mows briefly, and returns within minutes.

**Important:** This behavior was confirmed while the app schedule was active. Whether it persists after the app schedule is disabled is unconfirmed — Node-RED's end-of-day safety net is the primary mechanism once the app schedule is gone.

### Mid-Charge at Schedule End (Edge Case)

If Mowen docks for a battery recharge mid-session and the schedule end time arrives while he is still charging, he will attempt to resume autonomously after charging via `starting`. Node-RED does not send `start_mowing` outside the schedule window, creating a gap.

**Handling:**
1. At `schedule_end`, if `battery_low_pending` is set: send `lawn_mower.dock` — whether this cancels the pending autonomous resume is **unconfirmed, requires empirical testing**
2. **Narrow `starting` safety net** — if `starting` fires outside the schedule window, immediately send `lawn_mower.dock`. Scoped exclusively to post-schedule `starting` events.

**Validation test:** Next time Mowen docks mid-session for battery, wait for charging to begin, then send `lawn_mower.dock`. Observe whether he resumes after charging or remains docked.

### Coverage Map Retention and Completion Priority

The WR344 maintains a persistent coverage map across all mapped zones simultaneously. Interrupted sessions are queued and resumed from the point of interruption on the next session, regardless of zone.

**Confirmed end-to-end:** Front yard (rain delay) completed before rear yard (manual dock), both from exact interruption points. Both zones, both interruption types confirmed.

**Implication for scheduling:** Node-RED issues `start_mowing` without zone specification. Mowen determines where to go based on his coverage queue.

### Battery Voltage Profile

`sensor.vision_cloud_4wd_battery_voltage` — precision set to two decimal places for trend detection. The WR344 does **not** trickle charge; firmware uses a deliberate charge/discharge cycle.

| Condition | Voltage |
|-----------|--------|
| Full charge ceiling | ~20.0–20.5V |
| Docked discharge lower threshold | ~17.0–17.3V |
| Active mow discharge floor (at `battery_low`) | ~17V or below |

`binary_sensor.vision_cloud_4wd_charging` is **non-functional**. Use voltage trend instead.

| Condition | Inferred state |
|-----------|---------------|
| Voltage < 20V and rising between polls | Charging |
| Voltage ≥ 20V | Fully charged |
| Voltage < 20V and falling between polls | Discharging |

Require two consecutive deltas in the same direction before committing to a state.

### `battery_low` Error Payload Structure

```json
{
  "payload": "battery_low",
  "data": {
    "old_state": { "state": "no_error" },
    "new_state": { "state": "battery_low" }
  },
  "topic": "sensor.vision_cloud_4wd_error"
}
```

`msg.payload` contains the error string directly — use as routing key in switch nodes.

---

## Maintenance

### Blade Runtime Sensors

| Entity | Purpose |
|--------|---------|
| `sensor.vision_cloud_4wd_blade_runtime_total` | Odometer — cumulative lifetime hours; never resets |
| `sensor.vision_cloud_4wd_blade_runtime_since_reset` | Trip meter — hours since last replacement logged in Worx app |

**Threshold:** 150 hours based on WR344 community consensus. Adjust at first replacement.

**Workflow:** Replace blades → log in Worx app → Highland notifies at threshold → repeat.

### Regular Maintenance Schedule

**After every few sessions:** Clear debris from cutting disc, check camera lenses, inspect wheels, check dock contacts.

**Monthly:** Full undercarriage clean, inspect bumper sensor, check blade disc bolts, inspect camera housing.

**Pre-season:** Full inspection, fresh blades, verify base level, clean contacts.

**Post-season / winter storage:** Full clean, remove battery if sub-freezing storage, store indoors.

### Blade Replacement Notification

Watch `sensor.vision_cloud_4wd_blade_runtime_since_reset`. At 150 hours → mobile notification. No reset within 24 hours → follow-up reminder.

---

## Phase 1: `landroid_cloud` HA Integration

### Installation

1. Install `landroid_cloud` via HACS
2. Restart Home Assistant
3. Add integration via Settings → Devices & Services using Worx credentials
4. **Disable schedule entirely in Worx app**
5. Set "Mow When It Is Raining" to **ON** (disables onboard rain delay)
6. Install `landroid-card` via HACS for the dashboard

### Entities

| Entity | Type | Notes |
|--------|------|-------|
| Lawn mower | `lawn_mower` | Primary control entity |
| GPS tracker | `device_tracker` | State permanently `away` — lat/lon in attributes only |
| Battery | Sensor | Charge level |
| Battery voltage | Sensor | Precision set to two decimal places |
| Error state | Sensor | Current error code string |
| Zone | `select` | Read-only on Vision hardware |
| Blade runtime since reset | Sensor | Hours since last replacement |
| Blade runtime total | Sensor | Cumulative lifetime hours |

**Supported mower states:** `mowing`, `docked`, `returning`, `error`, `edge_cut`, `starting`, `escaped_digital_fence`

### Available HA Actions

- `lawn_mower.start_mowing` — send mower out; resumes from coverage map position (confirmed)
- `lawn_mower.dock` — stop and return to base
- `lawn_mower.pause` — stop in place
- `landroid_cloud.ots` — one-time schedule; `boundary: true` triggers dedicated edge cut (confirmed); `runtime` parameter purpose for edge cuts is unclear — test once fix lands

### Edge Cut — Current Status

`landroid_cloud.ots` with `boundary: true` triggers a dedicated edge cut on Vision hardware. However, a bug in `landroid_cloud` currently causes a "This device does not support Edgecut-on-demand" error on Vision models. A fix is in beta (see [GitHub issue #1253](https://github.com/MTrab/landroid_cloud/issues/1253)) and is expected in the next general release. Edge cut scheduling in Node-RED is designed but not yet implementable.

### Known Fragility

- Depends on Worx cloud API remaining stable
- March 2025 release caused excessive API retries locking up HA — resolved, but noted
- `pyworxcloud` is reverse-engineered; Worx API changes can break it without notice
- **If Node-RED is down, Mowen does not mow** — no fallback schedule exists

---

## Phase 1 Automations

### Schedule, Edge Cuts, and Rain Suppression

Owned entirely by Node-RED (`Utility: Landroid`). Implemented as two independent FSMs within the flow, each with a distinct responsibility.

---

#### Architecture: Two FSMs

```
 Tempest MQTT ──► Rain Monitor FSM ──► mow_ready (global context) ──► Session Manager FSM
                       │
                       ├── binary_sensor: Rain Delay Active
                       ├── sensor: Cooldown End Time
                       ├── sensor: Event Accumulation
                       ├── sensor: Rain Tier
                       └── sensor: Rain Monitor State

                  Session Manager FSM
                       │
                       └── sensor: Next Mow Time
```

**Rain Monitor** runs continuously 24/7. Owns all weather observation, accumulation tracking, and cooldown logic. Exposes a single inter-FSM signal (`mow_ready`) to the Session Manager and publishes operational state as HA entities.

**Session Manager** runs within the schedule window. Owns the daily session lifecycle, edge cut sequencing, and completion detection. Consumes `mow_ready` from the Rain Monitor but has no knowledge of rainfall, accumulation, or cooldown internals.

**Communication surface between FSMs:**

| Signal | Type | Direction | Purpose |
|--------|------|-----------|----------|
| `mow_ready` | bool | Rain Monitor → Session Manager | OK to send mow/edge cut commands right now |
| `cooldown_end_time` | timestamp \| null | Rain Monitor → Session Manager | Raw cooldown end; null if no delay; used by Session Manager to compute Next Mow Time |

The interface is deliberately thin. The Session Manager never knows how much it rained, which tier was selected, or how long the cooldown is. It only knows whether it can mow right now, and when it will be able to.

**HA entities published:**

| Entity | Publisher | Type | Notes |
|--------|-----------|------|-------|
| Rain Delay Active | Rain Monitor | `binary_sensor` | On during RAINING and COOLDOWN states |
| Cooldown End Time | Rain Monitor | `sensor` | Timestamp when delay ends; null/unavailable when DRY |
| Event Accumulation | Rain Monitor | `sensor` | Inches accumulated in current or most recent rain event |
| Rain Tier | Rain Monitor | `sensor` | Trace / Light / Moderate / Heavy / Significant |
| Rain Monitor State | Rain Monitor | `sensor` | DRY / RAINING / COOLDOWN |
| Next Mow Time | Session Manager | `sensor` | Always populated; accounts for both cooldown and schedule window — the human-facing answer to "when will Mowen next mow?" |

**Next Mow Time** is the key display entity. It combines Rain Monitor's `cooldown_end_time` with the schedule config:
- No delay, within schedule window → next `schedule_start` or immediately
- Delay clears before `schedule_end - resume_cutoff_minutes` → today at `cooldown_end_time`
- Delay clears after schedule window → tomorrow at `schedule_start`
- Significant rain (skip day) → tomorrow at `schedule_start`

The Rain Monitor does not compute this — it requires schedule knowledge that belongs to the Session Manager.

---

#### Context Variables

| Variable | Type | Purpose |
|----------|------|---------|
| `battery_low_pending` | bool | Set when `battery_low` error fires; cleared when error returns to `no_error`. The key discriminator for `returning` disambiguation — applies to both mowing and edge cut sessions |
| `edge_cut_pending` | bool | Set when an edge cut day passes without a completed edge cut. Carries forward until conditions allow completion |
| `last_rain_end` | timestamp | When Tempest last reported precipitation stopping |

---

#### Completion Detection

Completion is detected differently depending on whether `battery_low_pending` is set:

**Natural completion** (either mowing or edge cut):
- `returning` fires AND `battery_low_pending` is false AND Node-RED did not issue the dock command → session complete

**Battery recharge mid-session:**
- `returning` fires AND `battery_low_pending` is true → recharge cycle; Mowen resumes autonomously; do not treat as completion

**Node-RED forced dock:**
- Node-RED issued `lawn_mower.dock` → forced return; not a completion

---

#### State Machine

**WAITING**
Before `schedule_start`. Monitoring Tempest continuously.

Transitions:
- `schedule_start` reached AND today is edge cut day (or `edge_cut_pending`) AND conditions good → send `landroid_cloud.ots` with `boundary: true` → **EDGE_CUTTING**
- `schedule_start` reached AND no edge cut due AND conditions good → send `lawn_mower.start_mowing` → **MOWING**
- `schedule_start` reached AND conditions bad → **COOLDOWN**

---

**EDGE_CUTTING**
Node-RED sent OTS edge cut. Mowen is running the perimeter. Monitoring continuously.

Transitions:
- `battery_low` error fires → set `battery_low_pending`; no state change — recharge cycle; Mowen resumes autonomously; when `starting` fires after charge cycle, watch for `edge_cut` state to confirm resume
- `returning` fires AND `battery_low_pending` false AND no dock command → edge cut complete → clear `edge_cut_pending` → **EDGE_CUT_COMPLETE**
- `returning` fires AND `battery_low_pending` true → recharge; stay in EDGE_CUTTING; clear `battery_low_pending` when error clears
- Rain detected → send `lawn_mower.dock` → `edge_cut_pending` remains set → **COOLDOWN**

---

**EDGE_CUT_COMPLETE**
Edge cut finished. Evaluating whether time remains for mowing.

Transitions:
- Time remaining >= `min_edge_cut_window_minutes` → send `lawn_mower.start_mowing` → **MOWING**
- Time remaining < `min_edge_cut_window_minutes` → **DONE**

---

**MOWING**
Node-RED sent `start_mowing`. Mowen is cutting. Monitoring continuously.

Transitions:
- `battery_low` error fires → set `battery_low_pending`; no state change — recharge cycle; Mowen resumes autonomously
- `returning` fires AND `battery_low_pending` false AND no dock command → natural completion → **DONE**
- `returning` fires AND `battery_low_pending` true → recharge; stay in MOWING; clear `battery_low_pending` when error clears
- Rain detected → send `lawn_mower.dock` → **COOLDOWN**

---

**COOLDOWN**
Rain stopped or conditions not yet met at schedule start. Waiting.

Resume conditions (all must be met):
1. No active precipitation
2. `now >= last_rain_end + cooldown_minutes`
3. NWS minutely forecast clear for `forecast_clear_minutes` ahead
4. `now <= schedule_end - resume_cutoff_minutes`

All met AND edge cut pending → time gate: `now <= schedule_end - min_edge_cut_window_minutes` → send OTS → **EDGE_CUTTING**
All met AND no edge cut pending → send `lawn_mower.start_mowing` → **MOWING**
Conditions met but past all time gates → **DONE**

---

**DONE**
Day complete.

Actions on entry:
- If today was an edge cut day AND `edge_cut_pending` is still set (edge cut did not complete) → retain `edge_cut_pending` for tomorrow
- If edge cut completed today → `edge_cut_pending` already cleared
- Clear `battery_low_pending`
- Return to **WAITING** at next `schedule_start`

---

#### End-of-Day Handling

**Safety net:** At `schedule_end + end_of_day_safety_minutes`, if mower state is not `docked` → send `lawn_mower.dock` → **DONE**.

**Mid-charge edge case:** If `battery_low_pending` is set at `schedule_end`:
- Send `lawn_mower.dock` — whether this cancels pending autonomous resume is unconfirmed
- **Narrow `starting` safety net:** if `starting` fires outside schedule window → immediately send `lawn_mower.dock`

---

#### Dynamic Cooldown — Rainfall Accumulation Tiers

Rather than a fixed cooldown, Node-RED tracks rainfall accumulation during each rain event and calculates a dynamic cooldown when rain stops. Tiers are based on cool-season grass on clay-heavy soil (Hudson Valley typical) — clay drains significantly slower than sandy or loamy soil, and wheel traffic on saturated clay causes root zone compaction that is slow to recover.

**Precipitation floor:**

The Rain Monitor does not react to every non-zero `rain_accumulated` reading. A minimum intensity floor filters out trace precipitation and drizzle that would evaporate before meaningfully wetting the ground. Initial value: **0.0017 in/min** (approximately 0.10 in/hour — the lower boundary of light rain). This is a single tunable config value; a full season of observation will establish the right threshold for this property.

Trigger condition: `precipitation_type != 'None'` AND `rain_accumulated >= precipitation_floor`

**Accumulation tracking:**

Node-RED subscribes to `highland/state/weather/station` (retained, ~1-minute updates). The `rain_accumulated` field is the rainfall in the last report interval (inches per minute). While `precipitation_type != 'None'`, each observation adds `rain_accumulated` to `event_accumulation`. When `precipitation_type` returns to `None`, the tier lookup fires and `event_accumulation` resets.

**Tier table:**

| Accumulation | Tier | Cooldown | Rationale |
|---|---|---|---|
| < 0.05 in | Trace | 30 min | Surface moisture only; drains quickly |
| 0.05 – 0.15 in | Light | 60 min | Light wetting; clay needs time but surface recovers within an hour |
| 0.15 – 0.50 in | Moderate | 180 min | Meaningful saturation in clay; 3 hours minimum before mowing safely |
| 0.50 – 1.00 in | Heavy | 360 min | Significant saturation; 6 hours to allow drainage and avoid compaction |
| > 1.00 in | Significant | Skip day | Genuine saturation; transition to DONE and let tomorrow's schedule fire fresh |

The **Significant** tier does not use a numeric cooldown. Node-RED transitions directly to DONE for the day. Tomorrow's `schedule_start` fires normally with a fresh conditions check — if the ground has recovered by 9am, mowing proceeds; if not, the standard conditions check (NWS, Tempest) catches it.

**All tier values are tunable config.** A full mow season of observation will establish what thresholds actually work at this property. Factors that affect actual recovery time beyond accumulation: ambient temperature, cloud cover, and which zone (the front yard slope drains faster than the flatter rear and side yards).

**The cooldown is a floor, not a ceiling.** During the cooldown period, the NWS minutely forecast check continues to run. If more precipitation is forecast before the cooldown would elapse, the resume is deferred regardless of the tier value. The tier sets the minimum; conditions checks govern the actual resume decision.

**Config:**

```json
"rain_accumulation_tiers": [
    { "max_inches": 0.05, "cooldown_minutes": 30 },
    { "max_inches": 0.15, "cooldown_minutes": 60 },
    { "max_inches": 0.50, "cooldown_minutes": 180 },
    { "max_inches": 1.00, "cooldown_minutes": 360 },
    { "max_inches": null, "cooldown_minutes": null, "skip_day": true }
]
```

**Full config block:**

```json
"schedule": {
    "schedule_start": "09:00",
    "schedule_end": "18:00",
    "edge_cut_days": ["wednesday", "sunday"],
    "min_edge_cut_window_minutes": 90
},
"rain_suppression": {
    "precipitation_floor_in_per_min": 0.0017,
    "forecast_clear_minutes": 30,
    "resume_cutoff_minutes": 15,
    "end_of_day_safety_minutes": 30
},
"rain_accumulation_tiers": [
    { "max_inches": 0.05, "cooldown_minutes": 30 },
    { "max_inches": 0.15, "cooldown_minutes": 60 },
    { "max_inches": 0.50, "cooldown_minutes": 180 },
    { "max_inches": 1.00, "cooldown_minutes": 360 },
    { "max_inches": null, "cooldown_minutes": null, "skip_day": true }
]
```

`min_edge_cut_window_minutes` is a placeholder — set once actual edge cut duration is observed.

---

### Error Notification

When `sensor.vision_cloud_4wd_error` transitions away from `no_error`, route based on `msg.payload` string value.

**Maintenance mode:** `input_boolean.landroid_maintenance_mode` suppresses urgent tier. Auto-timeout 60 minutes. Dashboard indicator required.

| State value | Meaning | Routing |
|-------------|---------|---------|
| `lifted` | Mower lifted unexpectedly | 📺 TV + mobile — suppressed if maintenance mode active |
| `trapped` | Mower stuck | 📺 TV + mobile (urgent) |
| `trapped_timeout` | Stuck extended period | 📺 TV + mobile (urgent) |
| `upside_down` | Mower fell over | 📺 TV + mobile (urgent) |
| `outside_wire` | Escaped mowing zone | 📺 TV + mobile (urgent) |
| `excessive_slope` | Terrain it can't handle | 📺 TV + mobile (urgent) |
| `unreachable_charging_station` | Cannot return to base | 📺 TV + mobile (urgent) |
| `blade_motor_blocked` | Blade obstruction | 📱 Mobile only |
| `wheel_motor_blocked` | Wheel obstruction | 📱 Mobile only |
| `charge_error` | Charging fault | 📱 Mobile only |
| `battery_temperature_error` | Battery thermal fault | 📱 Mobile only |
| `map_error` | Navigation map failure | 📱 Mobile only |
| `mapping_exploration_failed` | Mapping failed | 📱 Mobile only |
| `camera_error` | Vision AI camera fault | 📱 Mobile only |
| `missing_charging_station` | Cannot locate base | 📱 Mobile only |
| `timeout_finding_home` | Timed out returning | 📱 Mobile only |
| `close_door_to_mow` | User action required | 📱 Mobile only |
| `close_door_to_go_home` | User action required | 📱 Mobile only |
| `charging_station_docking_error` | Docking fault | 📱 Mobile only |
| `insufficient_sensor_data` | Sensor fusion failure | 📱 Mobile only |
| `mapping_exploration_required` | Mapping required | 📱 Mobile only |
| `blade_height_adjustment_blocked` | Height adjustment fault | 📱 Mobile only |
| `unknown` | Unrecognized error | 📱 Mobile only |
| `rain_delay` | Rain delay active | 📋 Daily Digest only (should not appear once rain delay disabled) |
| `battery_low` | Low battery | 📋 Daily Digest only — set `battery_low_pending` flag; escalate to mobile only if active AND battery not rising |
| `locked` | Mower locked | 📋 Daily Digest only |
| `battery_trunk_open_timeout` | Battery compartment issue | 📋 Daily Digest only |
| `training_start_disallowed` | Training blocked | 🔇 Log only (user-initiated) |
| `wire_missing` | Wire not detected | 🔇 Log only (should not fire on WR344) |
| `reverse_wire` | Wire polarity error | 🔇 Log only (should not fire on WR344) |
| `wire_sync` | Wire sync error | 🔇 Log only (should not fire on WR344) |
| `ota_error` | Firmware update failed | 🔇 Log only |
| `hbi_error` | Hardware bus error | 🔇 Log only (contact support) |
| `rfid_reader_error` | RFID reader fault | 🔇 Log only (contact support) |
| `headlight_error` | Headlight fault | 🔇 Log only (contact support) |

**Note:** `battery_low` has a dual role — it triggers the Daily Digest notification AND sets `battery_low_pending` in the scheduling state machine. Both must be handled in the flow.

### Blade Replacement Reminder

Watch `sensor.vision_cloud_4wd_blade_runtime_since_reset`. At 150 hours → mobile notification. No reset within 24 hours → follow-up reminder.

### Daily Digest Contribution

Mower state, last mow timestamp, battery level, blade runtime since reset, any active errors.

---

## Security & Anti-Theft

### Threat Model

**Vector 1 — Stolen from dock:** Mower lifted from base while charging or idle.
**Vector 2 — Stolen while running:** Mower intercepted mid-cycle in the yard.

### On-Device Defenses

- **Lift sensor** — always active when powered; stops blades immediately on lift
- **Security PIN** — wrong PIN triggers audible alarm
- **Lock function** — alarm if mower lifted outside GPS/geofence perimeter
- **WiFi kill** — stops operating after three consecutive days outside WiFi coverage

### Dedicated Security Camera

**Reolink Argus Eco Ultra** — tree-mounted, pointing directly at the dock. Exact tree TBD once base installation is finalized (leveling and brick edging pending).

Prompt pattern:
> *"This is a fixed camera pointed at a robotic lawn mower on its charging base. Does the image show a person approaching, touching, lifting, or otherwise interacting with the mower? The mower should be stationary and unattended. Answer yes/no and briefly describe what you see."*

**Pipeline:** CPAI person detection gate → Gemini snapshot analysis → Utility: Notifications with keyframe.

| Signal | Vector 1 | Vector 2 | Latency |
|--------|----------|----------|---------|
| Camera (person detection) | ✅ | ✅ (if in frame) | Seconds |
| HA error state (lift error) | ✅ (with delay caveat) | ✅ | Seconds–minutes |

**Camera siting:** 6–8 feet high, ~45° to side of base, 10–15 foot approach radius. Favor lens angle over panel angle if afternoon sun creates a conflict.

---

## Phase 2: Custom MQTT Bridge (If Warranted)

Phase 2 is worth pursuing if `landroid_cloud` proves consistently unreliable, direct rain delay toggle is needed, or automation needs outgrow HA.

Mosquitto bridge on Communication Hub replaces `landroid_cloud` entirely — must replace, not supplement (rate limiting risk).

```
Mower ──► AWS IoT Core ──► Mosquitto Bridge ──► highland/state/landroid/#
                                             ◄── highland/command/landroid/#
```

**Reference material:** `virtualzone/landroid-bridge` (cert extraction), `pyworxcloud` (`dump_mapping.py`), `iobroker.worx`, HA Community forums, `roboter-forum.com`. All from wire-based models — Vision behavior needs validation.

**Phase 2 validation items:** Topic prefix, `dat.le` for lifted on Vision, `dat.le` while docked vs mowing, position coordinates in `commandOut`, zone targeting via `commandIn`.

---

## Open Questions

- **Edge cut fix general release** — `landroid_cloud` fix for Vision edge cut is in beta (issue #1253); implement edge cut scheduling once released and validated
- **Edge cut duration** — observe actual runtime once fix lands; use to calibrate `min_edge_cut_window_minutes`
- **Edge cut resume after battery recharge** — does an interrupted edge cut resume from where it left off (consistent with coverage mowing), or restart from scratch? Unconfirmed
- **Dock-while-docked behavior** — does `lawn_mower.dock` while Mowen is mid-charge cancel his pending autonomous resume? Validate empirically
- **App schedule disabled + schedule_end behavior** — confirmed while app schedule was active; unconfirmed after disable
- **`lawn_mower.start_mowing` from Node-RED** — expected identical to manual HA call; confirm when flow is first deployed
- Rain suppression thresholds — requires full mow season of Tempest data; all config values are initial estimates
- Camera mount point — exact tree TBD once base installation is finalized

---

*Last Updated: 2026-05-11*
