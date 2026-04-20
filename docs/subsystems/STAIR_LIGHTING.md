# Stair Lighting — Design & Architecture

## Overview

Continuous wall-side LED accent lighting along the 14-step main staircase. Motion-triggered, with the FSM inferring direction of travel for state tracking. Active window gated by a combination of solar schedule and outdoor lux override. No dependency on HA for runtime logic — HA is dashboard-only.

**Design philosophy:** Node-RED owns decisions (mode, active window, traversal FSM). WLED executes choreography via a preset catalog. ToF sensor nodes publish motion events. Each layer has one job.

---

## Implementation Status

✅ **Designed — not yet implemented.** Hardware partially on hand (WLED controller). Remaining BOM to be acquired before build.

---

## Hardware

### WLED Controller — GLEDOPTO GL-C-015WL-D

ESP32-based WLED controller. FCC Part 15 compliant, flame-retardant PC enclosure, rated indoor-only.

| Spec | Value |
|------|-------|
| Input voltage | DC 5–24V |
| Total output current | 15A max |
| Per-channel output | 10A max |
| Output channels | 3 (GPIO16 default, GPIO2 configurable, IO33 extended) |
| Firmware | WLED (ships flashed; reflashable via Micro-B UART port) |
| Dimensions | 108 × 45 × 18 mm |

**Features relevant to Highland:**
- Native MQTT support with LWT
- JSON REST API (also accessible via MQTT `/api` topic)
- MOSFET relay cuts strip power when WLED output is off — idle quiescent current approaches zero
- Onboard microphone (sound-reactive modes) — **disabled** in Highland config, not used

**Phase 1 uses a single output channel.** With the shift to a continuous wall-side strip, the split-channel option originally considered is no longer advantageous — data runs are short enough that channel-splitting provides no meaningful integrity benefit. Channels 2 and 3 remain available for future subsystems.

### LED Strips — RGB IC FCOB (WS2811 Protocol)

Twelve-volt addressable RGB IC FCOB (flexible chip-on-board) strip, single continuous run mounted along the wall side of the staircase following the stair slope. BTF-Lighting branded.

| Spec | Value |
|------|-------|
| Voltage | 12V DC |
| Physical LED density | 630–720 LEDs/m (vendor SKU dependent) |
| Addressable pixel density | ~90 pixels/m (one IC group per ~11 cm) |
| Protocol | WS2811 (IC FCOB standard at 12V) |
| Run length | ~15 ft (stairs only) to ~19 ft (stairs + upper landing extension) |
| Addressable pixels (total) | ~410–520 |
| Theoretical max draw | ~60–70W (full white, full brightness, 720/m) |
| Realistic working draw | 15–30W (motion-triggered operation) |

**Choice rationale:** RGB IC FCOB over SMD strip (e.g. WS2815) because the installation is viewed at close range. COB construction places hundreds of tiny LEDs per meter under a continuous phosphor coating, producing a dotless line of light. SMD strips at 60 LEDs/m show visible "string of pearls" dots at close viewing distance, which is undesirable for a stair-adjacent accent where people pass within a few feet of the strip.

**Protocol and controller compatibility:** WS2811 at 12V is natively supported by the GL-C-015WL-D. WLED handles WS2811 IC FCOB out of the box with no special configuration beyond selecting the correct LED type and count.

**Tradeoff vs WS2815 accepted:** WS2815 was the original spec primarily for its dual data line redundancy over a longer discrete-segment run. With the shift to a short continuous strip, that robustness advantage no longer applies meaningfully, and the visual quality advantages of COB dominate. RGB IC FCOB is a single-data-line protocol — a dead pixel IC kills pixels downstream of it — but failure rates are low and the entire strip would be a single replacement unit if ever necessary.

**Density choice (630 vs 720):** Both densities produce dotless light at close viewing distance — human perception is already saturated at 630/m. Let availability, price, and IP rating drive the SKU decision rather than density.

**Landing inclusion is an open question** — whether the strip terminates at the top step or extends across the upper landing is a design decision pending further evaluation. See Open Questions.

### Power Supply — Meanwell LRS-150-12 (or BTF-Lighting 130W equivalent)

150W, 12V DC. Sized for realistic working load with comfortable headroom for full-brightness emergency scenarios at the RGB IC FCOB's higher physical LED density. A BTF-Lighting 130W LED-rated transformer is an equivalent option in the same capacity class; both land near the 50–60% loaded sweet spot for SMPS efficiency.

**Location:** Adjacent bedroom. No usable outlet exists at the top or bottom of the stairs, and the stair underside is a finished closet (no access). Both the PSU *and* the WLED controller live in the bedroom. A 14/3 cable runs from the bedroom to the top of the staircase, carrying 12V + GND + DATA (see Cable Routing). PSU-in-bedroom is Phase 1 solution; an electrician-installed outlet at the top of the stairs is a possible future refinement.

**PSU-to-controller run:** Trivial — both live in the bedroom, connected by short leads. No long DC distribution run required.

### Motion Sensors — M5Stack Atom Stack

Two identical sensor nodes, one at the top of the stairs and one at the bottom. Each runs ESPHome with direct MQTT publishing (no HA auto-discovery).

| Component | Purpose | Approx. dimensions |
|-----------|---------|--------------------|
| M5Stack Atom Lite (or AtomS3 Lite) | ESP32/ESP32-S3 controller | 24 × 24 × 10–13 mm |
| M5Stack Atomic RS485 Base | Dock providing 12V→5V DC-DC step-down, terminal block input, and enclosure | ~24 × 24 × ~15 mm (stacks under Atom) |
| M5Stack ToF Unit — U010 (VL53L0X) *primary* or U172 (VL53L1X) *fallback* | Time-of-Flight distance sensor with Grove connector | ~30 × 24 × 13 mm |

**Per-node stack form factor:** ~24 × 24 × 20–25 mm (Atom + RS485 Base combined). ToF Unit tethers via Grove cable, positioned at the channel's optical port.

**Node symmetry:** Both nodes run identical hardware and firmware, differing only in MQTT client ID. This simplifies spares and BOM.

**Sensing approach:** ToF distance threshold crossing generates a motion event. Thresholds are applied in ESPHome itself — MQTT sees boolean events, not raw distance values. Mounting geometry aims the sensor beam across the first/last step at ankle-to-knee height.

**Why ToF over PIR/mmWave:** PIR has sluggish warmup/cooldown (tens of seconds to reliable detection); the lighting response needs to fire in under 100 ms to feel responsive. mmWave is geometrically hostile to staircases — the sensors want flat-plane coverage from above, not a vertical slope.

#### Why this assembly

The three-component stack was chosen specifically to eliminate breadboarding:

- **No discrete buck converter.** The Atomic RS485 Base has a built-in 12V→5V DC-DC step-down regulator marketed for powering the Atom from RS485's 12V supply rail. For our purposes the RS485 functionality is unused — we consume the Base purely for its packaged 12V→5V step-down, terminal block input, and enclosure.
- **No buck calibration.** The RS485 Base's regulator output is fixed at 5V, eliminating the risk of accidentally over-volting the Atom during bench assembly.
- **No soldering.** Atom stacks onto Base via pin header, ToF Unit plugs into Grove port, 12V enters via terminal block. Bench assembly is plug-and-play.

**Unused RS485 chip consideration:** The RS485 transceiver on the Base is electrically connected to the Atom's UART pins (typically G19/G22). Since we're not driving RS485, those pins remain idle — no conflict with our GPIO needs (I²C is on G26/G32 via the Grove port).

#### Sensor SKU selection: U010 vs U172

The ToF Unit has two variants, both with identical Grove plug-and-play form factors and both lacking externally-accessible XSHUT pins:

| SKU | Sensor | Range | Field of view | ESPHome support |
|-----|--------|-------|---------------|-----------------|
| **U010** | VL53L0X | 2 m | 25° fixed | Native first-class |
| **U172** | VL53L1X | 4 m | 15–27° configurable ROI | External community component |

**Primary choice: U010.** Native ESPHome support makes it the lower-maintenance option. The 25° FoV and 2 m range are adequate for stair traversal detection in the expected mounting geometry.

**Fallback: U172.** If bench POC reveals that the U010's wider fixed FoV picks up unwanted reflections from the opposite wall or other objects, pivoting to the VL53L1X's narrower configurable ROI is straightforward. The VL53L1X requires an `external_components:` reference in the ESPHome YAML (community component, e.g. `soldierkam/vl53l1x_sensor`), which is well-trodden but adds a community dependency.

#### GPIO assignment

Atom Lite exposes eight usable GPIO pins — G26, G32 via the Grove port, plus G19, G21, G22, G23, G25, G33 on edge headers. Our needs are modest:

| Function | Atom Lite GPIO | Connection |
|----------|----------------|------------|
| I²C SDA (ToF Unit) | G26 | Grove port pin 1 |
| I²C SCL (ToF Unit) | G32 | Grove port pin 2 |
| RS485 TX (unused, tied to transceiver) | G19 | RS485 Base internal |
| RS485 RX (unused, tied to transceiver) | G22 | RS485 Base internal |

Remaining free: G21, G23, G25, G33 (G25 is shared with the onboard IR LED and wants care).

### Sensor Recovery

Neither the U010 nor U172 exposes the VL53Lxx's XSHUT pin externally in the M5Stack packaging, so we cannot power-cycle just the sensor chip from ESPHome. Recovery from a hung sensor follows a layered approach:

**Primary: Atom-level reboot via ESPHome watchdog.** If the sensor stops producing readings for a configurable interval (provisional: 5 minutes), ESPHome triggers a full Atom reboot via its built-in `restart` component. The Atom goes offline for ~10 seconds during boot — acceptable for a non-safety-critical motion sensor. LWT publishes `offline` during boot and `online` on recovery; Highland's standard device-monitoring logic applies.

**Secondary: Node-RED-initiated reboot.** If longer-term observability reveals pattern failures the ESPHome watchdog misses (e.g., sensor returns stuck values rather than stopping entirely), Node-RED's device monitoring flow can publish to `highland/command/sensor/stairs_*/reboot`. ESPHome subscribes and executes `restart`. Same practical outcome, centrally orchestrated.

**Tertiary (planned refinement): MOSFET-switched sensor power.** If bench POC or production experience reveals that Atom reboots do not reliably recover the sensor (i.e., the sensor maintains its hung state across the Atom's I²C re-init), add a small GPIO-driven MOSFET between the Atom's 5V rail and the Grove port's VCC. ESPHome then controls sensor power independently of Atom state. Cost: one transistor plus a dedicated GPIO. Not required for Phase 1.

---

## Physical Installation

### Channel Profile — Single-Chamber U

**Approach:** Standard aluminum U-channel with frosted diffuser, mounted along the **wall side of the staircase** following the stair slope as a single continuous run.

- **Channel interior** holds the LED strip down its full length plus a compact Atom sensor stack at each end (top and bottom). The middle of the channel carries only the strip.
- **Diffuser** snaps into retention grooves at the top of the channel's side walls, covering the strip and electronics uniformly.

**Why single-chamber over dual-chamber H-profile:**

The design initially called for a dual-chamber H-profile to hide electronics and a cable trunk in a separate rear cavity. Three developments simplified the problem:

1. **No full-length cable trunk needed.** The Atoms tap the LED strip's own integrated 12V/GND pads at each end of the run (see Cable Routing below). There is no separate power wire running the length of the channel — only the strip itself.
2. **Smaller electronics footprint than estimated.** The Atom + RS485 Base stack is ~24 × 24 × 25 mm, compact enough to fit inside a reasonable U-channel alongside the strip at each end.
3. **Single cable entry point.** The 14/3 from the bedroom enters the channel at the top end only. A small wall-side pass-through drilled behind the channel routes the cable invisibly from the wall cavity into the channel interior.

**Visual compromise accepted:** The Atom stacks at each end occupy ~30 mm of channel length and read as slightly darker shadows through the diffuser near the ends of the strip. In practice this is a minor aesthetic concession that tends to read as "end of strip" rather than "something is wrong."

**Cost and sourcing advantages:**

| Profile | Cost per m | Sourcing | Splice accessories |
|---------|------------|----------|-------------------|
| Standard U-channel | $5–10 | Amazon, Home Depot, multiple SKUs | Widely available |
| Architectural H-profile | $15–25 | Specialty suppliers (Klus, etc.) | Limited |

For a ~5–6 m run, the channel-only savings are $50–100, plus meaningful accessory savings and significantly better local availability.

**Target profile dimensions:**

| Dimension | Target |
|-----------|--------|
| Interior width | ≥ 25 mm (fits strip plus Atom stack side-by-side at endpoints) |
| Interior depth | ≥ 15 mm (accommodates Atom stack height comfortably) |
| Overall external | ~30 × 18 mm typical |
| Length | 1 m or 2 m sections (most common), spliced to full 15–19 ft run |

**Sourcing direction:** Generic aluminum LED channels in this size range are plentiful on Amazon, Home Depot, and local electrical supply houses. Brand matters less than dimensions — shop by interior measurements. Look for channels explicitly listed as compatible with LED strips and including a matching frosted PC or acrylic diffuser.

**Final profile selection is deferred to bench POC.** Small-scale testing with sample lengths of candidate profiles will verify assembly fit (especially at endpoints where Atom stacks reside), diffuser appearance, and mounting practicality before committing to the production SKU.

### Mounting Height

Open question — three candidate positions on the wall:

- **Top edge of the skirt board** (light skims across the treads; closest visual analog to under-nosing lighting)
- **Mid-wall above the skirt** (decorative horizontal line; illuminates less of the stair surface itself)
- **Recessed into the skirt board** (most invisible hardware, requires finish woodwork on the channel-wall side)

Decision pending bench POC and in-situ evaluation. See Open Questions.

### Rationale for Wall-Side Mounting

Two independent constraints ruled out both under-tread and riser-face mounting:

- **Under-tread:** The stair underside is a finished closet with no access. True under-tread mounting would require opening the closet ceiling.
- **Riser-face:** Molding installed beneath each nosing leaves insufficient clearance for an LED channel, ruling out the shadow-line placement originally considered.

Wall-side mounting sidesteps both constraints and — with a reasonably-sized U-channel — delivers a clean installation: a continuous light line that follows the stair geometry, with electronics tucked at each end and only a single cable entry required at the top.

### Sensor Integration

Both sensor nodes (top and bottom) are fully integrated into the channel itself. No separate sensor enclosures or visible mounting hardware.

**Assembly placement:** Each Atom + RS485 Base stack sits inside the channel at the end of the run — one at the top, one at the bottom — immediately adjacent to the strip's endpoint. The Grove-tethered ToF Unit sits positioned so its optical face aligns with the fabricated port in the channel wall. The stack occupies ~30 mm of channel length at each end; the middle of the channel holds only the strip.

**ToF optical port:** The VL53L0X's (or VL53L1X's) 940 nm laser cannot see through a frosted diffuser. Optical path is provided by:

- A hole drilled through the channel wall at the ToF location (side-facing or front-facing, depending on desired beam direction)
- A small clear window bonded over the hole — thin clear acrylic or polycarbonate, 10–15 mm square, CA-glued into a shallow recess cut into the channel exterior
- ToF Unit positioned inside the channel with its optical face aligned against the window

Fabrication is minor: drill, shallow countersink with a Dremel, cut acrylic square to fit, glue. Estimated 15 minutes per node at the workbench.

**Power entry at each Atom:** Short jumper wires (~3–4 inches) run from the LED strip's own +12V and GND solder pads to the RS485 Base's terminal block at each Atom location. Both Atoms share the strip's 12V rail — the top Atom taps the rail at the strip's top end (close to where the controller feed enters), the bottom Atom taps at the strip's bottom end.

**Thermal considerations:** At motion-triggered duty cycles (seconds of full brightness at a time, not continuous), ambient temperature inside the channel during operation is not expected to approach the Atom's or Base's limits. If prolonged emergency-bright operation became common, additional thought would be warranted. Placement of the assemblies at the *ends* of the channel puts them slightly further from the main length of the active LED strip, which also mitigates thermal buildup.

**Atom status LED visibility:** The Atom Lite has an onboard RGB status LED on its top face. Inside the channel with the diffuser overhead, the LED's glow would be visible through the diffuser as a faint colored spot at each end of the channel. Default ESPHome behavior uses the LED for WiFi/MQTT connection status (blue when connected, blinking on connection issues) — useful diagnostically but visually intrusive on a strip designed as architectural accent lighting.

For Phase 1, disable the onboard LED in ESPHome (set its default state to off). This eliminates the visual intrusion; sensor health is still monitored via standard Highland LWT and MQTT status topics. If commissioning reveals a passive visual indicator would be useful (e.g., glance-check that both sensors are alive), the LED can be re-enabled and repurposed as a deliberate health indicator (steady color = healthy, blink pattern = degraded) at that point.

### Cable Routing

**Controller-to-strip cable:** A single 14/3 stranded cable runs from the WLED controller in the bedroom to the top end of the channel on the staircase. This is the only external cable in the installation. It carries three conductors:

| Conductor | Purpose |
|-----------|---------|
| +12V (14 AWG) | Power rail for strip and Atom nodes |
| GND (14 AWG) | Return and data reference |
| DATA (14 AWG) | WS2811 control signal from GLEDOPTO |

14 AWG sizing accommodates the ~6.25A steady-state and 12–18A inrush of the full strip plus both Atoms, with voltage drop under 6% at 20 ft round trip. Stranded construction for flexibility during wall-cavity routing.

**CL2-rated in-wall cable** is preferred if any portion of the run passes inside a wall cavity. Monoprice and Southwire both sell CL2-rated 14/3 stranded cable by the spool.

**Cable entry at the channel:** A small pass-through drilled through the wall behind the top end of the channel carries the cable from the wall cavity into the channel interior. Cable is not visible externally — the entry point is covered by the channel itself or a matching end cap.

**Cable entry at the bedroom:** Cable exits the wall near the PSU and controller location. Controller connects to cable via its built-in screw terminals.

**No full-length power trunk inside the channel.** The LED strip's own integrated +12V/GND pads serve as the power distribution along the channel's length. Each Atom taps the strip pads at its location via short jumpers (see Sensor Integration above). The only cable running beyond the top-end entry point is the strip itself.

**Bottom-end injection option:** If commissioning reveals visible brightness drop at the bottom of the strip under full-brightness conditions (a symptom of accumulated voltage drop across the strip's own conductors over 5–6 m), a dedicated power-injection wire can be added. This would run a pair of 14 AWG conductors from the top-end cable entry point down the length of the channel to tap into the strip's +12V and GND pads at the bottom end. Not expected to be necessary at realistic brightness levels, and adding it later is straightforward.

### Signal Conditioning (Production Install)

The GLEDOPTO GL-C-015WL-D outputs a 3.3V data signal from its onboard ESP32. WS2811 ICs nominally accept 3.3V data, but over the 15–20 ft controller-to-strip run, signal integrity can degrade enough to cause intermittent pixel glitches.

**For bench POC:** Not required. Short leads between controller and strip produce clean signal without conditioning.

**For production install:** A small signal conditioning module at the strip-input end addresses data integrity concerns:

| Component | Purpose |
|-----------|---------|
| 74AHCT125 quad level shifter | Converts 3.3V data in → 5V data out for the strip's WS2811 inputs |
| 100 nF ceramic decoupling capacitor | Between 12V and GND at strip input, smooths supply noise |
| 330Ω series resistor | Between level shifter output and strip data input, dampens reflections |

The level shifter is powered from 5V, which is available locally from the top Atom's RS485 Base output rail.

The module fits in a ~30 × 40 mm perfboard and lives inside the channel at the top end, near the cable entry point and the top Atom stack. Components are inexpensive (a few dollars total from Mouser or similar).

**Mouser part references:**

- 74AHCT125: **595-SN74AHCT125N** (Texas Instruments, through-hole DIP)
- 100 nF ceramic: any 0.1 µF, 50V, X7R MLCC
- 330Ω resistor: any 1/4W, 5% tolerance

### Sensor Placement

**Top node:** Atom stack inside the channel at the top end, immediately adjacent to the strip's top pad terminations. ToF optical port angled to sweep across the upper landing and top step at ankle-to-knee height. Stairlift parks "around the corner" from the top landing and does not occlude the ToF beam.

**Bottom node:** Atom stack inside the channel at the bottom end, immediately adjacent to the strip's bottom pad terminations. ToF optical port angled to sweep across the bottom step similarly. Stairlift has a parking position at the bottom that is also around a corner and does not occlude the ToF beam.

**Stairlift consideration:** Non-factor. The lift has parking positions at both the top and bottom of the stairs, but both are around corners and out of ToF line-of-sight. No current clamp, dry-contact, or other instrumentation of the lift is needed.

### Bench POC

Ahead of production installation, a small-scale bench proof-of-concept is planned to validate:

- Actual stacked dimensions of the Atom + RS485 Base + ToF Unit assembly
- Fit of the assembly inside candidate U-channel profiles (especially interior width at endpoints)
- Sample lengths of 2–3 candidate channel profiles for visual and mechanical evaluation
- Diffuser appearance at candidate mounting heights
- ToF optical port fabrication technique (drill, cut, clear window bond)
- ESPHome U010 native-component behavior against live sensor; validate detection performance with bench-top simulated traversal
- Escalation path to U172 with external ESPHome component if U010 FoV proves problematic
- Atom-reboot recovery behavior if sensor is deliberately hung
- Data signal integrity over short bench leads *without* the signal conditioning module (short runs should not need it; validates the module is only required at production scale)

Assembly is intentionally plug-and-play — no breadboarding at bench scale. Bench build is:

1. Connect 12V bench supply to Atomic RS485 Base's terminal block
2. Stack Atom Lite onto RS485 Base
3. Plug ToF Unit (U010) into Atom's Grove port
4. Flash Atom with ESPHome YAML
5. Confirm MQTT traffic on the broker; validate threshold crossing triggers events
6. Separately, wire GLEDOPTO to a ~1 m strip segment with short leads to validate WLED control

Findings from POC feed final channel SKU selection, mounting height decision, U010-vs-U172 confirmation, signal conditioning necessity confirmation, and any wiring or housing refinements.

---

## Architecture

```
Tempest station  ──► highland/state/weather/station  ──┐
                                                        │
Schedex (dusk/dawn + offset)  ──► schedule state  ─────┤
                                                        ▼
                                             Active Window Gate
                                              (schedule OR lux)
                                                        │
Top ToF node  ──► highland/event/motion/stairs_top  ───┤
                                                        ▼
Bottom ToF node  ──► highland/event/motion/stairs_bottom  ──►  Traversal FSM
                                                        │
                                                        ▼
                                              Mode Resolver
                                             (priority layers)
                                                        │
                                                        ▼
                                         highland/command/stair_lights/preset
                                                        │
                                                        ▼
                                          WLED — GL-C-015WL-D
                                          (co-located with PSU
                                           in adjacent bedroom)
                                                        │
                                                14/3 cable run
                                               (12V, GND, DATA)
                                                15–20 ft to top
                                                   of stairs
                                                        │
                                                        ▼
                                           LED strip (in channel,
                                            along wall side of
                                              staircase)
```

**Layer responsibilities:**

| Layer | Owns |
|-------|------|
| Sensor nodes (ESPHome) | Raw detection → boolean motion events |
| Tempest flow | Normalized weather state including outdoor lux |
| Active Window Gate (Node-RED) | Combining schedule + lux into on/off gate |
| Mode Resolver (Node-RED) | Priority resolution across mode inputs |
| Traversal FSM (Node-RED) | Direction inference and preset sequencing |
| WLED | Per-pixel choreography (preset execution) |
| HA | Dashboard display and manual mode overrides |

---

## Active Window Gating

The FSM responds to motion events only during the active window. Outside the active window, motion events are logged but do not drive lighting.

### Two-input gate

```
schedule_active  = schedex dusk→dawn (with configurable offset)
lux_override     = outdoor_lux < threshold (with hysteresis)

stairs_active = schedule_active OR lux_override
```

Either input being true enables motion response. Both being false gates motion events out.

### Schedule input

Driven by schedex solar elevation calculation. The schedule *is* the default; the lux reading handles cases where the schedule disagrees with reality (storm darkness at 2pm, long dim dusks, etc.).

**Configurable offset:** Minutes before/after civil dusk/dawn to begin/end the active window. Starting value: 0 offset. Tune against observed behavior.

### Outdoor lux override

Uses the Tempest station's solar radiation / brightness reading from `highland/state/weather/station`. Threshold logic is applied in the stair lighting flow, not in the Tempest flow (Tempest publishes normalized data; consumers apply their own thresholds).

**Provisional thresholds (tune from real data once collected):**

| Transition | Lux value |
|------------|-----------|
| Enable (darkening) | < 200 |
| Disable (brightening) | > 500 |

Hysteresis prevents flapping during transitional lighting.

**Graceful degradation:** If `highland/status/weather/station` indicates Tempest is offline or lux data is stale (> 10 min), the lux override input is treated as `false` and the gate falls back to schedule-only. Failing to schedule-only is the correct behavior — we can't read outdoor lux, so we trust the clock.

### Thresholds storage

Values live in `config/thresholds.json` under the `stair_lighting` key:

```json
"stair_lighting": {
    "outdoor_lux_enable": 200,
    "outdoor_lux_disable": 500,
    "lux_stale_minutes": 10
}
```

See #45 for ongoing config taxonomy work.

---

## Mode Hierarchy

The stair lighting subsystem operates in one of three modes. Modes are resolved by priority — higher-priority modes override lower.

| Priority | Mode | Behavior |
|----------|------|----------|
| 1 (highest) | `emergency` | Full bright white, overrides `on`. Triggered by safety alarm conditions only (smoke/CO, security). See Emergency Triggers below. |
| 2 | `on` (default) | Schedule-gated motion response. Normal operation — the FSM drives cascades when the active window is open. |
| 3 (lowest) | `off` | Disabled. No cascades fire on the strip, though the FSM continues to track occupancy. |

**Mode state:** `highland/state/stair_lights/mode` (retained).

**Mode command:** `highland/command/stair_lights/mode` (not retained). Payload: `{"mode": "on" | "off"}`. Emergency cannot be commanded directly — it is set by the Mode Resolver in response to alarm state.

**Mode Resolver logic:**

```
if any safety alarm active:
    effective_mode = emergency
else:
    effective_mode = user_commanded_mode  (on | off)
```

The Mode Resolver subscribes to both the user command topic and alarm-state inputs, and publishes the resolved effective mode to the mode state topic.

**FSM autonomy.** The occupancy FSM processes motion events and publishes state to `highland/state/stair_lights/occupancy` regardless of mode — including when mode is `off` or `emergency`. Mode controls only whether cascade presets fire on the strip; it does not gate the FSM itself. This makes occupancy a first-class signal available to other subsystems (security, analytics, future automations) independent of whether the stair lighting is currently acting on it.

Implications:

- In `off` mode: motion detected → FSM goes `unoccupied → occupied`, state topic publishes, but no cascade fires on the strip.
- In `emergency` mode: motion detected → FSM tracks normally, occupancy publishes, but the emergency preset holds full bright white on the strip regardless.
- The preset topic `highland/state/stair_lights/preset` reflects what's actually on the strip (preset 1 `off` in `off` mode; preset 10 `emergency_bright_white` in `emergency`), not what the FSM would otherwise choose.

### Mode Transitions

When a mode change arrives while the FSM is in `occupied` or `clearing`, the handling depends on the target:

**To `emergency` (alarm activated):** Applies immediately. The emergency preset replaces whatever is currently on the strip mid-animation. The FSM continues to track occupancy underneath — it is not interrupted, just visually overridden.

**From `emergency` (alarm cleared):** Applies immediately. The emergency preset stops and the subsystem resumes from whatever FSM state is current. If `occupied`, fire the appropriate cascade or hold preset for the current direction. If `unoccupied`, go to `off`. Emergency transitions are safety-driven and should not defer in either direction.

**Between `on` and `off` (user command):** Deferred until the FSM naturally returns to `unoccupied`. The currently-running cycle completes its natural course (motion → hold → quiescence → fade-out → unoccupied). The new mode takes effect for future motion events. This avoids the UX weirdness of lights cutting out on someone mid-stair when a mode toggle arrives at an inconvenient moment.

The mode state topic updates immediately on command receipt — dashboards reflect the user's intent, even though the visual behavior lags by up to one FSM cycle (bounded by the 90 s quiescence timeout plus the 2 s fade). A user who toggles `off` while someone is on the stairs will see the dashboard flip immediately; the lights will finish their current cycle before staying dark for future motion.

### Emergency Triggers

The `emergency` mode is reserved for safety-critical conditions where maximum visibility of the stairway takes priority over ambient lighting behavior. Triggers are limited to:

| Trigger | Duration | Notes |
|---------|----------|-------|
| Smoke / CO alarm active | Minutes to ~1 hour (until alarm clears) | Evacuation path illumination |
| Security system in alarm state (intrusion or duress) | Potentially extended | Maintain stair visibility during active intrusion response |

**Power recovery is not an emergency trigger.** Resuming normal operation after an outage should restore the user-commanded mode (`on` or `off`) from retained MQTT state, not force the stairs to full-bright white. See Resilience and Recovery → Boot-time Recovery.

**Manual dashboard control is not an emergency trigger.** User-visible operation is `on` / `off` only. Phase 1 deliberately omits a "forced bright" or "forced accent" user mode — the three-mode scope keeps operational surface area minimal. If a richer user mode becomes genuinely useful in practice (e.g., a dedicated "stay on steady" for moving furniture up the stairs), it can be added later with its own priority slot.

**Thermal considerations for emergency mode:** Full-bright cool white at ~60–70W draw can run for the full duration of an alarm condition. The aluminum channel provides some heat sinking, and duty cycle across a year is low, but sustained operation during a long alarm is the worst realistic continuous load. No preemptive duration cap — the point of emergency is maximum visibility, and dimming during a fire is the wrong tradeoff. Validate at bench POC and during any real emergency; add a cap only if observed thermals warrant it.

**Electrical infrastructure assumption.** Emergency mode assumes mains power is available. In a total power failure, the PSU has no input and the entire subsystem is dark regardless of alarm state. Smoke/CO alarms have their own battery backup and will still sound, but the stair lighting cannot help illuminate an evacuation path during an outage. UPS-backed operation is a Phase 3+ consideration — for Phase 1, the stairs are dark during power failures, same as every other light in the house.

---

## Motion Detection

### Sensor node topology

Each ESP32 sensor node publishes directly to MQTT via ESPHome's native MQTT component. HA auto-discovery is disabled on these nodes — Node-RED is the authoritative consumer.

**Published topics per node:**

| Topic | Type | Retained | Notes |
|-------|------|----------|-------|
| `highland/event/motion/stairs_top` | Event | No | Rising edge on detection |
| `highland/event/motion/stairs_bottom` | Event | No | Rising edge on detection |
| `highland/status/sensor/stairs_top` | LWT | Yes | `online` \| `offline` |
| `highland/status/sensor/stairs_bottom` | LWT | Yes | `online` \| `offline` |

### ESPHome threshold configuration

ToF distance threshold is set in ESPHome itself (not in Node-RED). The node publishes a motion event only when distance crosses the threshold from above. Hysteresis and debouncing are handled locally for fastest response.

**Edge-triggered behavior:** Motion events fire on *transition* from above-threshold to below-threshold, not continuously while an object is in the beam. Without edge triggering, an occluded sensor would fire a motion event on every measurement cycle, flooding the FSM. See Occlusion Detection below for how persistent below-threshold readings are handled.

**Bipedal gait and debouncing.** A biped crossing the beam does not produce a single clean below-threshold period. Depending on leg geometry and walking speed, three patterns are possible:

- **Clean:** Leading leg obscures the trailing leg from the sensor's perspective for the entire crossing. One continuous below-threshold period, ~300–500 ms total.
- **Gap:** Trailing leg briefly visible between the leading leg's exit and the trailing leg's entry. Two below-threshold periods separated by a ~50–150 ms above-threshold gap.
- **Edge noise:** Jitter around the threshold at the entry/exit of the beam cone.

Without debouncing, the gap pattern produces a double motion event per traversal. Downstream behavior is still semantically correct (direction inference is preserved, timers reset idempotently), but it muddles logging and gives downstream flows noise they don't need. ESPHome's `delayed_off` filter absorbs the inter-leg gap — once the sensor trips, it stays tripped for at least N ms after distance rises back above threshold; if it re-trips within that window, no untripped transition is emitted.

**Provisional ToF configuration:**

| Parameter | Value | Purpose |
|-----------|-------|---------|
| Detection threshold | 150 cm | Distance below which the sensor is considered tripped |
| `delayed_on` | 0 ms | No rising-edge debounce — fastest possible response on the leading leg |
| `delayed_off` | 250 ms | Bipedal gait debounce — absorbs the above-threshold gap between leading and trailing leg into a single motion event |
| Minimum event spacing | 750 ms | Belt-and-suspenders rate limit against pathological rapid events; normal gait is handled by `delayed_off` |

**Calibration bounds for `delayed_off`:**

- *Lower:* must exceed typical inter-leg gap (100–150 ms) plus margin. 200 ms is the practical floor.
- *Upper:* must stay shorter than legitimate inter-person separation on the same sensor (~1 second minimum, since the first person has to clear the step before the second can step onto it). 500–700 ms is the practical ceiling before distinct people start getting falsely merged. Tight parent-with-small-child separations could be closer; worth real-world observation.

Starting value of 250 ms sits comfortably in the middle of the safe band, but all four parameters are expected to need tuning against real-world observation. Bench POC captures baseline signatures; final calibration happens post-install with real household traffic (normal adult walk, kids running, slow descent with hands full, stairlift glide, pet dart). All values are ESPHome YAML parameters and are adjustable without any Node-RED or infrastructure changes — iterate freely until behavior matches household reality.

### Occlusion Detection

A real-world scenario: the user sets an item on a stair step that happens to intersect a ToF sensor's beam. The sensor reads "object present" continuously and indefinitely. Without special handling, this would flood the FSM with motion events or — with naive edge triggering — trigger once and then never fire again even when the item is eventually removed.

**ESPHome-side detection:** If distance remains continuously below threshold for longer than `occlusion_detect_seconds`, the node declares the sensor occluded and publishes `true` to `highland/status/sensor/stairs_{top,bottom}/occluded` (retained). While occluded, motion events from that sensor are suppressed — they don't fire to MQTT even if the sensor measurement briefly rises and drops again. Once distance returns to above-threshold continuously for `occlusion_clear_seconds`, the occlusion flag clears.

**Un-occlusion fires a motion event.** When the occlusion flag clears, the sensor node publishes a synthetic motion event on `highland/event/motion/stairs_{top,bottom}` as part of the transition. This guarantees the FSM sees the removal as traversal-initiating activity regardless of how the object was physically removed — reached for from the side, picked up while the person stands still, etc. A rising-edge-only trigger would miss this interaction entirely. The direction inference rule that follows — "whichever sensor fires first establishes direction" — applies naturally to the synthetic event, so the just-un-occluded side becomes the direction origin for the ensuing cascade.

**Threshold values (provisional):**

| Parameter | Value | Notes |
|-----------|-------|-------|
| `occlusion_detect_seconds` | 30 s | Time of continuous below-threshold reading to declare occluded. Must be longer than the slowest realistic sensor-beam crossing (a person pausing while crossing, a pet lingering briefly). |
| `occlusion_clear_seconds` | 3 s | Time of continuous above-threshold reading to clear occlusion. Intentionally fast — the user has just moved the item and expects normal behavior to resume quickly. Also serves as a settle window against bounce (rummaging at the object, setting it down briefly, etc.). |

The asymmetry is intentional: detection is slow to avoid false positives; clearing is fast to provide responsive recovery.

**FSM degraded-mode behavior (single-sensor occlusion):** When one sensor is occluded, the FSM falls back to a simpler mode:

- Motion events from the non-occluded sensor still trigger `unoccupied → occupied` transitions
- Direction inference becomes unreliable — the occluded sensor can't provide a first-fire signal
- Preset selection falls back to a generic (non-directional) fade-in, regardless of which sensor fired
- All other FSM behavior (quiescence timeout, `clearing` state, motion-cancels-fade) continues normally

This is graceful degradation — the user gets motion-triggered lighting, just without direction-aware preset variation. The occluded state is visible in Highland's MQTT topics and dashboard; the user can see *why* the lighting is behaving slightly differently without debugging.

**Total outage (both sensors occluded):** A meaningfully different failure mode. No motion events fire from either end, the FSM parks in `unoccupied`, and the subsystem is effectively offline — every traversal is invisible. The per-sensor occlusion notifications have already fired individually, but the combined state deserves its own notification because the actionability is different ("stair lighting is non-functional until sensors are unblocked" vs. "one sensor is blocked; lighting is degraded"). See **Notifications** below.

**Visual warning on occlusion:** When a sensor transitions to `occluded=true`, trigger a one-shot warning flash on the strip. This is the `occlusion_warning` preset (ID 11).

**Pulse pattern (provisional):** Three amber pulses at 100% brightness. 500 ms on, 300 ms gap between pulses. Total duration ~2.1 s. Tune at commissioning against subjective "urgent but not frantic" feel.

Three rules govern when the flash fires:

1. **Not gated by the active window.** The normal cascade presets are gated — no point running a fade at noon when the strip is otherwise dark. The flash is gated *out* — it breaks through intentionally because it's a warning signal, not a feature. Three pulses at 100% amber are visible in any ambient light, and anyone in line of sight will see it.
2. **Deferred during active traversal.** If occlusion detection happens to land while the FSM is in `occupied` or `clearing` (rare but possible — e.g., someone sets an object down and continues up the stairs), the flash is queued and fires on the next transition to `unoccupied`. Interrupting a cascade with a warning flash would be visually chaotic.
3. **Suppressed during `emergency` mode.** The emergency preset is already maximum visibility, and interrupting a safety-driven full-bright white with a warning flash would be counterproductive. The occlusion notification still fires through the standard framework; only the visual warning is skipped. When emergency clears, if the occlusion is still active and the FSM is in `unoccupied`, the queued flash fires at that point.

**Return-to-state behavior:** After the three pulses complete, the strip returns to whatever state the underlying mode dictates for `unoccupied`:

- `on` mode, `unoccupied` → dark (preset 1 `off`)
- `off` mode → dark (preset 1 `off`)
- Future `accent_dim_warm` mode (reserved) → would return to the steady accent glow

The Mode Resolver handles the return — the flash preset doesn't need to know what to resume; it just terminates and the resolver re-applies the current mode's resting state.

Un-occlusion does not fire a visual warning. The natural cascade triggered by the synthetic motion event (see above) is the acknowledgment during the active window; outside the active window, un-occlusion is silent and the original notification has already established the system is watching. Worth calling out as a known silent transition so future-us doesn't get confused about the asymmetry.

**Notifications:** Two notification IDs registered with the Highland notification framework (`notifications.json`):

| Notification ID | Fires on | Cadence |
|-----------------|----------|---------|
| `stair_sensor.occluded` | Any single sensor transitions to `occluded=true` during the active window | One-shot on detection, silent on clear |
| `stair_sensor.both_occluded` | Both sensors are simultaneously `occluded=true` | One-shot on entering the dual-occluded state, silent on partial or full clear |

Payload for both includes which sensor(s) and notification text that suggests the stairs may have something on them. No nag — these are actionable "go move the thing" notices, and the degraded lighting behavior is itself a passive reminder during the active window. Active-window gating applies to the single-sensor notification (notifying about a blocked stair sensor at 3 AM when the lights are off and no one is using the stairs is noise); the dual-occluded notification fires regardless of window because the subsystem is fully offline and the user should know even if they're not currently using the stairs.

### Direction Inference

Direction is inferred from **which sensor fires first** on an `unoccupied → occupied` transition:

| First trigger | Direction |
|---------------|-----------|
| `stairs_bottom` | Ascending |
| `stairs_top` | Descending |

Direction is used to select which preset variant plays (ascending vs descending). It does not affect FSM state transitions — the FSM is occupancy-based, not traversal-based. See Traversal FSM for details.

---

## Traversal FSM

The FSM tracks **stair occupancy** — not traversal progress. The sensors at the top and bottom of the staircase detect when someone enters or exits the sensed region, but cannot see the middle ~12 ft of the staircase. Any logic that tries to infer "stairs are empty" from sensor events alone is guessing, because a person standing or moving slowly through the middle section generates no sensor activity. The FSM is deliberately simple: any sensor fire marks the stairs as occupied, and the occupied state only clears after a long quiescence period with no further activity.

### States

| State | Description |
|-------|-------------|
| `unoccupied` | No recent sensor activity. Lights off. |
| `occupied` | Sensor activity detected; assumed occupied until quiescence expires. Lights on. |
| `clearing` | Quiescence expired; lights fading out but any motion restores occupancy. |

### Transitions

```
unoccupied  ──(any motion)──────►  occupied   [select preset by direction, fade in]

occupied    ──(any motion)──────►  occupied   [reset quiescence timer]
occupied    ──(quiescence expires)─►  clearing   [fade out preset]

clearing    ──(any motion)──────►  occupied   [cancel fade, restore lighting]
clearing    ──(fade complete)────►  unoccupied
```

### Direction inference

Direction is recorded on the `unoccupied → occupied` transition, based on which sensor fired first (bottom = ascending, top = descending). It drives **preset selection** (ascending vs descending preset ID), not FSM state. The FSM itself is direction-agnostic.

In multi-person scenarios where the first-firing sensor doesn't accurately represent a single traversal (e.g., two people entering from opposite ends), the direction inference will be partially wrong. Since we've landed on simple fade-in as the likely final visual style, direction mismatches have minimal visual impact. Direction remains valuable for logging, Highland state topics, and future refinements.

### Timers

| Timer | Default | Notes |
|-------|---------|-------|
| `quiescence_timeout` | 90 s | Time with no sensor activity before entering `clearing`. Must be long enough to cover the slowest legitimate traversal (stairlift at ~45 s + margin). |
| `fade_duration` | 2 s | Time to fade from `occupied` brightness to off during `clearing`. |

**Why 90 seconds for quiescence:** traversal speeds vary widely across the household. A fast traversal is ~5 s, a slow one ~10 s, elderly visitors 20–30 s, and the stairlift 45+ s. A single timeout has to accommodate the slowest legitimate case without turning lights off on someone mid-staircase. The cost is that false positives (single-sensor trigger without actual traversal) leave the lights on for 90 s — accepted as the price of safety.

**Configuration:** Both timers are externalized (see Configuration section). The values above are provisional defaults; tune against real household traversal patterns at commissioning.

### Edge cases

**False positive (single-sensor trigger, no actual traversal):** Person starts up, changes mind. Pet wanders near the beam. Bottom sensor fires, no further activity. FSM sits in `occupied` for the full quiescence timeout (90 s), then transitions to `clearing` and fades out. Lights stay on for 90+ seconds after a false positive — the accepted cost of safety.

**Two people, opposite directions (unsupported edge case):** Two ankle-height sensors at the top and bottom leave ~12 ft of middle staircase with no sensor coverage. A person walking through that region generates zero sensor events. If two people pass each other mid-staircase, the sensor activity pattern is indistinguishable from one person making a round trip, and the FSM cannot tell them apart. The quiescence timeout will eventually fire `clearing` while one or both people may still be on the stairs.

This scenario is rare in most households (requires overlapping stair traffic in opposite directions) and is not engineered around. Ambient light from adjacent spaces generally fills the gap; in practice, guests in this scenario typically trigger overhead lighting anyway. If multi-person scenarios become common, a middle-staircase sensor would resolve the ambiguity — this is a Phase 3 refinement option, not Phase 1 scope.

**Person stops mid-staircase:** Someone pauses on the stairs (tying a shoe, talking, etc.). No sensor fires during the pause. If the pause lasts longer than the quiescence timeout (90 s), lights enter `clearing`. Any subsequent motion (shifting position, continuing the traversal) will fire a sensor and bring lights back immediately via the `clearing → occupied` transition. Brief standstills are handled naturally; pathological 90+ s standstills cause a brief lights-off period that resolves on the next movement.

**Stairlift traversal:** Lift takes 45+ seconds to complete a full ascent or descent. The rider's feet pass through the bottom beam at start, then the lift cruises for ~40 s with the rider above ankle-height sensor level, then the rider exits past the top beam. The 90 s quiescence timeout comfortably accommodates this, with or without mid-traversal sensor activity.

**Sensor occluded (item on stairs):** Handled in ESPHome — see Motion Detection → Occlusion Detection. The FSM treats events from occluded sensors as suppressed; direction inference falls back to "unknown" when only the non-occluded sensor fires. Lights still function in a degraded mode using a generic (non-directional) fade-in preset.

**Pets:** ToF aiming at ankle-to-knee height will miss cats and small dogs but catch larger dogs. Pet triggers are accepted as benign false positives — this subsystem is an automation enhancement, not a security or alarm trigger. No sensor aiming, threshold, or filtering changes are warranted to exclude pets. The worst-case outcome is that the stairs light briefly when a dog passes through, which is harmless.

---

## WLED Preset Catalog

Choreography lives as WLED presets defined in the WLED UI. Node-RED references presets by ID — it does not manipulate pixels directly.

### Visual style: directional fade

The original design called for a chase / cascade effect that visually tracked the user's direction of travel. On reflection, this choice made more sense for the previously-planned under-nosing mounting where the lights were spatially discrete and the chase emphasized stair structure. With wall-mounted continuous-strip mounting, the strip is fundamentally architectural and a continuous chase loses its structural meaning — it reads as "a wave of light along the wall" rather than "each step lighting in turn."

**Chosen default: directional fade.** Rapid fade-in combined with a rapid sweep in the direction of travel. A brief gradient brightens from the origin end (where motion was first detected) to the destination end over 300–500 ms — enough to give a subtle sense of direction without the attention-grabbing feel of a chase. Architectural, not flashy. The strip reads as accent lighting that happens to acknowledge your arrival, rather than as a light effect.

Three candidate visual styles for reference:

| Style | Direction-aware | Attention level | Feel |
|-------|----------------|-----------------|------|
| Simple fade-in | No | Very low | Ambient architectural lighting |
| **Directional fade** *(chosen)* | Subtly | Low | Gradient lighting with a hint of flow |
| Cascade / chase | Strongly | High | Light effect |

**Fallback to simple fade-in.** If commissioning reveals that the directional fade feels busy, or that direction inference is unreliable enough in real-world edge cases (heavy pet traffic, guests, dual-occupancy) that the gradient frequently points the wrong way, simplifying to a non-directional fade-in is a zero-risk simplification — the preset definitions change in WLED and the FSM continues to publish the same events and state. No topic or flow changes required.

**Holiday and effect presets** (Phase 2) are the appropriate home for chase / sparkle / cascade patterns. Normal operation prioritizes architectural ambience; seasonal modes can be showy.

The FSM is indifferent to which visual style is selected — it triggers an `ascending` preset or a `descending` preset, and the preset itself defines the visual. Ascending and descending preset IDs can be identical (simple fade fallback) or mirrored (directional fade default) or distinct cascades without any FSM change.

### Preset IDs

| ID | Name | Purpose |
|----|------|---------|
| 1 | `off` | All pixels off |
| 2 | `accent_dim_warm` | Steady low-level warm glow. **Reserved** — not used in Phase 1 three-mode scope (on/off/emergency); preserved for possible future user-commanded bright/accent mode. |
| 3 | `ascending` | Motion detected ascending — fade-in or directional fade (origin: bottom) |
| 4 | `descending` | Motion detected descending — fade-in or directional fade (origin: top) |
| 5 | `hold_full` | Full brightness, warm white — traversal hold state |
| 6 | `fade_out` | Smooth fade from current to off |
| 7 | `ascending_dim` | Late-night dimmed ascending (reduced brightness, warmer color) |
| 8 | `descending_dim` | Late-night dimmed descending |
| 9 | `hold_dim` | Late-night dimmed hold |
| 10 | `emergency_bright_white` | Full bright cool white, instant on, no animation |
| 11 | `occlusion_warning` | Three amber pulses at 100% brightness, ~500 ms each. Fired on occlusion detection (see Occlusion Detection). Not gated by active window. |
| 20+ | `effect_*` | Holiday/seasonal presets (Phase 2) |

Preset IDs are referenced from Node-RED; preset *definitions* (colors, speeds, brightness, visual style) are tuned in the WLED UI during commissioning.

### Time-of-day variants

The FSM selects which preset set to use based on time of day:

| Window | Ascending | Descending | Hold |
|--------|-----------|------------|------|
| Evening (active window start → 22:00) | 3 | 4 | 5 |
| Late night (22:00 → 05:00) | 7 | 8 | 9 |
| Pre-dawn (05:00 → active window end) | 3 | 4 | 5 |

Windows are configurable. Schedex publishes time-of-day state; the FSM reads it at traversal start.

---

## MQTT Topics

### State Topics (Retained)

All state topics publish JSON per Highland's MQTT conventions. See **State Topic Payloads** below for full schemas.

| Topic | Summary |
|-------|---------|
| `highland/state/stair_lights/mode` | Effective mode: `on` \| `off` \| `emergency`, plus timestamp |
| `highland/state/stair_lights/occupancy` | Occupancy FSM state with direction, timestamp, last sensor, and degraded flag |
| `highland/state/stair_lights/active_window` | Active window state with gate source |
| `highland/state/stair_lights/preset` | Currently-running WLED preset |

### State Topic Payloads

**`highland/state/stair_lights/mode`**

```json
{
    "mode": "on",
    "since": "2026-04-20T14:32:18Z"
}
```

- `mode`: current effective mode (`on` \| `off` \| `emergency`)
- `since`: ISO8601 UTC timestamp of when this mode was entered

**`highland/state/stair_lights/occupancy`**

```json
{
    "state": "occupied",
    "direction": "ascending",
    "since": "2026-04-20T14:32:18Z",
    "last_sensor": "stairs_bottom",
    "degraded": false
}
```

- `state`: FSM state (`unoccupied` \| `occupied` \| `clearing`)
- `direction`: direction of travel when meaningful (`ascending` \| `descending` \| `unknown`); `null` when `state` is `unoccupied`. `unknown` is used when one sensor is occluded and first-fire direction inference is unavailable.
- `since`: ISO8601 UTC timestamp of when this state was entered
- `last_sensor`: most recently fired sensor (`stairs_top` \| `stairs_bottom` \| `null` if no motion since Node-RED startup). Preserved across state transitions for debugging — updates only on new motion events.
- `degraded`: `true` if running in single-sensor-degraded mode (one sensor occluded)

**`highland/state/stair_lights/active_window`**

```json
{
    "active": true,
    "gate_source": "schedule",
    "since": "2026-04-20T14:32:18Z"
}
```

- `active`: whether motion response is currently armed (`true` \| `false`)
- `gate_source`: which input is causing activation (`schedule` \| `lux` \| `both` \| `none`). `none` when `active` is `false`.
- `since`: ISO8601 UTC timestamp of when the window last changed

**`highland/state/stair_lights/preset`**

```json
{
    "preset_id": 3,
    "name": "ascending",
    "since": "2026-04-20T14:32:18Z"
}
```

- `preset_id`: integer WLED preset ID currently on the strip
- `name`: human-readable preset name (informational)
- `since`: ISO8601 UTC timestamp of when this preset was commanded

### Event Topics (Not Retained)

| Topic | Fires on |
|-------|----------|
| `highland/event/motion/stairs_top` | Top ToF detection |
| `highland/event/motion/stairs_bottom` | Bottom ToF detection |
| `highland/event/stair_lights/traversal` | Completed traversal (ascending or descending) |

### Command Topics (Not Retained)

| Topic | Payload |
|-------|---------|
| `highland/command/stair_lights/mode` | `{"mode": "on" \| "off"}` — emergency cannot be commanded directly |
| `highland/command/stair_lights/preset` | `{"preset_id": N}` — direct preset trigger (debug/testing) |

### Status Topics (LWT-Retained)

| Topic | Source | Payload |
|-------|--------|---------|
| `highland/status/sensor/stairs_top` | Top ESP32 | `online` \| `offline` (LWT) |
| `highland/status/sensor/stairs_bottom` | Bottom ESP32 | `online` \| `offline` (LWT) |
| `highland/status/sensor/stairs_top/occluded` | Top ESP32 | `true` \| `false` (retained) |
| `highland/status/sensor/stairs_bottom/occluded` | Bottom ESP32 | `true` \| `false` (retained) |
| `highland/status/wled/stairs` | WLED controller | WLED native LWT |

### WLED Native Topics

WLED publishes to its own native topic namespace by default. Node-RED bridges between Highland's namespace and WLED's:

- `wled/stairs/api` ← Node-RED publishes preset commands here (WLED API string format)
- `wled/stairs/state` ← WLED state (JSON)
- `wled/stairs/g` ← WLED brightness
- `wled/stairs` ← WLED online/offline LWT

The bridging flow subscribes to Highland topics and translates to WLED topics.

---

## Configuration

### `config/thresholds.json`

Subsystem decision thresholds under the `stair_lighting` key (see #45 for convention).

```json
"stair_lighting": {
    "outdoor_lux_enable": 200,
    "outdoor_lux_disable": 500,
    "lux_stale_minutes": 10
}
```

### Subsystem parameters (location TBD)

Timing parameters, preset mappings, and active window offsets. These are not thresholds and may land in a dedicated subsystem config file, or in an environment variables block on the Node-RED tab, pending resolution of the broader config taxonomy in #45.

```json
{
    "timeouts": {
        "ascending_seconds": 8,
        "descending_seconds": 8,
        "traversing_seconds": 5,
        "fade_seconds": 2
    },
    "active_window": {
        "schedule_offset_minutes": 0
    },
    "time_of_day": {
        "late_night_start": "22:00",
        "late_night_end": "05:00"
    },
    "presets": {
        "off": 1,
        "accent_dim_warm": 2,
        "ascending": 3,
        "descending": 4,
        "hold_full": 5,
        "fade_out": 6,
        "ascending_dim": 7,
        "descending_dim": 8,
        "hold_dim": 9,
        "emergency": 10,
        "occlusion_warning": 11
    }
}
```

---

## HA Integration

HA exposes a dashboard tile for manual mode control. Entities are created via MQTT Discovery published by Node-RED (not by WLED's native HA integration — which would violate the Node-RED-as-authoritative principle).

| Entity | Type | Purpose |
|--------|------|---------|
| `select.stair_lights_mode` | `select` | Dropdown for mode selection (on/off). Emergency is not user-selectable. |
| `sensor.stair_lights_occupancy` | `sensor` | Informational occupancy state (unoccupied/occupied/clearing) with direction attribute |
| `binary_sensor.stair_lights_active_window` | `binary_sensor` | Whether motion is currently armed |
| `sensor.stair_lights_preset` | `sensor` | Current preset (informational) |

WLED's native HA integration is **not enabled** for this controller. All HA visibility is through Node-RED-published Discovery entities subscribed to Highland topics.

---

## Resilience and Recovery

### WLED Disconnect

If the WLED controller loses MQTT connection or reboots, its LWT flips to `offline` on `highland/status/wled/stairs`. Node-RED handles recovery deterministically rather than relying on WLED's own state retention.

**Controller power-on behavior:** WLED is configured to boot to preset 1 (`off`). This gives a known-clean starting point every time the controller comes up — no surprise full-bright wake from a mid-scene reboot, no stale preset from before the disconnect.

**Node-RED recovery behavior:** On observing WLED's LWT flipping from `offline` to `online`, Node-RED republishes the preset corresponding to the current FSM state:

- `unoccupied` → preset 1 (`off`)
- `occupied` → current direction-aware preset (or generic fade-in in degraded mode)
- `clearing` → `fade_out` preset, or resolve directly to `off` if the fade window has already elapsed

This handles common cases cleanly:

- *Brief disconnect with no state change:* WLED boots dark, Node-RED immediately republishes the correct preset. User sees a sub-second dark flicker at most.
- *Long disconnect where FSM has advanced:* WLED boots dark, Node-RED republishes whatever the FSM state dictates now (usually `off`, since long disconnects are likely outside active traversal).
- *Disconnect mid-traversal that resolves quickly:* WLED boots dark, Node-RED re-runs the current cascade. Might look slightly weird (fade-in restarts partway through), but the scenario is rare enough to accept without additional logic.

No dedicated disconnect notification for Phase 1 — WLED LWT is already consumed by Highland's standard health monitoring and notifies through that path.

### Boot-time Recovery

When Node-RED restarts, state is restored from retained MQTT topics. The subsystem comes up in a consistent state without requiring any manual intervention.

**Occupancy FSM state restoration:**

On boot, the flow reads `highland/state/stair_lights/occupancy`:

- `unoccupied` or no retained value → start in `unoccupied`, normal operation
- `clearing` → start in `unoccupied`. A partial fade cannot be meaningfully resumed, and the brief visual oddity of the strip going dark on Node-RED restart is acceptable.
- `occupied` → start in `occupied` and begin a **fresh** quiescence timer. Normal FSM rules take over — any motion event extends the timer, absence of motion ages out to `clearing` after 90 s.

The last case handles the worst scenario cleanly: Node-RED crashed mid-traversal and was down longer than the traversal. Retained state claims `occupied` even though the person has long since left. The fresh post-boot quiescence timer ages out in 90 seconds, bounding the recovery window. No manual reset required and no risk of getting stuck in `occupied` indefinitely.

**Sensor LWT consideration:**

Sensor LWTs come in retained. On boot, Node-RED checks `highland/status/sensor/stairs_top` and `highland/status/sensor/stairs_bottom` before trusting any per-sensor state:

- If either LWT reports `offline`, the corresponding `highland/status/sensor/stairs_{top,bottom}/occluded` retained topic is stale and is not used. The FSM treats that sensor as "unknown state" until it comes back online.
- Once the sensor's LWT flips to `online`, its retained occlusion flag becomes authoritative again.

This prevents running in single-sensor-degraded mode based on a stale occlusion flag from a sensor that isn't actually reporting — a subtle failure mode that would otherwise leave the subsystem in a strange half-state after an unclean shutdown of a sensor node.

**Active window and mode state restoration:**

`highland/state/stair_lights/active_window` is retained and recomputed immediately on boot from current schedule + lux inputs. `highland/state/stair_lights/mode` is retained and picked up directly — Node-RED resumes in whatever mode was active before the restart (almost always `on`). No special handling required for either.

---

## Phase Plan

### Phase 1 — Core Subsystem (This Design)

- GLEDOPTO GL-C-015WL-D controller co-located with PSU in adjacent bedroom
- Single continuous RGB IC FCOB strip (WS2811 protocol, 12V)
- Standard single-chamber aluminum U-channel with frosted diffuser
- 14/3 stranded CL2-rated cable from bedroom controller to top of staircase
- Two M5Stack Atom sensor nodes (Atom Lite + Atomic RS485 Base + ToF Unit U010), integrated into channel at each end, tapping strip pads for power
- ToF optical ports fabricated in channel walls with clear windows
- Signal conditioning module (74AHCT125 level shifter + decoupling + damping resistor) at top end of channel
- Motion-triggered operation with schedule + lux gating
- HA dashboard for manual mode control
- No physical switches

### Phase 2 — Enhancements

- **ZEN37 magnetic wall remotes** at top and bottom of stairs. Magnetic fake-wall-plate mount — no switched wiring required. Pattern matches the garage bay remote. Gives the user manual bump-to-full and mode cycling without requiring phone access.
- **Themed preset packs** — seasonal variations on the motion-triggered cascade presets. Halloween (orange/purple gradient), Christmas (red/green alternation), birthdays, etc. These are not independent modes — they substitute the normal `ascending`/`descending`/`hold` preset IDs during `on` mode's motion cycles, triggered by calendar or scheduled date ranges. The FSM behavior is unchanged; only the visual choreography swaps. Decorative effects never run independently — the primary function of this lighting is functional, not decorative.
- **Emergency triggers** — wire in smoke/CO alarm state and security alarm state to drive `emergency` mode. Requires the security subsystem to be live. (Note: power recovery is explicitly not an emergency trigger — see Emergency Triggers.)

### Phase 3 — Speculative

- Electrician-installed outlet at top of stairs, relocating PSU from bedroom
- Second staircase (if applicable)

---

## Open Questions

- [ ] Landing inclusion — strip terminates at top step, or extends across upper landing?
- [ ] Wall-side mounting height — skirt-top, mid-wall, or recessed into skirt board?
- [ ] U-channel SKU selection — confirm interior dimensions accommodate the Atom+Base stack alongside the strip at endpoints, evaluate diffuser appearance
- [ ] ToF optical port fabrication technique — refine at bench POC (drill size, countersink depth, window material and bond)
- [ ] Splice plan for the full run — most commercial channels ship in 1 m / 2 m lengths
- [ ] Atom variant — Atom Lite vs AtomS3 Lite; confirm RS485 Base compatibility with whichever is selected
- [ ] Confirm U010 FoV is adequate in actual install geometry; pivot to U172 if opposite-wall reflections cause false triggers
- [ ] Validate Atom-reboot recovery as sufficient for hung-sensor scenarios; promote MOSFET-switched power to Phase 1 if not
- [ ] Finalize ToF aiming geometry against real sensor performance — cat false positives vs. missed detections at knee height
- [ ] Confirm at bench POC whether data signal is clean over 15–20 ft without signal conditioning — if yes, signal conditioning module could be simplified or dropped
- [ ] Calibrate outdoor lux thresholds from observed Tempest data over several weeks
- [ ] Decide home for subsystem parameters (timeouts, preset mappings) — dependent on #45 outcome
- [ ] Validate PSU headroom against measured full-brightness draw at commissioning
- [ ] Determine whether schedex time-of-day state deserves its own utility flow or lives in the stair lighting flow directly
- [ ] Confirm `highland/state/stair_lights/occupancy` topic name — previous iteration used `fsm`; occupancy reads cleaner for consumers but depends on whether any other subsystems have already subscribed to the old name

---

*Last Updated: 2026-04-20*
