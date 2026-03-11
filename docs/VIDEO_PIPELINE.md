# Video Analysis Pipeline — Design & Architecture

## Overview

Node-RED-owned video analysis pipeline providing motion-triggered capture and a three-stage analysis ladder: local triage (mechanical gate), remote triage (intelligent scene assessment), and remote deep analysis (full clip analysis).

**Target state:** Operates without Home Assistant as a dependency in the motion detection and capture path (direct reolink_aio integration). **Initial implementation** uses HA's Reolink integration as a known-working scaffold while NVR API capabilities are validated. See [Phased Implementation Approach](#phased-implementation-approach) below.

---

## Design Goals

- **HA independence (target state)** — Motion detection and capture must not require HA to be running. Initial implementation uses HA as a scaffold; migrated once NVR API is validated.
- **Three-stage escalation ladder** — Local triage (free) gates remote triage (cheap); remote triage gates deep analysis (expensive). Each stage only fires if the previous passes.
- **Local triage is mechanical, not intelligent** — Edge AI eliminates obvious non-events (noise reduction only). Threat assessment enters at remote triage, not before.
- **Progressive notification** — Alert on motion, update as analysis completes, dismiss if nothing of interest
- **Property-aware filtering** — Zone-based detection; cameras observing adjacent properties require spatial filtering, not just object detection
- **Cost-controlled** — Remote triage operates on a single annotated still; deep analysis (clip) only reached after remote triage escalates

---

## Analysis Stages

Three distinct stages with increasing capability and cost. Each stage gates the next.

| Stage | Technology | Input | Cost | Job |
|-------|-----------|-------|------|-----|
| **Local Triage** | CPAI + Coral TPU | Still keyframe | Free | Eliminate non-events. Is there anything worth looking at? |
| **Remote Triage** | Gemini (still) | Annotated still | Cheap | Intelligent assessment. Is this worth full analysis? |
| **Remote Deep Analysis** | Gemini (clip) | Video clip | Expensive | Full scene description, narrative, threat assessment |

**Key principle:** Local triage is purely mechanical — it classifies objects and provides bounding boxes. It has no concept of threat, intent, or context. Threat assessment enters the pipeline at remote triage, not before.

---

## Event Flow

### Target Architecture (NVR)

```
NVR detects motion → starts recording (NVR-managed)
        │
        ▼
Sidecar receives push event
highland/event/camera/{camera_id}/motion published to MQTT
        │
        ▼
Pull still from active NVR stream
        │
        ▼
─────────────────────────────────────
STAGE 1: LOCAL TRIAGE
─────────────────────────────────────
CPAI object detection → bounding boxes
Zone filtering (Node-RED, bottom-center anchor)
        │
        ├── No in-scope detections → done
        │   NVR recording continues/expires naturally
        │   Early notification dismissed
        │
        └── In-scope detections found
                │
                ▼
        Build annotated still:
        • Draw bounding boxes for in-scope detections only
        • Suppressed detections (adjacent property) not marked
                │
                ▼
─────────────────────────────────────
STAGE 2: REMOTE TRIAGE
─────────────────────────────────────
Gemini receives: annotated still + triage context
Prompt: what was detected, which boxes are in scope,
        property boundary context, camera geometry
        │
        ├── Nothing of interest → done
        │   Notification dismissed
        │
        └── Worth escalating
                │
                ▼
        Pull clip from NVR-managed recording
        Early notification sent (category, triage summary)
                │
                ▼
─────────────────────────────────────
STAGE 3: REMOTE DEEP ANALYSIS
─────────────────────────────────────
Gemini receives: video clip (+ annotated still for reference)
Full scene description, behavior, threat assessment, narrative
        │
        ▼
Notification updated with full description
(or dismissed if nothing of interest after full analysis)
```

**Key dependency:** This architecture requires that the NVR stream is accessible for still pull while recording is actively in progress. To be validated when NVR is available.

### Current Implementation (Home Hub) — Reference

The Home Hub does not manage recording automatically on motion, requiring Node-RED to manually orchestrate both capture paths in parallel to avoid race conditions:

```
Motion detected
        │
        ├──────────────────────────────────────────┐
        ▼                                          ▼
  Still keyframe captured                   Manual clip recording triggered
        │                                          │
        ▼                                          │
  Edge AI triage                                   │
        │                                          │
        ├── Triage negative ──────────────────────►│ Recording discarded
        │                                          │
        └── Triage positive ──────────────────────►│ Clip pulled
                                                   ▼
                                          Gemini deep analysis
```

The parallel start on clip recording exists specifically because triggered recording always misses the leading edge of the event — without it, a fast-moving event that's positive in triage may have nothing left to analyze by the time the clip starts.

---

## Phased Implementation Approach

### Phase 1: HA-Dependent Scaffold (Initial Build)

The pipeline's motion trigger and still/clip capture path will initially use HA's Reolink integration rather than direct reolink_aio communication. This is a deliberate pragmatic choice:

- **Known working** — HA's Reolink integration is validated in the live system today
- **Unblocks implementation** — the full CPAI triage → Gemini analysis chain can be built and tuned without waiting on NVR API validation
- **Pipeline logic is unaffected** — Node-RED still owns the state machine, cooldown, kill switch, zone filtering, and all analysis stages; only the input source changes
- **Battery-powered camera reality** — wireless cameras only provide intermittent streams on motion wake; this is a temporary hardware constraint, not a design flaw

In Phase 1, Node-RED receives motion events via HA's event bus (rather than direct MQTT from reolink_aio) and uses HA service calls for still/clip capture where needed.

**This is a temporary scaffold, not the target state.** Any Node-RED flow sections that use HA as the capture path should be clearly marked:

```javascript
// TODO: Replace with direct reolink_aio path once NVR API is validated
// See VIDEO_PIPELINE.md — Phased Implementation Approach
```

### Phase 2: Direct reolink_aio Integration (Target State)

Once the NVR is physically available and the following are validated:
- Push event mechanism (TCP Baichuan vs. ONVIF SWN)
- Per-channel still capture via API
- Stream accessibility during active NVR recording
- Clip extraction capabilities (on-demand vs. timestamp-based)

...the HA-dependent input path is replaced with the reolink_aio sidecar. The rest of the pipeline is unchanged. This is a contained swap at the input end only.

The transition point is a decision, not a deadline — run Phase 1 until confidence is high and NVR API behavior is fully understood.

---

## Components

### Reolink Sidecar Service

A thin Python service using `reolink_aio` (starkillerOG) — the same library that powers the official HA Reolink integration.

**Responsibilities:**
- Maintain authenticated connection to NVR/cameras
- Subscribe to push events (TCP Baichuan or ONVIF SWN — TBD based on NVR capabilities)
- Publish motion events to MQTT
- Handle still keyframe capture on command
- Handle clip capture on command
- Manage connection lifecycle (subscribe, renew, reconnect)

**Deployment:** Docker container on Workflow, alongside Node-RED.

**MQTT interface:**

| Direction | Topic | Purpose |
|-----------|-------|---------|
| Publish | `highland/event/camera/{camera_id}/motion` | Motion detected |
| Publish | `highland/status/camera_sidecar/health` | Sidecar health |
| Subscribe | `highland/command/camera/{camera_id}/capture_still` | Trigger still capture |
| Subscribe | `highland/command/camera/{camera_id}/capture_clip` | Trigger clip capture |
| Publish | `highland/event/camera/{camera_id}/still_ready` | Still captured, path in payload |
| Publish | `highland/event/camera/{camera_id}/clip_ready` | Clip captured, path in payload |

**Note:** The sidecar is just another MQTT publisher/subscriber — architecturally equivalent to Z2M or Z-Wave JS UI. Node-RED doesn't care how the events were generated.

---

### Stage 1: Local Triage (CodeProject.AI)

Local inference on Edge AI box (SFF + Coral TPU). Purely mechanical — classifies objects and returns bounding boxes. No threat assessment, no behavioral inference.

**Responsibilities:**
- Object detection with bounding boxes on still keyframes
- Return detection results to Node-RED for zone filtering

**Node-RED handles:**
- Zone polygon definition per camera (config file)
- Bounding box zone membership using **bottom-center anchor point** (not centroid, not any-overlap — ground contact point is the ground truth for location given camera perspective)
- Suppression/escalation decisions based on detection + zone
- Building the annotated still for remote triage

**CPAI tool selection rationale:**
CPAI is the current baseline for local triage. The original maintainers departed in late 2024 and active development has slowed significantly. However, for a local inference service running in Docker with a stable, bounded use case (object detection on stills), an abandoned-but-working tool is acceptable — pin a known-good version, validate Coral TPU support before committing, and the container runs indefinitely without requiring ongoing development. The primary risk is Coral TPU compatibility across versions, which has historically been the most fragile aspect of CPAI. If CPAI becomes untenable in the future, the REST call pattern from Node-RED makes swapping the inference backend relatively low-friction.

**CPAI modules:**
- `ObjectDetectionCoral` — Coral TPU object detection (primary triage module)
- `ipcam-general` (MikeLud custom model) — person + vehicle only, two classes; highest accuracy for those targets because the model isn't diluted by other classes
- `ipcam-animal` (MikeLud custom model) — species-level animal detection: bird, cat, dog, horse, sheep, cow, bear, deer, rabbit, raccoon, fox, skunk, squirrel, pig
- `ipcam-delivery` (MikeLud custom model) — delivery carrier identification: Amazon, DHL, FedEx, Home Depot, IKEA, Lowes, Target, UPS, USPS, Walmart, U-Haul, garbage truck
- Note: Coral + animal detection has known accuracy limitations, particularly for small animals and distant subjects. Confidence thresholds for animals may need to be more permissive, accepting more false positives through to remote triage.

**Local triage as enrichment producer:**
Local triage is not solely a binary gate. It produces structured metadata that enriches the detection object and shapes downstream behavior. A delivery carrier identification at triage changes the remote triage prompt, pre-seeds event categorization, and may alter notification framing — before Gemini has seen anything. As the pipeline matures, additional CPAI modules (face recognition, LPR) add further enrichment layers. Node-RED accumulates all local triage results into a single detection object; each CPAI call extends it rather than replacing it.

**CPAI as a multi-call pipeline:**
Multiple CPAI modules may run sequentially against the same event. Node-RED owns the accumulated detection object and makes each call independently — CPAI modules are stateless and have no knowledge of each other. For enrichment calls (face recognition, LPR), Node-RED crops the image to the relevant bounding box from the prior detection call before submitting — a face recognition model operating on a face-sized crop is materially more accurate than one operating on a full scene image. Bounding boxes from earlier calls are inputs to later calls, not just metadata.

**Closed-set classifier behavior — unknown animals:**
CPAI's animal models are closed-set classifiers — they can only output labels they were trained on. An animal outside the training set is not returned as "unknown"; the model is forced to pick the closest match from known classes. Practical consequences:

| Animal | Likely CPAI output | Notes |
|--------|-------------------|-------|
| Coyote | `dog` | Similar body shape and gait |
| Fisher cat | `cat` or `dog` | Mustelids are visually ambiguous; confidence likely low |
| Groundhog | `cat` or no detection | May fall below confidence threshold |
| Large snake | No detection | No snake class; motion event fires but local triage returns negative |
| Chipmunk | No detection | Likely below pixel threshold for reliable detection |

This is a known limitation of local triage, not a failure. The pipeline is still correct — a coyote classified as `dog` passes the animal gate and escalates to Gemini, which will identify it correctly. The only genuine blind spot is an animal that triggers motion but produces no detections at all (snake example) — those events are silently dropped at local triage.

**NVR history as calibration mechanism:**
The NVR maintains a ground truth record of all motion events regardless of pipeline behavior. Comparing NVR event history against pipeline escalations will surface blind spots empirically over time — events where the NVR recorded something interesting and the pipeline produced nothing. This is the intended calibration path; the real-world blind spot profile cannot be known in advance.

**Custom model training as escape hatch:**
If closed-set limitations become material, custom model training is a viable path. Key considerations:
- Training data: own camera captures (domain-accurate but sparse for rare species) augmented with external wildlife images (iNaturalist, etc.). Own captures provide domain relevance; external images provide volume. Data augmentation (brightness, contrast, blur, noise, rotation) synthetically expands sparse real-world samples.
- The domain gap between wildlife photography and IR security camera footage is real — training sets should always include some real camera captures per class to anchor the model to deployment conditions.
- YOLOv5/v8 fine-tuning from a pre-trained base is well-documented; adding classes rather than training from scratch.
- Coral constraint: custom models require TensorFlow Lite conversion + Edge TPU compilation to run on Coral. If Coral compatibility is problematic for custom models, CPU inference on the i7-7700 is the fallback — slower but still sub-second for single stills.
- The NVR event history is the training data pipeline — footage of animals that CPAI missed is exactly the data needed to train for those gaps.

**Annotated still construction:**
After zone filtering, Node-RED draws bounding boxes only for in-scope detections. Suppressed detections (adjacent property, ignored zones) are not marked. This annotated still is the input to remote triage — it directs Gemini's attention to the subjects of interest without masking (which is unreliable due to camera perspective and elevation).

**Emergency vehicle exception:**
- Vehicle detected in a normally-suppressed zone (adjacent property)
- CPAI classifies vehicle as `emergency_vehicle`
- Override suppression → escalate

*Reliability of emergency vehicle classification to be validated before treating as a committed feature.*

---

### Stage 2: Remote Triage (Gemini — still)

Intelligent scene assessment on the annotated still. First point in the pipeline where threat assessment, behavioral inference, and contextual reasoning occur. Significantly cheaper than deep analysis (single image vs. video clip).

**Input:**
- Annotated still (in-scope bounding boxes drawn, suppressed detections not marked)
- Prompt includes: what local triage detected, count and category of in-scope subjects, camera position/geometry context, property boundary description

**Responsibilities:**
- Determine whether the event warrants deep analysis
- Initial threat/interest assessment
- Provide triage summary for early notification update

**Prompt context provided:**
- Which categories were detected and how many (e.g., "local triage identified one vehicle and two persons within the driveway zone")
- Camera geometry (elevation, angle, mounting position) so Gemini can reason about perspective
- Property boundary description (approximate location in frame)
- Explanation that marked boxes are in-scope subjects; other visible subjects are outside the property boundary

**Gate decision:** Remote triage result determines whether the pipeline escalates to deep analysis or terminates. Events that pass local triage but are assessed as low-interest by remote triage (e.g., a known-harmless recurring pattern) are dismissed here without incurring clip analysis cost.

---

### Stage 3: Remote Deep Analysis (Gemini — clip)

Full scene analysis on the video clip. Only reached after remote triage escalates.

**Input:**
- Video clip (NVR-managed recording)
- Annotated still (for reference/temporal anchor)
- Prompt with full scene context

**Responsibilities:**
- Full scene description and narrative
- Behavior and activity assessment
- Threat level assessment
- Classification for event storage (delivery, loitering, wildlife, etc.)
- Confidence scoring
- Content for final notification

---

## Zone Filtering

Cameras observing adjacent properties require spatial awareness that NVR-native AI cannot provide.

**Per-camera zone configuration (stored in config):**

```json
{
  "camera_driveway": {
    "zones": {
      "our_property": {
        "polygon": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],
        "default": "analyze"
      },
      "neighbor_north": {
        "polygon": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],
        "default": "suppress",
        "exceptions": ["emergency_vehicle"]
      }
    }
  }
}
```

**Logic:** Node-RED receives CPAI bounding boxes, tests zone membership using the **bottom-center point** of each bounding box (ground contact point, not centroid). This correctly handles the perspective distortion introduced by elevated/angled camera mounts — a person standing at the rear of the driveway has feet on-property even if their head appears to float over the neighbor's yard in the 2D frame. Masking is not used for the same reason — pixel-level masking is unreliable when objects straddle zone boundaries due to camera geometry.

**Abstract contextual exceptions** (smoke, fire, altercation on adjacent property) are **deferred** — can be layered in later once baseline pipeline is stable and proven.

---

## Classification Targets

Triage and analysis operate against three classification targets. Everything else is ignored.

| Target | Examples | Notes |
|--------|----------|-------|
| `animal` | deer, bear, raccoon, coyote | Species matters for threat escalation |
| `vehicle` | car, truck, emergency vehicle | Type matters for zone exception logic |
| `person` | any human | Never auto-cooldown; see below |

Count and composition per zone are tracked alongside classification — not just "animal detected" but "2 animals detected in rear yard zone." Changes in either count or composition are meaningful signals.

---

## Cooldown / Governor

Prevents notification fatigue and unnecessary Gemini API spend during sustained activity (e.g., animals lingering in the yard for hours during spring/summer).

### Cooldown Eligibility

| Target | Auto-Cooldown | Rationale |
|--------|---------------|-----------|
| `animal` | Yes | Lingering/repeated animal presence is low-stakes and high-frequency |
| `vehicle` | Yes | Parked vehicle in driveway doesn't need repeated analysis |
| `person` | **No** | Too high-stakes to suppress automatically; use kill switch for expected presence |

### Cooldown State Model

Cooldown state is per-camera, per-zone, and stores enough context to evaluate whether a new triage result represents a meaningful change from what triggered the cooldown. Stored in Node-RED flow context (disk-backed).

```javascript
// Example cooldown state structure
{
  "camera_driveway": {
    "rear_yard": {
      "active": true,
      "triggered_by": {
        "animal": 3,      // count when cooldown activated
        "vehicle": 0
      },
      "composition": ["animal"],
      "activated_at": "2026-03-07T14:30:00Z",
      "expires_at": "2026-03-07T14:45:00Z"
    }
  }
}
```

### Cooldown Override Conditions

Any of the following breaks cooldown and forces analysis:

| Condition | Example |
|-----------|---------|
| **New classification target detected** | Animal cooldown active, person now detected |
| **Species/type escalation** | Deer in cooldown, bear now detected |
| **Composition change** | Animals only → animals + vehicle now present |
| **Count change** (threshold TBD) | 2 animals in cooldown, now 5 animals |
| **Zone change** | Animal cooldown for rear yard, animal now detected at entry zone |
| **Threat-relevant object** | Weapon classification positive on person |
| **Proximity escalation** | Person in yard → person at door/window zone |

### Cooldown State Management

**Node-RED owns the state.** HA is view + control surface only via MQTT discovery.

**Topic structure (per camera, per zone):**
```
homeassistant/switch/camera_{id}_cooldown_{zone}/config   ← discovery (retained)
highland/state/camera/{id}/cooldown/{zone}                 ← Node-RED → HA (retained, ON/OFF)
highland/command/camera/{id}/cooldown/{zone}               ← HA → Node-RED (manual clear)
```

**Flow:**
- Cooldown activates → Node-RED updates flow context + publishes `ON` to state topic
- Override condition detected → cooldown breaks, Node-RED clears context + publishes `OFF`
- Timeout expires → Node-RED clears context + publishes `OFF`
- Manual clear from HA dashboard → Node-RED receives command, clears context, confirms state

*Cooldown timeout threshold TBD during calibration. Especially relevant for spring/summer animal activity.*

---

## Kill Switch (Manual Override)

For scenarios where prolonged expected activity would generate unavoidable noise — gatherings, yard work, expected deliveries — a per-camera kill switch provides a hard circuit-breaker that sits upstream of all processing.

**Kill switch = hard off.** When active: no triage runs, no analysis runs, no notifications sent. Everything is suppressed regardless of what is detected.

**Cooldown = soft gate.** Algorithm-driven, mid-pipeline, temporary. Kill switch is not a substitute and cooldown is not a substitute for kill switch.

### When to Use Each

| Scenario | Mechanism |
|----------|-----------|
| Deer lingering in yard for an hour | Cooldown (automatic) |
| Backyard gathering — people coming and going | Kill switch (manual) |
| Expected delivery window | Kill switch (manual) |
| Bear detected during deer cooldown | Cooldown override (automatic) |

### State Management

Same MQTT discovery pattern as cooldown — Node-RED owns state, HA provides dashboard control.

**Topic structure:**
```
homeassistant/switch/camera_{id}_kill/config    ← discovery (retained)
highland/state/camera/{id}/kill                 ← Node-RED → HA (retained, ON/OFF)
highland/command/camera/{id}/kill               ← HA → Node-RED
```

**Kill switch and cooldown are independent.** Clearing a kill switch does not clear active cooldowns. Activating a kill switch does not affect cooldown state — if the kill switch is later cleared and a cooldown was running, it resumes.

---

## Progressive Notification Pattern

Leverages HA Companion App notification update/dismiss via `tag` field (see NODERED_PATTERNS.md).

| Stage | Action |
|-------|--------|
| Motion detected | Send notification: "Motion detected — [camera friendly name]" |
| Local triage negative | Clear notification via tag |
| Local triage positive | Update notification: category + count of in-scope detections |
| Remote triage negative | Clear notification |
| Remote triage positive | Update notification: remote triage summary |
| Deep analysis complete | Update notification with full description and narrative |
| Nothing of interest after deep analysis | Clear notification |

---

## Event Storage

Video analysis events are stored in PostgreSQL (shared infrastructure with HA Recorder).

*Schema and storage design TBD — to be fleshed out in a dedicated session.*

**Calendar integration for Assist queryability:**
- Positive events → Local Calendar entity (`calendar.video_analysis_timeline`)
- Enables Assist queries: "Was my package delivered?" "Was there anyone in the driveway today?"
- Calendar event schema TBD alongside storage design

---

## Open Questions

### MQTT Topics
- [ ] Video pipeline topics (`highland/state/camera/{id}/kill`, `highland/state/camera/{id}/cooldown/{zone}`, detection events, triage results) need formal registration in MQTT_TOPICS.md — currently listed there as a pending domain

### NVR vs. Home Hub Architecture
- [ ] Home Hub and NVR likely have different APIs/event mechanisms — won't know until NVR is physically available and tested
- [ ] Need to determine which push mechanism is more reliable for NVR: TCP Baichuan or ONVIF SWN
- [ ] Understand what the NVR exposes vs. what was available via Home Hub — may fundamentally change capture approach

### Capture Mechanics
- [ ] Does NVR API support on-demand still capture per channel?
- [ ] Does NVR API support on-demand clip capture, or is capture always from stored recordings?
- [ ] Is clip capture via reolink_aio or via direct RTSP pull (ffmpeg)?
- [ ] NVR enables potential for 24/7 recording — does this change the clip capture strategy vs. battery cameras?
- [ ] **Can RTSP stream be read concurrently while NVR is actively recording?** If yes, eliminates the triggered-recording-then-wait pattern used with Home Hub
- [ ] **If 24/7 recording is running, can clips be extracted by timestamp?** ("Give me 10s centered on this event") — this is architecturally cleaner than triggered capture and avoids all contention issues
- [ ] What is the latency profile of timestamp-based clip extraction vs. triggered recording?
- [ ] Does continuous recording change the still keyframe approach, or is on-demand still pull still the fastest triage path regardless?
- [ ] Pre-motion buffer — if continuous recording is available, clips could include seconds *before* the motion trigger, which triggered recording can never provide

### Local Triage (CodeProject.AI)
- [ ] Validate Coral TPU compatibility with pinned CPAI version before committing — Coral support has been the most fragile aspect of CPAI across versions
- [ ] Validate emergency vehicle classification reliability before committing as a feature
- [ ] Confirm bounding box format returned by CPAI for zone overlap math
- [ ] Calibrate confidence thresholds per category — animal thresholds likely need to be more permissive given known Coral accuracy limitations for that class
- [ ] Evaluate ipcam-general (person/vehicle) + ipcam-animal as two sequential calls vs. a single combined model — accuracy tradeoff vs. latency tradeoff

### Event Storage / Schema
- [ ] PostgreSQL schema for video events
- [ ] Calendar event structure optimized for LLM parsing (Assist)
- [ ] Retention policy — events vs. media (keyframes, clips) likely different
- [ ] Orphaned reference handling (media deleted but event record remains)

### Camera Hardware Notes
- [ ] Dual-lens 180° cameras (rear yard, side yard planned for spring, possibly front yard) present as two independent channels on the NVR — two motion events, two zone configs, potential center-frame overlap. Deduplication applies within a single physical device as well as across separate cameras
- [ ] Front yard coverage TBD — porch overhang mount for 180° camera likely; doorbell camera covers final approach to front door (confirm whether Reolink or standalone device — integration path differs)
- [ ] Doorbell camera: if on NVR via reolink_aio, slots into pipeline naturally; if standalone (Ring, Nest, etc.) needs separate integration path
- [ ] Same physical event seen by multiple cameras = multiple events or one correlated event?
- [ ] Correlation strategy TBD — time window + category matching is the likely approach
- [ ] "Same person" problem (facial recognition for cross-camera identity) deferred
- [ ] **Street-facing camera feasibility pending late spring foliage assessment** — current property setback (~300ft from street) means garbage trucks and mail trucks are not visible on main cameras. A street-facing camera would enable mail truck detection (correlated with LoRaWAN mailbox sensor) and garbage truck detection. Foliage with full leaf cover may obstruct viable angles — evaluate in late spring before committing to hardware.

---

## Deferred

| Item | Reason |
|------|--------|
| Contextual exceptions on adjacent property (smoke, fire, altercation) | Requires Gemini in triage path; add after baseline pipeline is proven |
| Facial recognition / LPR as local triage enrichment | Architecture is defined (crop-and-resubmit, Node-RED reconciliation layer, detection object accumulation); CPAI modules exist. Deferred until baseline pipeline is stable. When added: recognized household members suppress notification entirely; unknown persons escalate with higher priority; known plates downgrade or suppress. |
| Cross-system delivery context | An unbranded box truck on a day with a known expected delivery has different meaning than the same truck on a day with none. Requires integration between video pipeline and a future delivery tracking system. Context-dependent interpretation belongs in Node-RED logic above local triage once that data is available. |
| Custom model training for out-of-distribution animals | Viable escape hatch if closed-set classifier blind spots become material. NVR event history is the training data pipeline. Deferred until blind spot profile is known from real-world calibration. |
| 24/7 recording infrastructure | NVR capability exists but scope is separate from pipeline design |
| Parallel Gemini vs. CPAI scoring for triage validation | Useful for calibration but not for initial build |
| PTZ camera integration | PTZ cameras are self-contained auto-tracking devices — they augment static camera coverage but are not detection inputs in the pipeline. No motion events, no triage, no zone filtering. Footage may be accessible via NVR for after-the-fact review. If additional exterior PTZs are added in future, same pattern applies. |

---

## Related Documents

| Document | Relevance |
|----------|-----------|
| **NODERED_PATTERNS.md** | MQTT discovery pattern (cooldown/kill switch entities), utility flow conventions |
| **EVENT_ARCHITECTURE.md** | MQTT topic conventions, payload standards |
| **CALENDAR_INTEGRATION.md** | Calendar-driven kill switch automation; gathering events → camera suppression |

---

*Last Updated: 2026-03-11*
