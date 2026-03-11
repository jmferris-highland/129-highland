# Appliance Monitoring — Cycle Detection

## Purpose & Scope

Power-based cycle detection for the three ZEN15-monitored appliances: washing machine, dryer, and dishwasher. The goal is reliable start/end detection that works correctly for each appliance's unique power signature — without false-starts, premature termination, or missing actual completion.

This is a Node-RED-owned system. Node-RED runs the state machine and owns all cycle state. HA receives entities via MQTT Discovery and can display state, trigger notifications, and expose Assist commands — but it is a consumer, not the source of truth.

**Inspired by:** [ha_washdata](https://github.com/3dg1luk43/ha_washdata) — a sophisticated HACS custom component implementing the same core concept. This design extracts the essential logic (state machine + energy gate) and reimplements it natively in Node-RED, consistent with Highland's infrastructure.

---

## Why Simple Thresholds Fail

Each appliance has a specific failure mode that ruins naive "power below X for Y seconds" logic:

**Washing machine** — Rinse cycles drop power to near-zero (no heating element, drum motor only) before spinning hard again. A 3-minute off-delay fires right in the middle of a rinse-spin sequence.

**Dryer** — The heating element is thermostat-controlled and cycles on/off throughout the entire run. Near the end, there's a cool-down phase: element off, drum motor still running (~200–300W). This looks like "ending" but the cycle isn't done. You're stuck choosing between "fires too early on every cool-down" or "waits several minutes past actual completion."

**Dishwasher** — The worst case. Has genuine zero-power gaps between wash/rinse/heated-dry phases. Air-dry mode is literally zero watts for 20+ minutes at the end. You cannot distinguish "between phases" from "done" with a power threshold alone.

**The solution** (from ha_washdata's core insight): instead of asking "is power below threshold?", ask "has effectively zero *energy* flowed in the last N seconds?" Combined with per-appliance off-delays calibrated to each device's behavior, this collapses all three failure modes.

---

## Data Pipeline

```
ZEN15 (Z-Wave) → Z-Wave JS UI → MQTT → Node-RED
                                         ↓
                                  State Machine
                                         ↓
                              MQTT Topics (state + events)
                                         ↓
                              HA via MQTT Discovery
                              PostgreSQL (cycle records)
```

### Z-Wave JS UI MQTT Topics

Z-Wave JS UI publishes device values to MQTT. The exact topic format depends on the Z-Wave JS UI MQTT configuration — verify on first bring-up. Typical format:

```
zwave/{node_name}/Electric_Meter/0/value/66049
```

Or with the "named topics" mode enabled:

```
zwave/{node_name}/power
```

**Action required at bring-up:** Subscribe to `zwave/{node_name}/#` for each appliance node and observe what topics Z-Wave JS UI publishes. The power value (Watts) is the one we care about. Confirm unit is Watts (ZEN15 reports in W, not kW). Update this doc with confirmed topic paths.

**ZEN15 values available:**
- Active Power (W) — what we use for cycle detection
- Energy (kWh) — available but we compute our own Wh from power samples for accuracy
- Voltage, Current, Power Factor — available, ignore for now

**Polling vs. push:** ZEN15 in Z-Wave JS UI can be configured for report-on-change with a threshold (e.g., report when power changes by ≥5W) plus a periodic fallback (e.g., every 60 seconds). Prefer report-on-change with a low threshold (2–5W) for responsive detection. Configure in Z-Wave JS UI per-node parameters.

---

## State Machine

Seven states, one per appliance instance:

```
OFF ──────────────────────► STARTING ──► RUNNING ◄──► PAUSED
 ▲                              │             │
 │                          (abort)           │
 │                              ▼             ▼
 └──── FINISHED ◄───────── ENDING ◄──────────┘
       INTERRUPTED
       FORCE_STOPPED
```

### State Descriptions

| State | Meaning |
|-------|---------|
| `off` | No cycle in progress. Below start threshold. |
| `starting` | Power above threshold, accumulating energy to confirm real start (not a spike). |
| `running` | Cycle confirmed and actively running. |
| `paused` | Power dropped during cycle (rinse gap, DW phase gap). Waiting to see if it resumes. |
| `ending` | Power has been low long enough to start considering cycle complete. Energy gate applied. |
| `finished` | Cycle completed normally. Auto-expires to `off` after 30 minutes. |
| `interrupted` | Cycle ended too short to be a valid run (< completion threshold). |
| `force_stopped` | Watchdog forced termination (sensor went silent). |

### Transition Logic

**OFF → STARTING:** Power ≥ `start_threshold_w` for the first time.

**STARTING → RUNNING:** Time above threshold ≥ `start_duration_s` AND accumulated energy ≥ `start_energy_wh`. Both gates must pass. This kills false-starts from startup spikes.

**STARTING → OFF:** Power drops back below threshold before RUNNING confirmed. False start, abort.

**RUNNING → PAUSED:** Time continuously below `stop_threshold_w` ≥ `pause_delay_s` (dynamic, see below).

**PAUSED → RUNNING:** Power rises above `start_threshold_w` again. Cycle resumes.

**PAUSED → ENDING:** Time continuously below threshold ≥ `end_candidate_delay_s`. At least as long as pause_delay, typically longer.

**ENDING → RUNNING:** Power rises (end spike or genuine resume). If current duration < expected duration (Phase 2), resume. If past expected, absorb the spike and stay in ENDING.

**ENDING → FINISHED:** Time below threshold ≥ `off_delay_s` AND energy in recent window ≤ `end_energy_wh_gate`. Both gates required.

**ENDING → INTERRUPTED:** Same transition path, but total cycle duration < `completion_min_s`. Gets flagged as interrupted rather than finished.

### Dynamic Pause/End Thresholds

The pause threshold and end-candidate threshold are calibrated to the sensor's actual update cadence. The ZEN15 reports on change with a periodic fallback — if reports come every 30s, a 15s pause threshold would be meaningless (we'd never see it). The thresholds are:

```
pause_delay_s = max(configured_min, 3 × p95_update_interval)
end_candidate_delay_s = max(pause_delay_s + 15s, configured_min_end)
```

`p95_update_interval` is tracked as a rolling statistic from the last 20 received readings. This means if the ZEN15 is reporting every 30s, the pause threshold auto-adjusts to ~90s minimum.

---

## Per-Appliance Configuration

Three instances of the same flow, each with its own config profile stored in flow context.

### Washing Machine

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `start_threshold_w` | 2.0 W | Any real motor draw |
| `stop_threshold_w` | 2.0 W | Symmetric hysteresis |
| `start_duration_s` | 5 s | Debounce |
| `start_energy_wh` | 0.2 Wh | ~50W for 15s or 200W for 3s |
| `pause_delay_s` | 90 s (min) | Rinse gap tolerance |
| `end_candidate_delay_s` | 180 s (min) | |
| `off_delay_s` | 300 s | 5 min after energy gate passes |
| `end_energy_wh_gate` | 0.05 Wh | Effectively zero |
| `min_off_gap_s` | 480 s | 8 min — soak cycle handling |
| `completion_min_s` | 600 s | 10 min to be a valid cycle |
| `interrupted_min_s` | 150 s | < 2.5 min = interrupted |

### Dryer

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `start_threshold_w` | 5.0 W | Higher — element draws hard |
| `stop_threshold_w` | 3.0 W | Cool-down still has motor |
| `start_duration_s` | 5 s | |
| `start_energy_wh` | 0.5 Wh | Element kicks in immediately |
| `pause_delay_s` | 60 s (min) | Thermostat cycling is fast |
| `end_candidate_delay_s` | 120 s (min) | |
| `off_delay_s` | 300 s | 5 min — cool-down phase |
| `end_energy_wh_gate` | 0.05 Wh | |
| `min_off_gap_s` | 300 s | 5 min |
| `completion_min_s` | 600 s | |
| `interrupted_min_s` | 150 s | |

**Dryer-specific note:** The stop_threshold_w is set above zero because the drum motor runs throughout cool-down at ~200-300W. The energy gate is what actually confirms the cycle is done — the motor will eventually stop and power drops to true idle.

### Dishwasher

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `start_threshold_w` | 2.0 W | Pump/heater |
| `stop_threshold_w` | 2.0 W | |
| `start_duration_s` | 5 s | |
| `start_energy_wh` | 0.2 Wh | |
| `pause_delay_s` | 300 s (min) | Phase gaps can be long |
| `end_candidate_delay_s` | 600 s (min) | Air-dry is 20+ min of zero |
| `off_delay_s` | 1800 s | 30 min — handles air-dry |
| `end_energy_wh_gate` | 0.05 Wh | |
| `min_off_gap_s` | 2000 s | ~33 min — drying pauses |
| `completion_min_s` | 900 s | 15 min |
| `interrupted_min_s` | 150 s | |

**Dishwasher-specific note:** The `off_delay_s` of 30 minutes is not a bug — it's the only way to reliably handle air-dry mode without profile matching. Phase 2 (profile matching) will allow smart termination much earlier once the machine's expected duration is known.

---

## Energy Gate Logic

The energy gate is implemented in a Function node via trapezoidal integration:

```javascript
// Compute Wh from an array of {t (ms epoch), p (Watts)} samples
function integrateWh(samples) {
    if (samples.length < 2) return 0;
    let wh = 0;
    for (let i = 1; i < samples.length; i++) {
        const dt_hours = (samples[i].t - samples[i-1].t) / 3_600_000;
        const avg_p = (samples[i].p + samples[i-1].p) / 2;
        wh += avg_p * dt_hours;
    }
    return wh;
}
```

**End energy gate:** At the ENDING transition check, compute energy over the window covering the last `off_delay_s` seconds of recorded samples. If that energy ≤ `end_energy_wh_gate`, the cycle is done. If it exceeds the gate (meaning real power drew in that window), hold in ENDING.

**Start energy gate:** Accumulated from the first reading above threshold until `start_energy_wh` is reached. Resets on false-start abort.

---

## MQTT Topics

Following the `highland/` namespace conventions.

### State Topics (Retained)

**`highland/state/appliance/{appliance}/cycle`** ← RETAINED

Current cycle state for one appliance. Published on every meaningful state change.

`{appliance}` values: `washing_machine` | `dryer` | `dishwasher`

```json
{
  "timestamp": "2026-03-11T14:30:00Z",
  "source": "appliance_monitor",
  "appliance": "washing_machine",
  "state": "running",
  "cycle_start": "2026-03-11T14:15:00Z",
  "duration_s": 900,
  "energy_wh": 42.3,
  "power_w": 387.2,
  "max_power_w": 1820.0,
  "matched_profile": null,
  "estimated_remaining_s": null
}
```

`state` values: `off` | `starting` | `running` | `paused` | `ending` | `finished` | `interrupted` | `force_stopped`

`matched_profile` and `estimated_remaining_s` are null until Phase 2 profile matching is implemented.

### Event Topics (Not Retained)

**`highland/event/appliance/{appliance}/cycle_started`**

```json
{
  "timestamp": "2026-03-11T14:15:00Z",
  "source": "appliance_monitor",
  "appliance": "washing_machine",
  "cycle_id": "wm_20260311_141500"
}
```

**`highland/event/appliance/{appliance}/cycle_finished`**

```json
{
  "timestamp": "2026-03-11T15:02:00Z",
  "source": "appliance_monitor",
  "appliance": "washing_machine",
  "cycle_id": "wm_20260311_141500",
  "status": "completed",
  "duration_s": 2820,
  "energy_wh": 187.4,
  "max_power_w": 1820.0,
  "matched_profile": null
}
```

`status` values: `completed` | `interrupted` | `force_stopped`

**`highland/event/appliance/{appliance}/cycle_interrupted`**

Same payload as `cycle_finished`, fired when status = `interrupted`. Separate topic so consumers can subscribe only to what they care about.

### Command Topics

**`highland/command/appliance/{appliance}/force_end`**

Force the current cycle to end immediately (user says "it's done, stop waiting").

```json
{
  "timestamp": "2026-03-11T15:05:00Z",
  "source": "ha_assist"
}
```

**`highland/command/appliance/{appliance}/reset`**

Force state machine to OFF. Use if stuck in a bad state.

---

## HA Entities via MQTT Discovery

Node-RED registers these entities on startup for each appliance. Three device groups, one per appliance.

### Per-Appliance Entities

Using `washing_machine` as the example — repeat pattern for `dryer` and `dishwasher`.

**Device group:** `highland_washing_machine`

| Entity | Type | State Topic Field | Notes |
|--------|------|-------------------|-------|
| Cycle State | `sensor` | `state` | Values: off/starting/running/paused/ending/finished/interrupted |
| Cycle Duration | `sensor` | `duration_s` | Unit: `s`, device_class: `duration` |
| Cycle Energy | `sensor` | `energy_wh` | Unit: `Wh`, device_class: `energy` |
| Current Power | `sensor` | `power_w` | Unit: `W`, device_class: `power` |
| Cycle Active | `binary_sensor` | derived from `state` | `on` when state not in {off, finished, interrupted, force_stopped} |
| Estimated Remaining | `sensor` | `estimated_remaining_s` | Phase 2; null until profile matching |

All state topics are `highland/state/appliance/{appliance}/cycle`.

**Binary sensor value_template example:**
```
{{ value_json.state not in ['off', 'finished', 'interrupted', 'force_stopped'] }}
```

Discovery configs are published by the Config Loader flow on Node-RED startup (retained, idempotent).

---

## PostgreSQL Schema

Cycle records written to PostgreSQL on cycle completion (finished or interrupted).

```sql
CREATE TABLE appliance_cycles (
    id              SERIAL PRIMARY KEY,
    cycle_id        TEXT NOT NULL UNIQUE,          -- e.g. wm_20260311_141500
    appliance       TEXT NOT NULL,                 -- washing_machine | dryer | dishwasher
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ NOT NULL,
    duration_s      INTEGER NOT NULL,
    energy_wh       NUMERIC(8,3) NOT NULL,
    max_power_w     NUMERIC(8,2) NOT NULL,
    status          TEXT NOT NULL,                 -- completed | interrupted | force_stopped
    termination_reason TEXT,                       -- timeout | smart | user | force_stopped
    matched_profile TEXT,                          -- Phase 2: profile name if matched
    match_confidence NUMERIC(4,3),                 -- Phase 2: 0.0–1.0
    power_trace     JSONB,                         -- Array of {t, p} samples (optional, for Phase 2 learning)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON appliance_cycles (appliance, started_at DESC);
CREATE INDEX ON appliance_cycles (appliance, status);
```

`power_trace` stores the raw power samples for the cycle. Initially nullable — enable storage when Phase 2 profile learning is implemented to avoid unbounded data growth.

---

## Node-RED Flow Architecture

One flow per appliance. Flows are structurally identical; only the config profile differs.

### Flow Structure

```
[MQTT In: zwave power topic]
        │
        ▼
[Function: Validate & Normalize]   ← strip bad readings, convert units if needed
        │
        ▼
[Function: State Machine]          ← the core; reads/writes flow context
        │
        ├──► [MQTT Out: highland/state/appliance/{a}/cycle]      retained
        │
        ├──► [MQTT Out: highland/event/appliance/{a}/cycle_*]    on transitions
        │
        └──► [Function: PostgreSQL Writer]  ← only on cycle completion
                    │
                    ▼
             [PostgreSQL node]

[MQTT In: highland/command/appliance/{a}/#]
        │
        ▼
[Function: Command Handler]        ← force_end, reset
        │
        ▼
[Link Out → State Machine]         ← injects command into state machine flow
```

### State Machine Function Node

The state machine lives in a single Function node with the full cycle context in `flow.get()`/`flow.set()`. It is the only writer to flow context for that appliance.

```javascript
// Pseudo-structure of the state machine Function node
const cfg = flow.get('config');          // appliance config profile
let st  = flow.get('state') || initState();

const power = msg.payload.power_w;
const now   = msg.payload.timestamp;     // ms epoch

// 1. Update cadence tracker (p95 of recent dt)
updateCadence(st, now);

// 2. Update time/energy accumulators
updateAccumulators(st, power, now, cfg);

// 3. State machine transitions
switch (st.state) {
    case 'off':       handleOff(st, power, now, cfg);       break;
    case 'starting':  handleStarting(st, power, now, cfg);  break;
    case 'running':   handleRunning(st, power, now, cfg);   break;
    case 'paused':    handlePaused(st, power, now, cfg);    break;
    case 'ending':    handleEnding(st, power, now, cfg);    break;
    // terminal states auto-expire to off after 30 min
    default:          handleTerminal(st, now);               break;
}

flow.set('state', st);

// 4. Build output messages
// msg[0] = retained state update (always)
// msg[1] = transition event (only on state change, null otherwise)
// msg[2] = cycle record (only on completion, null otherwise)
return [stateMsg(st), eventMsg(st), cycleRecord(st)];
```

### Context Schema

```javascript
// flow context key: 'state'
{
  // State machine
  state: 'running',          // current state
  state_enter_time: 1741700400000,   // ms epoch

  // Current cycle
  cycle_id: 'wm_20260311_141500',
  cycle_start: 1741699500000,        // ms epoch
  max_power_w: 1820.0,
  energy_wh: 42.3,                   // accumulated over cycle

  // Accumulators
  time_above_s: 0,
  time_below_s: 47.3,
  energy_since_idle_wh: 0,           // accumulated since last idle

  // Cadence tracking
  last_reading_time: 1741700400000,  // ms epoch
  recent_dts: [28.1, 30.4, 29.7],   // last 20 dt values (seconds)
  p95_dt: 30.4,                      // p95 of above

  // Last reading
  last_power_w: 1.2,

  // Power trace (for Phase 2 learning)
  samples: [{ t: 1741699500000, p: 0 }],  // capped at max_samples
}
```

### Config Profile Structure

```javascript
// flow context key: 'config' — set by Config Loader on startup
{
  appliance: 'washing_machine',
  start_threshold_w: 2.0,
  stop_threshold_w: 2.0,
  start_duration_s: 5,
  start_energy_wh: 0.2,
  pause_delay_s_min: 90,
  end_candidate_delay_s_min: 180,
  off_delay_s: 300,
  end_energy_wh_gate: 0.05,
  min_off_gap_s: 480,
  completion_min_s: 600,
  interrupted_min_s: 150,
  max_samples: 2000,           // cap on power_trace length
  zwave_topic: 'zwave/washing_machine/power',  // verified at bring-up
}
```

---

## Watchdog

If the ZEN15 stops reporting (sensor offline, Z-Wave JS UI restart), the state machine would be stuck in RUNNING indefinitely. A watchdog catches this:

- If state is `running` or `paused` and no reading has arrived in `no_update_timeout_s` (default: 600s), force-transition to `force_stopped`.
- Published as `status: "force_stopped"`, `termination_reason: "watchdog"`.
- Notification fired.

Implementation: inject node set to `no_update_timeout_s`, reset on every received reading.

---

## Notifications

Cycle events integrate with the Highland notification bus (`highland/event/notify`).

| Trigger | Default Recipients | DnD Override |
|---------|-------------------|--------------|
| Cycle finished | household | No |
| Cycle interrupted | household | No |
| Force stopped (watchdog) | household | Yes |

Notification messages pulled from `global.config` (configurable strings). Example:

```
"Washing machine finished — 47 min, 187 Wh"
"Dryer done — 62 min, 3.2 kWh"
"Dishwasher finished — 1h 23m"
```

The notification flow subscribes to `highland/event/appliance/+/cycle_finished` and formats the message from the event payload.

---

## Phase 2 — Profile Matching (Backlog)

Once the core state machine is proven stable across several weeks of actual cycles, Phase 2 adds:

**What it enables:**
- Identify which wash/dry program is running (quick wash vs. heavy duty, etc.)
- Smart termination: end the cycle early when duration matches a known profile, rather than waiting for the full off-delay
- Estimated time remaining entity in HA

**Approach:**
- Power traces stored in PostgreSQL (enable `power_trace` column)
- After N cycles, compute per-appliance "profile envelopes" (avg, min, max power curve) via DTW alignment
- Each new cycle matched against known envelopes via cross-correlation → DTW refinement
- Match confidence and expected duration fed back into the state machine's ENDING logic

**Implementation note:** DTW is CPU-bound and belongs in a Function node running plain JS (no numpy). A JS implementation of the Sakoe-Chiba constrained DTW used by ha_washdata is ~100 lines and entirely feasible. Profile matching runs on a timer (every 5 min during a cycle), not on every reading.

---

## Open Questions

- **ZEN15 MQTT topic paths** — exact paths from Z-Wave JS UI to be confirmed at bring-up. Update `config.zwave_topic` per appliance once verified.
- **ZEN15 report parameters** — configure per-node in Z-Wave JS UI: report threshold (W), minimum report interval, periodic fallback interval. Initial recommendation: 5W threshold, 10s min interval, 60s periodic. Tune per appliance after observing actual signatures.
- **Power trace storage** — decide whether to store traces from day one (for faster Phase 2) or enable later. Storage cost: ~2KB per cycle at 30s intervals for a 60-minute cycle. Probably worth enabling from the start.
- **Dryer cool-down handling** — the `stop_threshold_w` of 3W may need tuning. If the drum motor idles well above 3W during cool-down, the state machine will never enter PAUSED/ENDING until the motor fully stops. Observe actual signatures and adjust.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-03-11 | Initial design. Reverse-engineered from ha_washdata source. Adapted to Highland MQTT conventions and Node-RED architecture. |
