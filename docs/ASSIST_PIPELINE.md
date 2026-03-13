# Assist Pipeline — Planning &amp; Architecture

## Status

Working notes — not yet promoted to project-level documentation. Decisions here are directional; implementation follows baseline infrastructure build.

---

## Overview

HA Assist pipeline provides both chat and voice interfaces for home control and queries. Unlike most of the Highland architecture, Assist lives entirely within Home Assistant — it is explicitly HA-dependent. If HA is down, Assist is down. This is acceptable because:

- Critical automations run via Node-RED/MQTT independent of HA
- Assist is a convenience/accessibility layer on top of a resilient control plane
- Making HA itself stable (dedicated HAOS box) is the correct mitigation, not trying to externalize Assist

---

## Pipeline Components

Assist is modular — four loosely-coupled stages, each independently swappable:

| Stage | Purpose | Selected Option |
|-------|---------|-----------------|
| **Wake Word** | Trigger hands-free listening | openWakeWord (local, HA add-on) |
| **STT** | Audio → text | Whisper (local, HA add-on) |
| **Conversation Agent** | Interpret intent, formulate response | Two-tier (see below) |
| **TTS** | Text → audio | Google Cloud TTS (Neural2 tier) |

---

## Conversation Agent: Two-Tier Strategy

Two agents, routed based on use case. HA supports multiple conversation agents simultaneously with per-pipeline routing.

### Tier 1: Local Ollama (Home-Aware)

**Hosted on:** Edge AI SFF (co-located with Coral TPU vision inference)

**Handles:**
- Device control ("turn off the living room lights")
- Home state queries ("is the back door locked?")
- Video analysis calendar queries ("was my package delivered today?")
- Anything requiring access to home context

**Rationale for Edge AI placement:** HAOS is a locked-down OS — Ollama can only run there via addon, inside a constrained supervisor environment. The Edge AI box runs Ubuntu with full Docker access, making it the correct home for arbitrary inference workloads. Ollama (LLM inference) and Coral TPU (vision inference) are distinct workloads that coexist without meaningful resource contention. Network latency to HAOS over LAN is negligible. 32GB RAM on the Edge AI box provides headroom for capable models (13B-32B range) alongside vision inference overhead.

**Note:** For simple, unambiguous commands, HA's built-in intent engine handles the request before it reaches Ollama (faster, no LLM overhead).

### Tier 2: Cloud LLM (General Purpose)

**Integration:** Extended OpenAI Conversation (HACS) — supports Claude and OpenAI-compatible APIs

**Handles:**
- General knowledge queries
- Tasks that benefit from a capable model but don't need home context

---

## TTS: Google Cloud TTS

**Selected voice tier:** Neural2 (sweet spot of quality vs. cost; Studio tier available if Neural2 proves inadequate)

**Key rationale: Unified voice across the system.** Google Cloud TTS is available both in HA (first-party integration) and to Node-RED via standard HTTP API. A single voice ID and API key means HA Assist responses and Node-RED notification TTS utterances sound identical. Pick the voice once; use it everywhere.

**Credentials:** Store API key in `secrets.json` as `google_tts_api_key` (or consolidate under a `google` block if Calendar API key also present).

---

## Satellite Hardware

Satellites handle mic input and speaker output, offloading STT/TTS/inference to the HAOS server via the Wyoming protocol.

### Available Hardware

| Device | Quantity | Protocol | Notes |
|--------|----------|----------|-------|
| M5Stack ATOM Echo | 2 | Wyoming / ESPHome | Audio-only; good for pipeline validation |
| Echo Show (Gen 1, NOS) | 1 | Android (post-LineageOS) | Primary experiment unit, unopened |
| Echo Show 5 | 1 | Android (post-LineageOS) | Secondary experiment unit ($35) |

### Echo Show Experiment

**Goal:** Flash LineageOS → install ViewAssist → evaluate as wall-mounted voice+visual satellite

**Why this matters:** Touchscreen + voice is a significantly better UX than audio-only, particularly for non-ambulatory users. Visual confirmation of responses, ability to display camera feeds, dashboard cards, etc.

**Timing:** Post-baseline-infrastructure. Hardware on hand, not yet opened.

**Risks:**
- LineageOS on Echo Show hardware is community-supported, not mainstream — expect some friction, particularly around mic array drivers
- Proceed with eyes open; not a blocker for anything else

**Fallback:** If Echo Show experiment fails or stalls, Android tablets (e.g., Fire HD with Play Store sideloaded) running ViewAssist provide similar visual+voice UX with less effort. Mic quality will be worse but likely adequate for wall-mounted close-range use. Same software story, less interesting hardware.

### Target Deployment (If Experiment Succeeds)

| Location | Count | Notes |
|----------|-------|-------|
| Living Room | 1 | Open concept — may need 2 depending on coverage |
| Kitchen | 1 | |
| Master Bedroom | 2 | One per nightstand |
| Guest Room One | 1 | |
| Guest Room Two | 1 | |
| Office | 1 | |
| **Total** | **7** | |

Transitional spaces (stairway, hallways) excluded — not worth deploying in spaces where no one stops to have a conversation.

**Sourcing note:** Gen 1 Echo Shows are no longer manufactured. eBay and Facebook Marketplace are primary sourcing channels. When experiment is validated and ready to scale, move quickly rather than leisurely — units in unbootloaderable states or priced by people who know what they have are an increasing share of available inventory.

### ATOM Echoes

Use as audio-only satellites during Echo Show experiment. Gets pipeline end-to-end validated before committing to visual hardware.

---

## Proactive Conversations &amp; Satellite Targeting

### House-Initiated Conversations

Assist supports house-initiated (proactive) conversations — a triggering event causes HA (or NR via a service call) to push a spoken prompt to a satellite, which plays the prompt and then *opens its mic and listens* for a response. The response flows back through the normal STT → conversation agent → action pipeline.

This is distinct from user-initiated flow. The house starts the conversation. For a non-ambulatory user this is significant — the system surfaces things proactively rather than requiring the user to remember to ask.

**Continuing conversations** is a related feature: after an initial wake word trigger and exchange, the satellite remains in listening mode for a follow-up without requiring another wake word. Enables natural multi-turn exchanges ("turn on the living room lights" → "to what brightness?" → "fifty percent") as a single conversation rather than three separate triggers.

*Implementation note: Both features were functional but still maturing as of knowledge cutoff. Check current HA release notes when implementing — this area was moving fast.*

### Satellite Targeting

When the house initiates a conversation, it must choose which satellite(s) to use. Options:

| Strategy | Use Case |
|----------|----------|
| **Area-based** | "Play in the room where motion was last detected" |
| **Explicit** | "Play on the kitchen satellite" (known location) |
| **Broadcast** | "Play on all satellites" (emergency alerts) |
| **User-following** | "Play on the satellite nearest to [user]" (presence-based) |

User-following requires presence detection infrastructure (FP300 sensors, phone tracking, etc.) — deferred until that's in place.

---

## Node-RED Integration Points

Assist is HA-native, but Node-RED has several integration surfaces:

### TTS via Service Call

NR can trigger TTS on any satellite via HA service call:

```yaml
service: tts.speak
data:
  media_player_entity_id: media_player.kitchen_satellite
  message: "The garage door has been open for 30 minutes."
```

Uses the same Google Cloud TTS voice as Assist responses.

### Proactive Conversation Trigger

NR can initiate a proactive conversation via HA service call:

```yaml
service: conversation.process
data:
  agent_id: conversation.ollama
  text: "The garage door has been open for 30 minutes. Should I close it?"
  device_id: &lt;satellite_device_id&gt;
```

*Note: Exact service and parameters may have evolved — verify against current HA docs during implementation.*

### Notification vs. Conversation Decision

NR flows decide: is this a one-way notification (TTS only) or a conversation (expects response)?

| Scenario | Approach |
|----------|----------|
| "Package delivered" | TTS notification |
| "Garage door open 30 min — close it?" | Proactive conversation |
| "Severe weather alert" | TTS notification (broadcast) |
| "Visitor at front door — unlock?" | Proactive conversation |

Handoff points: HA service calls (NR → Assist) and HA events (Assist outcome → NR subscribes and reacts).

---

## MQTT Topology Note

The NR → Assist integration points described above (TTS service calls, proactive conversation triggers) are HA service calls, not MQTT events. They do not currently have a `highland/` bus presence.

MQTT_TOPICS.md designates **HA Assist / Voice** as a domain pending definition. If future designs require MQTT surface for Assist interactions — e.g., NR publishing a trigger that initiates a proactive conversation, or Assist outcomes being published for downstream NR flows — those topics must follow the established `highland/` namespace conventions from EVENT_ARCHITECTURE.md and MQTT_TOPICS.md.

---

## Implementation Sequence

All of this follows baseline infrastructure (PNC → HAOS → Node-RED) being stable.

1. **Google Cloud TTS** — Configure integration, select voice, validate output quality. Foundational dependency; low risk, do first.
2. **Whisper STT** — Install HA add-on. Enables text *or* voice input via Companion app; no satellite hardware required yet.
3. **Ollama + Conversation Agent** — Stand up on Edge AI box, wire to Assist pipeline via HA's Ollama integration, validate home control and calendar queries via dashboard chat. *Note: Edge AI box must be built and online before this step.*
4. **ATOM Echo satellites** — Get wake word + full audio pipeline end-to-end. Validates hardware path before Echo Show effort.
5. **Echo Show experiment** — LineageOS + ViewAssist. Parallel workstream once baseline infra is stable.
6. **Wall-mounted dashboards** — Separate planning session required (see below).

---

## Deferred: Wall-Mounted Dashboard / Room Displays

Significant topic, not yet planned. Intersection of:
- Satellite hardware selection (Echo Show, tablet, dedicated display)
- Per-room dashboard design (what cards, what context per room)
- Accessibility requirements (primary user is non-ambulatory)
- ViewAssist integration

Deserves a dedicated planning session. Capture ideas here as they emerge; full design deferred.

---

## Companion App as Assist Interface

The HA Companion app is not a satellite in the Wyoming protocol sense, but it is a fully capable Assist interface — both text and voice — connecting directly to HA via WebSocket with Assist built in natively.

**Key distinction from room satellites:** The Companion app carries native user identity. Requests made through it are authenticated as a specific HA user account. This solves the "who asked?" problem that room satellites can only approximate via topology.

**Relevance to advanced interactions:** For use cases like "notify me when a specific person arrives," the Companion app is the cleanest path — NR can store the requesting user alongside the expectation and target the response notification back to that user's mobile device with no ambiguity.

**Additional notes:**
- Works over Nabu Casa for remote access (local satellites do not)
- A tablet running the Companion app is a legitimate Assist interface independent of satellite hardware — relevant for accessibility use cases

*To investigate during implementation: exact behavior of user identity in conversation context, how it surfaces to scripts/NR, and whether it can inform satellite targeting for response delivery.*

---

## Deferred: Persistent Memory Architecture

**Status: Backburner — blocked on two fronts. Revisit when blockers resolve.**

### Blockers

**1. HA pipeline events are fully internal**

`assist_pipeline_event` does not propagate to the external WebSocket API, the HA event bus as seen by NR, or Developer Tools event listener. Verified on HA 2026.3.1. The only externally visible artifact of an Assist interaction is a single `state_changed` event — insufficient for capture purposes.

Clean per-interaction capture would require NR to own the conversation orchestration entirely, talking to Ollama directly rather than routing through HA's Assist pipeline. HA would be demoted to audio hardware layer only (wake word, STT, TTS). This is architecturally viable but adds significant complexity.

**2. No viable local LLM inference hardware**

The Edge AI box (Dell OptiPlex 7050 SFF) has one PCIe slot occupied by the Coral TPU. GPU acceleration for Ollama would require displacing the Coral, which is a non-starter. CPU-only inference on the i7-7700 produces marginal latency for voice use (~5-8 t/s on a 13B model) — technically functional but a poor experience. A dedicated GPU box resolves this but adds hardware cost and complexity. GPU hardware prices need to come down before this is worth pursuing.

### Design Direction (When Revisited)

**Core concept:** Marvin maintains durable long-term context via externalized markdown files, analogous to the architectural guidance files used in the hand-crafted SDLC workflow.

**Daily cycle pattern:**
- Context accumulates throughout the day in a working memory store
- Nightly distillation (natural trigger: `highland/event/scheduler/midnight`) extracts and persists the items worth carrying forward
- Each day starts fresh but informed — stale noise purged, durable knowledge retained

**Memory categories (preliminary):**
- User preferences and household context (known visitors, vehicle descriptions, recurring schedules)
- Household knowledge — infrequently-changing facts Marvin should just know
- Episodic carry-forward — things explicitly taught or important enough to survive the nightly purge
- Learned behavioral patterns

**Capture mechanism (when NR owns orchestration):**
- NR intercepts input before sending to Ollama, and captures Ollama's response before returning to HA
- Full exchange available at NR without depending on HA event exposure
- Evaluation pass determines tier: ephemeral (today only), temporal (carries forward with expiry), permanent (durable household knowledge)

**Write path:** Explicit teaching ("Marvin, remember that...") triggers immediate write. Implicit capture goes through nightly distillation pass.

**Access pattern (to be designed):** Tool-calling preferred — Marvin calls `read_memory` when warranted rather than injecting full context on every turn.

### What Was Tried (Live System, HA 2026.3.1)

Approximately two hours of live experimentation confirmed the following dead ends:

- `assist_pipeline_event` does not appear in Developer Tools event listener — fully internal
- NR `events: all` node catches nothing pipeline-related regardless of filter configuration
- HA automation triggered on `assist_pipeline_event` never fires
- `mqtt.publish` not exposed as a callable action within conversation agent tool context
- Intents require explicit phrase matching — cannot be wildcarded to cover all interactions
- No native writable entity surface with sufficient payload capacity reachable from within the pipeline
- `input_text` helper capped at 255 characters — insufficient
- Entity attributes are computed/owned by their integration — not arbitrarily writable via service call

**Conclusion:** HA has deliberately sandboxed the conversation agent. It can read home state and control devices through the sanctioned tool interface, and that's the extent of it. The read side (injecting external context) is likely equally constrained. This is not a solvable problem within the current HA architecture without owning the conversation orchestration entirely outside of HA.

*Revisit triggers: HA exposes pipeline events externally, or dedicated LLM inference hardware becomes viable enough to justify a standalone Ollama box with NR owning full conversation orchestration.*

**See PERSISTENT_MEMORY.md** — full architecture for the persistent memory system, proxy design, classification layer, and context lifecycle. Captured as a standalone document given the scope.

---

## Open Questions

- [ ] Ollama model selection — which model(s) for home control vs. general use? Depends on HAOS SFF memory headroom under real load.
- [ ] Google TTS voice selection — Neural2 vs Studio tier; specific voice ID
- [ ] Wake word selection (openWakeWord library vs. custom trained)
- [ ] Per-room pipeline routing — e.g., bedroom satellite uses lower-volume TTS response
- [ ] ATOM Echo placement for initial testing
- [ ] Echo Show LineageOS build process and any known blockers for Gen 1
- [ ] Speaker recognition — monitor [EuleMitKeule/speaker-recognition](https://github.com/EuleMitKeule/speaker-recognition): HA addon + custom integration using Resemblyzer neural voice embeddings. Trains on audio samples per user, returns speaker name + confidence score. Plugs into STT and conversation agent pipeline natively. Early project (low star count) but implementation looks legitimate. Python &lt;3.10 requirement for server capabilities worth noting. If this matures, closes the "who asked?" gap for shared room satellites and enables per-user response targeting without relying on Companion app identity or satellite topology heuristics.

---

*Last Updated: 2026-03-12 — Session working notes, not yet promoted to project docs*
