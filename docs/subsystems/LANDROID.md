# Landroid Integration

Integration of Worx Landroid Vision robotic mower into Highland.

**Status:** 📋 Phase 1 planned — hardware in-hand, base sited. `landroid_cloud` integration and dashboard card are the immediate next step.

---

## Hardware

**Worx Landroid Vision 1-Acre 4WD (WR344)**

- Wire-free boundary detection via camera/AI (no perimeter wire required)
- Built-in WiFi; communicates exclusively via Worx cloud (AWS IoT Core)
- No local API
- Sited: side yard (late afternoon direct sun only)
- Zones mapped: side yard (0.02 acres), rear yard (0.09 acres); front yard (est. 0.25–0.30 acres) mapping in progress

**Reolink Argus Eco Ultra + Solar Panel** *(to be purchased)*

- 4K, 125° FOV, color night vision
- Solar/battery powered, WiFi
- Strap mounts included for both camera and solar panel — supports tree or pole mounting where no fixed structure is available
- Connects to the RLN16-410 NVR over WiFi; NVR exposes RTSP stream to the Highland video pipeline identically to PoE cameras

---

## Integration Philosophy

Connectivity is **additive nicety, not a requirement.** The mower functions fully as a standalone appliance without any Highland integration. If the integration breaks temporarily or permanently, the mower continues to operate on its own schedule with its own onboard rain sensor — exactly as most users run their mower. This shapes every integration decision: keep it thin, keep it optional, don't build dependencies on it.

This also drives the phasing decision. The mower is an **informational device** from Highland's perspective, not a critical automation surface. Building a custom MQTT bridge before establishing baseline infrastructure is not a good use of time. Phase 1 uses the existing `landroid_cloud` community integration to get visibility and a control surface quickly. Phase 2, if warranted, migrates to a custom bridge for deeper Node-RED integration.

---

## Mowing Schedule

| Day | Zone |
|-----|------|
| Monday | Front |
| Tuesday | Rear + Side |
| Wednesday | Front |
| Thursday | Rear + Side |
| Friday | Front |
| Saturday | Rear + Side |
| Sunday | Off |

Front yard gets 3 sessions per week given its larger size (est. 0.25–0.30 acres) and visibility. Rear and side run together; the side yard is small enough (0.02 acres) that it adds negligible time to a rear session. Schedule configured in the Worx app; Highland does not own scheduling in Phase 1.

---

## Phase 1: `landroid_cloud` HA Integration

### Approach

Install the `landroid_cloud` HACS custom component. It handles all AWS MQTT complexity internally via `pyworxcloud`, creates well-formed HA entities automatically, and requires no custom infrastructure. This is the right starting point for an informational device — low setup cost, immediately useful, and the fallback behavior (mower runs autonomously) is acceptable.

### Installation

1. Install `landroid_cloud` via HACS
2. Restart Home Assistant
3. Add the integration via Settings → Devices & Services using Worx app credentials
4. Install `landroid-card` via HACS for the dashboard

### Entities Created

| Entity | Type | Notes |
|--------|------|-------|
| Lawn mower | `lawn_mower` | Primary control entity |
| GPS tracker | `device_tracker` | Disabled by default; expected to provide real-time position on WR344 given built-in RTK Cloud positioning — validate after install |
| Next schedule | Sensor | Timestamp + schedule details as attributes |
| Battery | Sensor | Charge level |
| Error state | Sensor | Current error code |
| Rain delay | Sensor | Active/inactive + remaining time |

**Supported mower states:** mowing, docked, returning, error, edge cut, starting, rain delayed, escaped digital fence

**Vision-series limitation:** Zones and schedules are read-only via this integration — they can be read from the mower but not modified from HA. This is acceptable; schedule management stays in the Worx app.

### Available HA Actions

- `lawn_mower.start_mowing` — send mower out
- `lawn_mower.dock` — stop and return to base
- `lawn_mower.pause` — stop in place
- `landroid_cloud.ots` — one-time schedule (start with runtime parameter)

### Dashboard

Install `landroid-card` (by Barma-lej, available via HACS) for a purpose-built mower dashboard card. Displays mower state, battery, error status, and control buttons in a single card.

### Known Fragility

- Depends on Worx cloud API remaining stable and accessible
- A March 2025 release caused excessive API retries that locked up HA — resolved since, but worth noting as a precedent for fragility
- `pyworxcloud` is reverse-engineered; Worx API changes can break it without notice

These risks are acceptable given the "additive nicety" posture. If the integration breaks, the mower continues to operate autonomously.

---

## Phase 1 Automations

All automations in Phase 1 live in HA (YAML automations), not Node-RED. This is consistent with the principle of not building custom Node-RED subsystems for informational devices before baseline infrastructure is complete.

### Rain Suppression

If the mower starts a mow session and conditions indicate it should not be running, send `lawn_mower.dock` to return it to base. Conditions evaluated:

- **Active rainfall** — Tempest rainfall rate above threshold
- **Recent accumulation** — Tempest accumulated rainfall in the last N hours above threshold (ground saturation)
- **Imminent rain** — NWS precipitation probability within the session window above threshold

The mower's own onboard rain sensor handles the obvious cases. HA automation supplements with Tempest and NWS data for the cases the onboard sensor cannot see — primarily recent accumulation and forecast-based suppression.

Thresholds cannot be set meaningfully before a full mow season of observed Tempest data. Start conservative and tune based on actual ground conditions and observed mower behavior.

**Design note:** This is reactive, not predictive. The mower starts on its own schedule; HA evaluates conditions and sends it home if warranted. There is no way to suppress a scheduled start preemptively without taking scheduling away from the Worx app entirely, which is not a Phase 1 goal.

### Error Notification

When `sensor.vision_cloud_4wd_error` transitions away from `no_error`, send a notification. The sensor returns human-readable string values from a fixed map in `landroid_cloud` — routing logic matches directly against these strings.

**Error state map and notification tiering:**

| State value | Meaning | Routing |
|-------------|---------|---------|
| `lifted` | Mower lifted unexpectedly | 📺 TV + mobile (urgent — primary theft signal) |
| `trapped` | Mower stuck | 📺 TV + mobile (urgent — needs intervention) |
| `trapped_timeout` | Stuck for extended period | 📺 TV + mobile (urgent) |
| `upside_down` | Mower fell over | 📺 TV + mobile (urgent — blades may have been running) |
| `outside_wire` | Escaped the mowing zone | 📺 TV + mobile (urgent) |
| `excessive_slope` | Reached terrain it can't handle | 📺 TV + mobile (urgent) |
| `unreachable_charging_station` | Cannot return to base | 📺 TV + mobile (urgent — won't recover on its own) |
| `blade_motor_blocked` | Blade obstruction | 📱 Mobile only |
| `wheel_motor_blocked` | Wheel obstruction | 📱 Mobile only |
| `charge_error` | Charging fault | 📱 Mobile only |
| `battery_temperature_error` | Battery thermal fault | 📱 Mobile only |
| `map_error` | Navigation map failure | 📱 Mobile only |
| `mapping_exploration_failed` | Could not complete mapping | 📱 Mobile only |
| `camera_error` | Vision AI camera fault | 📱 Mobile only |
| `missing_charging_station` | Cannot locate base | 📱 Mobile only |
| `timeout_finding_home` | Timed out returning to base | 📱 Mobile only |
| `close_door_to_mow` | User action required to start | 📱 Mobile only |
| `close_door_to_go_home` | User action required to return | 📱 Mobile only |
| `charging_station_docking_error` | Docking fault | 📱 Mobile only |
| `insufficient_sensor_data` | Sensor fusion failure | 📱 Mobile only |
| `training_start_disallowed` | Training blocked | 📱 Mobile only |
| `mapping_exploration_required` | Mapping required before mowing | 📱 Mobile only |
| `blade_height_adjustment_blocked` | Height adjustment fault | 📱 Mobile only |
| `unknown` | Unrecognized error code | 📱 Mobile only |
| `rain_delay` | Rain delay active | 📋 Daily Digest only |
| `battery_low` | Low battery | 📋 Daily Digest only (mower self-recovers) |
| `locked` | Mower is locked | 📋 Daily Digest only (deliberate state) |
| `battery_trunk_open_timeout` | Battery compartment issue | 📋 Daily Digest only |
| `wire_missing` | Wire not detected | 🔇 Log only (wire-based error; should not fire on WR344) |
| `reverse_wire` | Wire polarity error | 🔇 Log only (wire-based error; should not fire on WR344) |
| `wire_sync` | Wire synchronization error | 🔇 Log only (wire-based error; should not fire on WR344) |
| `ota_error` | Firmware update failed | 🔇 Log only (not user-actionable) |
| `hbi_error` | Hardware bus error | 🔇 Log only (contact support) |
| `rfid_reader_error` | RFID reader fault | 🔇 Log only (contact support) |
| `headlight_error` | FiatLux headlight fault | 🔇 Log only (contact support) |

**Wire-based error codes** (`wire_missing`, `reverse_wire`, `wire_sync`) should not fire on the WR344 which has no perimeter wire. If they do appear, log them and investigate — they may indicate something unexpected in Vision firmware behavior.

**TV notification** uses the existing Android TV notification infrastructure (HA Companion `notify.mobile_app_*` with `androidtv` target). Include a concise title and the friendly error description. The existing `largeIcon`, `smallIcon`, and `smallIconColor` fields apply.

### Daily Digest Contribution

Expose mower state, last mow timestamp, battery level, and any active errors or rain delay as HA sensor data. Node-RED Daily Digest flow consumes these via HA state for inclusion in the morning digest.

---

## Security & Anti-Theft

### Threat Model

Two distinct theft vectors:

**Vector 1 — Stolen from dock (mower charging/idle)**
The mower is sitting on the base between mow sessions. Someone picks it up and walks off.

**Vector 2 — Stolen while running (mower actively mowing)**
Someone intercepts the mower mid-cycle in the yard.

### On-Device Defenses

The mower has built-in security independent of any Highland integration:

- **Lift sensor** — physical tilt/lift sensor always active when powered on; stops blades immediately on lift regardless of mowing state
- **Security PIN** — locks the mower; wrong PIN triggers audible alarm
- **Lock function** — when enabled, triggers an audible alarm if the mower is lifted and carried outside the yard perimeter (GPS/geofence-based on Vision models)
- **WiFi kill** — mower stops operating after three consecutive days outside WiFi coverage

### Dedicated Security Camera

A **Reolink Argus Eco Ultra** pointed at the charging base serves as the primary real-time detection layer. Camera is tree-mounted pointing directly at the dock; exact tree TBD once a suitable candidate is confirmed, but mount type and orientation are settled.

The mower base is a fixed, known scene. The camera has a single binary question to answer: *is a person interacting with the mower?* This makes analysis cheaper, faster, and higher-confidence than general perimeter surveillance.

The prompt pattern:

> *"This is a fixed camera pointed at a robotic lawn mower on its charging base. Does the image show a person approaching, touching, lifting, or otherwise interacting with the mower? The mower should be stationary and unattended. Answer yes/no and briefly describe what you see."*

**Pipeline** (see `subsystems/VIDEO_PIPELINE.md`):
1. **CPAI triage** — person detection gate; no person in frame → discard
2. **Gemini snapshot analysis** — focused prompt above; yes → escalate
3. **Notification** — immediate push via Utility: Notifications with keyframe attached

**Two-signal confirmation:**

| Signal | Vector 1 (docked) | Vector 2 (running) | Latency |
|--------|-------------------|--------------------|---------|
| Camera (person detection) | ✅ | ✅ (if in frame) | Seconds |
| HA error state (lift error) | ✅ (with delay caveat) | ✅ | Seconds–minutes |

**Camera siting:**

- Tree-mounted, pointing directly at the dock
- Mount at roughly 6–8 feet; higher loses the human-object interaction geometry
- FOV should cover a 10–15 foot approach radius, not just the base itself
- Avoid pointing into late afternoon sun — if solar panel orientation and lens direction conflict, favor the lens angle; afternoon sun in this location is strong enough that an oblique panel angle still charges adequately

**Authorized interaction handling:** Accept false positives during legitimate maintenance. Time-of-day context handles most ambiguity — a 3am alert is categorically different from a 2pm one.

---

## Phase 2: Custom MQTT Bridge (If Warranted)

Phase 2 is only worth pursuing if one or more of the following is true:

- The `landroid_cloud` integration proves consistently unreliable
- Automation needs grow beyond what HA can deliver (e.g., Node-RED-owned scheduling, zone targeting, deeper Tempest/NWS synthesis)
- The "HA is consumer only" architectural principle becomes a friction point for the mower specifically

### Approach

A **Mosquitto bridge** on the Communication Hub connects directly to Worx's AWS IoT Core endpoint, bypassing the `landroid_cloud` integration entirely. Node-RED becomes the normalization and automation engine; HA receives entities via MQTT Discovery.

```
Mower ──► AWS IoT Core ──► Mosquitto Bridge ──► highland/state/landroid/#
                                             ◄── highland/command/landroid/#
```

### Setup Requirements (One-Time)

1. Authenticate against the Worx REST API to retrieve TLS cert and AWS endpoint
2. Write `landroid-bridge.conf` for Mosquitto (drop-in, does not modify main config)
3. Cert files live in `/etc/mosquitto/certs/landroid/` on the Communication Hub

**Known fragility:** Worx has historically rotated certs without notice. Re-running cert extraction resolves it. Acceptable given the additive nicety posture.

### Reference Material

Community reverse engineering sources, in order of usefulness:

- **`virtualzone/landroid-bridge`** — original cert extraction and bridge documentation; read the source
- **`pyworxcloud` (PyPI/GitHub)** — most up-to-date payload field mappings and command structures; `dump_mapping.py` decodes raw MQTT payloads into human-readable output
- **`iobroker.worx`** — well-maintained ioBroker adapter; good secondary reference for payload structures
- **HA Community forums** — search "Landroid MQTT bridge"; practical notes from people who've done it
- **`roboter-forum.com`** — German robotic mower forum; raw payload samples posted; Google Translate sufficient

**Critical caveat:** Virtually all community documentation is from wire-based models. Vision-series payload behavior needs validation against observed WR344 payloads. The first MQTT Explorer session watching a live mow cycle is primary research — WR344-specific payload documentation is sparse.

### Phase 2 Validation Items

Before building the Node-RED normalization flow, validate against observed WR344 payloads:

- Exact topic prefix (community reports `PRM100` but may be model-dependent)
- Exact `dat.le` error code for lifted state on Vision hardware
- Whether `dat.le` fires immediately while docked or only during active mow
- Whether position coordinates appear in `commandOut` payload (would enable live position on HA map card)
- Whether zone targeting via `commandIn` works on Vision firmware (would enable Node-RED-owned scheduling)

---

*Last Updated: 2026-05-04*
