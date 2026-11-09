<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.png">
    <img src="docs/logo.png" height="150" alt="Neighborhood Watch">
  </picture>
</p>

<p align="center"><b>Know which animal is outside, using the cameras you already have.</b></p>

<p align="center">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5?logo=homeassistantcommunitystore&logoColor=white" alt="HACS Custom"></a>
  <a href="https://ko-fi.com/goobis2dmx"><img src="https://img.shields.io/badge/Ko--fi-Support-FF5E5B?logo=kofi&logoColor=white" alt="Support on Ko-fi"></a>
</p>

A Home Assistant integration. When a camera sees motion, an AI model looks at the frame and tells you what's there: *a raccoon at the bins*, *Milo at the back door*.

## What it does

- **Names every animal** on any camera Home Assistant can show. No NVR or subscription; Gemini's free tier covers a typical night.
- **Tracks your own animals:** last seen, where, and the exact photo.
- **Missing mode:** any possible sighting of a lost pet sends a critical alert.
- **Scares pests off** with lights or a sound.
- **Gets better:** ✓ Right, ? Not sure or Other on every alert fixes tracking and labels the saved photo.

## Installation

You need Home Assistant 2026.10+ and at least one **AI Task** model that accepts images (e.g. Google Generative AI).

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=b2dmx&repository=neighborhood-watch&category=integration)

1. Open in HACS (button above), download **Neighborhood Watch**, restart.
2. **Settings → Devices & services → Add integration → Neighborhood Watch**.

Manual: copy `custom_components/neighborhood_watch` into `config/custom_components` and restart.

## Onboarding

Setup walks you through it, all in the UI:

1. **AI models:** a first look, and a second opinion (needed for priority and strict animals).
2. **When to watch:** set hours, darkness, or both.
3. **Pests:** e.g. raccoon, opossum, skunk.
4. **Cameras:** sensors are filled in from each camera's device.
5. **Who gets alerts:** phones with the Home Assistant app.
6. **Your animals:** name, looks, and an optional reference photo. Leave the name empty and submit when you're done, or straight away if you only care about pests.

Add the **Neighborhood Watch** card to a dashboard. It installs itself.

A **Neighborhood Watch** page also appears in the sidebar (admins only): **Review** for the sightings waiting for an answer, then a tab per topic (Cameras, Animals, People, Alerts, AI, Detector) with every setting.

## Check it works

**Developer tools → Actions → Neighborhood Watch: Test sighting**: pick a camera and an animal. It sends the alerts a real sighting would, titled TEST, and never changes tracking. Real checks only run in watch hours: by default 20:00 to 08:00, and whenever it's dark (change it under **When to watch**).

## Tips

- Put the cheapest model with the biggest free allowance (e.g. Gemini Flash-Lite) first in **First look**, and a smarter one first in **Second opinion**.
- Mark a pet as **priority** to have two models double-check it. Use **Confirmed on camera** to drive a feeder or trap.
- Nothing needs answering at night: sightings wait in **To review** for 7 days.
- After "It was…", use **Add a note** so the AI learns that camera's quirks.
- Run the optional [local detector](docs/reference.md#local-detector-optional) to skip empty frames and save quota.

## More

[Full reference →](docs/reference.md) (settings, entities, alerts, events, actions, card options, privacy)

## License

The code is [MIT](LICENSE). The Neighborhood Watch logo and icons (`brand-source/`, `vision/`, the `brand/` folder in the integration, and the logo images in `docs/`) are © Goobis, all rights reserved. They are not covered by the MIT license, so please don't reuse them without permission.

---

<p align="center">
  <a href="LICENSE">MIT</a> · <a href="https://ko-fi.com/goobis2dmx">Support on Ko-fi</a><br><br>
  Neighborhood Watch v0.24.0 by Goobis<br>
  AI was used to help develop this application
</p>
