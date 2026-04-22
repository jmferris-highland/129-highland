# Deliveries — Design & Architecture

## Scope

The `Utility: Deliveries` flow owns the **informational layer** of every delivery that touches the property — letter mail, USPS packages, and eventually other carriers (UPS, FedEx, Amazon). It consumes normalized email events from `Utility: Email Ingress`, parses delivery-specific content, maintains authoritative delivery state, and publishes a clean consumer-facing surface for dashboards, notifications, and other flows.

The flow does **not** own IMAP connection management, folder conventions, or deduplication. Those concerns belong to `Utility: Email Ingress` (see `subsystems/EMAIL_INGRESS.md`).

Physical sensors also belong elsewhere. When a mailbox door sensor or driveway package sensor is installed, its raw events are published by `Area: Driveway`. `Utility: Deliveries` subscribes to those raw events and fuses them with the informational layer to produce richer state.

### Phases

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1 — Baseline** | USPS Informed Delivery letter mail only (morning digest + delivery confirmation) | 📋 Planned |
| **Phase 2 — Packages** | Multi-carrier package tracking (USPS, UPS, FedEx, Amazon) | Backlog |
| **Phase 3 — Physical sensor fusion** | Consume `Area: Driveway` mailbox door events; detect retrieval; reconcile email vs. physical signals | Blocked on LoRa hardware |

This document covers Phase 1 in detail and sketches Phase 3 to establish the contract that the eventual `Area: Driveway` flow will publish against. Phase 2 is captured in `AUTOMATION_BACKLOG.md`.

---

## Dependencies

- **`Utility: Email Ingress`** — Provides the normalized email stream via `highland/event/email/informed_delivery/received`. This flow must be live before Phase 1 can go into production. See `subsystems/EMAIL_INGRESS.md`.
- **USPS Informed Delivery registration** — Requires a mailed PIN for identity verification before email delivery begins. Done once, out-of-band, before Phase 1 goes live.

---

## Phase 1 — Informed Delivery Baseline

### Problem

USPS Informed Delivery sends scan-previews and delivery confirmations via email, free of charge. This provides enough signal to answer "do I have mail today?" and "has the carrier come yet?" without any physical infrastructure at the mailbox — a useful baseline that:

1. Delivers value immediately, before LoRa hardware arrives.
2. Establishes the consumer-facing `highland/state/deliveries/*` contract so downstream flows, cards, and notifications can be built once and never rewritten.
3. Remains useful *after* LoRa arrives — the email signal becomes one input to the fused state rather than the only input.

### Email Contract

USPS sends two relevant email types on mail days. Both originate from the Informed Delivery sender domain and are routed by the Gmail filter into the `Highland/Informed Delivery` folder.

| Email | Typical Timing | Signal |
|-------|---------------|--------|
| **Daily Digest** | 6–9am local | Preview of letter mail expected that day, with scanned images. Piece count inferable from image count. |
| **Delivery Confirmation** | Shortly after physical delivery | "Your mail has been delivered today." Confirms letter mail is in the box. |

Critical points:

- The delivery confirmation email is **reliable** for this address, per observed behavior at the property. It is the load-bearing signal for the `MAIL_DELIVERED` transition.
- The morning digest is **informational, not load-bearing** — it tells us what to expect but not when it arrives.
- On Sundays, federal holidays, and other no-mail days, neither email fires. `NO_MAIL_SCHEDULED` is inferred from absence of the digest by a cutoff time.

### Ingress Contract

This flow subscribes to `highland/event/email/informed_delivery/received` and receives payloads in the standard shape defined in `subsystems/EMAIL_INGRESS.md § Payload Schema`. For every successfully processed (or deliberately rejected) message, this flow publishes `highland/ack/email` with the matching `message_id`.

The Gmail filter that labels incoming Informed Delivery mail is manually configured in Gmail, not defined in this flow. Gmail filter setup is part of the Ingress operational runbook.

### State Machine

```
UNKNOWN
  │
  │ Digest arrives, pieces > 0
  ▼
MAIL_EXPECTED ◄─── Digest arrives, pieces > 0 (from UNKNOWN or NO_MAIL_SCHEDULED)
  │
  │ Delivery confirmation arrives
  ▼
MAIL_DELIVERED
  │
  │ Midnight rollover
  ▼
UNKNOWN (reset for next day)

UNKNOWN ──── Digest absence by cutoff time ───► NO_MAIL_SCHEDULED
NO_MAIL_SCHEDULED ──── Midnight rollover ────► UNKNOWN

MAIL_EXPECTED ──── Midnight rollover w/o delivery ────► DELIVERY_EXCEPTION ──── Next digest ────► MAIL_EXPECTED / NO_MAIL_SCHEDULED
```

**States:**

| State | Meaning |
|-------|---------|
| `UNKNOWN` | No data yet today. Set at startup (before digest arrives) and at midnight rollover. |
| `NO_MAIL_SCHEDULED` | No mail day. Inferred when no digest arrives by cutoff time (default 10am), or explicitly via digest text variants indicating no mail. |
| `MAIL_EXPECTED` | Digest received with piece count > 0. Delivery has not yet been confirmed. |
| `MAIL_DELIVERED` | Delivery confirmation email received. Letter mail is in the box. |
| `DELIVERY_EXCEPTION` | `MAIL_EXPECTED` at midnight with no delivery confirmation received. Carrier didn't come, or the confirmation email never arrived. Resolves normally with next day's digest. |

**Design notes:**

- Single state machine per day. State resets to `UNKNOWN` at midnight, persistent context cleared.
- Midnight rollover is the only time-based transition. The "digest absence → NO_MAIL_SCHEDULED" transition fires at a configured cutoff time, not on a running timer.
- `DELIVERY_EXCEPTION` is non-terminal — a delayed confirmation arriving the next morning is uncommon enough that we don't special-case it; the daily reset takes priority.

### MQTT Topics

**State (retained):**

`highland/state/deliveries/mail`

```json
{
  "timestamp": "2026-04-21T14:15:00-04:00",
  "source": "informed_delivery",
  "state": "MAIL_DELIVERED",
  "expected_pieces": 3,
  "digest_received_at": "2026-04-21T07:15:00-04:00",
  "delivered_at": "2026-04-21T14:15:00-04:00"
}
```

**Events (not retained):**

| Topic | Fires When | Payload |
|-------|-----------|---------|
| `highland/event/deliveries/digest_received` | Daily digest parsed | `{ piece_count, has_packages, timestamp }` |
| `highland/event/deliveries/letter_delivered` | Delivery confirmation parsed | `{ timestamp }` |
| `highland/event/deliveries/exception` | Midnight rollover with `MAIL_EXPECTED` unresolved | `{ expected_pieces, digest_received_at, timestamp }` |

**ACK (not retained):**

`highland/ack/email` — published after each ingress message is processed. See `subsystems/EMAIL_INGRESS.md § ACK` for schema.

### Flow Outline — `Utility: Deliveries`

Per `nodered/OVERVIEW.md` conventions: groups are the primary organizing unit; link nodes connect groups; no node has more than two outputs.

**Group 1 — Ingress Subscription**
- MQTT In on `highland/event/email/informed_delivery/received`
- Route by subject pattern / sender confirmation → Daily Digest parser / Delivery Confirmation parser
- On unknown subject pattern: publish `highland/ack/email` with `status: "rejected"` and log

**Group 2 — Digest Parser**
- Extract piece count (image count heuristic)
- Detect "no mail scheduled" text variant
- Mutate flow context with digest data
- On success: publish `highland/ack/email` with `status: "ok"`
- On parse failure: publish `highland/ack/email` with `status: "parse_error"`
- Link-out to State Machine on state change

**Group 3 — Delivery Confirmation Parser**
- Confirm sender + subject shape
- Timestamp the confirmation
- On success: publish `highland/ack/email` with `status: "ok"` and link-out to State Machine
- On parse failure: publish `highland/ack/email` with `status: "parse_error"`

**Group 4 — State Machine**
- Single transition engine; reads flow context, applies rules, emits new state
- Publishes retained `highland/state/deliveries/mail` on any transition
- Publishes corresponding event topics

**Group 5 — Scheduler Hooks**
- Midnight reset (via `Utility: Scheduling` period transition or CronPlus)
- Digest cutoff check (default 10am — if no digest, transition `UNKNOWN → NO_MAIL_SCHEDULED`)
- Midnight exception check (if `MAIL_EXPECTED`, transition to `DELIVERY_EXCEPTION`)

**Group 6 — HA Discovery**
- Sensor: `sensor.mail_status` (string — current state)
- Sensor: `sensor.mail_expected_pieces` (int)
- Sensor: `sensor.mail_last_digest_received` (timestamp)
- Sensor: `sensor.mail_last_delivered` (timestamp)

Notably absent compared to earlier drafts: no IMAP group, no folder-management group. `Utility: Email Ingress` owns those.

### Configuration

Delivery-specific tunables only. All IMAP/folder/retention concerns live in `config/email_ingress.json`.

```json
{
  "informed_delivery": {
    "sender_domain": "email.informeddelivery.usps.com",
    "digest_cutoff_time": "10:00"
  }
}
```

File location is TBD — depends on how configuration groups ultimately organize. Candidates include a dedicated `config/deliveries.json` or inclusion in a broader file once the shape stabilizes. Decision deferred to implementation.

---

## Phase 3 — Physical Sensor Fusion (Future)

When the LoRa mailbox door sensor is installed (per `subsystems/LORA.md`), the design splits across two flows:

### Producer: `Area: Driveway`

Publishes raw physical events only — no interpretation. Published topics are defined in `subsystems/LORA.md § Use Case 2`:

- `highland/state/driveway/mailbox` (retained) — sensor telemetry including `door_state`, battery, env, signal.
- `highland/event/driveway/mailbox/opened` (not retained) — door opened.

No state machine; no delivery logic. The driveway flow doesn't know whether a door-open is a delivery, a retrieval, or a mail stuffer.

### Consumer: `Utility: Deliveries`

Subscribes to `highland/event/driveway/mailbox/opened` and fuses it with the email signal. The state machine extends:

- New state: `MAIL_RETRIEVED` — door opened after `MAIL_DELIVERED`.
- New transition: `MAIL_EXPECTED` + door opened shortly before delivery confirmation arrives → classify as the delivery event itself (the confirmation email lags the physical delivery by a configurable window).
- Existing `DELIVERY_EXCEPTION` logic gains nuance: a door open during `MAIL_EXPECTED` with no confirmation email by midnight may still indicate delivery happened but the email didn't fire.

The sensor hardware, installation, and raw event contract are owned by `subsystems/LORA.md § Use Case 2`. This document owns the fused state and classification logic.

---

## Open Questions

**Phase 1 — Informed Delivery**

- [ ] Confirm exact sender address format once PIN verification completes and real emails arrive
- [ ] Confirm digest "no mail scheduled" text variant (may require observation across a Sunday/holiday)
- [ ] Validate piece count heuristic (image count ≈ piece count) against real digests
- [ ] Calibrate `digest_cutoff_time` — 10am is a starting guess; adjust based on observed arrival pattern
- [ ] Finalize configuration file location (dedicated `deliveries.json` vs. broader grouping)

**Phase 3 — LoRa sensor fusion (future)**

- [ ] Calibrate `delivery_email_lag_window_minutes` — how long the confirmation email typically lags the physical door-open event at this address. Cannot be determined without real observation.
- [ ] Decide whether a door-open in `NO_MAIL_SCHEDULED` state should trigger an unexpected-activity notification (retrieval of a previously un-retrieved package, or something going on at the mailbox).

**Phase 2 — Multi-carrier packages**

- Captured in `AUTOMATION_BACKLOG.md` — separate design session when scoped.

---

*Last Updated: 2026-04-22*
