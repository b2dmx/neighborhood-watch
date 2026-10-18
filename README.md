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
3. **The first AI model looks.** The **First look** list is tried in order: if a model is busy, errors or is out of quota, the next one tries, then the next. Paid models are a last resort.
4. **A second model double-checks** (the **Second opinion** list, in order) when the first answer named a priority animal, a strict animal or an unknown animal, was unsure, or nobody answered. It also checks when a backup gave the first answer and named an animal; that check uses free models only.
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

1. **AI models.** The first look, the second opinion and any paid models, each in order. Only one model is needed (for the first look); the rest is optional.
2. **When to watch.** Start and end times, whether darkness also counts, and when the night summary is built.
3. **Cameras.** Pick them, then confirm each one. Its name and sensors are filled in from the camera's own device: motion and animal sensors start a check, person and vehicle sensors skip it.
4. **Pests.** Pick (or type) the animals that are pests, e.g. raccoon, opossum, skunk. Nothing else about wildlife needs setting up: every animal the AI names is recognised and tracked.
5. **Who gets alerts.** Pick from the phones with the Home Assistant app. iPhone and Android critical alerts are both supported.
6. **Animals.** Named animals, one at a time: name, what it looks like, identification tips, coat pattern and main tone at night (infrared shows tones, not colours), and whether it is a priority or always-strict animal. A reference photo is optional. Leave the name empty when you're done, or straight away if you only care about pests. This last page also says if no cameras were picked or no phones were found, so nothing is missed.

Afterwards, the integration page lists your animals (**Add animal** adds one); cameras and people are under **Configure → Cameras** and **Configure → People**, where you pick one to change it or add a new one. On the camera, person and animal forms, the less-used settings sit in sections you can open (for a camera: **Timing**, **Critical alerts** and **Scaring pests off**; for a person: **Critical alerts**, **Mutes** and **Quiet zones and hours**; for an animal: **Looks**, **Alerts and strictness** and **Last seen**):

- **Cameras**. Besides its sensors and cooldowns, a camera has:
  - **Critical alerts here, except**: while its Critical alerts switch is on, anything seen here rings through silent mode, except the animals listed (a pet that's allowed in the garage);
  - **What the AI should know about this view**: notes only this camera's checks see ("the bins are at the left"; "a cat behind the window on the right is an indoor pet");
  - **lights and a sound** that scare pests off.
- **Add animal**: animals told apart by name, like your cats. Each has **Alert at** and **Critical alerts only at** (see [Alerts](#alerts)).
- **People**: someone who gets alerts. Each has these settings:
  - their phone, and their critical sound and volume;
  - **Don't notify me about**: animals this person never hears about;
  - **quiet zones** (like work) and **quiet hours**, and what happens there: critical alerts arrive as normal ones, or no alerts at all;
  - whether they get missing-mode alerts.

  Each person also has a **"<name> gets critical alerts"** switch, shown on their phone's device (or the Neighborhood Watch device if no phone is found). Off: their critical alerts arrive as normal notifications.

**Configure** holds:

| Page | Settings (defaults) |
|---|---|
| Cameras / People | Pick one to change or remove, or add one |
| Alerts | Send alerts (on); alert from confidence (medium); Right / Not sure buttons on alerts (on) |
| Wildlife and catch-alls | The pest list; Alert at / Critical alerts only at for Pests, Other wildlife, Unknown animals and Unsure motion (all every camera, except unsure motion: none) |
| When to watch | 20:00–08:00, also when dark, or all day; summary at 08:05; on camera for 10 min |
| AI models | First look, second opinion, paid models; optional hours for the first model (see [AI models](#ai-models)) |
| Advanced → AI instructions | The AI instructions (with placeholders), notes for the AI (all cameras), common visitors (hints, not a limit), recently absent after 14 days, reference photos (on) |
| Advanced → Training data | Save training data (on); folder (media folder, or a NAS share); keep at most 5 GB |
| Advanced → Local detector | Address (empty: off); use (Shadow); animal threshold (0.2). See [Local detector](#local-detector-optional) |
| Advanced → Notification pictures | Public address for picture links (defaults to the external URL) |

### AI models

- **First look** runs on every sighting. Its models are tried in order until one answers.
- **Second opinion** looks again only when the first answer is unsure or unknown, names a priority or strict animal, or came from a backup and names an animal. Its models are tried in order too.
- **Paid models** are only a last resort: they are tried after the free ones give no answer, and never to double-check a backup's answer. Put them last in each list.
- **First model's hours** (optional): outside these hours, the first model in First look waits behind the free backups, so its daily quota lasts through the busiest hours. Empty: it always goes first.

**Free-tier tip:** put the cheapest model with the biggest free allowance (for example Gemini Flash-Lite) first in First look, and a smarter model first in Second opinion.

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
| Recently absent | On after the absent window without a sighting; while on, the AI must be sure. Not for priority animals, which have their own stricter rules |

Every animal has **Alerts**, **Critical alerts** and **Missing mode** switches. Priority animals also get **Last confirmed**. They also get **Confirmed on camera**: on for the on-camera window after a high-confidence sighting only, with the camera as an attribute. **Confirmed today** shows whether that happened today. Use Confirmed on camera to trigger a feeder.

### Wildlife

Wildlife needs no device per animal. The Neighborhood Watch device has:

| Entity | What it is |
|---|---|
| Wildlife | The latest wildlife sighting; attribute `kinds` lists every kind seen, each with last seen, where, tonight and this-week counts, whether it's a pest, and its last sighting id (its photo) |
| Wildlife on camera / Pest on camera | On for the on-camera window after a sure sighting; attributes `animal` and `camera`. Use them for lights or sounds |
| Pests / Other wildlife / Unknown animals / Unsure motion critical alerts | Switches |

### Per camera

These join the camera's own device (from its integration, e.g. UniFi Protect), so each camera is listed once.

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

## The sighting event

`neighborhood_watch_sighting` fires for every check:

| Field | Example |
|---|---|
| `animal`, `confidence`, `pattern`, `description` | `Luna`, `high`, `blotchy`, `` |
| `location`, `location_name`, `camera` | `backyard`, `Backyard`, `camera.backyard` |
| `priority_candidate`, `priority_animal` | `yes`, `Luna`: either model named a priority animal, at any confidence |
| `counted` | `yes` when it counts for tracking |
| `first_look_said`, `second_opinion_said`, `models_used` | the raw (tidied) answers and which models gave them |
| `time`, `image` | `9:05 PM`, a picture link with a cache-busting `?t=` |
| `test`, `source`, `sighting_id` | `no`, `neighborhood_watch`, an id for corrections |

## Alerts

The integration sends the alerts itself, to every person you add. Every alert is about something: one of your animals, or one of four catch-alls under **Configure → Wildlife and catch-alls**: **pests**, **other wildlife** (every other wild animal), **unknown animals** (a cat or dog that isn't one of yours) and **unsure motion**. The catch-alls are set per group, not per kind. Each animal and catch-all has two settings:

- **Alert at:** all cameras, chosen cameras, or none.
- **Critical alerts only at:** where its **Critical alerts switch** makes it ring through silent mode. Empty: wherever it alerts.

Every critical setting is a switch, so you can put it on a dashboard and flip it at short notice: one per camera and animal, and one per catch-all on the Neighborhood Watch device. These all start off. Each person's **"<name> gets critical alerts"** switch starts on.

Then:

- **Confidence.** Answers below the **Alert from** level (medium by default) don't alert; they wait in the review queue. Priority animals are always worded "Possible Luna".
- **A camera's critical switch** makes anything seen there ring, except the animals listed on its page. Critical overrides "don't alert", so you hear about anything in the garage even if its own alerts are off. Harmless things (squirrels, birds, bugs, leaves) and a confident empty frame never alert.
- **Visits.** The same animal seen again on the same camera within the camera's cooldown plus 10 minutes updates the earlier alert silently. Pests alert every time, so you know when one you scared off comes back.
- **Missing mode** (any named animal) beats everything: any possible sighting, from either model at any confidence on any camera, sends a critical alert at full volume to the people who get missing-mode alerts. The AI always takes a second look.
- **Per person.** Muted animals are skipped. In a quiet zone or quiet hours, critical alerts arrive as normal ones, or not at all; missing-mode alerts always arrive.

Each alert carries the frame the AI judged and three buttons: **✓ Right**, **? Not sure** and **It was…**, which opens a text box: type what it was (an animal or a wildlife kind).

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

For your own automations, the Neighborhood Watch device has **Pest on camera** (pests only) and **Wildlife on camera** (any wildlife) sensors.

## Corrections and training data

**Nothing has to be answered at night.** Every sighting with an animal in it waits in a **review queue** for up to 7 days. Confidently empty frames are left out. The card's **To review** section shows each photo with ✓ Right, ? Not sure and "It was…" (every animal and wildlife kind); it disappears when there is nothing to review. The **To review** sensor counts them. The buttons on an alert also still work when tapped the next morning. "Not sure" takes a sighting off the queue without labelling it. After "It was…", the card offers **Add a note so it doesn't happen again**, which saves it to that camera's view notes or that animal's tips, where the AI will see it (also the `add_note` action). "It was…" accepts any animal: your own animals and wildlife kinds move tracking; anything else (a groundhog, a heron) is labelled for training, just not tracked.

The AI models never learn from your corrections, so the integration keeps what's needed to train a model that does:

- **Training data.** Every checked frame is saved, downscaled to 1280 px, beside a file that records what the AI said. Files are stored under `<folder>/<date>/<sighting id>.jpg|json`.
- **Corrections.** A button on an alert, the `correct` action, or the card's ✓ / ✗ labels that frame. It also fixes tracking: the sighting, its photo and the last-seen time move to the right animal.
- **Pruning.** When the folder reaches its size limit, the oldest unlabelled frames go first.

## Local detector (optional)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="vision/logo-light.png">
  <img src="vision/logo.png" alt="Neighborhood Watch vision" width="320">
</picture>

The `vision` folder holds a small service that runs [MegaDetector](https://microsoft.github.io/Biodiversity/base/models/detection/ultralytics_based/megadetectorv6/), a free camera-trap model that finds animals, people and vehicles. It can run on your own machine: a NAS, a mini PC or any Docker host. It uses an Intel GPU if there is one, otherwise the CPU. Once it's set under **Configure → Advanced → Local detector**, it looks at every frame before the AI:

- **Shadow:** it's asked about every frame, and the **Detector agreement** sensor shows how often it agrees with the AI. A *miss* means the AI saw an animal and the detector didn't.
- **Skip empty frames:** when it finds no animal, the frame never reaches the AI. That saves quota on wind, cobwebs and shadows. In missing mode the AI always looks, and if the detector is down every frame goes to the AI as usual.

### Training the detector on your photos

MegaDetector was trained on trail cameras, so it misses animals on some home cameras, for example at night in infrared or through a fisheye doorbell lens. The service can learn from your own labelled photos (the integration's [training data](#corrections-and-training-data)) and gets better every week:

1. **Labelling.** Every animal photo gets its boxes from two "teachers": a general COCO YOLO11 model and the boxes the detector stored with the photo. Photos marked empty become background examples. Photos you marked "Not sure" are skipped. AI-only photos (nobody confirmed them) are used for training, but never to judge a model.
2. **Fair test.** The confirmed photos from the most recent ~20% of days are held back as the test set and never trained on. So are confirmed animal photos that no teacher could box, which are exactly the misses that matter. Confidently empty frames from those days are the empty side of the test.
3. **Training.** A one-class ("animal") YOLO11 model is trained on everything else: from YOLO11s the first time, then from the last promoted model.
4. **Promotion.** The model being served and the new one are both run on the test set the way the service runs them, at a 0.2 threshold. The new model replaces it only if it **misses fewer animals** and its **false alarms don't more than double** (or stay within 5% of the empty photos). It's then exported to OpenVINO and served within a minute, no restart. If it fails to load, MegaDetector is used.

Set it up by mounting the training-data folder into the container (see `vision/compose.yaml`; adjust the path on the left to where that folder lives, read-only is enough):

```yaml
    volumes:
      - /volume1/your-share/neighborhood-watch/dataset:/dataset:ro
    shm_size: 1gb           # room for training's data loaders
    environment:
      TZ: Europe/Berlin     # your time zone; the schedule uses local time
```

- **Schedule:** every Sunday from 13:00 local time (`NW_TRAIN_DAY`, `NW_TRAIN_HOUR`), when at least 30 new confirmed photos arrived since the last round, or for the first round once 40 animal photos are confirmed. `NW_TRAIN=0` turns it off. Training runs in a separate low-priority process, so detection stays responsive. A round takes from tens of minutes to a few hours on a CPU, depending on how many photos there are. The first round downloads the YOLO11 weights (about 60 MB) into `work/train/weights`.
- **Now:** `POST /train` starts a round (409 if one is running). `GET /train` shows progress and the last round's results: misses, false alarms and agreement before and after, and whether it was promoted. Every round is kept in `work/train/history.json`, the log in `work/train/train.log`.
- **Which model is in use:** `GET /health` shows `model` and `trained`. `work/models/current.txt` names the trained model being served (the last 3 are kept). Delete that file to go back to MegaDetector.

## Bulk scanning (photos and videos)

Got a trail-cam card, a phone video or old recordings? Put them in the detector's scan folder, then use the card's **Scan a folder** box or the `neighborhood_watch.scan` action:

1. The detector checks every photo, and a frame every second of every video. It keeps the moments with an animal, grouped into **visits** (one per photo, or a stretch of a video), each with its best frame. Hours of footage become a short list.
2. Each visit is named by the same AI as the cameras (one or two calls per visit, up to the limit you set).
3. A report, `neighborhood-watch-report/index.html` plus `report.csv`, is written into the scanned folder, with every visit's frame. The **Last scan** sensor and the card show the totals, and the people who get alerts get a summary.
4. Every visit joins the **review queue** and the training data. Scanned footage can be old, so it never changes anyone's last-seen tracking.

## Dashboard card

The integration installs a **Neighborhood Watch** card, so there's no resource to add. It finds your devices on its own and shows:

- **To review**: sightings waiting for a ✓ / ✗ (see [Corrections](#corrections-and-training-data));
- each animal's photo, where and when it was last seen, whether it's on camera now, and its Alerts, Critical and Missing mode switches. Without a recent photo it shows a face drawn from the animal's description (colours, calico, tortie, tabby, spotted, tuxedo, bicolour, colourpoint, long hair, ear tufts, folded ears, hairless, eye colour);
- wildlife, last night's summary, and each camera's last detection with ✓ / ✗ buttons;
- the critical-alert switches;
- **Scan a folder**: start a [bulk scan](#bulk-scanning-photos-and-videos) and see the last one's totals.

Its editor picks the sections and the devices to hide, and sets the rest:

| Option | What it does (default) |
|---|---|
| `title` | The card's title (Neighborhood Watch) |
| `sections` | Which sections to show: `review`, `animals`, `wildlife`, `summary`, `recent`, `switches`, `scan` (all except `scan`, which needs the [local detector](#local-detector-optional); it also shows once a scan has run) |
| `hide` | Devices to leave out |
| `order` | Animal order: `recent` puts animals on camera now first, then the most recently seen, ties by name; `name` is alphabetical (`recent`) |
| `columns` | Animal columns (3) |
| `photo_height` | Photo height in px (140) |
| `photo_hours` | How long a sighting photo shows before the drawn face comes back; 0 = always (12) |
| `review_limit` | Sightings to review at once (6) |

```yaml
type: custom:neighborhood-watch-card
sections: [review, animals, wildlife, summary, recent, switches, scan]  # scan is opt-in
order: recent
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
| `neighborhood_watch.scan` | Bulk-scans a folder of photos and videos on the detector's machine (see [Bulk scanning](#bulk-scanning-photos-and-videos)) |
| `neighborhood_watch.add_note` | Adds a note for the AI to a camera's view notes or an animal's tips |
| `neighborhood_watch.move_training_data` | Moves saved training photos into the current training folder, e.g. after pointing it at a NAS |

## Privacy

Frames go only to the AI models you choose. Everything else stays local:

- Tracking lives in Home Assistant's `.storage` folder.
- Photos are in `config/neighborhood_watch`.
- Frames and reference photos are in the media folder.
- Notification pictures are in a random-named folder inside `config/www/neighborhood_watch`, so their links can't be guessed. Like everything in `www`, though, anyone who has a link can open it without logging in.
- Training data is in the folder you choose.

## License

MIT

---

<p align="center">Neighborhood Watch v0.13.6 by Goobis<br>AI was used to help develop this integration.</p>
