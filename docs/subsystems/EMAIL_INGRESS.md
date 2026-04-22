# Email Ingress — Design & Architecture

## Scope

The `Utility: Email Ingress` flow owns the **email intake surface** for the Highland system. It connects to the household Gmail account, polls for new messages across Highland-scoped folders, normalizes them into a common schema, and publishes them to MQTT for consumption by downstream flows.

It is deliberately content-agnostic. It knows nothing about USPS, calendar invites, warranty notifications, or any other email source's meaning. Its only concerns are: connect to IMAP reliably, get messages out, publish them in a consistent shape, and manage message lifecycle (the move from unprocessed to processed folders).

This is the email equivalent of `Utility: Device Registry` — a single owner for a cross-cutting concern so consuming flows don't each reimplement the same plumbing.

---

## Why a Dedicated Flow

Every email-driven subsystem would otherwise duplicate:

- IMAP connection management (credentials, TLS, reconnect handling)
- Gmail rate-limit awareness (15 simultaneous IMAP connections per account)
- `Message-Id` deduplication across restarts
- Folder convention enforcement (`Highland/*` namespace)
- IMAP health monitoring and heartbeat
- App password revocation detection
- Processed-email retention and purge

Owning all of that in one place makes each consumer trivially simple — a consumer's entire job is "subscribe to my label's topic, parse content, publish semantic state." No IMAP code anywhere else in the system.

---

## Gmail Account

The flow connects to a single dedicated household Gmail account (referenced in `secrets.json` as `gmail.account`). The same account is used for Google Calendar and is the registered mailbox for USPS Informed Delivery. See `subsystems/DELIVERIES.md § Gmail Account` for rationale on the dedicated-account choice.

### Authentication

IMAP access requires 2FA on the account (Google deprecated "less secure app access" in 2022):

1. 2FA is enabled on the account.
2. A single app password is generated, scoped to "Mail," and named **"Node-RED Highland IMAP"** — an obvious name so a human reviewing the account's security page doesn't mistake it for suspicious activity.
3. The app password is stored in `secrets.json` as `gmail.app_password`.
4. Node-RED's IMAP node uses the account email + app password for standard IMAP auth.

App passwords are revocable independently of the account's primary credential.

---

## Folder Namespace Convention

All automation-relevant email lives under a `Highland/` label prefix in Gmail. This establishes a clear boundary between "robot-managed mail" and "human-managed mail."

```
Highland/
├── Informed Delivery/
│   └── Processed/
├── Calendar Invites/         (future)
│   └── Processed/
└── ... (one sub-namespace per consuming subsystem)
```

**Conventions:**

- Each consuming subsystem gets its own top-level folder under `Highland/`.
- Each folder has a `Processed/` subfolder for completed messages.
- Gmail filters (configured manually, once) route incoming mail to the appropriate folder *and* strip it from the inbox.
- Humans using the account for unrelated mail never see `Highland/*` folders in their default inbox view.

**Adding a new consumer:** the operational task is (1) create the Gmail filter and label, (2) add the folder to the ingress flow's config, (3) build the consumer flow. No code changes to ingress beyond config.

---

## Message Lifecycle

```
[new message arrives in Highland/<folder>]
         │
         ▼
[Ingress polls folder, sees new Message-Id]
         │
         ▼
[Publish normalized event to MQTT]
         │
         ▼
[Consumer processes, publishes ACK]
         │
         ▼
[Ingress moves message to Highland/<folder>/Processed]
```

**Key behaviors:**

- **Unprocessed = in source folder.** Processed = in `Processed/` subfolder. State lives in folder membership, not IMAP `\Seen` flags — a human reading an email in the Gmail UI does not affect processing state.
- **Idempotent.** The ingress flow maintains a rolling record of recently-published `Message-Id` values (in flow context, with a 24-hour TTL). If the same message appears twice — because of a failed move, a restart race, or a human dragging it back — it's recognized as a duplicate and not republished.
- **ACK-gated move.** After publishing, ingress waits for `highland/ack/email` with the matching `message_id` before moving to `Processed/`. This prevents loss if a consumer is down or restarting at publish time.
- **Fallback move TTL.** If no ACK arrives within a configured window (default 24 hours), the message gets moved anyway with a warning log. Prevents indefinite accumulation in the source folder when a consumer is genuinely broken or unconfigured. The consumer can still recover the message from `Processed/` if needed.
- **Retention purge.** Messages in `Processed/` subfolders older than a configurable retention (default 14 days) are permanently deleted by a scheduled sweep.

---

## MQTT Topics

### Events (not retained)

One topic per label. Each label is a direct mirror of its Gmail folder name, snake-cased.

| Topic | Fires When |
|-------|-----------|
| `highland/event/email/<label>/received` | A new email is published from that label |

**Current labels:**

| Label | Consumer |
|-------|----------|
| `informed_delivery` | `Utility: Deliveries` |

**Planned labels (as consumers come online):** captured in `AUTOMATION_BACKLOG.md`.

### Payload Schema

```json
{
  "message_id": "<CAxxxx@mail.gmail.com>",
  "label": "informed_delivery",
  "folder": "Highland/Informed Delivery",
  "from": "USPSInformeddelivery@email.informeddelivery.usps.com",
  "from_name": "USPS Informed Delivery",
  "to": "ferris.smarthome@gmail.com",
  "subject": "Your Daily Digest",
  "received_at": "2026-04-22T07:15:23-04:00",
  "body_text": "...",
  "body_html": "...",
  "attachment_count": 3
}
```

**Design notes:**

- `message_id` is the canonical identifier. Consumers use it for their own deduplication and must echo it in the ACK.
- `body_text` and `body_html` are both included when available. Consumers pick whichever they prefer.
- **Attachments are not inlined.** `attachment_count` is metadata only. Binary attachments are deliberately excluded from the payload to keep MQTT traffic reasonable. No current consumer needs attachment bytes; if one ever does, we add a separate fetch-by-message-id mechanism rather than polluting the ingress topic.
- HTML bodies for some senders (Informed Delivery digests with embedded scan references) can be large — several hundred KB. Mosquitto handles this fine at our volume (single-digit emails per day per label), but we keep the firehose tight by not including binary attachment content.

### ACK

Consumers publish an ACK after successful processing:

**`highland/ack/email`** — not retained

```json
{
  "message_id": "<CAxxxx@mail.gmail.com>",
  "consumer": "utility_deliveries",
  "processed_at": "2026-04-22T07:15:45-04:00",
  "status": "ok"
}
```

`status` values: `"ok"` | `"rejected"` | `"parse_error"`.

**On `status: "ok"`:** ingress moves the message to `Processed/`. Normal path.

**On `status: "rejected"`:** consumer saw the message but decided it wasn't theirs (e.g., sender mismatch for a shared label, or subject pattern the consumer doesn't handle). Ingress still moves the message to `Processed/` — it has been "seen" — but tags the log entry. If multiple consumers could potentially claim a label, rejection semantics get more complex; current design assumes one consumer per label.

**On `status: "parse_error"`:** consumer tried to process but failed. Ingress leaves the message in the source folder for retry, logs a warning, and publishes a notification on repeated failures (threshold configurable, default 3 attempts). This is the "something is broken" path — a parser bug or an unexpected email variant shouldn't silently vanish into `Processed/`.

### Health

**`highland/status/email_ingress/health`** — retained

```json
{
  "status": "healthy",
  "last_poll_at": "2026-04-22T07:20:00-04:00",
  "last_successful_auth": "2026-04-22T07:20:00-04:00",
  "messages_in_flight": 0,
  "imap_connection": "connected",
  "timestamp": "2026-04-22T07:20:00-04:00"
}
```

`status` values: `"healthy"` | `"degraded"` | `"unhealthy"`.

Published on every poll cycle. Degraded conditions include: messages awaiting ACK past the TTL, repeated parse failures from a consumer, elevated poll-to-poll latency. Unhealthy conditions include: IMAP authentication failure (likely app password revoked), no successful poll in >N intervals.

Per `nodered/HEALTH_MONITORING.md`, this topic is a first-class health surface — Healthchecks.io pings from this flow monitor external availability.

---

## Flow Outline — `Utility: Email Ingress`

Per `nodered/OVERVIEW.md` conventions.

**Group 1 — IMAP Connection**
- IMAP node (credentials from `secrets.json`)
- Connection state tracking (connected / reconnecting / failed)
- Health publishing on state change

**Group 2 — Folder Poller**
- Scheduled poll (every 5 min, configurable)
- Iterates configured folders under `Highland/*`
- For each folder, fetches new messages since last-seen UID
- Link-out to Dedup

**Group 3 — Deduplication**
- Checks `Message-Id` against rolling context store (24h TTL)
- Drops duplicates; link-outs remainders to Normalizer

**Group 4 — Normalizer**
- Extracts headers, body parts, attachment metadata
- Shapes into the standard payload schema
- Derives `label` from folder name

**Group 5 — Publisher**
- Publishes to `highland/event/email/<label>/received`
- Records message as "awaiting ACK" in flow context with timestamp

**Group 6 — ACK Handler**
- Subscribes to `highland/ack/email`
- On `ok` or `rejected`: triggers folder move to `Processed/`
- On `parse_error`: logs, increments retry counter, notifies on threshold

**Group 7 — Lifecycle Management**
- Scheduled sweep for unacked messages past TTL → forced move + warning
- Scheduled sweep for `Processed/` messages past retention → delete
- Health publisher

---

## Configuration

Captured in `config/email_ingress.json`:

```json
{
  "imap": {
    "server": "imap.gmail.com",
    "port": 993,
    "tls": true,
    "poll_interval_minutes": 5
  },
  "folders": [
    {
      "path": "Highland/Informed Delivery",
      "label": "informed_delivery",
      "processed_subfolder": "Processed"
    }
  ],
  "ack_timeout_hours": 24,
  "processed_retention_days": 14,
  "parse_error_notification_threshold": 3,
  "dedup_ttl_hours": 24
}
```

Adding a consumer is a config edit — append to `folders`, set up the Gmail filter manually, build the consumer flow. No ingress code changes.

---

## Startup Behavior

Per `nodered/STARTUP_SEQUENCING.md` patterns:

- The flow does **not** immediately process all mail in source folders at startup. That would republish a week's worth of backlog if the flow was offline.
- Instead, it reads the last-seen `Message-Id` list from persistent context and only publishes messages newer than the latest one it recorded.
- On first-ever run (empty context), it records current state as the baseline and waits for new arrivals. Existing mail in folders stays put.
- Manual reprocessing, if needed, is a separate utility — not automatic startup behavior. (Likely implemented later as a `highland/command/email_ingress/reprocess` topic accepting a `message_id` or folder path.)

---

## Open Questions

- [ ] **Reprocessing mechanism.** The command-topic approach above is a sketch, not a committed design. Decide when (and whether) to build it based on real operational needs.
- [ ] **Notification routing for ingress failures.** App-password revocation or IMAP auth failure needs to reach a human. Current thinking: tie into `Utility: Notifications` with severity `critical`, targeting the Daily Digest recipient. Confirm when notifications framework is wired in.
- [ ] **Attachment fetch mechanism.** If a future consumer ever needs attachment bytes, a fetch-by-message-id pattern is the intended approach — but exact topic shape (`highland/command/email_ingress/fetch_attachment/<message_id>` → response on `highland/event/...`?) is deferred until a concrete consumer drives it.
- [ ] **Rejection semantics if multiple consumers share a label.** Current design assumes one consumer per label. If that assumption breaks, rework needed.
- [ ] **Gmail filter configuration tracking.** The manually-configured filters aren't version-controlled. Consider capturing them in a `docs/ha/` or `docs/standards/` reference doc as they proliferate, so a rebuild isn't an archaeological dig through Gmail settings.

---

*Last Updated: 2026-04-22*
