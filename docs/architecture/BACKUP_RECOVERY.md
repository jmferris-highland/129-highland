# Backup & Recovery

## Philosophy

Each host owns its backup. No SSH between hosts required. Node-RED is the single trigger authority — all backups fire from `highland/event/scheduler/backup_daily` and results are published back to the bus for failure notification. HA manages its own backup schedule independently; Node-RED audits it rather than orchestrating it.

---

## Per-Host Backup Strategy

| Component | What to Back Up | Method |
|-----------|----------------|--------|
| **HAOS** | Full HA backup (config, database, add-ons) | Native HA automatic backup; Nabu Casa cloud sync |
| **Communication Hub** | Mosquitto config, Z2M data, Z-Wave JS data, docker-compose | Bash script triggered by MQTT command; publishes result |
| **Workflow** | Node-RED flows (live export), `/config`, `/data`, `/assets` | Backup Utility Flow; publishes result |

---

## Retention Policy

| Type | Retention | Notes |
|------|-----------|-------|
| Logs (JSONL) | 30 days | Daily rotation, cron cleanup |
| Hub local backups | 7 days | Cron cleanup in backup script |
| Workflow local backups | 7 days | `find` cleanup in Build Tar Command function |
| Nabu Casa (HA) | 3–5 backups | Managed by Nabu Casa |
| NAS (future) | TBD | Define when NAS is available |

---

## Backup Trigger Architecture

```
Utility: Scheduling (CronPlus 3:15 AM)
      │
      │ highland/event/scheduler/backup_daily
      ▼
Utility: Backup — Orchestration group
      │
      ├──► highland/command/backup/trigger/hub
      │         │
      │         ▼
      │    Hub backup listener daemon
      │    (highland-backup-listener.service)
      │         │
      │         └──► highland/event/backup/completed {host:"hub"}
      │              highland/event/backup/failed    {host:"hub"}
      │
      ├──► [link] Workflow Backup group
      │         │
      │         └──► highland/event/backup/completed {host:"workflow"}
      │              highland/event/backup/failed    {host:"workflow"}
      │
      └──► [link] HA Backup Check group
                │
                └──► highland/event/backup/completed {host:"ha"}
                     highland/event/backup/failed    {host:"ha"}

Result Collection group subscribes to highland/event/backup/failed
→ Notifies on any failure
```

---

## Communication Hub — Backup Scripts

Two scripts work together. Neither is included in backups (passwords embedded in plaintext; scripts are trivial to recreate from this doc).

### `highland-backup.sh`

Location: `/usr/local/bin/highland-backup.sh`

Tars Mosquitto config, Z2M data, Z-Wave JS data, and docker-compose. Publishes `completed` or `failed` event to MQTT.

```bash
#!/bin/bash
BACKUP_DIR="/var/backups/highland"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="hub_backup_${TIMESTAMP}.tar.gz"
MQTT_HOST="localhost"
MQTT_USER="svc_scripts"
MQTT_PASS="YOUR_SVC_SCRIPTS_PASSWORD"

tar -czf "${BACKUP_DIR}/${BACKUP_FILE}" \
    /opt/highland/mosquitto/config \
    /opt/highland/zigbee2mqtt/data \
    /opt/highland/zwavejs/data \
    /opt/highland/docker-compose.yml

if [ $? -eq 0 ]; then
    find "${BACKUP_DIR}" -name "hub_backup_*.tar.gz" -mtime +7 -delete
    mosquitto_pub -h "$MQTT_HOST" -u "$MQTT_USER" -P "$MQTT_PASS" \
        -t "highland/event/backup/completed" \
        -m "{\"host\":\"hub\",\"file\":\"${BACKUP_FILE}\",\"timestamp\":\"$(date -Iseconds)\"}"
else
    mosquitto_pub -h "$MQTT_HOST" -u "$MQTT_USER" -P "$MQTT_PASS" \
        -t "highland/event/backup/failed" \
        -m "{\"host\":\"hub\",\"error\":\"tar failed\",\"timestamp\":\"$(date -Iseconds)\"}"
fi
```

### `highland-backup-listener.sh`

Location: `/usr/local/bin/highland-backup-listener.sh`

Long-running daemon. Blocks on `mosquitto_sub -C 1` waiting for a command on `highland/command/backup/trigger/hub`. On receipt, invokes `highland-backup.sh`. On broker disconnect or error, sleeps 5 seconds and retries — prevents CPU spinning when MQTT is unavailable.

```bash
#!/bin/bash
MQTT_HOST="localhost"
MQTT_USER="svc_scripts"
MQTT_PASS="YOUR_SVC_SCRIPTS_PASSWORD"
TRIGGER_TOPIC="highland/command/backup/trigger/hub"

while true; do
    mosquitto_sub -h "$MQTT_HOST" -u "$MQTT_USER" -P "$MQTT_PASS" \
        -t "$TRIGGER_TOPIC" -C 1 2>/dev/null

    if [ $? -eq 0 ]; then
        /usr/local/bin/highland-backup.sh
    else
        sleep 5
    fi
done
```

### Deployment

```bash
sudo chmod +x /usr/local/bin/highland-backup.sh
sudo chmod +x /usr/local/bin/highland-backup-listener.sh
```

### Systemd Service

Location: `/etc/systemd/system/highland-backup-listener.service`

Starts after `network-online.target` — the listener daemon must reach the MQTT broker, which is a Docker container (not a systemd unit). Restart on failure with 5-second delay.

```ini
[Unit]
Description=Highland Backup Listener
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/highland-backup-listener.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> **Note:** `User=` is intentionally omitted — the service runs as root. The backup script must read Mosquitto config files (specifically `password.txt`) that are owned by root because they are written by the Docker container. Running as a non-root user produces `Permission denied` errors.

```bash
sudo systemctl daemon-reload
sudo systemctl enable highland-backup-listener
sudo systemctl start highland-backup-listener
sudo systemctl status highland-backup-listener
```

---

## Workflow Host — Backup Flow

Handled entirely by the Backup Utility Flow in Node-RED. No separate script.

**Volume mount required** — add to the `nodered` service in `/opt/highland/docker-compose.yml`:

```yaml
volumes:
  - /var/backups/highland:/backups
```

Apply with:

```bash
cd /opt/highland
docker compose up -d --force-recreate nodered
```

**Backup sequence:**

1. `Build Timestamp` — generates a `YYYY-MM-DD_HH-MM-SS` string, stores in `flow.backup_timestamp`
2. `Export Flows` — HTTP GET `http://localhost:1880/flows` (live in-memory export)
3. `Prepare Flow Export` — sets `msg.filename` to `/backups/workflow_flows_<ts>.json`
4. `Write Flow Export` — file node writes flows JSON to disk
5. `Build Tar Command` — constructs tar command: archives `/config`, `/data`, `/assets`, and the flows JSON file; then removes the standalone flows JSON; then prunes backups older than 7 days
6. `Run Tar` — exec node runs the command (`command=""`, `addpay=true`, `oldrc=false`); `msg.rc` carries exit code on output 1
7. `Evaluate Workflow Result` — routes on `msg.rc === 0`; publishes `completed` or `failed` to MQTT

**Filesystem layout inside the Node-RED container:**

```
/config    ← /home/nodered/config (git-tracked config JSON, secrets.json gitignored)
/data      ← /opt/highland/nodered/data (context storage, credentials, settings)
/assets    ← /home/nodered/assets (weather icons, static assets)
/backups   ← /var/backups/highland (added volume mount)
```

---

## HA Backup — Audit Check

HA manages its own automatic backup schedule. Node-RED does not trigger HA backups. Instead, the HA Backup Check group in the Backup Utility Flow audits `sensor.backup_last_successful_automatic_backup` at 3:15 AM and reports a failure if the timestamp is older than 26 hours.

**26-hour window:** provides buffer against timing variation between HA's backup schedule and the 3:15 AM audit — catches genuine missed backups without false-positives from minor schedule drift.

**Failure payload example:**
```json
{
  "host": "ha",
  "status": "failed",
  "elapsed_hours": 29.4,
  "timestamp": "2026-03-28T03:15:00.000Z"
}
```

**HA configuration:** Leave HA automatic backups enabled. Nabu Casa handles cloud sync. No Node-RED involvement in the backup itself.

---

## Utility: Backup Flow

**Tab:** `Utility: Backup`

**Groups:**

| Group | Purpose |
|-------|---------|
| Orchestration | Receives `backup_daily` event; fans out to hub command + workflow + HA check |
| Workflow Backup | Exports flows, tars config/data/assets, publishes result |
| HA Backup Check | Reads HA last backup sensor, evaluates against 26h window, publishes result |
| Result Collection | Subscribes to `highland/event/backup/failed`; builds and publishes failure notification |

**Node configuration:**
- All MQTT nodes: select the `highland` broker config
- `Get HA Last Backup` node: select the `Highland HA` server config
- Wire Initializer Latch subflow between `Receive Backup Trigger` and `Prepare Orchestration`

---

## Recovery Scenarios

| Scenario | Recovery Approach |
|----------|-------------------|
| **HAOS failure** | Restore from Nabu Casa cloud or local backup; Communication Hub + Workflow continue running during recovery |
| **Communication Hub failure** | Redeploy Docker Compose, restore config volumes from backup; devices remain paired in coordinator database |
| **Workflow failure** | Redeploy Docker Compose, import flow JSON from `/var/backups/highland/workflow_flows_<latest>.json`, restore config files; MQTT events queue until back online |
| **Total loss** | Restore all from backup; re-pair devices if coordinator database lost |

---

## MQTT Topics

| Topic | Purpose |
|-------|---------|
| `highland/event/scheduler/backup_daily` | Daily backup trigger (from Scheduler) |
| `highland/command/backup/trigger/hub` | Command to Hub to run backup script |
| `highland/event/backup/completed` | Backup completed successfully — payload includes `host` |
| `highland/event/backup/failed` | Backup failed or HA backup stale — payload includes `host`, `error`/`elapsed_hours` |

See `standards/MQTT_TOPICS.md` for full payload schemas.

---

*Last Updated: 2026-03-27*
