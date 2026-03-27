# Highland — Node-RED Flow Exports

Exportable Node-RED flows from the Highland home automation project. Each file is a tab-level export that can be imported directly into Node-RED.

---

## How to Use

1. In Node-RED, open the menu (top right) → **Import**
2. Select the JSON file you want
3. Click **Import**
4. After importing, re-configure any config nodes (MQTT broker, Home Assistant server) to point at your own instances

---

## Dependencies

All flows assume the following are already present in your Node-RED instance:

### Palette packages

| Package | Purpose |
|---------|---------|
| `node-red-contrib-home-assistant-websocket` | HA integration nodes (`api-current-state`, `ha-api`, etc.) |
| `node-red-contrib-cron-plus` | CronPlus scheduling node (Scheduler flow) |
| `schedex` | Sunrise/sunset scheduling (Scheduler flow) |
| `node-red-node-email` | SMTP email delivery (Daily Digest flow) |

### Subflows

These subflows must be imported before any flow that depends on them. Import these first:

| File | Description |
|------|-------------|
| `subflow-initializer-latch.json` | Gates flow execution until Utility: Initializers is ready |
| `subflow-connection-gate.json` | Guards message flow based on HA or MQTT connection state |

### Config nodes

After importing, you will need to configure:

- **MQTT broker** — point at your Mosquitto broker host and credentials
- **Home Assistant server** — point at your HA instance with a long-lived access token

---

## Config and Secrets

Flows reference external JSON config files mounted at `/config` inside the Node-RED container. The expected structure is documented in `docs/nodered/CONFIG_MANAGEMENT.md`. Key files:

- `/config/secrets.json` — API keys, MQTT credentials, SMTP credentials (never committed)
- `/config/device_registry.json` — device/area data (written by Utility: Device Registry flow)
- `/config/notifications.json` — notification target mappings
- `/config/thresholds.json` — battery, health, and other threshold values

See `docs/nodered/CONFIG_MANAGEMENT.md` for full schemas.

---

## Flow Index

| File | Tab Label | Description |
|------|-----------|-------------|
| `subflow-initializer-latch.json` | — | Subflow: gates on Utility: Initializers readiness |
| `subflow-connection-gate.json` | — | Subflow: gates on HA or MQTT connection state |
| `utility-initializers.json` | Utility: Initializers | Registers global helper functions; signals readiness |
| `utility-config-loader.json` | Utility: Config Loader | Loads config JSON files into global context on startup |
| `utility-scheduler.json` | Utility: Scheduling | Publishes period transitions and task events to the MQTT bus |
| `utility-connections.json` | Utility: Connections | Tracks live connection state for HA and MQTT |
| `utility-logging.json` | Utility: Logging | Subscribes to `highland/event/log`; writes JSONL to disk |
| `utility-notifications.json` | Utility: Notifications | Delivers notifications to HA Companion and other channels |
| `utility-health-monitor.json` | Utility: Health Monitor | Pings Healthchecks.io; monitors service health |
| `utility-battery-monitor.json` | Utility: Battery Monitor | Tracks Zigbee device battery levels; notifies on state changes |
| `utility-device-registry.json` | Utility: Device Registry | Builds device/area registry from HA WebSocket APIs |
| `utility-backup.json` | Utility: Backup | Orchestrates nightly backups for Hub, Workflow, and HA audit |
| `utility-daily-digest.json` | Utility: Daily Digest | Composes and sends the daily summary email |

---

## Recommended Import Order

Import in this order to satisfy dependencies:

1. `subflow-initializer-latch.json`
2. `subflow-connection-gate.json`
3. `utility-initializers.json`
4. `utility-config-loader.json`
5. `utility-connections.json`
6. All remaining utility flows (order does not matter after step 5)

---

## Notes

- Flows are exported with placeholder config node IDs. Node-RED will prompt you to re-configure broker and server nodes on first deploy — this is expected.
- The `device_registry.json` file does not exist until `Utility: Device Registry` runs for the first time. On first startup you may see file-not-found warnings from Config Loader — these are harmless and resolve after the first registry refresh.
- Flow exports are updated alongside documentation commits. The exported JSON reflects the last stable, documented state of each flow.
- **`utility-daily-digest.json` — SMTP server:** The `node-red-node-email` node does not support runtime injection of the server hostname, so it cannot be stored in `secrets.json`. The server field in the exported JSON has been redacted to `smtp.your-domain.example`. Update this value directly in the email node configuration after importing. All other email settings (credentials, recipients) are correctly read from `config.secrets` at runtime.

---

*See `docs/` for full architecture documentation.*
