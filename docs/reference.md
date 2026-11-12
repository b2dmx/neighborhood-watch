# Neighborhood Watch — full reference

**Find out which animal is outside, using the cameras you already have.** Neighborhood Watch is a Home Assistant integration. It looks at your camera when something moves, asks an AI model what's there, and tells you: *a raccoon at the bins*, *your cat Milo at the back door*, *an unknown ginger cat in the yard*.

## What it's for

- **Catching pests in the act.** Something keeps getting into the garbage, the shed or the chickens. Get an alert with the photo when it's there. Turn on a floodlight or play a sound to scare it off. Get a morning summary of what came by.
- **Without an NVR, a subscription or a recognition service.** Any camera Home Assistant can show works: a cheap RTSP or ONVIF camera, a doorbell, any brand. A camera with no motion sensor can be checked on a timer. The AI is any AI Task model; Google's free Gemini tier covers a typical night.
- **Keeping track of animals you know.** Your outdoor cats, a neighbour's dog, a stray you're trying to catch. It records when and where each was last seen, with the exact photo the AI judged.
- **Never missing the one that matters.** Every named animal has a missing mode: while it's on, any possible sighting, from either AI model at any confidence, sends a critical alert. Mark an animal as priority, and it's also double-checked by two AI models and never falsely confirmed. A "confirmed" signal (both models sure) is safe to drive a feeder or a trap.
- **Getting better over time.** Every alert has ✓ Right, ? Not sure and Other buttons. Corrections fix tracking right away, and every labelled photo is saved to train a custom model.

It is all set up in the UI. A dashboard card comes with it.

## How it works

1. **A camera sees motion or an animal** during the watch window: between set times, or whenever it's dark, whichever is wider. Person and vehicle sensors skip the check; each camera has its own cooldown.
2. **A snapshot is taken.** If it fails, the check stops, so the AI never judges an old photo.
3. **The first AI model looks.** The **First look** list is tried in order: if a model is busy, errors or is out of quota, the next one tries, then the next. Paid models always go last. If none of them answers, the paid models from either list try, then (if there's time) the first look once more after 45 seconds. Every model is also asked where the animal is in the picture, so the photo can be cropped to it and the detector can learn from it.
4. **A second model double-checks** (the **Second opinion** list, in order) when the first answer named a priority animal, a strict animal or an unknown animal, was unsure, or nobody answered. It also checks when a backup gave the first answer and named an animal, and when the first answer names wildlife that can alert (a raccoon or a fox; not a bird or a deer); those two checks use free models only.
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
- At least one **AI Task** entity that accepts images, for example Google Generative AI. A second model is strongly recommended for the double-check: without one, priority animals are never confirmed and strict or recently absent animals are never counted.
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

1. **AI models.** The first look and the second opinion, each in order. Only one model is needed (for the first look); the rest is optional. Paid models and the first model's busy hours are set later under **Configure → AI models**.
2. **When to watch.** Start and end times, whether darkness also counts, or **Watch all day**. Under **More**: when the night summary is built, and how long an animal shows as on camera. While any animal's missing mode is on, cameras are checked all day.
3. **Pests.** Pick (or type) the animals that are pests, e.g. raccoon, opossum, skunk. Nothing else about wildlife needs setting up: every animal the AI names is recognised and tracked.
4. **Cameras.** Pick them, then confirm each one. Its name and sensors are filled in from the camera's own device: motion and animal sensors start a check, person and vehicle sensors skip it.
5. **Who gets alerts.** Pick from the phones with the Home Assistant app. iPhone and Android critical alerts are both supported.
6. **Animals.** Named animals, one at a time: name, what it looks like, identification tips, coat pattern and main tone at night (infrared shows tones, not colours), and whether it is a priority or always-strict animal. A reference photo is optional. Leave the name empty when you're done, or straight away if you only care about pests. This last page also says if no cameras were picked, no phones were found or no second opinion model was picked, so nothing is missed.

Afterwards, change anything on the [Neighborhood Watch page](#the-neighborhood-watch-page) in the sidebar, or on the integration page: **Add animal**, each animal's ⋮ → **Edit animal**, and **Configure** for the rest. Less-used settings sit in sections you can open. What each has:

- **Cameras**. Besides its sensors and cooldowns, a camera has:
  - **Never ring through silent mode here for**: while its **Critical alerts for anything here** switch is on, anything seen here rings through silent mode, except the animals listed (a pet that's allowed in the garage);
  - **What the AI should know about this view**: notes only this camera's checks see ("the bins are at the left"; "a cat behind the window on the right is an indoor pet");
  - **lights and a sound** that scare pests off.
- **Add animal**: animals told apart by name, like your cats. Each has **Alert at** and **Critical alerts at** (see [Alerts](#alerts)).
- **People**: someone who gets alerts. Each has these settings:
  - their phone, and their critical sound and volume;
  - **Never alert them about**: animals this person never hears about;
  - **quiet zones** (like work) and **quiet hours**, and what happens there: critical alerts arrive as normal ones, or no alerts at all;
  - whether they get missing-mode alerts.

  Each person also has a **"<name> gets critical alerts"** switch, shown on their phone's device (or the Neighborhood Watch device if no phone is found). Off: their critical alerts arrive as normal notifications.

### The Neighborhood Watch page

After setup, a **Neighborhood Watch** page appears in Home Assistant's sidebar (admins only). It has everything in one place, by topic:

- **Review:** the sightings waiting for your answer, the same list as the card's To review section.
- **Cameras:** every camera (open one to change it, add one, or **Remove camera**), and **When to watch**. A camera's page starts with its **Critical alerts for anything here** switch.
- **Animals:** every animal (open, add, **Remove animal**). An animal's page starts with its switches and draws its card picture as you pick a look. The tab also has the critical alerts switches for pests, unknown animals, other wildlife and unsure motion, and the **Pests** and **Other animals and motion** settings.
- **People:** everyone who gets alerts (open, add, **Remove person**); a person's page starts with their critical alerts switch.
- **Alerts:** the alert settings and **Pictures in alerts**.
- **AI:** AI models and AI instructions.
- **Detector:** **Local detector**, the detector's status, its training and past rounds, a **This week** line (how often it agreed with the AI, and how many animals it missed), and **Saved photos**.

Removing asks first. These are the same settings as **Configure** and **Add animal** on the integration page, with the same checks and explanations. A change in one shows in the other.

**Configure** holds:

| Page | Settings (defaults) |
|---|---|
| Cameras / People | Pick one to change or remove, or add one |
| When to watch | 20:00–08:00, also when dark, or all day; summary at 08:05; on camera for 10 min |
| Pests | The pest list; where pests alert (Alert at / Critical alerts at; default every camera) |
| Other animals and motion | Alert at / Critical alerts at for Other wildlife, Unknown animals and Unsure motion (all every camera, except unsure motion: none) |
| Alerts | Send alerts (on); lowest confidence that alerts (medium); Right / Not sure / Other buttons on alerts (on); page that opens when you tap an alert (empty) |
| Pictures in alerts | Public address for picture links (defaults to the external URL) |
| AI models | First look, second opinion, paid models; optional busy hours for the first model (see [AI models](#ai-models)) |
| Advanced → AI instructions | Notes for the AI (all cameras), common visitors (hints, not a limit), reference photos (on), recently absent after 14 days, the AI instructions (with placeholders; empty follows the built-in default) |
| Advanced → Saved photos | Save checked photos (on; needed for Review); folder (empty: the media folder's `neighborhood_watch/dataset`, or a NAS share); keep at most 5 GB |
| Advanced → Local detector | Address (empty: off; checked when you save); mode (Test only); how sure the detector must be (0.2). See [Local detector](#local-detector-optional) |

### AI models

- **First look** runs on every sighting. Its models are tried in order until one answers.
- **Second opinion** looks again only when the first answer is unsure or unknown, names a priority or strict animal, names wildlife that can alert (a raccoon or a fox; not a bird or a deer), or came from a backup and names an animal. Its models are tried in order too, and must differ from the First look ones (a model rarely disagrees with itself).
- **Paid models** always go last in each list, whatever order you put them in, and are never used to double-check a backup's answer. When no free model answers the first look, they're its last resort.
- **No answer**: when no model answers at all, the sighting says **No answer**, not Nothing. It waits in To review for you to say what it was.
- **Save the first model for busy hours** (optional): outside these hours, the first model in First look waits behind the free backups, so its daily quota lasts through the busiest hours. Empty: it always goes first.

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
| Wildlife | The latest wildlife sighting; attribute `kinds` lists every kind seen, each with last seen, where, tonight and this-week counts, whether it's a pest, and its last sighting id (its photo); attribute `unknown` lists unknown cats and dogs the same way, with the coat of their latest sighting (shown on the card under Also seen) |
| Wildlife on camera / Pest on camera | On for the on-camera window after a sure sighting; attributes `animal` and `camera`. Use them for lights or sounds |
| Pests / Other wildlife / Unknown animals / Unsure motion critical alerts | Switches |

### Per camera

These join the camera's own device (from its integration, e.g. UniFi Protect), so each camera is listed once.

| Entity | What it is |
|---|---|
| Animal | What it is seeing now (clears after the on-camera window), or "None" |
| Last detection | The last result, with both AI answers, the models used and the time |
| Last frame | The frame the AI last judged |
| Critical alerts for anything here | A switch: anything seen here rings through silent mode, except the animals on the camera's page |

### Overall

| Entity | What it is |
|---|---|
| Night summary | Each animal's sightings last night, with places; low-confidence wildlife counts as possible, e.g. "Raccoon — 3 sightings (1 confirmed, 2 possible)"; full detail in attributes |
| Last sighting | The latest result from any camera |
| AI calls today | Calls per model today (quota day, midnight Pacific), answers, errors and the last error |
| To review | How many sightings wait for an answer; attributes list them, the last answer (which can be undone) and what a sighting can be corrected to |
| Detector agreement | How often the local detector agreed with the AI over 7 days, in %; attributes `misses` (animals it missed), `extras`, the recent misses and the last error |
| Last scan | The latest [bulk scan](#bulk-scanning-photos-and-videos): its progress, then how many visits it found; attributes hold the folder, totals and report |

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

The integration sends the alerts itself, to every person you add. Every alert is about something: one of your animals, or one of four catch-alls under **Configure → Pests** and **Configure → Other animals and motion**: **pests**, **other wildlife** (every other wild animal), **unknown animals** (a cat or dog that isn't one of yours) and **unsure motion**. The catch-alls are set per group, not per kind. Each animal and catch-all has two settings:

- **Alert at:** all cameras, chosen cameras, or none.
- **Critical alerts at:** where its **Critical alerts switch** makes it ring through silent mode. Empty: every camera in Alert at. Picking a camera here also alerts there.

Every critical setting is a switch, so you can put it on a dashboard and flip it at short notice: one per camera and animal, and one per catch-all on the Neighborhood Watch device (also on the sidebar page's Animals tab). These all start off. On an iPhone, allow Critical Alerts for the Home Assistant app in iOS Settings → Notifications. Each person's **"<name> gets critical alerts"** switch starts on.

Then:

- **Confidence.** Answers below **Lowest confidence that alerts** (medium by default) don't alert; they wait in To review. Critical alerts, unsure motion and missing mode ignore it. Priority animals are always worded "Possible Luna".
- **A camera's critical switch** makes anything seen there ring, except the animals listed on its page. Critical overrides "don't alert", so you hear about anything in the garage even if its own alerts are off. Harmless things (squirrels, birds, bugs, leaves) and a confident empty frame never alert.
- **Visits.** The same animal seen again on the same camera within the camera's cooldown plus 10 minutes updates the earlier alert silently. Pests alert every time, so you know when one you scared off comes back.
- **Missing mode** (any named animal) beats everything: any possible sighting, from either model at any confidence on any camera, sends a critical alert at full volume to the people who get missing-mode alerts. The AI always takes a second look, and cameras are checked all day, not only in watch hours. Turn it on with the animal's **Missing mode** switch: on its page (sidebar page → Animals) or on its device.
- **Per person.** Muted animals are skipped. In a quiet zone or quiet hours, critical alerts arrive as normal ones, or not at all; missing-mode alerts always arrive.

Each alert carries the frame the AI judged and three buttons: **✓ Right**, **? Not sure** and **… Other** (it was something else: it comes off that animal's tracking and waits in To review so you can pick what it was). They all work from a watch. Tapping the alert itself opens the page set under Configure → Alerts, e.g. `/neighborhood-watch#review` (the sidebar page, admins only) or a dashboard with the card, e.g. `/lovelace/pets#review`.

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

## Corrections and saved photos

**Nothing has to be answered at night.** Every sighting with an animal in it waits in **To review** (on the card, and the sidebar page's Review tab) for up to 7 days. Answer ✓ Right, ? Not sure (off the list, no label) or It was… (your animals, wildlife kinds, or **Something else (type it)**). An answered photo leaves the list at once; **Undo** takes the last answer back (also the `undo_last_answer` action). Your animals and wildlife kinds move tracking; anything else is only labelled. After It was…, **Add a note so it doesn't happen again** gives the AI a note for that camera or animal (also the `add_note` action). Alert buttons still work the next morning.

The AI models never learn from your corrections, so the integration keeps what's needed to train a model that does:

- **Saved photos.** Every checked frame is saved, downscaled to 1280 px (or at the camera's own size, up to 4K, with **Save photos at full size**: about 4 times the space, and the local detector then trains on animals as large as it sees them live), beside a file that records what the AI said. Files are stored under `<folder>/<date>/<sighting id>.jpg|json`; the folder is the media folder's `neighborhood_watch/dataset` unless you set another under **Saved photos**.
- **Corrections.** A button on an alert, the `correct` action, or the card's ✓ / ? / It was… labels that frame. It also fixes tracking: the sighting, its photo and the last-seen time move to the right animal.
- **Pruning.** When the folder reaches its size limit, the oldest unlabelled frames go first.

## Local detector (optional)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../vision/logo-light.png">
  <img src="../vision/logo.png" alt="Neighborhood Watch vision" width="320">
</picture>

The `vision` folder holds a small service that runs [MegaDetector](https://microsoft.github.io/Biodiversity/base/models/detection/ultralytics_based/megadetectorv6/), a free camera-trap model that finds animals, people and vehicles. It can run on your own machine: a NAS, a mini PC or any Docker host. It uses an Intel GPU if there is one, otherwise the CPU. Once it's set under **Configure → Advanced → Local detector**, it looks at every frame before the AI:

- **Test only:** it's asked about every frame, and the **Detector agreement** sensor shows how often it agrees with the AI. A *miss* means the AI saw an animal and the detector didn't.
- **Skip empty frames:** when it finds no animal, the frame never reaches the AI. That saves quota on wind, cobwebs and shadows. In missing mode the AI always looks, and if the detector is down every frame goes to the AI as usual.

Once it's [trained on your photos](#training-the-detector-on-your-photos), the same model also **names** what it finds (each of your animals, Raccoon, Deer, ...), and the aim is to need the AI less and less, until not at all. **Use the detector's names** (same page) decides how its names are used:

- **Trust it first** (default): a name it has proved it gets right is used straight away, with no AI call. "Proved" means right at least 95% of the time on the held-back photos (and at least 15 times), from a line measured for each name separately; every held-back photo it wrongly gave that name to counts against it, lookalikes without a name of their own included. Priority, always-strict, long-absent and missing animals still go to the AI, and while **any** animal is in missing mode the AI looks at everything (the model could call the missing one by a lookalike's name). Below the line, the AI looks as usual.
- **Only when no AI model answers:** its name (sure or not) instead of **No answer**. A priority, always-strict, long-absent or missing animal named this way is shown but not counted until you confirm it.

A name the model gave itself is never used to train it (only your answers and the AI's are), and such sightings don't count towards the detector's agreement score.
- **Never.**

The Detector tab shows which names it's trusted with, which it's still learning, and how many sightings this week were named without an AI call.

**Running it:** copy [`vision/compose.yaml`](../vision/compose.yaml) to the machine and follow its comments: put the MegaDetector model folder next to it as `./models/megadetector` (its `.xml` and `.bin` files), point the `/dataset` line at your saved-photos folder, and either copy this repo's `vision` folder to `./app` or remove the `./app` line to use the image's own code. Start it with `docker compose up -d`, then enter its address under **Detector → Local detector**.

A new or changed address is checked when you save it: if the service doesn't answer at `<address>/health`, the form says so.

**Where to see it:** the **Detector** tab of the [Neighborhood Watch page](#the-neighborhood-watch-page) shows the detector, its training and past rounds, and has the training settings. The service has the same page of its own at its address, for example `http://your-nas:8765/`.

### Training the detector on your photos

MegaDetector was trained on trail cameras, so it misses animals on some home cameras, for example at night in infrared or through a fisheye doorbell lens. The service can learn from your own labelled photos (the integration's [saved photos](#corrections-and-saved-photos)) and gets better every week:

1. **Labelling.** Every animal photo gets its boxes from two "teachers": a general COCO YOLO11 model and the boxes the detector stored with the photo (and, for a confirmed photo neither could box, where the AI said the animal was, or else the best guess of MegaDetector or the teacher at a very low threshold: you said an animal is there, so the likeliest spot is worth learning from). The animal's box is named after what it was: your answer, or the AI's when it was sure and nobody answered. Each animal with at least 12 confirmed photos gets a name of its own; the rest are just "animal". Photos marked empty become background examples. Photos you marked "Not sure" are skipped. AI-only photos (nobody confirmed them) are used for training, but never to judge a model.
2. **Fair test.** The confirmed photos from the most recent ~20% of days are held back as the test set and never trained on. So are confirmed animal photos that no teacher could box, which are exactly the misses that matter. Confidently empty frames from those days are the empty side of the test.
3. **Training.** One YOLO11 model that both finds and names is trained on everything else: from YOLO11s the first time, then from the model being served. Each photo is trained on whole and as the tiles the service cuts it into, so animals are the size the detector sees them. "Is an animal there" is all its names' scores together, so a cat it can't tell apart is still found; which one it is has a score of its own.
4. **Promotion.** The model being served and the new one are both run on the test set the way the service runs them, at a 0.2 threshold. The new model replaces it if it **names more of the test animals right without missing more** (or raising many more false alarms), or if it **misses fewer animals** and makes **at least 10% fewer mistakes in all** (misses plus false alarms) while naming no fewer right. For each name it also measures the confidence from which that name was right at least 95% of the time (kept in `id.json` with the model): that's the line **Trust it first** uses. The model is then exported to OpenVINO and served within a minute, no restart. If it fails to load, MegaDetector is used.

It needs the saved-photos folder mounted at `/dataset` (read-only is enough), and `shm_size`, both already in [`vision/compose.yaml`](../vision/compose.yaml).

- **Settings** (Detector tab, or the service's own page; they save straight away and are kept in `work/settings.json`, the `NW_*` environment variables only set the defaults):
  - **Detects with:** GPU or CPU for checking photos (GPU when the container can see one through `/dev/dri`).
  - **Training** on or off, and when a round **Starts**: **On a schedule** (any days you pick, from an hour, the NAS's local time; a day with fewer than **New answered photos needed** is skipped) or **After new answers** (as soon as that many new photos are answered, counted every 10 minutes; the Detector tab shows how many so far). **Photos for the first round** (40), **New answered photos** (100), **Trains with** GPU or CPU, **Training length** (Quick, Normal, Thorough) and **Picture size** (Faster, Normal, Sharper: finds small animals better, trains slower and checks photos about 25% slower).
- **Schedule:** Sundays from 13:00 by default. Training runs in a separate low-priority process, so detection stays responsive, and falls back to the CPU if the GPU fails. A round takes from tens of minutes to a few hours. The first round downloads the YOLO11 weights (about 60 MB) into `work/train/weights`.
- **Now:** **Train now** (`POST /train`) starts a round (409 if one is running); **Stop training** (`POST /train/stop`) ends it without changing the model; **Undo the last round** (`POST /train/undo`) goes back to the model from before it (the last 3 trained models are kept). `GET /train` shows progress and the last round's results: misses, false alarms and agreement before and after, and whether it was promoted. `GET /train/history` lists every round, `GET`/`POST /settings` read and change the settings. Every round is kept in `work/train/history.json`, the log in `work/train/train.log`.
- **Which model is in use:** `GET /health` shows `model`, `trained`, the `names` it can give and the line each is `sure` from. `work/models/current.txt` names the trained model being served (the last 3 are kept). Delete that file to go back to MegaDetector.

## Bulk scanning (photos and videos)

Got a trail-cam card, a phone video or old recordings? Put them in the detector's scan folder, then use the card's **Scan a folder** box or the `neighborhood_watch.scan` action:

1. The detector checks every photo, and a frame every second of every video. It keeps the moments with an animal, grouped into **visits** (one per photo, or a stretch of a video), each with its best frame. Hours of footage become a short list.
2. Each visit is named like a camera's sighting, up to the limit you set: by the local model when it has proved it gets the name right, else by the AI. Scans only use free AI models, so old footage never spends paid calls or the quota live checks need.
3. A report, `neighborhood-watch-report/index.html` plus `report.csv`, is written into the scanned folder, with every visit's frame. The **Last scan** sensor and the card show the totals, and the people who get alerts get a summary.
4. Every visit appears in **To review** and joins the saved photos. Scanned footage can be old, so it never changes anyone's last-seen tracking.

## Dashboard card

The integration installs a **Neighborhood Watch** card, so there's no resource to add. It finds your devices on its own and shows:

- **To review**: sightings waiting for a ✓, ? or It was… (see [Corrections](#corrections-and-saved-photos));
- each animal's photo, where and when it was last seen, whether it's on camera now, and its Alerts, Critical and Missing mode switches. Without a recent photo it shows a face drawn from the animal's description (colours, calico, tortie, tabby, spotted, tuxedo, bicolour, colourpoint, long hair, ear tufts, folded ears, hairless, eye colour);
- wildlife, last night's summary, and each camera's last detection with ✓ / ? buttons (It was… is in To review);
- the critical-alert switches;
- **Scan a folder**: start a [bulk scan](#bulk-scanning-photos-and-videos) and see the last one's totals.

Its editor picks the sections, the devices to hide and the animals to show under Also seen, and sets the rest:

| Option | What it does (default) |
|---|---|
| `title` | The card's title (Neighborhood Watch) |
| `sections` | Which sections to show: `review`, `animals`, `wildlife`, `summary`, `recent`, `switches`, `scan` (all except `scan`, which needs the [local detector](#local-detector-optional); it also shows once a scan has run) |
| `hide` | Devices to leave out |
| `others` | Animals shown under **Also seen** instead of the main row (none). Unknown cats and dogs the AI has seen also show there |
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

### Pixel sprites

A cat can be drawn as an animated pixel sprite instead of the drawn face: pick a **Picture on the card** on its page (cats only), a ready-made coat or a common breed. To change one, or draw your own, use **Advanced: custom card picture** (in *Card picture and tips*): a small object such as `fur: "#7c7e84"` plus options like `tabby`, `tuxedo`, `fluffy`, `whitePaws`, `whiteChest`, `chestPatch`, `collar: "#3f9a55"` with `bell: true`. Extra markings go in `marks`, as runs of pixels per colour. The sprite idles (tail, breathing, blinks, ear flicks, a yawn, a paw lick) and switches to an alert pose while the animal is on camera. An animal the AI couldn't name (an Unknown Cat) shows under **Also seen**, drawn as the ready-made look nearest to the coat it described. An animal that is away lately dozes with its tail down and Zs drifting up, waking now and then: asleep half the time at first, three quarters after a month away, almost always after two months. A collar shows grey when the animal's latest photo is infrared. Wildlife rows show each kind's sprite, walking in when it was just seen. Sprites hold still when the device asks for reduced motion.

A single sprite can go anywhere, e.g. for an indoor pet:

```yaml
type: custom:neighborhood-watch-sprite
preset: brown_tabby                        # and/or appearance: { fur: "#7c7e84", tabby: true }; or kind: Raccoon
name: Pepper
size: 96
```

## Actions

| Action | What it does |
|---|---|
| `neighborhood_watch.identify` | Checks a camera now (ignoring its cooldown) and returns the result |
| `neighborhood_watch.test_sighting` | Fires a fake sighting event marked `test: yes` and sends the alerts a real one would, titled `TEST · …`; tracking is never changed |
| `neighborhood_watch.correct` | Right, wrong, or what it really was, for a sighting (by id, by camera, or the latest) |
| `neighborhood_watch.undo_last_answer` | Takes back the last Right / Not sure / Other / It was… answer: the sighting goes back to To review, and tracking and the photo follow |
| `neighborhood_watch.mark_wrong` | Removes an animal's latest counted sighting from tracking, e.g. when it was really another animal |
| `neighborhood_watch.set_reference_photo` | Uses an animal's last photo, or a fresh snapshot, as its reference photo |
| `neighborhood_watch.build_summary` | Builds the night summary now and starts a new night |
| `neighborhood_watch.scan` | Bulk-scans a folder of photos and videos on the detector's machine (see [Bulk scanning](#bulk-scanning-photos-and-videos)) |
| `neighborhood_watch.add_note` | Adds a note for the AI to a camera's view notes or an animal's tips |
| `neighborhood_watch.move_training_data` | Move saved photos: moves them into the folder set under Saved photos, e.g. after pointing it at a NAS |

## Privacy

Frames go only to the AI models you choose. Everything else stays local:

- Tracking lives in Home Assistant's `.storage` folder.
- Photos are in `config/neighborhood_watch`.
- Frames and reference photos are in the media folder.
- Notification pictures are in a random-named folder inside `config/www/neighborhood_watch`, so their links can't be guessed. Like everything in `www`, though, anyone who has a link can open it without logging in.
- Saved photos are in the folder set under Saved photos (by default the media folder's `neighborhood_watch/dataset`).

## License

MIT
