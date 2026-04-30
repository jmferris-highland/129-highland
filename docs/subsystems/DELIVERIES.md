# Deliveries — Design & Architecture

## Scope

The `Utility: Deliveries` flow owns the **informational layer** of terminal delivery events at the property — letter mail that was expected today, packages that are out for delivery today, and the deliveries of both. It consumes normalized email events from `Utility: Email Ingress`, parses delivery-specific content, maintains authoritative daily state, and publishes a clean consumer-facing surface for dashboards, notifications, and other flows.

The flow does **not** own IMAP connection management, folder conventions, or deduplication. Those concerns belong to `Utility: Email Ingress` (see `subsystems/EMAIL_INGRESS.md`).

The flow also does **not** track packages through their journey — pickup, hub transit, transit updates, expected-date slippage, etc. That's a separate concern scoped to a hypothetical future `Utility: Shipments` flow, not this one. See § Architectural Principles.

Physical sensors also belong elsewhere. When a mailbox door sensor or driveway package sensor is installed, its raw events are published by `Area: Driveway`. `Utility: Deliveries` subscribes to those raw events and fuses them with the informational layer to produce richer state.

### Phases

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1 — USPS Informed Delivery** | Letter mail state machine + today's USPS packages (driven by OFD and Item Delivered emails) | 📋 Planned |
| **Phase 2 — Other carriers** | Same terminal-events model extended to non-USPS carriers (UPS, FedEx, Amazon) | Backlog |
| **Phase 3 — Physical sensor fusion** | Consume `Area: Driveway` mailbox door events; maintain pending-retrieval queue composing letter mail + mailbox-destined packages; reconcile email vs. physical signals | Blocked on LoRa hardware |

This document covers Phase 1 in detail and establishes the architectural framework for Phase 3. Specific Phase 3 parameters (timing windows, thresholds, classification rules) will be calibrated from empirical data once hardware is installed. Phase 2 is captured in `AUTOMATION_BACKLOG.md`.

---

## Architectural Principles

Before diving into phases, several principles shape the whole design:

### Separation of claims from reality

- The **email layer** is authoritative about USPS's claims — "USPS says letter mail is coming today," "USPS says a package is on the truck today," "USPS says item X was delivered to Y." It knows nothing about physical reality.
- The **physical layer** (Phase 3) is authoritative about observable events — "the mailbox door opened." It knows nothing about USPS's claims.
- The **synthesis layer** (Phase 3) composes both to produce user-facing semantics — "there is probably mail waiting," "a package is likely on the porch."

Each layer has a clean contract. This separation means each can evolve, be replaced, or be extended without disturbing the others.

### Affirmative signaling over inference

State changes happen when a positive signal arrives, not when time elapses without one. Default states ("not expecting mail," "no packages expected today") only promote on an explicit incoming email signal, rather than using cutoff timers that introduce race conditions.

### Terminal events, not journey tracking

`Utility: Deliveries` is scoped to **terminal events and today's expectations**. What's been delivered, where, and what's actively being delivered today. Nothing else.

Specifically **out of scope**:

- Tracking packages across days (pickup, hub transit, in-transit updates)
- Maintaining persistent per-package records spanning multiple days
- Expected-delivery-date slippage detection
- "Packages inbound sometime this week" dashboard visibility
- Any package signal weaker than "out for delivery right now"

The rationale: USPS's digest-based delivery estimates are soft (dates slip routinely), and a convenience flow doesn't benefit from tracking that squishiness. The strong, commitment-grade signals are:

- **Letter mail:** the digest's enumeration of pieces for today (there is no separate pre-delivery commit signal for letter mail; the digest IS the commit)
- **Packages:** the Out For Delivery email (USPS has loaded the carrier; delivery is actively happening)
- **Delivery confirmations** (Mail Delivered, Item Delivered): the terminal events themselves

Everything else is journey-tracking noise that, if ever needed, would be built as a separate `Utility: Shipments` flow subscribing to the same email ingress stream with different concerns.

### Probabilistic by design (Phase 3)

No combination of hardware we can reasonably deploy will definitively identify *who* interfaces with the mailbox at a given moment. Phase 3 is an inherently probabilistic subsystem — its outputs should express confidence, not claim certainty, and a manual override is a first-class feature, not a fallback. This is a principled design choice, not a limitation to apologize for. Future enhancements should improve confidence, not chase certainty.

### Letter mail and packages are different concerns

Letter mail is a single-per-day binary: is there letter mail today or not? Modeled as a state machine with daily reset.

Packages are independent items, each potentially expected or delivered today or not. Modeled as two ephemeral today-lists (expected, delivered) that reset at midnight. No persistent per-package records across days.

Treating these as one model confuses both. The design uses a **letter-mail state machine** and a **today's packages** structure in parallel, each consuming the appropriate signals.

---

## Dependencies

- **`Utility: Email Ingress`** — Provides the normalized email stream via `highland/event/email/deliveries/+/received`. This flow must be live before Phase 1 can go into production. See `subsystems/EMAIL_INGRESS.md`.
- **USPS Informed Delivery registration** — Requires a mailed PIN for identity verification before email delivery begins. Done once, out-of-band, before Phase 1 goes live.

---

## Understanding Informed Delivery Emails

USPS Informed Delivery is a free service that proactively notifies subscribers about mail and packages being sent to their address. The service produces **four distinct email types**, each with different semantics:

| # | Email Type | Fires When |
|---|-----------|-----------|
| 1 | **Informed Delivery Digest** | Letter mail expected today, OR a package expected today, OR a package is inbound for any future day. Typically arrives 6–9am local. May fire on Sundays and mail holidays for future-package-only scenarios. |
| 2 | **Package Status Update** | A specific inbound package undergoes a meaningful status change (picked up from sender, hub transit, out for delivery, unable to deliver, etc.). One email per status change, per package. |
| 3 | **Mail Delivered** | Letter mail (first-class only, not parcels/packages) that was expected today has been placed in the mailbox. Single email per day. |
| 4 | **Item Delivered** | A package has been delivered. One email per package regardless of delivery location (mailbox, front porch, etc.). Body text identifies the location. |

### Which emails we consume

Not all four types drive state in this flow. Under the terminal-events-only framing, we consume different subsets for letter mail vs. packages:

| Email Type | Letter Mail | Packages |
|-----------|:-----------:|:--------:|
| Informed Delivery Digest | ✅ Letter-mail section | ❌ Package section ignored |
| Package Status Update | — | ✅ OFD only; other statuses dropped |
| Mail Delivered | ✅ | — |
| Item Delivered | — | ✅ |

The digest's package section is intentionally ignored. It contains USPS's delivery *estimates*, which routinely slip. The Out For Delivery email is the commitment-grade signal that a package is actually coming today.

Other Package Status Updates (picked up, in-transit, hub traversal, unable to deliver, etc.) are journey-tracking signals that fall outside Deliveries' scope. The parser recognizes them as valid USPS emails and ACKs them as `ok`, but does not publish downstream events or update state.

### 1. The digest is multi-purpose — we use it for letter mail only

The digest arrival **does not by itself mean letter mail is coming today**. It could contain only future-package tracking information with no letter mail. The parser must extract each signal independently and only act on the letter-mail portion.

- **Letter mail for today** → piece count (parsed from the HTML body's "You have N mailpiece(s)..." line — see § Confirmed parse signals) — promotes letter-mail state machine to `EXPECTING`
- **Package information** → parsed for debugging/observability but not used to drive state

Because digests can arrive on no-mail days (Sundays, holidays) when only a future package is tracked, "digest received today" is not a synonym for "mail day." Letter-mail state only promotes on explicit letter-mail content in the digest.

### 2. Status updates — OFD is the package ingress signal

The OFD (`OUT_FOR_DELIVERY`) Package Status Update email is the commitment-grade signal that a specific package is coming today. It typically arrives mid-morning to early afternoon, providing some lead-time visibility before the Item Delivered email.

- OFD email for a tracking number → add to today's expected packages
- Any other status update → ACK as `ok`, do not publish downstream event

Deferring to OFD (rather than the digest's today-listing) avoids the slippage problem: a package that's OFD is on the truck; a package in today's digest may or may not actually deliver today.

### 3. Mail Delivered — letter-mail terminal signal

The Mail Delivered email is the load-bearing signal for the letter-mail state machine's `EXPECTING → DELIVERED` transition. It covers first-class letter mail only — parcels and packages generate Item Delivered emails instead. The email's `Date:` header is the canonical delivery timestamp — USPS sends the notification at or within a few minutes of actual delivery — preferred over scraping a date out of the body.

Typical delivery timing at this address: primary 3–5pm window, tail extending to ~7:30pm (observed latest in ~8 months of historical data).

### 4. Item Delivered — per-package terminal signal

Each delivered package generates its own Item Delivered email. Key properties:

- **Delivery location** is parseable from the email body. Canonical values: `"mailbox"`, `"front_door"`, `"other"` (catch-all for variants like "on porch," "with resident," etc.).
- **Ordering is not guaranteed.** On days with both letter mail and package delivery, Mail Delivered and Item Delivered emails may arrive in any order with arbitrary gaps. Packages that fit in the mailbox typically arrive on the same carrier stop as letter mail (delivery emails close together in time); packages that don't fit are delivered on a separate house stop, which may precede or follow the mailbox stop.
- **Tracking number** in the body allows correlation back to an entry in today's expected packages.
- **Surprise deliveries** (Item Delivered with no prior OFD seen) are expected and normal — we just record them in today's delivered list without a matching expected entry.

### Why this matters for the design

The architectural implications:

- **Digest parsing** extracts only the letter-mail signal. Package content in the digest is ignored by design.
- **Status update parsing** filters for OFD; other statuses are silently accepted and dropped.
- **Letter-mail state machine** subscribes only to Mail Delivered signals and letter-mail digest content. Package signals don't touch it.
- **Today's packages** structure subscribes to OFD and Item Delivered emails. Two lists (expected, delivered) — both ephemeral, both cleared at midnight.
- **Phase 3 synthesis** composes signals from both structures when reasoning about mailbox contents. A letter mail delivery *and* a mailbox-destined package delivery both contribute to "what's in the box." OFD drives the package-today deferral for door-open classification.

### Coverage limitations

Informed Delivery has known gaps. It covers **first-class mail** (captured by USPS's automated scanning) and **promoted advertisements** (opt-in by the sender). It does not cover:

- **Bulk mail / standard mail class** — unpromoted advertisers, circulars, mass mailings. Delivered physically but never announced via email.
- **Non-USPS items placed in the mailbox** — other carriers' drivers, delivery services, or neighbors occasionally using the mailbox. Invisible to USPS and therefore invisible to us.

The first gap is the notable one: bulk mail is a regular occurrence that produces real physical deliveries with no corresponding email signal. On days where Highland's state otherwise shows nothing mailbox-bound expected, bulk mail can still arrive. Phase 1 is informational-only and isn't affected — it simply reports what USPS told us. Phase 3 is where the gap manifests as a blind spot in mailbox-contents reasoning (see Phase 3 § Known blind spot: bulk-mail-only deliveries).

This is not a bug to fix — it's an honest limitation of an email-driven design. Our model of reality is bounded by what USPS reports to us.

### Confirmed parse signals

These are empirically verified from real digest and Mail Delivered emails. OFD and Item Delivered patterns will be added once samples are observed.

**Sender (all email types observed so far):**

- Address: `USPSInformeddelivery@email.informeddelivery.usps.com`
- Display name: `USPS Informed Delivery`
- Recommended Gmail filter match: the domain `@email.informeddelivery.usps.com` rather than the full local-part-sensitive address

**Subject prefixes — load-bearing for sub-routing inside the USPS Parser:**

- Daily Digest: starts with `Your Daily Digest for ` (followed by day-of-week, `M/D`, ` is ready to view`)
- Mail Delivered: starts with `Your Mail Was Delivered ` (followed by day-of-week, abbreviated month, day)

The day/date in the subject is informational only — the `Date:` header is authoritative for timing.

**Daily Digest body content:**

- The plain text part is boilerplate-only (dashboard URL, unsubscribe URL, copyright) — identical between mail-day and no-mail-day digests except for the date string. **Not usable for parsing.**
- The HTML body contains a count line in visible text matching `/You have (\d+) mailpiece\(s\) and (\d+) inbound package\(s\) arriving soon\./`. The first capture group is the authoritative letter-mail piece count for state-machine promotion. The second capture group (digest's package-section count) is intentionally discarded per § Why this matters for the design.
- The phrase appears twice in the rendered HTML (mobile and desktop layouts); first match is sufficient.

**Daily Digest MIME structure:**

- Mail-day digest: `multipart/related` containing `multipart/alternative` plus inline `image/jpeg` parts.
- No-mail-day digest: `multipart/alternative` only — zero image parts.
- **The inline image count is not a reliable piece count.** USPS-promoted mailer images appear alongside actual scans, individual mailpieces can lack scans entirely, and other variations occur. The Email Ingress payload's `attachment_count` is useful only as a coarse "had images / didn't" sanity check, never as a count.

**Mail Delivered body and timing:**

- Single-part `text/html`. No plain text alternative, no attachments.
- The email's `Date:` header is the canonical delivery timestamp — USPS sends the notification at or within a few minutes of actual delivery. Body content confirms the date but adds no precision over the header.

---

## Phase 1 — USPS Informed Delivery

### Problem

USPS Informed Delivery provides emails that let us answer:

- Is letter mail scheduled today?
- Has letter mail been delivered today?
- Is a package out for delivery today?
- Which packages have been delivered today, and where?

All answerable without physical infrastructure. This delivers a useful informational baseline that:

1. Ships before LoRa hardware arrives.
2. Establishes the consumer-facing `highland/state/deliveries/*` and `highland/event/deliveries/*` contracts so downstream flows, cards, and notifications can be built once and never rewritten.
3. Remains useful *after* LoRa arrives — the email signal becomes one input to the fused state rather than the only input.

### Ingress Contract

This flow subscribes to `highland/event/email/deliveries/+/received` — a wildcard match for all sources under the `Highland/Deliveries/` Gmail label namespace. Payloads arrive in the standard shape defined in `subsystems/EMAIL_INGRESS.md § Payload Schema`. For every successfully processed (or deliberately rejected) message, this flow publishes `highland/ack/email` with the matching `message_id`.

Per the label convention in `EMAIL_INGRESS.md`, Gmail labels under `Highland/Deliveries/` identify the email's source (carrier/service), not the product name. For Phase 1, the only active source is `Highland/Deliveries/USPS`, which captures USPS Informed Delivery emails. Phase 2 will add sources like `Highland/Deliveries/UPS`, `Highland/Deliveries/FedEx`, etc.

The Gmail filters that route incoming mail to the appropriate label are manually configured in Gmail, not defined in this flow. Gmail filter setup is part of the Ingress operational runbook.

### Letter-Mail State Machine

Three primary states plus one exception branch. Affirmative signals only — default is `NOT_EXPECTING`, and only an incoming email can promote to higher-information states.

```
NOT_EXPECTING ──(digest w/ letter mail)──▶ EXPECTING ──(Mail Delivered)──▶ DELIVERED
                                                │                                │
                                       (8pm, no confirmation)                    │
                                                │                                │
                                                ▼                                │
                                            EXCEPTION                            │
                                                │                                │
                                                └────(midnight)────▶ NOT_EXPECTING ◀┘
```

**States:**

| State | Meaning |
|-------|---------|
| `NOT_EXPECTING` | No digest indicating letter mail has arrived today. Either it's a no-mail day, the digest hasn't arrived, or the digest that did arrive contained only package information. Default state; entered on startup and at midnight rollover. |
| `EXPECTING` | Digest received that enumerates letter-mail pieces for today. USPS reports letter mail is coming. Delivery has not yet been confirmed. |
| `DELIVERED` | Mail Delivered email received. USPS reports letter mail has been placed in the box. |
| `EXCEPTION` | In `EXPECTING` state when the exception threshold (8pm) is reached. USPS said letter mail was coming, but the delivery window has effectively closed without confirmation. Resolves at midnight. |

**Important:** The digest promotes to `EXPECTING` only if letter-mail pieces are present in the digest. A package-only digest leaves letter-mail state at `NOT_EXPECTING`. This is what makes "digest received" a weak signal in isolation — the letter-mail content must be explicitly present.

**Exception threshold rationale:** 8pm is chosen from ~8 months of historical Mail Delivered emails at this address. Delivery times cluster in a primary 3–5pm window with a tail extending to 7:30pm (latest observed). 8pm represents a 30-minute margin past the latest-observed delivery, covering >99% of normal deliveries. After 8pm with no confirmation, the probability that delivery will still occur today is vanishingly small, and the state reflects that by promoting to `EXCEPTION`. This threshold is a claim about USPS delivery patterns, not about operator behavior — it should recalibrate if ~6 months of operational data show a distribution shift.

**Design notes:**

- Single state machine per day. State resets to `NOT_EXPECTING` at midnight; persistent letter-mail context cleared.
- Only two time-based transitions: the 8pm exception check (if still `EXPECTING`) and the midnight rollover.
- **Mail Delivered is unconditional.** The `EXPECTING → DELIVERED` path shown above is the primary sequence, but Mail Delivered transitions to `DELIVERED` from any starting state — including `NOT_EXPECTING` (surprise Mail Delivered with no prior digest seen today) and `EXCEPTION` (Mail Delivered arriving after the 8pm threshold fired). The state diagram shows the primary path; implicit edges from `NOT_EXPECTING` and `EXCEPTION` to `DELIVERED` also exist.
- `EXCEPTION` is not an alarm or a notification in itself. It's a state that surfaces the anomaly in `highland/state/deliveries/letter_mail` for dashboards and downstream consumers to react to as they see fit.
- The letter-mail state machine makes no claim about whether mail is physically present in the mailbox. It represents USPS's claims only. Physical reality is the concern of the Phase 3 synthesis layer.

### Today's Packages

Two ephemeral lists, both cleared at midnight:

- **Expected today** — tracking numbers we've seen OFD emails for, not yet delivered
- **Delivered today** — tracking numbers we've seen Item Delivered emails for, including delivery location

Both lists live in flow context (`packages_expected_today`, `packages_delivered_today` — default store, so they survive restart within the same day).

**Lifecycle per tracking number, within a single day:**

```
                ┌──────────────────────────┐
                │                          │
      (OFD email)                   (Item Delivered
                │                    without prior OFD)
                ▼                          │
     ┌──────────────────┐                  │
     │ expected_today[] │                  │
     └────────┬─────────┘                  │
              │                            │
              │ (Item Delivered matches)   │
              ▼                            ▼
     ┌──────────────────────────────────────┐
     │          delivered_today[]           │
     └──────────────────────────────────────┘
              │
              │ (midnight rollover)
              ▼
          (both lists cleared)
```

**Expected today — entry shape:**

```json
{
  "tracking_number": "9400111899220000000001",
  "ofd_at": "2026-04-22T14:02:00Z",
  "source": "informed_delivery"
}
```

**Delivered today — entry shape:**

```json
{
  "tracking_number": "9400111899220000000002",
  "delivered_at": "2026-04-22T18:30:00Z",
  "delivered_to": "mailbox",
  "source": "informed_delivery"
}
```

`delivered_to` values: `"mailbox"` | `"front_door"` | `"other"`.

**Processing rules:**

- **OFD email arrives:** if the tracking number isn't already in expected or delivered, append to expected. Publish `package/expected` event. Republish aggregate state. If the tracking number is already in delivered (out-of-order: Item Delivered arrived before OFD — shouldn't happen but possible), do nothing new.
- **Item Delivered email arrives:** remove the tracking number from expected (if present), append to delivered with location. Publish `package/delivered` event. Republish aggregate state. If the tracking number is already in delivered (USPS duplicate send), ignore.
- **Other status updates:** ACK as `ok`, do nothing.
- **Midnight rollover:** clear both lists. Publish empty aggregate state.

**Edge cases:**

- **Surprise delivery** (Item Delivered without prior OFD). Append directly to delivered. Not an error; expected behavior for packages USPS didn't pre-announce or where OFD firing was missed.
- **Unparseable tracking number** (on either OFD or Item Delivered). Publish `highland/ack/email` with `status: "parse_error"`, log the failing structure, do not publish downstream event. A delivery confirmation we can't correlate is a parse failure.
- **OFD but no delivery by end of day.** The tracking number stays in expected until midnight, then is cleared. No explicit "delivery failed" state — if it didn't deliver today, tomorrow's OFD (if any) will re-announce it.

### Restart Recovery

On Node-RED startup (cold start, post-crash, post-deploy), the flow does not rely on replay from Email Ingress — digests and delivery confirmations that were already processed are archived and will not be re-delivered. Instead, recovery happens from our own retained MQTT state.

**On startup, subscribe with retained-message delivery to:**
- `highland/state/deliveries/letter_mail`
- `highland/state/deliveries/packages/today`

**Initialization logic:**
- If the retained payload's `timestamp` is from today (local-day comparison): restore in-memory context from the payload. Letter-mail state, `expected_pieces`, `digest_received_at`, `delivered_at`, and today's expected/delivered package lists all rebuild from the retained state.
- If the retained payload's timestamp is from before today, or no retained payload exists: initialize to defaults (letter-mail `NOT_EXPECTING`, empty package lists) and publish fresh retained state.

This gives same-day restart continuity without requiring email replay. Multi-day outages are not a concern — if Deliveries is down for days, Highland has bigger problems than this flow, and the system converges at the next midnight rollover regardless.

Flow context (`default` store) also survives same-day restart and is used for within-day incremental updates. The retained-state recovery is specifically for the case where context is lost (first-ever run, context corruption, explicit reset).

### MQTT Topics

**State (retained):**

`highland/state/deliveries/letter_mail` — letter-mail state machine's current state

```json
{
  "timestamp": "2026-04-21T18:15:00Z",
  "source": "informed_delivery",
  "state": "DELIVERED",
  "expected_pieces": 3,
  "digest_received_at": "2026-04-21T11:15:00Z",
  "delivered_at": "2026-04-21T18:15:00Z"
}
```

`state` values: `NOT_EXPECTING` | `EXPECTING` | `DELIVERED` | `EXCEPTION`. Fields other than `state` and `timestamp` may be null in states that haven't reached the corresponding transition.

`highland/state/deliveries/packages/today` — today's packages picture

```json
{
  "timestamp": "2026-04-22T18:30:00Z",
  "source": "informed_delivery",
  "expected_count": 0,
  "delivered_count": 2,
  "expected": [],
  "delivered": [
    {
      "tracking_number": "9400111899220000000001",
      "delivered_at": "2026-04-22T18:28:00Z",
      "delivered_to": "mailbox",
      "source": "informed_delivery"
    },
    {
      "tracking_number": "9400111899220000000002",
      "delivered_at": "2026-04-22T18:30:00Z",
      "delivered_to": "front_door",
      "source": "informed_delivery"
    }
  ]
}
```

Published on every state change (OFD arrival, Item Delivered arrival, midnight clear). Small — even heavy package days rarely exceed a handful of concurrent entries.

**Events (not retained):**

| Topic | Fires When | Payload |
|-------|-----------|---------|
| `highland/event/deliveries/letter_mail/expected` | Digest parsed containing letter-mail pieces for today | `{ piece_count, timestamp }` |
| `highland/event/deliveries/letter_mail/delivered` | Mail Delivered email parsed | `{ timestamp }` |
| `highland/event/deliveries/letter_mail/exception` | 8pm threshold reached in `EXPECTING` state | `{ expected_pieces, digest_received_at, timestamp }` |
| `highland/event/deliveries/package/expected` | OFD email parsed (package added to today's expected) | `{ tracking_number, ofd_at, timestamp }` |
| `highland/event/deliveries/package/delivered` | Item Delivered email parsed | `{ tracking_number, delivered_to, timestamp }` |

**ACK (not retained):**

`highland/ack/email` — published after each ingress message is processed. See `subsystems/EMAIL_INGRESS.md § ACK` for schema.

### Flow Outline — `Utility: Deliveries`

Per `nodered/OVERVIEW.md` conventions: groups are the primary organizing unit; link nodes connect groups; no node has more than two outputs.

#### Source dispatch via link-call

The ingress source-routing layer dispatches to per-source parsers using Node-RED's `link call` node, treating each parser as a callable subroutine. The Source Router invokes the appropriate parser (e.g., `link call` → `USPS Parser`); the parser's `link out` in return mode hands flow control back to the caller. See `nodered/OVERVIEW.md § Link Call as Callable Subroutine` for the general pattern.

This is preferred over fan-out-by-output for source dispatch because:

- The dispatcher stays single-output-per-decision (link-call invocation, plus a separate path for unknown sources) rather than fanning to one branch per carrier
- Each parser is self-contained: a `link in` named for the source, processing nodes, and a `link out` in return mode
- Adding Phase 2 carriers means adding their `link in` nodes and another `link call` invocation; no restructuring of the dispatcher

Parsers publish their own ACKs directly to a shared `Publish ACK` MQTT out node — ACK responsibility belongs with the node that knows whether processing was `ok`, `parse_error`, or `rejected`. The link-call return path carries the original `msg` (with its `_linkSource` metadata intact) so the link call's downstream wires receive flow control after the parser completes. This means parser function nodes typically have **two outputs**: one for the ACK (to `Publish ACK`) and one for the return `msg` (to the parser's `link out` in return mode).

#### Groups

**Group 1 — Ingress Subscription**
- MQTT In on `highland/event/email/deliveries/+/received`
- `Route By Source` function: extract source from topic (segment 4 in `highland/event/email/deliveries/<source>/received`); validate `message_id` is present
- For known sources (currently `usps`): invoke parser via `link call`
- For unknown sources: build rejection ACK with `status: "rejected"` and emit to `Publish ACK` MQTT out
- For malformed messages (missing `message_id`, bad topic shape): set status indicator, return without ACK; the catch handler covers any thrown errors

**Group 2 — USPS Parser** *(callable subroutine)*

Entered via `link in` named `USPS Parser`; exits via `link out` in return mode.

- Sub-route by subject/body pattern → Digest path / Mail Delivered path / Item Delivered path / Package Status Update path
- **Digest path:** extract letter-mail content (piece count, has-images heuristic). Link-out to Letter-Mail State Machine if pieces present. Package content in the digest is ignored by design.
- **Mail Delivered path:** confirm sender and subject, timestamp, link-out to Letter-Mail State Machine.
- **Item Delivered path:** extract tracking number from body, parse delivery location, link-out to Today's Packages.
- **Package Status Update path:** identify status from subject/body. If OFD, extract tracking number, link-out to Today's Packages. For all other statuses, ACK `ok` and terminate (no downstream processing).
- On any parser success: build `ok` ACK, emit on output 1 to `Publish ACK`; emit original `msg` on output 2 to the return `link out`.
- On parser failure (including unparseable tracking number): build `parse_error` ACK, same dual-output pattern; log the failing structure.

The two-output pattern preserves `_linkSource` on the return path so the link-call dispatcher's downstream wires fire correctly. Replacing `msg` with the ACK payload would strip the link-call metadata and cause a return timeout.

**Group 3 — Letter-Mail State Machine**
- Reads flow context for current state, applies transition rules, emits new state.
- Publishes retained `highland/state/deliveries/letter_mail` on any transition.
- Publishes corresponding `letter_mail/*` events.

**Group 4 — Today's Packages**
- Maintains `packages_expected_today` and `packages_delivered_today` lists in flow context (default store).
- OFD from parser → append to expected (if not already tracked), publish `package/expected` event, republish aggregate state.
- Item Delivered from parser → move from expected to delivered (or append directly to delivered if surprise), publish `package/delivered` event, republish aggregate state.

**Group 5 — Scheduler Hooks**
- 8pm exception check (CronPlus, two-output per project convention): if letter-mail state is `EXPECTING`, transition to `EXCEPTION`.
- Midnight rollover: reset letter-mail state to `NOT_EXPECTING`; clear both today's-packages lists; republish cleared aggregate state.

**Group 6 — HA Discovery**

Letter mail:
- `sensor.letter_mail_status` (string — current state)
- `sensor.letter_mail_expected_pieces` (int)
- `sensor.letter_mail_last_digest` (timestamp)
- `sensor.letter_mail_last_delivered` (timestamp)

Packages:
- `sensor.packages_expected_today` (int — count currently in expected list)
- `sensor.packages_delivered_today` (int — count currently in delivered list)

### Consumer Surface

Letter mail and packages share a single dispatch path: when a transition warrants a notification, the producing parser group attaches `msg.notification` to the synchronous return-path message; a tab-level Notification Pipeline group invoked by the main Delivery Pipeline checks for this envelope and publishes to `highland/event/notify` if present. This separates notification *content* (carrier-specific, owned by the parser group) from notification *delivery* (carrier-agnostic, owned by the dispatch group). Future carriers and packages plug in by attaching their own `msg.notification` from inside their own parser groups; the dispatch logic does not change.

#### Letter-mail notifications (Phase 1)

| Transition | `notification_id` | Severity | Title | Notes |
|------------|-------------------|----------|-------|-------|
| `→ DELIVERED` | `usps.mail_delivered` | `low` | Mail delivered | Includes piece count when `EXPECTING`-derived; surprise-delivery framing otherwise |
| `→ EXCEPTION` | `usps.mail_exception` | `medium` | Mail running late | Includes `expected_pieces` and `digest_received_at` |

`expected` transitions deliberately do not produce notifications — mid-day "mail is coming" is low-signal noise.

Both notifications share the correlation_id `usps_mail`. This is intentional: today's delivered notification replaces yesterday's via the HA tag mechanism (day-over-day replacement); a `DELIVERED` transition that follows an earlier `EXCEPTION` on the same day replaces the stale exception notification (same-day supersession). The user always sees a single Highland letter-mail notification reflecting the latest state, never a stack of progressively-outdated alerts.

Different carriers' mail-equivalent notifications (if any ever exist) use separate correlation_ids and do not replace each other.

#### `notification_id` naming convention

Follows `{carrier}.{thing}_{event}`. Carrier is a meaningful axis here — different carriers produce different kinds of events with different failure modes, and may warrant carrier-differentiated routing in the future. This differs from single-axis subscription keys elsewhere in Highland (e.g., `dishwasher.cycle_finished`) where the manufacturer is implementation detail; for deliveries, the carrier is part of the action itself.

Within a carrier namespace the `_thing` qualifier disambiguates (`mail` vs. `package`). Across carriers the carrier prefix disambiguates. `letter_mail_*` would be redundant in the same way `package_parcel_*` would be — the carrier already implies the domain.

#### Other consumers

Dashboard cards, automation flows, and other consumers may subscribe directly to `highland/state/deliveries/*` and `highland/event/deliveries/*` topics. The retained-state topic is the appropriate subscription for current-status displays; the event topics are appropriate for transition-driven automations. The notification surface above is a parallel consumer of the same transitions; subscribing to it directly would duplicate routing already handled by `Utility: Notifications`.

### Configuration
Delivery-specific tunables only. All IMAP/folder/retention concerns live in `config/email_ingress.json`.

```json
{
  "informed_delivery": {
    "sender_domain": "email.informeddelivery.usps.com",
    "letter_mail_exception_time": "20:00"
  }
}
```

No `package_retention_days` — today's packages are ephemeral and cleared at midnight, so retention is not a concern. File location is TBD — depends on how configuration groups ultimately organize. Candidates include a dedicated `config/deliveries.json` or inclusion in a broader file once the shape stabilizes. Decision deferred to implementation.

### Time Handling

This flow follows the project-wide convention documented in `standards/TIME_HANDLING.md`: UTC for storage and transport, local time for scheduling and presentation. Specifically:

- MQTT payloads, flow context, and logs use UTC ISO 8601.
- `letter_mail_exception_time: "20:00"` is interpreted as 8pm **local**.
- Midnight rollover fires at 00:00 **local** (not UTC).
- Email timestamps from USPS are normalized to UTC at the parser boundary.
- "Today" comparisons (e.g., restart recovery's today-check) are against local calendar date.
- HA Discovery sensors display local time via HA's native timezone handling — no explicit conversion required from this flow.

See `standards/TIME_HANDLING.md` for the full convention, rationale, and implementation notes.

---

## Phase 3 — Physical Sensor Fusion (Future)

When the LoRa mailbox door sensor is installed (per `subsystems/LORA.md`), the design adds a third data model — a **pending-retrieval queue** — and a synthesis layer that composes it with the Phase 1 email-layer structures.

**This is not an extension of either existing structure.** Different concerns deserve different models:

- The **letter-mail state machine** represents USPS's daily claims about letter mail, and cycles daily.
- **Today's packages** represents today's package activity, and cycles daily.
- The **pending-retrieval queue** represents physical items in the mailbox awaiting retrieval, which can persist across multiple days.

Mail and small packages accumulate. With Informed Delivery providing daily previews, the operator may not physically check the mailbox every day — a delivery on Monday that isn't retrieved is still in the mailbox on Tuesday when another delivery arrives. The queue model captures this; extending either daily structure would force combinatorial states onto data with different lifecycles.

### Producer: `Area: Driveway`

Publishes raw physical events only — no interpretation. Published topics are defined in `subsystems/LORA.md § Use Case 2`:

- `highland/state/driveway/mailbox` (retained) — sensor telemetry including `door_state`, battery, env, signal.
- `highland/event/driveway/mailbox/opened` (not retained) — door opened.

No state machine; no delivery logic. The driveway flow doesn't know whether a door-open is a carrier, a retrieval, or someone checking an empty mailbox.

### Consumer: `Utility: Deliveries` — Mailbox-Contents Queue

Maintains a queue of pending-retrieval records, each representing one delivered item currently believed to be in the mailbox. Records come from two sources:

- `highland/event/deliveries/letter_mail/delivered` → appends letter-mail record (queue entry with `type: "letter_mail"`, `piece_count`)
- `highland/event/deliveries/package/delivered` with `delivered_to: "mailbox"` → appends package record (queue entry with `type: "package"`, `tracking_number`)

Package deliveries to `front_door`/`other` locations do not go into this queue — they're tracked in today's delivered list but aren't in the mailbox. A future phase (out of scope here) could introduce a porch-contents queue if that becomes valuable.

**Queue entry shape:**

```json
{
  "type": "letter_mail",
  "delivered_at": "2026-04-19T19:20:00Z",
  "piece_count": 2,
  "source": "informed_delivery"
}
```

or

```json
{
  "type": "package",
  "delivered_at": "2026-04-20T20:05:00Z",
  "tracking_number": "9400111899220000000002",
  "source": "informed_delivery"
}
```

**New topics:**

- `highland/state/deliveries/pending` (retained) — current queue contents, oldest-age, counts, and confidence indicator
- `highland/event/deliveries/retrieved` (not retained) — fires when the queue transitions from non-empty to empty via a retrieval classification

Aggregate payload shape (provisional):

```json
{
  "timestamp": "2026-04-21T23:15:00Z",
  "source": "deliveries_synthesis",
  "count": 3,
  "letter_mail_pieces": 2,
  "mailbox_packages": 1,
  "oldest_delivered_at": "2026-04-19T19:20:00Z",
  "oldest_age_hours": 52.0,
  "confidence": "high",
  "pending": [
    { "type": "letter_mail", "delivered_at": "2026-04-19T19:20:00Z", "piece_count": 2, "source": "informed_delivery" },
    { "type": "package", "delivered_at": "2026-04-20T20:05:00Z", "tracking_number": "9400111899220000000002", "source": "informed_delivery" }
  ]
}
```

### Door-Open Classification

The central challenge of Phase 3 is classifying door-open events as **carrier activity** (do not clear queue) or **retrieval activity** (clear queue). Signals from both email-layer structures inform this classification.

**Sequence of events on a normal mail day:** carrier opens mailbox → carrier deposits mail (and possibly a small package) → carrier closes box → carrier syncs device → USPS fires delivery confirmation email(s) (typically 10-30+ minutes later). The door-open *precedes* the email(s), not the other way around.

**Classification rule:**

A door-open is classified as **Deferred** if either of these is true at the moment of the event:

- Letter-mail state is `EXPECTING`
- Today's packages has at least one entry in `expected_today` that hasn't moved to `delivered_today`

Otherwise, the door-open is classified as **Retrieval** (clear queue).

| Condition at door-open | Classification |
|------------------------|----------------|
| Letter-mail `EXPECTING` OR `expected_today` non-empty | Deferred |
| Letter-mail `NOT_EXPECTING`/`DELIVERED`/`EXCEPTION` AND `expected_today` empty | Retrieval |

This unifies the letter-mail `EXPECTING` deferral and the package-OFD deferral into a single check: "is something mailbox-bound actively being delivered right now?" — with "actively being delivered" meaning "USPS has committed to today" (letter mail in today's digest or OFD for a package).

#### Known blind spots

Phase 3 door-open classification is inherently imperfect. It relies on email signals (which have coverage gaps) and on a binary classifier (carrier vs. retrieval) that doesn't capture all physical realities. Two specific blind spots are worth documenting explicitly so the system's limits are understood rather than discovered.

##### Bulk-mail-only deliveries

Because Informed Delivery doesn't cover bulk/standard-class mail (see § Coverage limitations), a carrier delivering only bulk mail on a day with no letter-mail expectations and no package OFDs produces:

- A door-open event with no matching delivery email
- Classification as **Retrieval** under the rule above (queue cleared)
- But the mailbox actually contains the bulk mail, unaccounted for

The system silently undercounts mailbox contents on these days. Functionally nothing breaks — the classification logic runs correctly given its inputs; the queue clears to empty (from empty, so no state change); the next operator door-open also classifies as retrieval (correct, if now vacuously so). But the dashboard briefly says "nothing pending" when there's actually junk mail in the box.

Operational impact is low: bulk mail is low-value content, and state converges to reality the next time the operator physically checks the mailbox. Worth acknowledging as a known limitation rather than trying to work around with fragile heuristics (time-of-day windows, neighbor correlation, etc.).

The Phase 3b USPS vehicle detection enhancement specifically resolves this blind spot — door-open + USPS vehicle detected in snapshot classifies as carrier regardless of email signals. Until Phase 3b is built (or shelved as infeasible), bulk-mail-only days are silently invisible to the system.

##### Outbound mail deposits

When the operator opens the mailbox to deposit outbound mail for carrier pickup, the system cannot distinguish this from a retrieval. The sequence:

- Door-open event with no corresponding delivery email (no active expectation, no email follow-up)
- Classification as **Retrieval** under the rule above (no expectations → clear queue)
- But the box now physically contains outbound mail, possibly alongside unretrieved inbound from prior days

Impact is **more severe** than the bulk-mail blind spot: rather than silently undercounting, this can actively cause false queue clears when the queue had pending items prior to the outbound deposit. The pending items are physically still in the box; our state representation erroneously shows them as retrieved.

This affects several sub-scenarios:

- **Outbound on a no-expectation day:** Door-open immediately classified as retrieval; queue clears. Most visible failure.
- **Outbound on a letter-mail-EXPECTING day:** Door-open deferred, then when Mail Delivered arrives, resolution classifies most-recent door-open as carrier and earlier (user's outbound) as retrieval. Queue ends up with today's letter mail delivery, but prior-day contents were erased.
- **Outbound on a package-OFD day:** Similar to the EXPECTING case, resolved via Item Delivered.

Unlike the bulk-mail case, **Phase 3b vehicle detection does not resolve this blind spot.** Both outbound mail and genuine retrieval produce "door-open with no USPS vehicle present" — indistinguishable from the camera's perspective.

Mitigating factors that make this less severe in practice than it sounds:

- **Outbound mail is infrequent** for most modern households (bills online, personal letters rare). A few times per month at most.
- **Behavioral consolidation** — many operators naturally retrieve pending mail when depositing outbound ("I'm already here, might as well grab it"). When they do, the "retrieval" classification is actually correct.
- **Real-world frequency** of the failing intersection (outbound happens AND queue was non-empty AND user didn't consolidate by retrieving) is likely a handful of times per year.

Accepted as a limitation. No clean automated recovery exists — if the operator notices a false clear after the fact, physical mailbox contents simply don't match Highland's state until the next actual retrieval. A future enhancement could introduce a mailbox-flag sensor (detecting whether the outbound-mail flag has been raised) as a third signal to distinguish deposit from retrieval, but this is hypothetical hardware not currently planned.

**Resolution of deferred classification:**

1. Door-open is recorded but does not immediately clear the queue.
2. If a matching delivery event (`letter_mail/delivered` or `package/delivered` with `delivered_to: "mailbox"`) arrives within the classification window, the most recent door-open is reclassified as carrier activity (discarded); any earlier door-opens during the same window are reclassified as retrievals.
3. If the window expires with no matching delivery event, the door-open is reclassified as a retrieval.

**Classification window** is bounded by state transitions and end-of-day. For letter-mail `EXPECTING`, the window closes on either `DELIVERED` arrival or the 8pm exception threshold. For OFD-driven deferral, the window closes on either the package's `package/delivered` arrival or end-of-day. Empirically these windows are typically minutes to a few hours.

**Package delivered to non-mailbox location:** If a package the door-open was deferred for delivers to `front_door` or `other` (not mailbox), the deferral should resolve as retrieval — the door-open wasn't associated with a mailbox delivery event. Classification logic should account for this.

**Ambiguous sequences:** when multiple door-opens occur before a matching delivery event, confidence drops. The system publishes with `confidence: "low"` and may request manual confirmation via the override topic (see below). These sequences are expected to be uncommon.

### Manual Override

Because classification is inherently probabilistic, a manual override is a first-class feature:

**`highland/command/deliveries/mark_retrieved`** — clears the pending queue regardless of current state or ambiguity. No payload required. Intended for:

- Voice commands ("I got the mail")
- HA dashboard button tap
- Scripted automation (e.g., reset when guests handle mail during an away period)

Manual override always succeeds silently — no confirmation dialog, no friction. Correcting the system should be as easy as interacting with it.

### Confidence Reporting

The `confidence` field on `highland/state/deliveries/pending` reflects how certain the system is about the current queue state:

| Value | Meaning |
|-------|---------|
| `high` | No recent ambiguous events; queue state derives from clean signals (unambiguous delivered transitions or unambiguous retrievals). |
| `medium` | Some ambiguity but classification was resolvable (e.g., deferred door-open that resolved normally). |
| `low` | Multiple ambiguous events; queue count may not reflect reality. Consumers should prompt for manual verification. |

Consumers (notifications, dashboards, voice responses) should phrase outputs according to confidence — "you have 3 pieces of mail waiting" for `high`, "you likely have mail waiting" for `medium`, "mail state is uncertain, please verify" for `low`.

### Future Enhancement: USPS Vehicle Detection

A camera positioned to observe the mailbox approach could detect USPS vehicle presence correlated with door-open events, providing a much stronger classification signal than email-state correlation alone:

- Door-open + USPS vehicle detected in snapshot → HIGH CONFIDENCE carrier
- Door-open + no vehicle in snapshot → HIGH CONFIDENCE retrieval

This would slot into the synthesis layer as an additional input, making door-open classification real-time and eliminating most of the deferred-classification logic. It also **resolves the bulk-mail-only blind spot** documented above — door-open + vehicle-present classifies as carrier regardless of email signals.

#### Proposed trigger architecture (battery-friendly)

Standard battery cameras burn most of their power on always-on motion detection, producing dozens of useless wakes per day (wind, cars, animals, lighting changes). A battery camera in a busy outdoor location typically drains in weeks.

Inverting the trigger pattern preserves battery:

1. Camera motion detection **disabled entirely**
2. Camera sits in deep sleep
3. LoRa mailbox contact sensor fires `highland/event/driveway/mailbox/opened`
4. Highland sends capture command to camera (API call)
5. Camera wakes, captures, returns to sleep

Expected wake events per day: 1–3 (carrier visits + occasional retrievals), rather than dozens. Battery life multiplies proportionally — "months" becomes plausible.

#### Latency is the central constraint

The approach works only if end-to-end latency from physical door-open to camera snapshot is **less than the carrier's vehicle-present window at the mailbox**.

Latency contributors:

| Stage | Estimate |
|-------|----------|
| LoRa uplink (sensor → MQTT on hub.local) | 500ms–2s (see `LORA.md § LoRaWAN configuration defaults`) |
| Node-RED flow processing + camera API call | sub-second |
| Camera wake from deep sleep + capture | 2–8s (varies by model) |
| **Total estimate** | **~5–10s** |

**Property-specific geometry favors feasibility.** Several factors extend the effective vehicle-present window well beyond a typical single-mailbox carrier stop:

- The mailbox is one of three on a shared bank (three properties share the driveway; all three mailboxes on one post).
- Our mailbox is the **first** accessed on approach — our door-open fires while the carrier still has two more boxes to service on the bank.
- We are the **last stop** on the carrier's route for this part of the drive, so the carrier turns around in place after the bank, adding repositioning time before departure.

Realistic vehicle-present window from our door-open to carrier departure: **~25–60 seconds**. A single snapshot at T+8s (well inside our latency budget) should reliably catch the vehicle in frame. Burst capture becomes redundancy rather than necessity.

#### Challenges to resolve before this is viable

- **Line of sight and distance.** ~275ft driveway with no line of sight to the road in the straight-ahead direction. Camera placement needs to see the mailbox approach clearly. Possibly a camera on a fence post or tree near the mailbox, or mounted on the house with zoom optics.
- **Camera wake latency** of candidate hardware. Varies massively by model; must measure before committing. Reolink Argus series, Wyze Cam Outdoor, Eufy SoloCam, and similar battery cameras are candidates.
- **Empirical LoRa latency validation** per `LORA.md` open question. If real-world latency is consistently >2s or highly variable, the whole approach needs reconsideration.
- **Power source for camera.** Battery is the constraint that makes the sensor-triggered approach interesting. PoE would eliminate the power question but requires cable pulls to the mailbox area — probably not justified for this use case alone.
- **Connectivity to camera.** Candidate placement near the property line is ~100ft from the garage, at the edge of reliable consumer WiFi. Outdoor AP (e.g., TP-Link Omada EAP610-Outdoor) on the garage exterior is a likely path. See `architecture/NETWORK.md` for the broader network-rebuild context.
- **Vision classification pipeline.** Distinguishing a USPS LLV from other vehicles. Distinctive silhouette and livery make this tractable with a reasonable model; exact pipeline (CodeProject.AI on edgeai.local, Gemini API, other) TBD.

#### Phased progression for Phase 3

Given these uncertainties, the sensible path is:

- **Phase 3a — LoRa door sensor only.** Build the pending-retrieval queue and email-state-based classification. Valuable on its own; confidence will be "high" most of the time, "low" in rare ambiguous cases handled by manual override. Deploy, observe for months, tune parameters.
- **Phase 3b — Add camera with contact-sensor-triggered capture** *if* empirical latency measurements show it's viable. Camera provides vehicle-detection input that upgrades confidence in deferred-classification cases. If latency proves unworkable, shelve and accept Phase 3a's accuracy.
- **Phase 3c — Always-on PoE camera with continuous vision classification** — the "certainty" option, significantly more expensive (cable pulls, always-on power, persistent vision compute). Not warranted unless Phase 3a/3b prove insufficient for actual operational needs.

Blocked on:

- LoRa hardware deployment and latency characterization (`LORA.md`)
- Camera infrastructure build-out (issue #23)
- Line-of-sight survey of the mailbox approach
- Candidate camera selection and wake-latency measurement
- Outdoor AP deployment for mailbox-area WiFi coverage (see `architecture/NETWORK.md`)
- Vision classification pipeline design (tied to `edgeai.local` infrastructure, issue #22)

Not a near-term priority. The email-state-based classification (Phase 3a) delivers most of the Phase 3 value; vehicle detection is a confidence-improvement enhancement for later.

### Synthesis Layer Architecture — Summary

```
┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│ Letter-Mail State       │  │ Today's Packages        │  │ Physical Event Stream   │
│ Machine (Phase 1)       │  │ (Phase 1)               │  │ (Area: Driveway)        │
│                         │  │                         │  │                         │
│ NOT_EXPECTING           │  │ expected_today[]        │  │ mailbox/opened events   │
│ EXPECTING               │  │ delivered_today[]       │  │ mailbox/state telemetry │
│ DELIVERED               │  │ (both cleared midnight) │  │                         │
│ EXCEPTION               │  │                         │  │                         │
└──────────┬──────────────┘  └──────────┬──────────────┘  └──────────┬──────────────┘
           │                            │                            │
           └────────────────────────────┼────────────────────────────┘
                                        │
                                        ▼
                        ┌─────────────────────────────────┐
                        │ Synthesis Layer                 │
                        │ (Utility: Deliveries, Phase 3)  │
                        │                                 │
                        │ Mailbox-contents queue          │
                        │ Door-open classification        │
                        │ Confidence reporting            │
                        │ Manual override handling        │
                        └──────────────┬──────────────────┘
                                       │
                                       ▼
                        ┌─────────────────────────────────┐
                        │ Consumer-facing topics          │
                        │                                 │
                        │ state/deliveries/pending        │
                        │ event/deliveries/retrieved      │
                        │ HA Discovery sensors            │
                        └─────────────────────────────────┘
```

The letter-mail state machine, today's packages, and physical event stream are **independent inputs** to the synthesis layer. Each remains internally consistent and doesn't know about the others. The synthesis layer is where they compose to produce user-facing semantics.

Additional inputs (future vehicle detection, other-carrier deliveries in Phase 2) slot into this architecture without structural change. The synthesis layer's internal logic gets richer as inputs accumulate; its external contract stays stable.

---

## Open Questions

**Phase 1 — USPS Informed Delivery (calibrate after PIN verification and real emails arrive):**

- [ ] Confirm exact sender address format for each email type — may differ between digest, status updates, and delivery confirmations. Affects Gmail filter configuration. *(2026-04-30: Daily Digest and Mail Delivered both confirmed at `USPSInformeddelivery@email.informeddelivery.usps.com`; OFD and Item Delivered still TBD.)*
- [ ] Validate digest parser against real structure: letter-mail piece count extraction, correct handling of package-only digests (must not promote letter-mail state). *(2026-04-30: HTML count-line parse anchor confirmed; package-only Sunday digest case verified — see § Confirmed parse signals.)*
- [ ] Confirm OFD Status Update email subject/body pattern for reliable classification as OFD vs. other status updates
- [ ] Confirm `letter_mail_exception_time: "20:00"` against longer-term observation
- [ ] Map canonical `delivered_to` values from real Item Delivered email bodies (expected: `mailbox`, `front_door`, `other`; refine on observation)
- [ ] Confirm Item Delivered and OFD emails reliably contain parseable tracking numbers; unparseable tracking is a hard parse_error path
- [ ] **OFD reliability question:** validate that USPS sends OFD emails reliably for delivered packages. If OFD sometimes doesn't fire (same-day local transfers, missed events), packages appear only as "surprise deliveries" — not broken, but worth understanding the frequency.
- [ ] Finalize configuration file location (dedicated `deliveries.json` vs. broader grouping)

**Phase 3 — Physical sensor fusion (calibrate after LoRa installation and empirical observation):**

- [ ] Calibrate deferred-classification window behavior for both letter-mail `EXPECTING` and package-OFD cases
- [ ] Define behavior when a deferred door-open resolves against a package that delivered to `front_door`/`other` (not mailbox) — should classify as retrieval
- [ ] Define behavior for door-open in `NOT_EXPECTING` letter-mail with empty `expected_today` — almost certainly retrieval, but worth validating
- [ ] **Empirical frequency of bulk-mail-only delivery days.** How often does the known blind spot (door-open with no email signal, silently classified as retrieval) actually occur? Helps calibrate whether Phase 3b vehicle detection is a nice-to-have or genuinely needed.
- [ ] **Empirical frequency of outbound-mail-caused false clears.** How often does outbound mail actually corrupt queue state (outbound deposit + non-empty pending queue + no consolidating retrieval)? If frequent enough to matter, consider mailbox-flag sensor as a future hardware enhancement. If rare, accept as a known limitation.
- [x] Decide whether entering `EXCEPTION` state should fire an operator notification *(yes — fires `usps.mail_exception` notification at severity `medium`. See § Consumer Surface.)*
- [ ] Calibrate confidence thresholds empirically
- [ ] Decide HA surface for manual override — voice intent, dashboard button, both?

**Phase 2 — Other carriers (UPS/FedEx/Amazon):**

- Captured in `AUTOMATION_BACKLOG.md` — separate design session when scoped.

---

*Last Updated: 2026-05-01*
