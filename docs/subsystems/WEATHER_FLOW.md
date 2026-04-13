# Weather Flow — Design & Architecture

## Implementation Status

**Tier 1 — NWS (Live)**

Two separate Node-RED flows, both built and publishing:

- **`Utility: Weather Forecasts`** — Resolves NWS grid coordinates on startup and daily at 23:55 (cronplus), storing the forecast URL in flow context. Fetches normalized 7-day forecast hourly and on startup. Publishes `highland/state/weather/forecast` with a date-keyed period map including NWS condition codes, temperature, precip chance, and wind.
- **`Utility: Weather Alerts`** — Polls NWS active alerts endpoint every 30 seconds. Tracks alert lifecycle (new/updated/expired) in flow context. Publishes `highland/state/weather/alerts` retained snapshot and fires three lifecycle event topics.

**Primary consumer:** Both Tier 1 flows feed the `Utility: Daily Digest` — the forecast and any active alerts are included in the nightly digest email sent to household residents.

**Tier 2 — Weather Analysis (Live)**

`Utility: Weather Analysis` is live and polling PirateWeather every minute (1-minute continuous cadence on paid PirateWeather plan — rate limiter removed). It provides:
- Minutely precipitation threat analysis with AccuWeather-style messaging
- `threat_type` entity state (title-cased type, e.g. `Rain`) — cleaner history than full message string
- Per-minute `minutely` array in state payload for dashboard chart rendering
- Persistent HA Companion notifications for imminent precipitation events
- Tempest cross-validation (PirateWeather forecast vs. ground truth)
- `highland/state/weather/analysis` retained state topic
- HA Discovery sensor (`sensor.weather_analysis` under `Highland Weather Analysis` device)

**Tier 2 — Weather Alerts (Enhanced — Live)**

`Utility: Weather Alerts` has been significantly enhanced beyond its original Tier 1 implementation:
- Three-tier deduplication (formal supersession → VTEC → heuristic) reduces duplicate NWS alerts to a single authoritative entry
- Pre-filter removes alerts with `expires` in the past regardless of NWS feed inclusion
- `highland/state/weather/alerts` consolidated to serve both operational consumers and HA Weather Alerts Card sensor entity
- Per-alert notifications via `Build Notifications` group with proximity-aware severity mapping
- HA Discovery sensor (`sensor.weather_alerts` under `Highland Weather Alerts` device)
- Weather Alerts Card (HACS) installed and configured on weather dashboard

Remaining Tier 2 work: `Utility: Weather Lightning` for hyperlocal lightning notifications (designed, not yet implemented — see issue #26), threshold calibration from observed weather events, and MinuteCast dashboard visualization (see issue #30).

The full synthesis layer (`highland/state/weather/conditions`) combining Tempest observations with PirateWeather data into a unified conditions snapshot is deferred until a concrete consumer need emerges.

---

## Overview

Node-RED utility flow providing weather awareness, precipitation sensing, and actionable notifications. Designed around a polling state machine that scales API call frequency to actual weather threat level, keeping costs predictable while maintaining responsiveness during active events.

---

## Data Sources

The Weather flow is a **black box synthesizer**. Multiple ingestion paths feed a single authoritative output. Source attribution never appears in published topics — the rest of the system sees one curated weather service.

### WeatherFlow Tempest Station

Local physical station publishing real-time observations via MQTT to the bus.

**Data provided:** Temperature, humidity, dew point, pressure, wind speed/gust/bearing, UV index, solar radiation, lightning strike distance and energy (hardware detection), local precipitation (optical sensor).

**Role:** Observations are ground truth for current conditions. Tempest data takes priority over model data for present-moment values. Lightning events come exclusively from Tempest hardware detection.

### Pirate Weather API

**Selected over Weatherbit:** ~$5/month vs $45/month; strong HRRR model sourcing for near-term minutely precision.

**Base URL:** `https://api.pirateweather.net/forecast/[apikey]/[lat],[lon]`

**Standard parameters — always include:** `?units=us&version=2`
- `units=us` — Fahrenheit, inches, mph. Confirmed from live response.
- `version=2` — Unlocks type-specific precipitation fields (`rainIntensity`, `snowIntensity`, `iceIntensity`), accumulation fields, `cape`, and ensemble spread (`precipIntensityError`). Always include; no reason not to.

**Credentials:** `config.secrets.weather_api_key`

### Location Context

Coordinates stored in `secrets.json` (lat/lon are private). Nearest API city: Gardnertown — confirms coordinate-based grid lookup, not population-center snap.

**Microclimate note:** Hudson Valley terrain creates significant hyperlocal variation. Forecasts snapped to "Newburgh" would often be wrong for this location. The HRRR grid cell resolves to approximately 1.5 mi offset.

---

## Known API Quirks

**`-999` sentinel value:** Several fields return `-999` when data is unavailable rather than null. Must null-guard in processing. Confirmed fields: `smoke`, `fireIndex` (only available for ~first 36 hours), `smokeMax`, `fireIndexMax` in daily.

**`sleetIntensity` in minutely:** Present in live response but undocumented. Treat as bonus data, not guaranteed.

**Rate limit headers:** `Ratelimit-Limit`, `Ratelimit-Remaining`, `Ratelimit-Reset`, `X-Forecast-API-Calls`. Log `X-Forecast-API-Calls` on every response. Alert at >50% quota before 50% through month; alert at >80% before 80% through month.

---

## Polling State Machine

The core cost-control mechanism. Polling frequency scales with weather threat level.

| State | Poll Interval | Purpose |
|-------|---------------|---------|
| `POLL_DORMANT` | 15 min | No precipitation expected; baseline monitoring |
| `POLL_MONITOR` | 5 min | Precipitation possible within forecast window |
| `POLL_ACTIVE` | 1 min | Precipitation imminent or occurring; full resolution |
| `POLL_LIGHTNING` | 1 min | Thunderstorm active |

### State Transitions

**DORMANT → MONITOR:** Any hourly block within next 6 hours: `precipProbability > 0.40`

**MONITOR → ACTIVE:** Any minutely block: `precipIntensity > 0.01 in/hr` AND `precipProbability > 0.70`, OR any hourly within next 2 hours: `precipProbability > 0.80`

**ACTIVE → MONITOR:** All minutely blocks below `0.005 in/hr` for 30 consecutive minutes AND no hourly within next 2 hours exceeds `0.70`

**MONITOR → DORMANT:** All hourly within next 6 hours below `0.25`; hold minimum 15 min in MONITOR before allowing transition

**Any → LIGHTNING:** `cape > 2500 J/kg` in current or next 2 hourly blocks AND `precipProbability > 0.50`

**LIGHTNING → ACTIVE:** `cape < 500 J/kg` sustained for 2 consecutive polls AND no lightning detection

### `precipIntensityError` as Confidence Gate

`precipIntensityError` is the standard deviation of `precipIntensity` across GEFS/ECMWF ensemble members. If `precipIntensityError > precipIntensity * 0.75`, treat the forecast as lower confidence and require higher `precipProbability` to trigger escalation.

### `exclude` Parameter Strategy

| State | `exclude` parameter |
|-------|---------------------|
| `POLL_DORMANT` | `minutely,alerts` |
| `POLL_MONITOR` | `alerts` |
| `POLL_ACTIVE` | *(none)* |
| `POLL_LIGHTNING` | *(none)* |

---

## Precipitation State Machine

Tracks the current precipitation event lifecycle. Separate from polling state.

| State | Description |
|-------|-------------|
| `PRECIP_NONE` | No precipitation |
| `PRECIP_IMMINENT` | High probability within 30 minutes per minutely |
| `PRECIP_ACTIVE` | Currently precipitating |
| `PRECIP_TAPERING` | Intensity declining; event may be ending |
| `PRECIP_DONE` | Event ended; cooling-off before returning to NONE |

### Precipitation Type Tracking

Uses v2 type-specific intensity fields: `rainIntensity`, `snowIntensity`, `iceIntensity`, `currentDaySnow`, `currentDayLiquid`, `currentDayIce`.

**Derived `precipitation_type` enum:** `rain` | `snow` | `ice` | `sleet` | `mixed` | `none`

---

## NWS Forecast — Icon Code Reference

NWS condition codes are extracted from the NWS icon URL path. Priority hierarchy resolves compound URLs — precipitation/severe codes take precedence over cloud cover codes.

| NWS Code | Condition | Meteocons Day | Meteocons Night |
|----------|-----------|---------------|-----------------|
| `skc` | Clear | `clear-day` | `clear-night` |
| `few` | Few clouds | `partly-cloudy-day` | `partly-cloudy-night` |
| `sct` | Scattered clouds | `partly-cloudy-day` | `partly-cloudy-night` |
| `bkn` | Broken clouds | `overcast-day` | `overcast-night` |
| `ovc` | Overcast | `overcast` | `overcast` |
| `rain` | Rain | `rain` | `rain` |
| `rain_showers` | Rain showers | `partly-cloudy-day-rain` | `partly-cloudy-night-rain` |
| `snow` | Snow | `snow` | `snow` |
| `snow_showers` | Snow showers | `partly-cloudy-day-snow` | `partly-cloudy-night-snow` |
| `tsra` | Thunderstorm | `thunderstorms-day` | `thunderstorms-night` |
| `tsra_sct` | Scattered thunderstorms | `thunderstorms-day-rain` | `thunderstorms-night-rain` |
| `fzra` | Freezing rain | `sleet` | `sleet` |
| `wind_few` | Windy, few clouds | `wind` | `wind` |
| `fog` | Fog | `fog-day` | `fog-night` |
| `blizzard` | Blizzard | `snow` | `snow` |

---

## Configuration

All weather configuration lives in `weather.json`, loaded into `global.config.weather` by the Config Loader. This includes radar loop profiles, layer definitions, and Tier 2 polling thresholds. See `config/weather.json` in the repo for the current schema.

Tier 2 threshold values are initial estimates — calibrate against observed events.

---

## Weather Analysis

### Architecture

`Utility: Weather Analysis` polls the PirateWeather API for minutely forecast data, analyzes the 61-minute window for precipitation threats, cross-validates against Tempest ground truth, publishes a state topic, and manages persistent HA Companion notifications.

**Data source:** PirateWeather API v2 (`https://api.pirateweather.net/forecast/{key}/{lat},{lon}?units=us&version=2`). Full payload fetched on every cycle — no `exclude` parameter.

**Cadence:** CronPlus fires every minute. 1-minute continuous polling on paid PirateWeather plan (Pirate Plan, 60K calls/month). Rate limiter removed.

**Tempest cross-validation:** The flow subscribes to `highland/state/weather/station` and tracks `precipitation_type` in flow context. `Build Message` uses this to determine dry vs. active state, driving different message framing for the same PirateWeather analysis result.

### Flow Design

`Utility: Weather Analysis` groups:

| Group | Purpose |
|-------|--------|
| **Analysis Pipeline** | Entry point: Begin Analysis Cycle link in → link calls to Forecast Acquisition, Forecast Analysis, MinuteCast Notifications |
| **Forecast Acquisition** | link in → Build Request → Fetch Forecast → Extract Data → return link |
| **Forecast Analysis** | link in → Analyze Minutely → Build Message → Publish State → return link |
| **MinuteCast Notifications** | link in → Manage Notification → MQTT Out → return link |
| **Local Observations** | MQTT In (`highland/state/weather/station`) → Extract Station Data (flow context only, no output) |
| **Sinks** | CronPlus Poll (every minute, continuous) → link out to Begin Analysis Cycle |
| **Test Cases** | Force Threat + Force Clear synthetic data injectors |
| **Home Assistant Discovery** | On Startup → Build Discovery Config → MQTT Out |
| **Error Handling** | Catch All → debug |

### Minutely Analysis

`Analyze Minutely` scans the 61-minute array and produces precipitation windows — contiguous blocks of active minutes. A minute is considered active when both `precip_probability >= 0.60` AND `precip_intensity >= 0.02 in/hr`.

**Provisional thresholds (calibrate from observed events):**

| Parameter | Value | Notes |
|-----------|-------|-------|
| Trigger probability | ≥ 0.60 | Create/maintain notification |
| Clear probability | < 0.30 | Hysteresis — clear notification |
| Minimum intensity | ≥ 0.02 in/hr | Ignore trace amounts |
| Heavy rain | > 0.30 in/hr | Prefix "Heavy" |
| Heavy snow | > 0.10 in/hr | ~1–2 in/hr snow equivalent |
| Thunderstorm CAPE | > 500 J/kg | Replace type label with thunder variant |

### Message Generation

`Build Message` generates AccuWeather-style strings based on Tempest dry/active state and the analysis window, and sets `threat_type` to the title-cased precipitation type label (e.g. `Rain`, `Heavy Snow`, `Thunderstorms`):

**Dry state (Tempest confirms nothing active):**
- `"Rain starting in 10 minutes"` — single window, clean onset
- `"Periods of rain starting in 10 minutes"` — multiple windows (intermittent)
- `"Rain starting shortly"` — onset at minute 0

**Active state (Tempest confirms precipitation):**
- `"Rain continuing for at least 60 minutes"` — extends to end of window
- `"Rain stopping in 7 minutes"` — clears within window
- `"Periods of rain for the next 23 minutes"` — intermittent, then clears

**Type labels:** `Rain` | `Snow` | `Sleet` | `Rain and Snow` | `Rain and Sleet` | `Snow and Sleet` | `Rain, Snow, and Sleet`. Thunder modifier replaces primary type: `Thunderstorms` | `Thundersnow` | `Thundersleet`. Heavy modifier prefixes single-type rain or snow: `Heavy Rain` | `Heavy Snow`.

`threat_type` is `null` when `has_threat` is false. The HA entity state uses `threat_type` (falling back to `'Clear'`) rather than `message` to avoid churning history on every poll cycle as timing counts down.

### Published State

`Publish State` publishes `highland/state/weather/analysis` retained on every cycle. The payload includes:
- All analysis fields (`has_threat`, `onset_minutes`, `clear_minutes`, etc.)
- `threat_type` — title-cased type string or `null`
- `message` — full human-readable string or `null`
- `minutely` — compact 61-entry array `[{ t, i, p, k }]` for dashboard chart rendering

See `standards/MQTT_TOPICS.md` — Weather Analysis section for full payload schema.

### HA Discovery

`Home Assistant Discovery` group publishes `sensor.weather_analysis` config on startup. Entity state = `threat_type` (or `Clear`). All analysis fields including `minutely` available as attributes for `apexcharts-card` MinuteCast visualization (see issue #30).

### Notification Lifecycle

`Manage Notification` maintains a single persistent notification per household with `correlation_id: weather_precip_forecast`. Hysteresis via `flow.notification_active` prevents flicker at threshold boundaries:

- **Threat emerges** — publish to `highland/event/notify` with `notification_id: weather.precip_forecast`, `sticky: true`, radar loop image, `clickAction` tap to weather dashboard
- **Threat persists** — re-publish same `correlation_id` (HA replaces in-place)
- **Threat clears** — publish to `highland/command/notify/clear`; only fires once (hysteresis)

Notification subscription in `notifications.json`:
```json
"weather.precip_forecast": {
  "targets": ["people.joseph.ha_companion"],
  "severity": "low"
}
```

### MQTT Topics

See `standards/MQTT_TOPICS.md` — Weather Analysis section for full payload schema.

- `highland/state/weather/analysis` — retained, full threat analysis on every cycle

---

## Weather Alerts

### Architecture

`Utility: Weather Alerts` polls the NWS active alerts API every 30 seconds and manages a complete alert lifecycle including deduplication, expiry filtering, state publishing, and per-alert notifications.

**Data source:** NWS alerts API (`https://api.weather.gov/alerts/active?point={lat},{lon}`). NWS is the sole data source for alerts — both for notifications and for the dashboard sensor. PirateWeather was evaluated as an alternative but rejected: PirateWeather refreshes alert polygons every 10 minutes, which would create inconsistency between notification arrival and dashboard display.

### Deduplication

Three-tier deduplication runs on every poll cycle before any processing:

**Tier 1: Formal supersession** — `messageType: Update` + non-empty `references` array. Superseded alert IDs are filtered from the incoming set.

**Tier 2: VTEC deduplication** — For alerts with a `VTEC` parameter, group by `(office.phenomenon.significance.eventNumber)`. Keep only the most recently `sent` per group.

**Tier 3: Heuristic** — For non-VTEC products (e.g. Special Weather Statements), group by `event + senderName + areaDesc`. If any two in a group have overlapping time windows, keep only the most recently `sent`. Handles the NWS pattern of issuing fresh alerts rather than formal updates for SPS products.

Pre-filter: alerts with `expires` in the past are removed before deduplication regardless of NWS feed inclusion (NWS sometimes returns stale alerts).

### Alert Lifecycle

`Process Alerts` tracks alert IDs in `flow.known_alerts` (disk-backed). On each cycle:
- **New** — ID appears in `deduped` but not in `known_alerts` → fires `highland/event/weather/alert/new`
- **Updated** — ID present in both but `sent` timestamp changed → fires `highland/event/weather/alert/updated`
- **Expired** — ID present in `known_alerts` but not in `deduped` → fires `highland/event/weather/alert/expired`
- **Ongoing** — ID present in both, `sent` unchanged → no event

A stable `correlation_id` is computed per alert for notification update-in-place: VTEC-based for watches/warnings/advisories (`weather_alert_{office}_{phenom}_{sig}_{num}`), sanitized `event+senderName` for non-VTEC products.

### Notifications

`Build Notifications` iterates `msg.events` and publishes to `highland/event/notify` for `new` and `updated` events, and to `highland/command/notify/clear` for `expired` events. Per-alert severity (`notification_severity`) overrides the subscription default.

Subscription: `weather.alert` → `people.joseph.ha_companion`, default severity `low`.

### Consolidated State Topic

`highland/state/weather/alerts` serves both operational consumers and the HA Weather Alerts Card sensor. The payload combines the `alerts` array (for Daily Digest and other consumers) with flat indexed attributes at the top level (for the card):

```
alerts[]          — full alert objects for operational consumers
count             — HA entity state
attribution       — "Pirate Weather" (triggers card auto-detection)
title / title_0   — NWS event name
severity / ...    — NWS severity
time / ...        — onset timestamp
expires / ...     — expiry timestamp
regions / ...     — areaDesc string
uri / ...         — empty string (kiosk-safe — full description in payload)
description / ... — full NWS alert text
```

### HA Discovery

`Home Assistant Discovery` group publishes `sensor.weather_alerts` config on startup. Entity state = alert count integer. Weather Alerts Card (HACS) auto-detects provider from `attribution: "Pirate Weather"` and renders severity-coded expandable alert cards.

Dashboard visibility condition: `numeric_state above 0` — card hidden when no alerts active.

### Flow Design

| Group | Purpose |
|-------|--------|
| **Sinks** | Every 30 Seconds inject → link out to Alert Pipeline |
| **Alert Pipeline** | link in → Build Request → Fetch Alerts → Process Alerts → Build Dispatches → MQTT Out; Process Alerts also wires to Build Notifications → MQTT Out |
| **Home Assistant Discovery** | On Startup → Build Sensors → MQTT Out |
| **Error Handling** | Catch All → debug |

---

## Weather Lightning

### Architecture

`Utility: Weather Lightning` — **Designed, not yet implemented** (see issue #26).

Dedicated flow for hyperlocal lightning detection and notification. Lightning events come exclusively from the Tempest hardware sensor via `highland/event/weather/station/lightning`. Separate flow maintains architectural clarity and provides a future home for third-party lightning data (triangulated strike mapping, etc.).

### Notification Logic

One persistent notification (`correlation_id: weather_lightning`, `sticky: true`) updated in place. Flow context tracks `last_notified_distance` and `last_notified_at`.

**Decision logic on each incoming strike:**

- **No prior notification** → notify (first strike)
- **Incoming distance < `nearby_miles` (5)** → always notify (proximity mode — no cooldown)
- **Incoming distance < last notified distance** → notify (storm approaching)
- **Last notified was nearby (< 5 mi)** → suppress unless `nearby_age_out_minutes` (10) has elapsed
- **Otherwise** → suppress unless `distant_cooldown_minutes` (5) has elapsed

Approaching storms generate more notifications than departing — intentional asymmetry. Proximity mode is a pure override. Aging out provides natural all-clear without explicit notification.

**Notification content:**
- Nearby (< 5 miles): Title `"Lightning Nearby"`, Message `"Lightning nearby — X miles away"`, Severity `high`
- Distant (≥ 5 miles): Title `"Lightning Detected"`, Message `"Strike detected X miles away"`, Severity `medium`
- `url: '/dashboard-weather/0'`, `group: 'weather_lightning'`

### Thresholds (`thresholds.json`)

```json
"lightning": {
    "nearby_miles": 5,
    "distant_cooldown_minutes": 5,
    "nearby_age_out_minutes": 10
}
```

### State Topic

`highland/state/weather/lightning` — retained, published on every incoming strike. See `standards/MQTT_TOPICS.md` for full payload schema.

### HA Discovery

`sensor.weather_lightning` under `Highland Weather Lightning` device. Entity state = `none` | `distant` | `nearby`.

---

## Weather Station

### Architecture

WeatherFlow Tempest station broadcasts UDP packets on port 50222 to the LAN. A lightweight `socat` relay running as a systemd service (`highland-tempest-relay`) on the Workflow host receives the broadcast and unicasts to port 50223, where Node-RED's Docker container can receive it. Docker bridge networking does not forward broadcast traffic to containers, making the relay necessary.

`Utility: Weather Station` is the sole consumer of the UDP stream. It decodes and dispatches all Tempest message types, normalizes observations to US customary units, and publishes to the MQTT bus.

### Message Types

| Type | Cadence | Handling |
|------|---------|----------|
| `obs_st` | Every 1 minute | Normalize → state + observation event |
| `evt_precip` | On rain onset | Publish precipitation start event |
| `evt_strike` | On lightning | Publish lightning event |
| `rapid_wind` | Every 3 seconds | Plumbing in place, not yet processed |
| `hub_status` | Every 10 seconds | Dropped (not yet handled) |
| `device_status` | Every minute | Dropped (not yet handled) |

### Flow Design

`Utility: Weather Station` groups:

| Group | Purpose |
|-------|--------|
| **Event Sinks** | UDP In → Decode Buffer → Build Target → Dispatch (dynamic link call) |
| **Tempest Events** | Link-in handlers for each message type: Observation, Precipitation Start, Lightning Strike, Rapid Wind |
| **Home Assistant Discovery** | On Startup → Build Sensors → MQTT Out (14 sensor configs, retained) |
| **Error Handling** | Catch All → debug |

The `Build Target` function sets `msg.target` to the link-in node name for each message type. The dynamic link call dispatches to the correct handler. Each handler normalizes its payload and returns via a return-mode link out.

### Unit Conversions

| Measurement | Raw Unit | Published Unit | Formula |
|-------------|----------|---------------|---------|
| Temperature | °C | °F | `(C × 9/5) + 32` |
| Wind speed | m/s | mph | `× 2.23694` |
| Pressure | mbar | inHg | `× 0.02953` |
| Rain | mm | inches | `× 0.0393701` |
| Lightning distance | km | miles | `× 0.621371` |

### MQTT Topics

See `standards/MQTT_TOPICS.md` — Weather Station section for full payload schemas.

- `highland/state/weather/station` — retained, full normalized observation
- `highland/event/weather/station/observation` — not retained, fires each minute
- `highland/event/weather/station/precipitation_start` — not retained, optical sensor trigger
- `highland/event/weather/station/lightning` — not retained, per-strike event

### Infrastructure

**Relay service:** `workflow/systemd/highland-tempest-relay.service`

```
socat UDP-RECV:50222,reuseaddr UDP-SENDTO:127.0.0.1:50223
```

**Docker port mapping** in `docker-compose.yml`:
```yaml
ports:
  - "1880:1880"
  - "50223:50223/udp"
```

---

## Radar Pipeline

### Architecture

Base reflectivity radar is implemented as a **standalone Python daemon on the hub** — entirely outside Node-RED and Docker. The daemon owns scheduling, fetching, compositing, and delivery. Node-RED's role is limited to:

- Pushing configuration to the daemon via MQTT on startup and on demand
- Subscribing to rendered/error/status events for logging and automation
- Enabling or disabling individual products via MQTT

This architecture eliminates the fragility of long-running exec nodes — Node-RED deploys no longer interrupt the radar pipeline.

### Components

```
/opt/highland/weather/
├── daemon.py                  — minute-tick scheduler, spawns products
├── config_listener.py         — MQTT config subscriber
├── lib/
│   ├── config.py              — config loading and dataclasses
│   ├── mqtt.py                — MQTT publish helpers
│   ├── tiles.py               — Web Mercator tile math
│   ├── rainviewer.py          — RainViewer API client
│   ├── cache.py               — frame and interp cache management
│   ├── imaging.py             — ImageMagick subprocess wrappers
│   ├── sftp.py                — HAOS file delivery via paramiko
│   └── logging_config.py      — JSONL dual-output logging
└── products/
    └── reflectivity.py        — base reflectivity product

/var/lib/highland/weather/
├── config/weather.json        — daemon's working config (written by config_listener)
├── assets/
│   ├── base_map.png           — Stadia Maps base map (shared, product-agnostic)
│   ├── overlays/
│   │   └── reflectivity.png   — per-product static overlay (legend, crosshair)
│   ├── cache/
│   │   └── reflectivity/      — per-product frame cache
│   │       ├── {hash}.png     — composited radar+basemap frame
│   │       └── interp_*.png   — interpolated blend frames
│   ├── loops/
│   │   └── reflectivity.gif   — final animated output
│   └── tmp/
│       └── reflectivity/      — per-product temp workspace
├── locks/                     — per-product lockfiles (PID-based)
├── state/                     — last-run timestamps per product
└── logs/weather.log           — JSONL log
```

### Systemd Services

Two services run on the hub as `hub-daemon`:

- **`highland-weather-daemon`** — wakes every 60 seconds, checks each enabled product's cadence, spawns product scripts in the background
- **`highland-weather-config-listener`** — persistent MQTT subscriber, writes config to disk on receipt

### Per-Product Pipeline (Reflectivity)

1. Fetch RainViewer API frame list
2. Short-circuit if frame hashes unchanged since last run
3. Evict stale cache entries
4. Fetch and composite any uncached frames (radar tiles over base map)
5. Apply static overlay + per-frame timestamp to all frames
6. Generate interpolated blend frames between consecutive real frames
7. Assemble animated GIF with per-frame delays
8. SFTP deliver to HAOS `/config/www/hub.local/weather/radar/`
9. Publish MQTT events

### Frame Caching

RainViewer frame paths are hash-based and immutable — content at a given hash never changes. The pipeline caches:

- **Real frames:** `/assets/cache/{hash}.png` — composited radar+basemap, cached indefinitely until hash leaves the active frame list
- **Interpolated frames:** `/assets/cache/interp_{hash_a}_{hash_b}_{n}.png` — morph blends between adjacent overlaid frames, cached until either parent hash is evicted

With 1-minute cadence and hash-based short-circuit, most runs cost only one RainViewer API call.

### HAOS Delivery

Completed GIFs are delivered via SFTP to `/config/www/hub.local/weather/radar/` on HAOS. Files are served at:

```
http://home.local:8123/local/hub.local/weather/radar/reflectivity.gif
```

This URL works both inside and outside the local network (via Nabu Casa or reverse proxy).

### Node-RED Flow — `Utility: Weather Radar`

The flow contains four groups:

| Group | Purpose |
|-------|--------|
| **Sinks** | On Startup → Latch → Build Configuration → MQTT Out (retained config push) |
| **Product Control** | Enable/Disable inject nodes → MQTT Out per product |
| **Event Listeners** | MQTT In nodes for rendered/error/status/last_updated |
| **Test Cases** | Manual inject for forcing config re-push |

All exec nodes, HTTP request nodes, and script invocation are removed. The flow is purely MQTT publish/subscribe.

### Configuration Flow

`Build Configuration` assembles the daemon config from Node-RED's global config (`config.location`, `config.secrets`, `config.weather`) and publishes it as a retained JSON message to `highland/command/weather/config`. The config listener on the hub receives it and writes `/var/lib/highland/weather/config/weather.json`. The daemon reads this file fresh on every tick.

---

## Notifications

| Trigger | Severity | Notes |
|---------|----------|-------|
| Rain event starting | `low` | During waking hours only unless heavy |
| Heavy rain (>0.3 in/hr) | `medium` | Any time |
| Snow event starting | `medium` | Always |
| Significant snow accumulation (>2") | `high` | Sensor-based |
| Ice/freezing rain detected | `high` | Type-specific field trigger |
| Thunderstorm imminent | `high` | CAPE threshold + precip |
| Severe weather alert | `high` or `critical` | From alerts block |
| Event ended after significant accumulation | `low` | Summary notification |

---

## MQTT Topics

See `standards/MQTT_TOPICS.md` for authoritative payload definitions.

**State (retained):** `highland/state/weather/conditions` | `forecast` | `alerts` | `analysis` | `lightning` | `precipitation`

> **Note:** `highland/state/weather/alerts` is published by `Utility: Weather Alerts` (live). `highland/state/weather/analysis` is published by `Utility: Weather Analysis` (live). `highland/state/weather/lightning` will be published by `Utility: Weather Lightning` (designed, not yet implemented). The remaining state topics are future targets.

**Events (not retained):** `highland/event/weather/precipitation_start` | `precipitation_end` | `precipitation_type_change` | `lightning_detected` | `wind_gust` | `alert/new` | `alert/updated` | `alert/expired`

---

## Open Questions

- [ ] Ultrasonic snow depth sensor hardware selection and mounting
- [ ] Optimal `precipIntensityError` confidence gate threshold — calibrate from observed events
- [ ] Whether `nearestStormDistance` / `nearestStormBearing` useful for lightning threat lead time
- [ ] Stadia Maps plan upgrade required before go-live — see Licensing section below

---

## Implementation Notes

- All `-999` values must be null-guarded before use in logic or display
- `sleetIntensity` in minutely: use if present, don't depend on it
- State machines persist state in flow context (disk-backed) to survive restarts
- HRRR subhourly source freshness: check `flags.sourceTimes.hrrr_subh` to confirm data recency

---

## Licensing — Stadia Maps

**Current status:** Free tier (development only)

The radar base map pipeline fetches 25 raster tiles and assembles them server-side. This constitutes bulk downloading and server-side caching, which is **prohibited on the Stadia Maps free tier** except via their dedicated `static_cacheable` endpoint.

The free tier is acceptable during development. Before go-live, upgrade to the **Starter plan ($20/month)**, which grants rights to cache and store map images digitally for as long as the subscription is active.

**Base map refresh cadence at go-live:** Weekly rebuild (daemon checks age on each tick), plus manual force-rebuild by deleting `base_map.png`. At 25 tile fetches per rebuild, this is negligible against the Starter credit allowance.

**Alternative worth evaluating:** The Stadia Maps `static_cacheable` endpoint returns a pre-rendered map image in a single API call rather than requiring tile assembly. This would simplify the pipeline further, though with less control over exact output dimensions and crop. Evaluate when upgrading.

---

*Last Updated: 2026-04-13*
