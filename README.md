<p align="center"><img src="custom_components/neighborhood_watch/brand/icon@2x.png" width="160" alt=""></p>

# Neighborhood Watch

**Find out which animal is outside, using the cameras you already have.** Neighborhood Watch is a Home Assistant integration. It looks at your camera when something moves, asks an AI model what's there, and tells you: *a raccoon at the bins*, *your cat Milo at the back door*, *an unknown ginger cat in the yard*.

## What it's for

- **Catching pests in the act.** Something keeps getting into the garbage, the shed or the chickens. Get an alert with the photo when it's there. Turn on a floodlight or play a sound to scare it off. Get a morning summary of what came by.
- **Without an NVR, a subscription or a recognition service.** Any camera Home Assistant can show works: a cheap RTSP or ONVIF camera, a doorbell, any brand. A camera with no motion sensor can be checked on a timer. The AI is any AI Task model; Google's free Gemini tier covers a typical night.
- **Keeping track of animals you know.** Your outdoor cats, a neighbour's dog, a stray you're trying to catch. It records when and where each was last seen, with the exact photo the AI judged.
- **Never missing the one that matters.** Every named animal has a missing mode: while it's on, any possible sighting, from either AI model at any confidence, sends a critical alert. Mark an animal as priority, and it's also double-checked by two AI models and never falsely confirmed. A "confirmed" signal (both models sure) is safe to drive a feeder or a trap.
- **Getting better over time.** Every alert has ✓ / ✗ buttons. Corrections fix tracking right away, and every labelled photo is saved to train a custom model.

It is all set up in the UI. A dashboard card comes with it.

## How it works

1. **A camera sees motion or an animal** during the watch window: between set times, or whenever it's dark, whichever is wider. Person and vehicle sensors skip the check; each camera has its own cooldown.
2. **A snapshot is taken.** If it fails, the check stops, so the AI never judges an old photo.
3. **The first AI model looks.** If it is busy, errors or is out of quota, the next model in your list tries, then the next. Paid models go last.
4. **A second model double-checks** when the first named a priority animal, a strict animal or an unknown animal, was unsure, or nobody answered.
5. **The two answers become one verdict:**
   - Models that disagree give low confidence.
   - A priority animal is high only when both models name it and both see its coat pattern and main colour. Without a second opinion it is capped at medium.
   - A strict animal (always strict, or not seen for 14 days) counts only when both models name it at high.
   - If the first look saw nothing but the second names an animal at high, the result is capped at medium.
6. **It is recorded and announced:**
   - The sighting updates tracking (known animals at medium or high), the camera's current animal and tonight's log.
   - The exact frame the AI judged becomes the animal's photo.
   - A `neighborhood_watch_sighting` event carries everything your alert automations need.

AI answers are tidied first (stray `**`, code fences, quotes and labels removed). A reply with no answer format counts as an unsure "nothing". If every model fails, the result is an unsure "nothing" too.

## Requirements

- Home Assistant 2026.10 or newer.
- At least one **AI Task** entity that accepts images, for example Google Generative AI. A second model is strongly recommended for the double-check.
- Cameras Home Assistant can show (any `camera` entity). Motion or animal sensors are best; without them, a camera can be checked on a timer.

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/b2dmx/neighborhood-watch`, category **Integration**.
2. Download **Neighborhood Watch**, then restart Home Assistant.
3. **Settings → Devices & services → Add integration → Neighborhood Watch**.

### Manual

Copy `custom_components/neighborhood_watch` into your `config/custom_components` folder and restart.

## Setup

Onboarding walks through:

1. **AI models.** The first look, the second opinion and any paid models, each in order.
2. **When to watch.** Start and end times, whether darkness also counts, and when the night summary is built.
3. **Cameras.** Pick them, then confirm each one. Its name and sensors are filled in from the camera's own device: motion and animal sensors start a check, person and vehicle sensors skip it.
4. **Pests.** Pick (or type) the animals that are pests, e.g. raccoon, opossum, skunk. Nothing else about wildlife needs setting up: every animal the AI names is recognised and tracked.
5. **Who gets alerts.** Pick from the phones with the Home Assistant app. iPhone and Android critical alerts are both supported.
6. **Animals.** Named animals, one at a time: name, what it looks like, identification tips, coat pattern and main colour, and whether it is a priority or always-strict animal. A reference photo is optional. Skip this step if you only care about pests.

Afterwards, the integration page has Add buttons for cameras, animals and people. Each also gets its own page, where you can edit or delete it:

- **Add camera**. Besides its sensors and cooldowns, a camera has:
  - **Critical alerts here, except**: while its Critical alerts switch is on, anything seen here rings through silent mode, except the animals listed (a pet that's allowed in the garage);
  - **What the AI should know about this view**: notes only this camera's checks see ("the bins are at the left"; "a cat behind the window on the right is an indoor pet");
  - **lights and a sound** that scare pests off.
- **Add animal**: animals told apart by name, like your cats. Each has **Alert at** and **Critical alerts at** (see [Alerts](#alerts)).
- **Add wildlife rules** (optional, rarely needed): gives one wildlife kind rules of its own instead of the shared Pests / Other wildlife ones, with its own device and switches.
- **Add person**: someone who gets alerts. Each has these settings:
  - their phone, and their critical sound and volume;
  - **Don't notify me about**: animals this person never hears about;
  - **quiet zones** (like work) and **quiet hours**, and what happens there: critical alerts arrive as normal ones, or no alerts at all;
  - whether they get missing-mode alerts.

  Each person also has a **"<name> gets critical alerts"** switch on the Neighborhood Watch device. Off: their critical alerts arrive as normal notifications.

**Configure** holds:

| Page | Settings (defaults) |
|---|---|
| Mode | Live; shadow (reuse another system's answers); shadow (independent, no paid models) |
| Alerts | Send alerts (on); alert from confidence (medium); Right / Wrong buttons on alerts (on) |
| Wildlife and catch-alls | The pest list; Alert at / Critical alerts only at for Pests, Other wildlife, Unknown animals and Unsure motion (all every camera, except unsure motion: none) |
| AI models | First look, second opinion, paid models; optional hours for the main model |
| When to watch | 20:00–08:00, also when dark, or all day; summary at 08:05; on camera for 10 min |
| Identification | The AI instructions (with placeholders), notes for the AI (all cameras), common visitors (hints, not a limit), recently absent after 14 days, reference photos (on) |
| Training data | Save training data (on); folder (media folder, or a NAS share); keep at most 5 GB |
| Notification pictures | Public address for picture links (defaults to the external URL) |

### Cooldowns

Each camera has a cooldown (3 min by default). Two optional overrides:

- **After a priority animal**: when the camera's last real animal was a priority animal, e.g. 30 min so one long visit doesn't fire every few minutes.
- **In missing mode**: while any missing mode is on, e.g. 2 min so nothing is missed.

**A check on these cameras ends the cooldown** reopens a camera when an animal is seen on its way there, e.g. the driveway reopens the garage.

## Entities

### Per animal

| Entity | What it is |
|---|---|
| Last seen | When it was last seen at medium or high confidence (high only for strict animals) |
| Location | The camera it was last seen on, or "Not seen" |
| On camera | On for the on-camera window after a sighting that counts; attribute `camera` |
| Last photo | The exact frame the AI judged |
| Reference photo | The photo sent to the AI beside each frame (if set) |
| Sightings tonight / this week | Counted sightings since the last summary, and over 7 days |
| Recently absent | On after the absent window without a sighting; while on, the AI must be sure |

Every animal has **Alerts**, **Critical alerts** and **Missing mode** switches. Priority animals also get **Last confirmed**. They also get **Confirmed on camera**: on for the on-camera window after a high-confidence sighting only, with the camera as an attribute. **Confirmed today** shows whether that happened today. Use Confirmed on camera to trigger a feeder.

### Wildlife

Wildlife needs no device per animal. The Neighborhood Watch device has:

| Entity | What it is |
|---|---|
| Wildlife | The latest wildlife sighting; attribute `kinds` lists every kind seen, each with last seen, where, tonight and this-week counts, whether it's a pest, and its last sighting id (its photo) |
| Wildlife on camera / Pest on camera | On for the on-camera window after a sure sighting; attributes `animal` and `camera`. Use them for lights or sounds |
| Pests / Other wildlife / Unknown animals / Unsure motion critical alerts | Switches |

### Per camera

| Entity | What it is |
|---|---|
| Animal | What it is seeing now (clears after the on-camera window), or "None" |
| Last detection | The last result, with both AI answers, the models used and the time |
| Last frame | The frame the AI last judged |
| Critical alerts | A switch: anything seen here rings through silent mode, except the animals on the camera's page |

### Overall

| Entity | What it is |
|---|---|
| Night summary | Each animal's sightings last night, with places; low-confidence wildlife counts as possible, e.g. "Raccoon — 3 sightings (1 confirmed, 2 possible)"; full detail in attributes |
| Last sighting | The latest result from any camera |
| AI calls today | Calls per model today (quota day, midnight Pacific), answers, errors and the last error |
| Shadow mismatches | In shadow mode: tonight's disagreements with the live system; recent comparisons in attributes |

## The sighting event

`neighborhood_watch_sighting` fires for every check in live mode:

| Field | Example |
|---|---|
| `animal`, `confidence`, `pattern`, `description` | `Luna`, `high`, `blotchy`, `` |
| `location`, `location_name`, `camera` | `garage`, `Garage`, `camera.backyard` |
| `priority_candidate`, `priority_animal` | `yes`, `Luna`: either model named a priority animal, at any confidence |
| `counted` | `yes` when it counts for tracking |
| `first_look_said`, `second_opinion_said`, `models_used` | the raw (tidied) answers and which models gave them |
| `time`, `image` | `9:05 PM`, a picture link with a cache-busting `?t=` |
| `test`, `source`, `sighting_id` | `no`, `neighborhood_watch`, an id for corrections |

## Alerts

The integration sends the alerts itself, to every person you add. Every alert is about something: one of your animals, a wildlife kind, or one of three catch-alls (**unknown animals**, **other wildlife** you haven't added, and **unsure motion**). Each has two settings:

- **Alert at:** all cameras, chosen cameras, or none.
- **Critical alerts only at:** where its **Critical alerts switch** makes it ring through silent mode. Empty: wherever it alerts.

Every critical setting is a switch that starts off, so you can put it on a dashboard and flip it at short notice: one per camera, animal and wildlife kind, and one per catch-all on the Neighborhood Watch device.

Then:

- **Confidence.** Answers below the **Alert from** level (medium by default) don't alert; they wait in the review queue. Priority animals are always worded "Possible Luna".
- **A camera's critical switch** makes anything seen there ring, except the animals listed on its page. Critical overrides "don't alert", so you hear about anything in the garage even if its own alerts are off. Harmless things (squirrels, birds, bugs, leaves) and a confident empty frame never alert.
- **Visits.** The same animal seen again on the same camera within the camera's cooldown plus 10 minutes updates the earlier alert silently. Pests alert every time, so you know when one you scared off comes back.
- **Missing mode** (any named animal) beats everything: any possible sighting, from either model at any confidence on any camera, sends a critical alert at full volume to the people who get missing-mode alerts. The AI always takes a second look.
- **Per person.** Muted animals are skipped. In a quiet zone or quiet hours, critical alerts arrive as normal ones, or not at all; missing-mode alerts always arrive.

Each alert carries the frame the AI judged and three buttons: **✓ Right**, **✗ Wrong** and **It's Luna**.

You can turn the alerts off and use the event in your own automations instead:

```yaml
triggers:
  - trigger: event
    event_type: neighborhood_watch_sighting
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.priority_candidate == 'yes' }}"
actions:
  - action: notify.mobile_app_phone
    data:
      title: "Possible {{ trigger.event.data.priority_animal }} — {{ trigger.event.data.location_name }}"
      message: "Confidence: {{ trigger.event.data.confidence }}"
      data:
        image: "{{ trigger.event.data.image }}"
```

## Scaring pests off

When a pest is seen at a camera with deterrent lights or a sound:

- The lights that were off turn on for the set time, then turn back off. Lights that were already on are left on.
- The sound plays on the chosen speaker.

Each kind also has an **On camera** sensor, for your own automations.

## Corrections and training data

**Nothing has to be answered at night.** Every sighting with an animal in it waits in a **review queue** for up to 7 days. Confidently empty frames are left out. The card's **To review** section shows each photo with ✓ Right, ✗ Wrong, ? Not sure and "It was…". The **To review** sensor counts them. The buttons on an alert also still work when tapped the next morning. "Not sure" takes a sighting off the queue without labelling it. After a ✗ or "It was…", the card offers **Add a note so it doesn't happen again**, which saves it to that camera's view notes or that animal's tips, where the AI will see it (also the `add_note` action). "It was…" accepts any animal: your own animals and wildlife kinds move tracking; anything else (a groundhog, a heron) is labelled for training, just not tracked.

The AI models never learn from your corrections, so the integration keeps what's needed to train a model that does:

- **Training data.** Every checked frame is saved, downscaled to 1280 px, beside a file that records what the AI said. Files are stored under `<folder>/<date>/<sighting id>.jpg|json`.
- **Corrections.** A button on an alert, the `correct` action, or the card's ✓ / ✗ labels that frame. It also fixes tracking: the sighting, its photo and the last-seen time move to the right animal.
- **Pruning.** When the folder reaches its size limit, the oldest unlabelled frames go first.

## Local detector (optional)

The `vision` folder holds a small service that runs [MegaDetector](https://microsoft.github.io/Biodiversity/base/models/detection/ultralytics_based/megadetectorv6/), a free camera-trap model that finds animals, people and vehicles. It can run on your own machine: a NAS, a mini PC or any Docker host. It uses an Intel GPU if there is one, otherwise the CPU. Once it's set under **Configure → Local detector**, it looks at every frame before the AI:

- **Shadow:** it's asked about every frame, and the **Detector agreement** sensor shows how often it agrees with the AI. A *miss* means the AI saw an animal and the detector didn't.
- **Skip empty frames:** when it finds no animal, the frame never reaches the AI. That saves quota on wind, cobwebs and shadows. In missing mode the AI always looks, and if the detector is down every frame goes to the AI as usual.

## Bulk scanning (photos and videos)

Got a trail-cam card, a phone video or old recordings? Put them in the detector's scan folder, then use the card's **Scan a folder** box or the `neighborhood_watch.scan` action:

1. The detector checks every photo, and a frame every second of every video. It keeps the moments with an animal, grouped into **visits** (one per photo, or a stretch of a video), each with its best frame. Hours of footage become a short list.
2. Each visit is named by the same AI as the cameras (one or two calls per visit, up to the limit you set).
3. A report, `neighborhood-watch-report/index.html` plus `report.csv`, is written into the scanned folder, with every visit's frame. The **Last scan** sensor and the card show the totals, and the people who get alerts get a summary.
4. Every visit joins the **review queue** and the training data. Scanned footage can be old, so it never changes anyone's last-seen tracking.

## Dashboard card

The integration installs a **Neighborhood Watch** card, so there's no resource to add. It finds your devices on its own and shows:

- each animal's photo, where and when it was last seen, whether it's on camera now, and its Alerts, Critical and Missing mode switches;
- wildlife, last night's summary, and each camera's last detection with ✓ / ✗ buttons;
- the critical-alert switches.

Its editor picks the sections and the devices to hide, and sets the columns and photo height.

```yaml
type: custom:neighborhood-watch-card
sections: [animals, wildlife, summary, recent, switches]
columns: 3
```

## Actions

| Action | What it does |
|---|---|
| `neighborhood_watch.identify` | Checks a camera now (ignoring its cooldown) and returns the result |
| `neighborhood_watch.test_sighting` | Fires a fake event marked `test: yes` to try your alerts; tracking is never changed |
| `neighborhood_watch.correct` | Right, wrong, or what it really was, for a sighting (by id, by camera, or the latest) |
| `neighborhood_watch.mark_wrong` | "That wasn't Pepper": removes the latest counted sighting of an animal from tracking |
| `neighborhood_watch.set_reference_photo` | Uses an animal's last photo, or a fresh snapshot, as its reference photo |
| `neighborhood_watch.build_summary` | Builds the night summary now and starts a new night |

## Shadow mode

Run the integration beside an existing setup before switching over:

- **Shadow, reuse answers** re-runs the rules on the existing system's own sighting events. It makes no AI calls of its own.
- **Shadow, independent** checks the cameras itself without paid models, to test snapshots and the fallback chain.

Neither fires the sighting event, so nothing alerts or feeds. **Shadow mismatches** lists every disagreement. Alerts are not sent in shadow mode.

## Privacy

Frames go only to the AI models you choose. Everything else stays local:

- Tracking lives in Home Assistant's `.storage` folder.
- Photos are in `config/neighborhood_watch`.
- Frames and reference photos are in the media folder.
- Notification pictures are in `config/www/neighborhood_watch`. Like everything in `www`, anyone with the link can open them.
- Training data is in the folder you choose.

## License

MIT

---

<p align="center">Neighborhood Watch v0.6.1 by Goobis<br>AI was used to help develop this integration.</p>
