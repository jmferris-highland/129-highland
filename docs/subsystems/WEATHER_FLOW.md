# Weather Flow — Design & Architecture

## Implementation Status

**Tier 1 — NWS (Live)**

Two separate Node-RED flows, both built and publishing:

- **`Utility: Weather Forecasts`** — Resolves NWS grid coordinates on startup and daily at 23:55 (cronplus), storing the forecast URL in flow context. Fetches normalized 7-day forecast hourly and on startup. Publishes `highland/state/weather/forecast` with a date-keyed period map including NWS condition codes, temperature, precip chance, and wind.
- **`Utility: Weather Alerts`** — Polls NWS active alerts endpoint every 30 seconds. Tracks alert lifecycle (new/updated/expired) in flow context. Publishes `highland/state/weather/alerts` retained snapshot and fires three lifecycle event topics.

**Primary consumer:** Both Tier 1 flows feed the `Utility: Daily Digest` — the forecast and any active alerts are included in the nightly digest email sent to household residents.

**Tier 2 — Synthesis (Future)**

The full architecture described below — Tempest station, Pirate Weather API, polling state machine, precipitation event tracking — is the target end state. The black-box synthesis model and MQTT topic namespace are designed to accommodate Tier 2 without breaking Tier 1 consumers.

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

**State (retained):** `highland/state/weather/conditions` | `forecast` | `alerts` | `precipitation`

> **Note:** `highland/state/weather/alerts` is published by `Utility: Weather Alerts` (Tier 1, live). The remaining state topics are Tier 2 targets.

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

*Last Updated: 2026-04-01*
