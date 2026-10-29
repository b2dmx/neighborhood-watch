"""Constants."""
from __future__ import annotations

from typing import Final

from .logic import (
    BUILTIN_MOTION,
    BUILTIN_OTHER,
    BUILTIN_PEST,
    BUILTIN_UNKNOWN,
    DEFAULT_INSTRUCTIONS,
    DEFAULT_WILDLIFE,
    EVERY,
    MEDIUM,
)

DOMAIN: Final = "neighborhood_watch"
NAME: Final = "Neighborhood Watch"
EVENT_SIGHTING: Final = "neighborhood_watch_sighting"
TASK_NAME: Final = "Neighborhood Watch"

SUBENTRY_ANIMAL: Final = "animal"
# Before 0.12 cameras, people and wildlife kinds were subentries too (see migrate.py).
SUBENTRY_CAMERA: Final = "camera"
SUBENTRY_SPECIES: Final = "species"
SUBENTRY_RECIPIENT: Final = "recipient"

# --- main entry options ---
CONF_FIRST_LOOK = "first_look"
CONF_SECOND_OPINION = "second_opinion"
CONF_PAID = "paid_models"
CONF_MAIN_START = "main_start"
CONF_MAIN_END = "main_end"
CONF_NIGHT_START = "night_start"
CONF_NIGHT_END = "night_end"
CONF_USE_DARK = "use_dark"
CONF_ALWAYS = "watch_always"
CONF_SUMMARY_TIME = "summary_time"
CONF_ON_CAMERA_MINUTES = "on_camera_minutes"
CONF_ABSENT_DAYS = "absent_days"
CONF_INSTRUCTIONS = "instructions"
CONF_EXTRA_RULES = "extra_rules"
CONF_WILDLIFE = "wildlife"
CONF_REFERENCE_PHOTOS = "reference_photos"
CONF_PUBLIC_URL = "public_url"
CONF_ALERTS = "alerts"
CONF_UNKNOWN_ALERTS = "unknown_alerts"  # before 0.7: migrated into the built-ins
CONF_MIN_CONFIDENCE = "min_confidence"
CONF_BUTTONS = "correction_buttons"
CONF_REVIEW_PAGE = "review_page"  # where an alert's "Other…" button opens
CONF_DATASET = "dataset"
CONF_DATASET_DIR = "dataset_dir"
CONF_DATASET_GB = "dataset_gb"
CONF_DETECTOR_URL = "detector_url"
CONF_DETECTOR_MODE = "detector_mode"
CONF_DETECTOR_MIN = "detector_min"
DETECTOR_SHADOW = "shadow"  # ask it, record the answer, change nothing
DETECTOR_SKIP = "skip_empty"  # no animal found: no AI call
DETECTOR_MODES = [DETECTOR_SHADOW, DETECTOR_SKIP]

# --- alert rules (animal, wildlife and built-in) ---
CONF_ALERT_AT = "alert_at"  # camera slugs; "*" = every camera; empty = never
CONF_CRITICAL_AT = "critical_at"
# The three catch-alls live in the options: "<key>_alert_at" / "<key>_critical_at".
BUILTIN_KEYS: dict[str, str] = {
    BUILTIN_PEST: "pest",
    BUILTIN_OTHER: "other",
    BUILTIN_UNKNOWN: "unknown",
    BUILTIN_MOTION: "motion",
}
CONF_PESTS = "pests"  # wildlife kinds that are pests: alert every time, scared off
BUILTIN_DEFAULTS: dict[str, tuple[list[str], list[str]]] = {
    "pest": ([EVERY], []),
    "unknown": ([EVERY], []),
    "other": ([EVERY], []),
    "motion": ([], []),
}
VISIT_SLACK_MINUTES = 10  # same visit: seen again within the camera's cooldown + this

CONF_CAMERAS = "cameras"  # {id: camera settings}
CONF_PEOPLE = "people"  # {id: person settings}

DEFAULT_OPTIONS: dict = {
    CONF_FIRST_LOOK: [],
    CONF_SECOND_OPINION: [],
    CONF_PAID: [],
    CONF_MAIN_START: "",
    CONF_MAIN_END: "",
    CONF_NIGHT_START: "20:00:00",
    CONF_NIGHT_END: "08:00:00",
    CONF_USE_DARK: True,
    CONF_ALWAYS: False,
    CONF_SUMMARY_TIME: "08:05:00",
    CONF_ON_CAMERA_MINUTES: 10,
    CONF_ABSENT_DAYS: 14,
    CONF_INSTRUCTIONS: DEFAULT_INSTRUCTIONS,
    CONF_EXTRA_RULES: "",
    CONF_WILDLIFE: list(DEFAULT_WILDLIFE),
    CONF_REFERENCE_PHOTOS: True,
    CONF_PUBLIC_URL: "",
    CONF_ALERTS: True,
    CONF_MIN_CONFIDENCE: MEDIUM,
    CONF_PESTS: [],
    CONF_BUTTONS: True,
    CONF_REVIEW_PAGE: "",
    CONF_DATASET: True,
    CONF_DATASET_DIR: "",
    CONF_DATASET_GB: 5,
    CONF_DETECTOR_URL: "",
    CONF_DETECTOR_MODE: DETECTOR_SHADOW,
    CONF_DETECTOR_MIN: 0.2,
    **{f"{k}_{CONF_ALERT_AT}": a for k, (a, _) in BUILTIN_DEFAULTS.items()},
    **{f"{k}_{CONF_CRITICAL_AT}": c for k, (_, c) in BUILTIN_DEFAULTS.items()},
}

# --- camera subentry ---
CONF_CAMERA = "camera"
CONF_NAME = "name"
CONF_SLUG = "slug"
CONF_TRIGGERS = "triggers"
CONF_SKIP_WHEN = "skip_when"
CONF_COOLDOWN = "cooldown"
CONF_PRIORITY_COOLDOWN = "priority_cooldown"
CONF_MISSING_COOLDOWN = "missing_cooldown"
CONF_REOPENED_BY = "reopened_by"
CONF_HOUSE = "house"  # before 0.7
CONF_INTERVAL = "check_every"
CONF_UNSURE_ALERTS = "unsure_alerts"  # before 0.7
CONF_AI_NOTES = "ai_notes"  # what the AI should know about this camera's view
CONF_CRITICAL_EXCEPT = "critical_except"  # lower-case names that never ring here
CONF_DETER_LIGHTS = "deterrent_lights"
CONF_DETER_MINUTES = "deterrent_minutes"
CONF_DETER_SOUND = "deterrent_sound"

# --- animal subentry ---
CONF_SPECIES = "species"
CONF_DESCRIPTION = "description"
CONF_TIPS = "tips"
CONF_PATTERN = "pattern"
CONF_BASE = "base"
CONF_APPEARANCE = "appearance"  # how the card draws the animal (its pixel sprite)
CONF_SPRITE = "sprite"  # a ready-made sprite look, by name (the card holds the looks)
SPRITE_PRESETS = (
    "calico_collar",
    "grey_chest_spot",
    "longhair_tuxedo",
    "brown_tabby",
    "charcoal_white_chest",
    "black",
    "smoky_banded_tail",
    "bicolour_mask_paw_up",
    "orange_tabby",
    "cream_tabby",
    "grey_tabby",
    "silver_tabby",
    "white",
    "russian_blue",
    "bombay",
    "british_shorthair",
    "black_white_bicolour",
    "grey_white_bicolour",
    "maine_coon",
    "siamese",
    "ragdoll",
    "birman",
    "himalayan",
    "sphynx",
    "tortoiseshell",
    "bengal",
    "abyssinian",
    "scottish_fold",
    "persian",
)
CONF_PRIORITY = "priority"
CONF_ALWAYS_STRICT = "always_strict"
CONF_REFERENCE_UPLOAD = "reference_upload"
CONF_REMOVE_REFERENCE = "remove_reference"
CONF_ADDED = "added"
CONF_KNOWN_LAST_SEEN = "known_last_seen"
CONF_KNOWN_LOCATION = "known_location"
CONF_ALERT = "alert"  # before 0.7

# --- species (wildlife) subentry ---
CONF_ALERT_SCOPE = "alert_scope"  # before 0.7
CONF_ALERT_CAMERAS = "alert_cameras"  # before 0.7
CONF_CRITICAL_SWITCH = "critical_switch"  # before 0.7
CONF_PEST = "pest"

# --- recipient subentry ---
CONF_PERSON = "person"
CONF_NOTIFY = "notify_service"
CONF_QUIET_ZONES = "quiet_zones"
CONF_MISSING_ALERTS = "missing_alerts"
CONF_CRITICAL_SOUND = "critical_sound"
CONF_CRITICAL_VOLUME = "critical_volume"
CONF_MUTED = "muted"  # names this person never hears about (missing mode still does)
CONF_QUIET_MODE = "quiet_mode"  # in a quiet zone or quiet hours: critical → normal, or none
CONF_QUIET_START = "quiet_start"
CONF_QUIET_END = "quiet_end"
QUIET_NORMAL = "normal"
QUIET_NONE = "none"

ACTION_PREFIX = "NW"  # phone notification buttons: NW|right|<id>, NW|wrong|<id>, NW|is|<id>|<slug>
EVENT_ACTION = "mobile_app_notification_action"

STORAGE_VERSION = 1
SIGHTING_DAYS = 30
SIGHTING_MAX = 4000
AI_TIMEOUT = 30  # seconds per model call; a picture normally takes 5-15
SNAPSHOT_TIMEOUT = 15

# Files. Frames and reference photos live in the media folder (the AI reads attachments
# from there); notification copies go to www so phones can fetch them.
MEDIA_SUBDIR = "neighborhood_watch"
PHOTO_DIR = "neighborhood_watch"  # under /config
WWW_SUBDIR = "neighborhood_watch"

QUOTA_TZ = "America/Los_Angeles"  # Gemini free-tier quotas reset at midnight Pacific


# Common wildlife offered during setup: (unused, pest, hint for the AI). Where each group
# alerts is set under Configure (pests, other wildlife, unknown animals), not per kind.
COMMON_WILDLIFE: dict[str, tuple[bool, bool, str]] = {
    "Raccoon": (True, True, "Ringed tail, black mask across the eyes, stocky hunched body."),
    "Opossum": (True, True, "Long hairless rat-like tail, long pointed pale face, grizzled grey body."),
    "Skunk": (True, True, "Black with white stripes down the back and a bushy tail."),
    "Rat": (True, True, "Small rodent with a long thin tail, bigger than a mouse."),
    "Mouse": (True, True, "Tiny rodent with big round ears and a long thin tail, much smaller than a rat."),
    "Fox": (True, False, ""),
    "Coyote": (True, False, "Dog-like, grey-brown, bushy tail held low."),
    "Bear": (True, False, ""),
    "Deer": (False, False, ""),
    "Rabbit": (False, False, ""),
    "Groundhog": (False, False, ""),
}
DEFAULT_WILDLIFE_PICKS = ["Raccoon", "Opossum", "Skunk"]
