# Parking Assist — Design & Architecture

## Overview

Per-bay vehicle position indicator for the garage. An ultrasonic sensor mounted at the front of each bay measures the distance from the bay's front wall to the vehicle's nose, and a visual indicator (LED strip or industrial tower light) provides color-coded guidance to the driver across six distance bands. No dependency on HA for runtime logic; the sensor node drives the indicator directly from ESPHome. Distance and parking state are published to MQTT so Highland can consume them for other purposes.

**Design philosophy:** Keep the subsystem architecturally self-contained. The visual logic is "distance → color," which is simple enough that ESPHome owns both sensing and indicator driving on a single device per bay. The MQTT surface exposes bay occupancy as a consumer signal for other subsystems (garage door, security, analytics); it does not drive the visual itself.

**Bay naming.** Bays are numbered from the exterior approach, left to right. `bay_one` is the primary bay and the left bay when viewed from outside the house; `bay_two` is the right bay. All MQTT topics, HA entities, and ESPHome device names follow this convention, aligning with the garage door subsystem.

**Hardware approach is pending.** Two candidate visual indicators — an addressable LED strip and an industrial tower light — are both documented below with their respective tradeoffs and visualization mappings. The sensing, FSM, MQTT surface, and operating-window logic are shared between the two approaches. The hardware decision is captured as an Open Question.

---

## Implementation Status

📋 **Designed — not yet implemented.** Hardware sourcing and installation pending; indicator choice between LED strip and tower light still open. Installation paired with garage door subsystem commissioning is natural but not required.

---

## Hardware

One sensor node per bay. Hardware is identical across bays — only MQTT client ID and topic paths differ. The sensor-side of the hardware is fixed; the visual-indicator-side has two candidate approaches.

### Sensor Node Stack (shared across both indicator approaches)

| Component | M5 SKU | Purpose |
|-----------|--------|---------|
| M5Stack AtomS3 Lite | C008 | ESP32-S3 controller with onboard programmable button |
| M5Stack Atomic RS485 Base | A126 | 12V → 5V DC-DC step-down, terminal block input, enclosure; stacks under Atom |
| M5Stack Ultrasonic Distance Unit I2C | U098-B1 | RCWL-9620 ultrasonic ranging sensor, Grove HY2.0-4P connector |

**Per-node stack form factor:** ~24 × 24 × 20–25 mm (AtomS3 Lite + RS485 Base). Ultrasonic Unit tethers via the included 20 cm Grove cable, positioned independently from the stack.

**Rationale for sharing the stair sensor pattern:**

- Same Atom variant, same Base, same Grove-sensor pattern — inventory reuse across Highland's ESPHome sensor subsystems
- Zero breadboarding; assembly is pin-header stack + Grove cable
- RS485 Base's built-in 12V → 5V regulator is convenient even though we don't need its namesake RS485 functionality
- AtomS3 Lite chosen over Atom Lite for the ESP32-S3's larger flash and the programmable button (see Button Function below)

### Ultrasonic Sensor — Unit U098-B1

| Spec | Value |
|------|-------|
| Chip | RCWL-9620 |
| Interface | I²C (address 0x57), Grove HY2.0-4P |
| Range | 2 cm – 450 cm |
| Accuracy | ±2% |
| Probe diameter | 16 mm |
| Detection angle | ~20° directional cone (wider effective detection in practice) |
| Temperature compensation | Built-in (reduces probe thermal drift) |

**Why ultrasonic over ToF here.** The stair sensor uses a ToF (VL53L0X) because narrow-beam fast-response rangefinding suits stair traversal detection. Parking assist has different requirements that favor ultrasonic:

- **Target is highly reflective.** Car bumpers, chrome badges, glossy paint, and license plates scatter laser ToF unpredictably at grazing angles. Ultrasonic is indifferent to target reflectivity — sound bounces equally well off any surface.
- **Target is large.** An approaching vehicle fills the beam cone well at range; ultrasonic's wider beam is a feature, not a bug.
- **Speed is low.** A car pulling into a garage moves at walking pace. The RCWL-9620's ~10 Hz sample rate is comfortably adequate.
- **Distance range is longer.** Car-to-front-wall distances span 0–3 m in most garages; the RCWL-9620's 4.5 m range fits comfortably.

The I²C variant (U098-B1) is preferred over the GPIO variant (U098-B2) for bus-sharing flexibility — if a future enhancement adds a second sensor per bay (e.g., a second ultrasonic at a different angle, an environmental sensor), it can daisy-chain on the same Grove bus without pin conflicts.

### Indicator Candidates

Two approaches are on the table. The sensing, FSM, MQTT, and operating-window logic are identical between them; only the physical indicator and its driver circuit differ.

#### Candidate A — Addressable LED Strip

| Spec | Value |
|------|-------|
| Type | WS2812B (or WS2811) 12V addressable RGB strip |
| Length | 0.5 m |
| Pixel count | ~30 LEDs at 60/m density, or ~15 at 30/m |
| Mounting | Aluminum channel with frosted diffuser, short section |

**Choice rationale:** Standard SMD WS2812B is adequate at this scale — the strip is viewed from ~6+ feet away, and the dot-visibility concern that drove the stair subsystem's RGB IC FCOB choice does not apply at this distance. Cost is meaningfully lower and availability is ubiquitous.

**Driver circuit:** AtomS3 Lite drives the strip's data input directly from GPIO via a short jumper. No signal conditioning required at this length.

**Pros:**

- More visual presence along the mounting surface
- Architectural aesthetic — blends with the bay's visual space rather than announcing itself
- Full RGB addressable surface enables future reuse as a general-purpose indicator for non-parking events
- Flexible mounting (front wall, ceiling, joist) depending on driver sightline

**Cons:**

- More complex wiring (power + data, diffuser channel, strip mounting)
- ESPHome FastLED/NeoPixel complexity vs. raw GPIO
- Per-pixel addressability is overkill for the six-band discrete state model
- Higher worst-case power draw (~10W)

#### Candidate B — Industrial Tower Light

**Product reference:** [Adafruit 2993 — Tower Light with Buzzer, 12VDC](https://www.adafruit.com/product/2993) or equivalent red/yellow/green stacked tower light.

| Spec | Value |
|------|-------|
| Lamps | Red, yellow, green (three discrete stacked segments) |
| Buzzer | Integrated, independently switchable |
| Input | 12VDC, common +12V with individual ground-switched control per lamp/buzzer |
| Dimensions | 340 mm tall × 50 mm diameter |
| Mounting | Integrated bracket, 180° rotation range |

**Driver circuit:** Common +12V to the tower. Each lamp wire and the buzzer wire are switched to ground via a small 4-channel N-channel MOSFET breakout (Adafruit or generic), driven from AtomS3 Lite GPIO at 3.3V. No PWM required — simple on/off per channel. Flashing patterns implemented in ESPHome software via GPIO toggle.

**Pros:**

- Unambiguous industrial-status-light vocabulary — familiar to anyone who has driven through an automated car wash (same color semantics, same visual pattern)
- Buzzer provides a genuinely useful audible escalation for the `overshot` state that the strip cannot match without adding a separate speaker
- Dramatically simpler wiring — three GPIO lines through MOSFETs, no addressable-LED complexity, no diffuser channel, no pixel-level driver
- Much higher brightness output per lamp — designed for factory-floor visibility
- Lower power draw (~1W per lit lamp)
- Single mounting decision (wall bracket, 180° tilt), no per-bay sightline judgment

**Cons:**

- Industrial aesthetic — some users will find this visually disruptive in a residential garage
- Only three discrete colors available — no gradient or per-pixel expression
- Cannot have yellow and red lit simultaneously (product constraint)
- Limited reuse potential as a general-purpose indicator (three colors + buzzer vs. the strip's full RGB addressable surface)
- ~2x the cost per bay

**Both candidates use the same sensor node, the same ESPHome firmware architecture, the same FSM, and the same MQTT surface.** The decision comes down to aesthetic preference and whether the buzzer's value on the `overshot` state tips the balance. See Open Questions for the hardware decision.

### Power Supply

Single 12V/2A wall-wart per bay (or per garage if locations permit sharing). Meanwell LRS-35-12 or equivalent enclosed supply recommended for reliability; generic barrel-jack wall-warts are acceptable.

**Power budget per bay (worst case):**

| Component | Candidate A (strip) | Candidate B (tower) |
|-----------|---------------------|---------------------|
| Indicator | ~9 W (strip at full brightness) | ~1 W (one lamp lit) |
| AtomS3 Lite + RS485 Base | ~1 W | ~1 W |
| Ultrasonic Unit | negligible | negligible |
| MOSFET breakout (tower only) | — | negligible |
| **Total worst case** | **~10 W** | **~2 W** |

A 12V/2A (24 W) supply comfortably handles either approach with headroom. The tower light approach is significantly more power-efficient but the strip approach is still well within supply capacity.

**No long DC distribution run.** The supply lives near the sensor assembly, short leads to the indicator and the RS485 Base's terminal block.

---

## Physical Installation

### Sensor Placement

**Location:** Front wall of each garage bay, at approximate vehicle bumper height (18–24 inches above the floor, depending on the vehicles parked in each bay).

**Aim:** Horizontal, perpendicular to the front wall, into the bay.

**Why bumper height rather than ceiling-mounted and angled down:**

- Ultrasonic performs best with the target surface approximately perpendicular to the beam axis. A bumper is roughly vertical when the car is parked; aiming horizontally gives a clean perpendicular return.
- Hood-height or ceiling-mounted angled-down configurations hit the hood at shallow angles, which are acoustically less forgiving than bumper hits.
- Matte-plastic bumpers give more consistent returns than glossy hoods across the mix of vehicles likely to park in a given bay.

**Stored items consideration.** Any items stored between the sensor and the vehicle's parked position will register as the "closest target" and defeat the parking assist. The sensor is dumb to what it's looking at. Mitigate by placing the sensor at a height where stored items (bikes on hooks, tool chests, shelving) are above or below the beam path.

### Indicator Placement

**If using the LED strip (Candidate A):** In the driver's forward line of sight when parking. Candidates include:

- Mounted on the front wall above or alongside the sensor (the "obvious" placement — visible through the windshield)
- Mounted on the ceiling just above the front wall (good for trucks/SUVs where the windshield view of the front wall is obstructed by the hood)
- Mounted on a joist or beam in the bay's upper volume

Pick per-bay based on the specific vehicle's sightlines. Channel is a standard aluminum LED channel, 0.5 m length, with frosted diffuser — a single off-the-shelf section per bay, no splicing.

**If using the tower light (Candidate B):** Mount on the front wall at a height where the top of the tower is at or slightly above the driver's natural eye level when seated in the vehicle. The tower's 180° tilt mount allows fine-tuning the angle so all three lamps are visible through the windshield at the parked position. Mounting location is less sightline-sensitive than the strip because the tower's brightness is high enough to be visible across a wide viewing angle.

### Enclosure and Wiring

**Sensor assembly enclosure:** A small project box (~50 × 50 × 30 mm internal) houses the Atom + RS485 Base stack. For the tower light candidate, the MOSFET breakout also lives in this enclosure. Cable exits for Grove cable to the Ultrasonic Unit, 12V input, and indicator output lines.

**Ultrasonic Unit mounting:** The Unit itself ships in its own small enclosure. Mount it directly to the front wall with a hole sized to expose the 16 mm probe face. The 20 cm Grove cable connects it back to the Atom stack in the enclosure.

**Wiring — Candidate A (LED strip):**

```
12V wall-wart
        │
        ├───────────────────────► WS2812B strip (+12V, GND)
        │
        └───────────────────────► RS485 Base terminal block (+12V, GND)
                                         │
                                 AtomS3 Lite (via pin header)
                                     │    │
                                     │    └── GPIO data ──► WS2812B strip (DIN)
                                     │
                                     └── Grove I²C ──► Ultrasonic Unit U098-B1
```

**Wiring — Candidate B (tower light):**

```
12V wall-wart
        │
        ├───────────────────────► Tower light (+12V common)
        │
        └───────────────────────► RS485 Base terminal block (+12V, GND)
                                         │
                                 AtomS3 Lite (via pin header)
                                     │    │
                                     │    └── 4× GPIO ──► 4-channel MOSFET board
                                     │                         │
                                     │                         ├── Red lamp GND switch
                                     │                         ├── Yellow lamp GND switch
                                     │                         ├── Green lamp GND switch
                                     │                         └── Buzzer GND switch
                                     │
                                     └── Grove I²C ──► Ultrasonic Unit U098-B1
```

**No signal conditioning required in either case.** Short runs between controller and indicator, no level-shifting concerns.

**Cable management:** Wall-wart plugs into the nearest outlet; all wiring is per-bay local — no long runs through walls or ceilings.

---

## Architecture

```
Ultrasonic Unit (I²C)
        │
        ▼
  AtomS3 Lite (ESPHome)
   │         │         ▲
   │         ▼         │
   │    Distance band FSM ──► Indicator driver (strip GPIO or MOSFET GPIOs)
   │    + active window gate    (fires only when door open
   │    (door state + target)    AND target within 300 cm)
   │         ▲
   │         │
   │    highland/state/garage/bay_{N}/door_state (subscribed)
   │
   ▼
  MQTT publishers
   ├─► highland/state/garage/bay_{N}/vehicle_distance_cm
   ├─► highland/state/garage/bay_{N}/parking_state
   ├─► highland/status/sensor/garage_bay_{N}/parking  (LWT)
   └─► highland/event/garage/bay_{N}/parking_calibrate  (button)
```

**Layer responsibilities:**

| Layer | Owns |
|-------|------|
| Ultrasonic Unit | Raw distance measurement |
| AtomS3 Lite (ESPHome) | Sample rate control, active-window gating (door state + target presence), distance-to-state FSM, direct indicator driving, MQTT publishing |
| Node-RED | Subscribes to state topics for downstream automations (garage door integration, analytics); does **not** drive the indicator |
| HA | Dashboard display only — current distance, parking state, bay occupancy |

**Why ESPHome owns the indicator directly.** The visual logic is a pure function of current distance: measure, classify, set state. No animation state, no time-of-day variation, no preset catalog, no cross-device coordination. Running that through MQTT to a separate controller (WLED or otherwise) adds latency and a moving part for no gain.

---

## Distance Band Logic (Hardware-Agnostic)

The FSM classifies the current ultrasonic reading into one of six discrete states. The state model is shared between both candidate indicators; only the visualization of each state differs. Bands are configurable per bay in the ESPHome YAML. Bands are evaluated on each sample; the resulting state drives the indicator.

### The Six States

**Provisional bands (tune per bay at commissioning):**

| Distance from front wall | `parking_state` | Meaning |
|--------------------------|-----------------|---------|
| > 300 cm | `clear` | No vehicle detected |
| 300 → 50 cm | `approaching` | Pull forward — plenty of room |
| 50 → 20 cm | `close` | Getting close, pay attention |
| 20 → 10 cm | `very_close` | Slow down, almost there |
| 10 → 5 cm | `parked` | Stop — correct position |
| < 5 cm | `overshot` | Back up, you've gone past the mark |

**Rationale for six bands over five.** An earlier draft used five bands, combining what are now `close` and `very_close`. The split matters because the discrete-indicator approach (tower light) benefits from a visually distinct "slow down" signal between "getting close" and "at the mark." The strip could have done without the split, but the hardware-agnostic design benefits from having both candidates work from the same state model.

**The `parked` band is the target state.** Red on the tower light (or the target color on the strip) indicates correct positioning. This mirrors the color semantics of automated car washes — a familiar visual vocabulary that drivers already understand without training.

**`overshot` is the error state.** Flashing indicator and (on the tower light) the buzzer. This only triggers when the vehicle has gone past the correct parked position.

### Hysteresis

Each band boundary has a small hysteresis margin (provisional: 5 cm for the larger bands, 2 cm for the narrower ones near the parked position) to prevent flapping when a vehicle is oscillating at a threshold. State transitions only occur when the distance crosses a threshold plus the hysteresis margin.

### Sampling Rate

ESPHome polls the Ultrasonic Unit at 10 Hz. Indicator updates at the same cadence. This is fast enough to feel responsive without being wasteful — a vehicle moving at 1 m/s closes 10 cm per sample, which is well under the narrowest distance band.

---

## Visualization

The six states map to the indicator differently depending on which candidate hardware is selected. Both mappings are documented so the decision can be made cleanly at commissioning.

### Candidate A — LED Strip Mapping

| State | Strip behavior |
|-------|---------------|
| `clear` | Off (or dim white at ~10% as optional "system alive" indicator) |
| `approaching` | Green, solid |
| `close` | Yellow, solid |
| `very_close` | Yellow, flashing (2 Hz) |
| `parked` | Red, solid |
| `overshot` | Red, flashing (2 Hz) — no buzzer available |

The strip's RGB addressability permits future expansion: gradient transitions between bands, per-pixel effects in the `very_close` band, a "bounce" pattern on `overshot`, etc. Phase 1 uses the simple solid-and-flash pattern above; expression beyond that is Phase 2+ territory.

### Candidate B — Tower Light Mapping

| State | Tower behavior |
|-------|---------------|
| `clear` | All lamps off |
| `approaching` | Green, solid |
| `close` | Yellow, solid |
| `very_close` | Yellow, flashing (2 Hz) |
| `parked` | Red, solid |
| `overshot` | Red, flashing (2 Hz) + buzzer (short burst, ~200 ms on / 800 ms off) |

The tower light constraint that yellow and red cannot be lit simultaneously does not affect this mapping — the six states are mutually exclusive and the mapping only ever requests one lamp at a time. The buzzer on `overshot` is the meaningful differentiator from the strip approach and is a significant value-add for the error-state signaling.

### Color Semantics (Both Candidates)

The color progression intentionally mirrors automated car wash indicator lights:

- **Green** = continue forward, action required
- **Yellow** = approaching stop position, slow down
- **Flashing yellow** = very close, prepare to stop
- **Red** = stop, you are correctly positioned
- **Flashing red** = you have gone too far, back up

Drivers will find this intuitive without explanation. The semantic that "red = correctly parked" is unusual outside of this specific domain, but the prior progression through green → yellow → flashing yellow establishes the context, and the flashing-red-plus-buzzer error state keeps the "red as danger" instinct meaningful when it actually applies.

---

## Active Operating Window

The indicator is not continuously active. It illuminates only during active parking scenarios, defined by a combination of garage door state and vehicle motion.

### Activation

The indicator begins responding to state transitions when **both** of the following are true:

1. The corresponding garage door is `open` (consumed from the garage door subsystem's state topic).
2. The ultrasonic sensor reports a target within the `approaching` band or closer (distance ≤ 300 cm).

Door open without a target present means no one is parking — indicator stays off. Target present without the door open is a state that shouldn't happen in normal operation; if it does (object stored in the beam path while the door is closed), the indicator still stays off because the door gate is not satisfied.

**No time-of-day gating.** The garage has one small window and otherwise relies on the open door for ambient light. The indicator is readily visible at any time of day when the door is open, so a schedule or lux gate adds no value.

### Deactivation (Parked Cooldown)

Once the vehicle reaches a stable position — distance reading stationary within a tolerance of ±2 cm for `parked_stable_seconds` (provisional: 10 s) — the system enters a cooldown phase. The indicator continues to display the current state for `parked_cooldown_seconds` (provisional: 30 s), then fades (strip) or extinguishes (tower light).

This handles the realistic end-of-parking sequence: driver stops at their preferred position, puts the vehicle in park, indicator confirms `parked` for a beat, then turns off. If the driver adjusts mid-cooldown (see Reverse Motion below), the stable-position timer resets and the cooldown is cancelled.

**Timer parameters (provisional):**

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `parked_stable_seconds` | 10 s | Duration of stationary distance reading (±2 cm) required to declare "parked" |
| `parked_cooldown_seconds` | 30 s | Duration after stable declaration before indicator turns off |
| `fade_seconds` | 1 s | Fade-out duration (strip only; tower light extinguishes instantly) |

All parameters are ESPHome YAML values, adjustable without firmware infrastructure changes.

### Reverse Motion

When the vehicle exits the bay (distance reading increases across band boundaries), the indicator runs the state progression in reverse — red during close proximity, transitioning through yellow to green as the vehicle moves away. This is factually accurate even though it is not informationally useful during exit.

**Reverse cascade is kept deliberately.** Two reasons:

1. **Minor in-parking adjustments require it.** A driver who has just parked and wants to reverse a few inches to center themselves or create more clearance is effectively re-approaching backwards. The indicator should remain lit and informative during those adjustments, which means it must stay active during reverse motion.
2. **Door-gated activation already bounds it.** The indicator only runs when the door is open. A full exit coincides with the door being open, so the reverse cascade plays during departure. This is a minor aesthetic oddity, not a functional problem — the signals are still factually correct about distance.

Building dedicated "approach-only" logic would introduce corner cases around the in-parking-adjustment scenario without meaningful benefit. The decision is to accept the reverse cascade as-is.

### Future: Indicator as General-Purpose Signal

Future enhancements may repurpose the indicator as a passive signal for other house events when the garage door is closed — notification relay for events elsewhere in the house, weather alerts, etc. Nothing concrete is planned for Phase 1; the indicator is dark whenever the door is closed for parking-assist purposes. If other consumers want to drive it at other times, they would publish to a dedicated command topic rather than affecting the parking-assist logic itself.

**Repurposing favors the strip.** The LED strip's full RGB addressability makes it a more capable general-purpose indicator than the tower light's three-color-plus-buzzer vocabulary. If general-purpose reuse is a weighted consideration, the strip wins; if it's speculative and not likely to materialize, this factor doesn't change the decision.

---

## Indicator States (High-Level)

Beyond the distance-band state mapping, the indicator has a few higher-level states worth calling out explicitly:

| Indicator State | Trigger | Behavior |
|-----------------|---------|----------|
| **Inactive** | Door closed, OR door open with no target within 300 cm | Indicator fully off |
| **Active** | Door open, target within 300 cm | Indicator displays per current distance band |
| **Cooldown** | Stable-parked timer elapsed | Indicator continues displaying current state; turn-off pending |
| **Fading / Off** | Cooldown timer elapsed | Strip fades over 1 s; tower light extinguishes instantly |

State transitions are evaluated on each 10 Hz sample. Timers reset whenever distance changes more than 2 cm between samples.

### Power-On Behavior

On ESPHome boot, the indicator initializes to **off** and remains off until the first valid ultrasonic reading plus the activation conditions are evaluated. Between power-on and first sample (~100 ms), no output occurs. This prevents any visual flicker or garbage output during the controller's initial I²C handshake with the Ultrasonic Unit.

### Wi-Fi Disconnect Behavior

If the AtomS3 Lite loses Wi-Fi, the local distance-band logic continues running — ESPHome executes local automations independently of network state. The indicator responds to distance bands as normal; the only impact is that MQTT publishing pauses during the disconnect. On reconnection, retained state topics are published to catch up any consumers.

Door state consumption requires Wi-Fi to be functioning; if disconnected, the indicator cannot gate on door state and defaults to displaying distance bands whenever a target is present. This is acceptable degradation — parking assist still works locally, the only missing behavior is the door-gated suppression.

---

## Button Function

The AtomS3 Lite's onboard programmable button is assigned a single function: **recalibrate the `parked` band to the current distance reading.**

**Workflow:**

1. Driver parks the vehicle at their preferred position.
2. Driver exits the vehicle and walks to the sensor assembly.
3. Driver presses the button on the AtomS3 Lite.
4. ESPHome reads the current distance, sets it as the center of the `parked` band (with the band extending symmetrically around it per the current width), and persists the value to flash.
5. ESPHome publishes `highland/event/garage/bay_{N}/parking_calibrate` with the new band values for logging.

The other bands (`approaching`, `close`, `very_close`, `overshot`) shift relative to the new `parked` center, preserving their widths.

**Why put this on the button rather than in a dashboard.** The physical button at the sensor lets the driver calibrate while the car is actually in the parked position — no need to access a dashboard, no trip back to the car to verify alignment. One physical action, one step. HA dashboard exposure of calibration is still possible as a Phase 2 enhancement if the button workflow proves awkward, but the button is the primary UX.

**Safety against accidental presses.** The button is on the AtomS3 Lite's face inside the sensor enclosure — not easily hit by accident. If the enclosure's mounting exposes the button, a long-press (~3 s) requirement can be added in ESPHome to prevent brush-bys from triggering recalibration.

---

## MQTT Topics

### State Topics (Retained)

All state topics publish JSON per Highland's MQTT conventions.

| Topic | Summary |
|-------|---------|
| `highland/state/garage/bay_{N}/vehicle_distance_cm` | Current measured distance in centimeters, with timestamp |
| `highland/state/garage/bay_{N}/parking_state` | Categorical parking state with timestamp and band boundaries |

**`highland/state/garage/bay_{N}/vehicle_distance_cm`**

```json
{
    "distance_cm": 47.3,
    "since": "2026-04-21T14:32:18Z"
}
```

- `distance_cm`: current distance reading, centimeters, one decimal place
- `since`: ISO8601 UTC timestamp of the reading

**`highland/state/garage/bay_{N}/parking_state`**

```json
{
    "state": "close",
    "distance_cm": 47.3,
    "since": "2026-04-21T14:32:18Z",
    "bands": {
        "parked_center_cm": 7.5,
        "parked_half_width_cm": 2.5
    }
}
```

- `state`: categorical (`clear` | `approaching` | `close` | `very_close` | `parked` | `overshot`)
- `distance_cm`: distance at the moment of the latest state transition (may be slightly stale compared to the `vehicle_distance_cm` topic)
- `since`: ISO8601 UTC timestamp of when this state was entered
- `bands.parked_center_cm`: current calibrated `parked` band center (for consumer reference / dashboard display)
- `bands.parked_half_width_cm`: half-width of the `parked` band

### Event Topics (Not Retained)

| Topic | Fires on |
|-------|----------|
| `highland/event/garage/bay_{N}/parking_calibrate` | Button press — new `parked` center committed |

**Payload:**

```json
{
    "parked_center_cm": 7.5,
    "parked_half_width_cm": 2.5,
    "since": "2026-04-21T14:32:18Z"
}
```

### Status Topics (LWT-Retained)

| Topic | Source | Payload |
|-------|--------|---------|
| `highland/status/sensor/garage_bay_{N}/parking` | AtomS3 Lite | `online` \| `offline` (LWT) |

No occlusion topic here — the ultrasonic sensor's failure modes are different from ToF (dead sensor rather than stuck-high), and the existing Highland device-monitoring flow's LWT-based liveness check is sufficient.

---

## HA Integration

HA exposes read-only dashboard entities via MQTT Discovery published by ESPHome (native ESPHome Discovery, not Node-RED-republished — the device is owned by the AtomS3 Lite firmware, not by Node-RED logic).

| Entity | Type | Purpose |
|--------|------|---------|
| `sensor.garage_bay_{N}_vehicle_distance` | `sensor` | Live distance in cm (numeric) |
| `sensor.garage_bay_{N}_parking_state` | `sensor` | Categorical state string |

No controllable entities in HA. Calibration is via the physical button; no "force state" override exists (and none is likely wanted — the vehicle's actual position is the ground truth, and overriding it serves no user purpose).

---

## Cross-Subsystem Integration

The `parking_state` signal is primarily consumed by Highland's garage door subsystem (and potentially future automations) rather than by the parking assist itself.

**Suggested future consumers (not Phase 1):**

- **Garage door:** Notify if door is open and bay is `overshot` for more than N seconds (probably someone hit the wall; worth verifying).
- **Garage door:** Notify if door is closed but bay is `clear` unexpectedly (car was removed without door operation — e.g., forgot, or unauthorized).
- **Security / away mode:** Cross-reference `parking_state` with away mode to confirm all vehicles are present when house is supposed to be occupied.
- **Daily Digest:** Summary of bay occupancy patterns if that proves interesting.

These are captured here for awareness. Implementation belongs in the consuming subsystem's flow, not in parking assist itself.

---

## Phase Plan

### Phase 1 — Core Subsystem (This Design)

- One AtomS3 Lite + RS485 Base + U098-B1 sensor node per bay
- One indicator per bay — either 0.5 m WS2812B strip (Candidate A) or tower light with MOSFET breakout (Candidate B); decision pending
- 12V/2A wall-wart per bay
- ESPHome firmware with six-state distance-band FSM and MQTT publishing
- Button-based calibration
- HA Discovery read-only sensor entities
- MQTT state topics for cross-subsystem consumption

### Phase 2 — Enhancements

- **Garage door integration** — wire `parking_state` into garage door notification logic (overshot alerts, unexpected absence alerts).
- **Dashboard calibration override** — HA number entity to directly set `parked_center_cm` if the physical button workflow proves awkward.
- **Temperature-compensated range** — pull garage ambient temp from an existing sensor (if one's added) into the ESPHome YAML for slightly better accuracy across seasonal temp swings. The RCWL-9620 already has built-in temperature compensation via an onboard thermistor; this is refinement, not necessity.
- **Strip general-purpose reuse** (Candidate A only) — command topic for driving the strip as a notification indicator for non-parking events when the door is closed.

### Phase 3 — Speculative

- **Vehicle identification.** If bay assignments are stable, no work needed. If vehicles swap bays, vehicle-specific `parked_center_cm` values could be supported — but that requires knowing which vehicle is in which bay, which is a meaningfully harder problem (license plate recognition, Bluetooth beacons, driver-initiated selection). Likely not worth the complexity for a residential garage.

---

## Open Questions

- [ ] **Indicator hardware decision: LED strip (Candidate A) vs. tower light (Candidate B).** The buzzer's value on `overshot`, aesthetic fit with the garage, and general-purpose-reuse potential are the main decision factors.
- [ ] Confirm bumper-height mounting clears stored garage items (bikes, shelving, tool chests) in both bays — may require repositioning clutter prior to install
- [ ] Calibrate initial `parked` bands per vehicle at commissioning; document per-bay values
- [ ] Determine whether long-press (~3 s) is needed on the button to guard against accidental calibration, or whether enclosure placement is sufficient
- [ ] Decide ESPHome `light` component specifics for the strip (FastLED platform, data GPIO choice, power-on behavior) — Candidate A only
- [ ] Source the 4-channel MOSFET breakout for driving the tower light — Candidate B only
- [ ] Validate RCWL-9620 reading stability against matte-plastic bumpers vs. chrome-heavy front ends (e.g., older trucks) at bench or first-install — confirm ±2% accuracy spec holds in practice
- [ ] Decide whether to share a single wall-wart across both bays (if electrically convenient) or use one per bay (simpler, more resilient to failures)
- [ ] Tune `parked_stable_seconds` and `parked_cooldown_seconds` values against observed parking behavior (first-install)
- [ ] Confirm garage door state topic path matches what the garage door subsystem actually publishes at integration time
- [ ] Decide whether the `clear` band should show any "system alive" indication (dim white on strip, brief startup blink on tower, or fully dark)
- [ ] Tune `overshot` buzzer pattern (duty cycle, duration) to be clearly attention-grabbing without being painful — Candidate B only

---

*Last Updated: 2026-04-21*
