<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/neighborhood_watch/brand/dark_icon@2x.png">
    <img src="custom_components/neighborhood_watch/brand/icon@2x.png" width="140" alt="Neighborhood Watch logo">
  </picture>
</p>

<h1 align="center">Neighborhood Watch</h1>

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
- **Gets better:** ✓ / ✗ on every alert fixes tracking and builds training data.

## Installation

You need Home Assistant 2026.10+ and at least one **AI Task** model that accepts images (e.g. Google Generative AI).

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=b2dmx&repository=neighborhood-watch&category=integration)

1. Open in HACS (button above), download **Neighborhood Watch**, restart.
2. **Settings → Devices & services → Add integration → Neighborhood Watch**.

Manual: copy `custom_components/neighborhood_watch` into `config/custom_components` and restart.

## Onboarding

Setup walks you through it, all in the UI:

1. **AI models:** a first look, and optionally a second opinion.
2. **When to watch:** set hours, darkness, or both.
3. **Cameras:** sensors are filled in from each camera's device.
4. **Pests:** e.g. raccoon, opossum, skunk.
5. **Who gets alerts:** phones with the Home Assistant app.
6. **Your animals:** name, looks, and an optional reference photo. Skip if you only care about pests.

Add the **Neighborhood Watch** card to a dashboard. It installs itself.

## Tips

- Put the cheapest model with the biggest free allowance (e.g. Gemini Flash-Lite) first in **First look**, and a smarter one first in **Second opinion**.
- Mark a pet as **priority** to have two models double-check it. Use **Confirmed on camera** to drive a feeder or trap.
- Nothing needs answering at night: sightings wait in **To review** for 7 days.
- After "It was…", use **Add a note** so the AI learns that camera's quirks.
- Run the optional [local detector](vision) to skip empty frames and save quota.

## More

[Full reference →](docs/reference.md) (settings, entities, alerts, events, actions, card options, privacy)

---

<p align="center">
  <a href="LICENSE">MIT</a> · <a href="https://ko-fi.com/goobis2dmx">Support on Ko-fi</a><br><br>
  Neighborhood Watch v0.18.1 by Goobis<br>
  AI was used to help develop this application
</p>
