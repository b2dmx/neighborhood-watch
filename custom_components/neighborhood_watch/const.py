"""Constants."""
from __future__ import annotations

from typing import Final

from .logic import DEFAULT_INSTRUCTIONS, DEFAULT_WILDLIFE

DOMAIN: Final = "neighborhood_watch"
NAME: Final = "Neighborhood Watch"
EVENT_SIGHTING: Final = "neighborhood_watch_sighting"
TASK_NAME: Final = "Neighborhood Watch"

SUBENTRY_CAMERA: Final = "camera"
SUBENTRY_ANIMAL: Final = "animal"
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
CONF_MODE = "mode"
CONF_PUBLIC_URL = "public_url"
CONF_ALERTS = "alerts"
CONF_UNKNOWN_ALERTS = "unknown_alerts"
CONF_BUTTONS = "correction_buttons"
CONF_DATASET = "dataset"
CONF_DATASET_DIR = "dataset_dir"
CONF_DATASET_GB = "dataset_gb"
CONF_DETECTOR_URL = "detector_url"
CONF_DETECTOR_MODE = "detector_mode"
CONF_DETECTOR_MIN = "detector_min"
DETECTOR_SHADOW = "shadow"  # ask it, record the answer, change nothing
DETECTOR_SKIP = "skip_empty"  # no animal found: no AI call
DETECTOR_MODES = [DETECTOR_SHADOW, DETECTOR_SKIP]

MODE_LIVE = "live"
MODE_SHADOW_REUSE = "shadow_reuse"
MODE_SHADOW_INDEPENDENT = "shadow_independent"
MODES = [MODE_LIVE, MODE_SHADOW_REUSE, MODE_SHADOW_INDEPENDENT]

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
    CONF_MODE: MODE_LIVE,
    CONF_PUBLIC_URL: "",
    CONF_ALERTS: True,
    CONF_UNKNOWN_ALERTS: True,
    CONF_BUTTONS: True,
    CONF_DATASET: True,
    CONF_DATASET_DIR: "",
    CONF_DATASET_GB: 5,
    CONF_DETECTOR_URL: "",
    CONF_DETECTOR_MODE: DETECTOR_SHADOW,
    CONF_DETECTOR_MIN: 0.2,
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
CONF_HOUSE = "house"
CONF_INTERVAL = "check_every"
CONF_UNSURE_ALERTS = "unsure_alerts"
CONF_DETER_LIGHTS = "deterrent_lights"
CONF_DETER_MINUTES = "deterrent_minutes"
CONF_DETER_SOUND = "deterrent_sound"

# --- animal subentry ---
CONF_SPECIES = "species"
CONF_DESCRIPTION = "description"
CONF_TIPS = "tips"
CONF_PATTERN = "pattern"
CONF_BASE = "base"
CONF_PRIORITY = "priority"
CONF_ALWAYS_STRICT = "always_strict"
CONF_REFERENCE_UPLOAD = "reference_upload"
CONF_REMOVE_REFERENCE = "remove_reference"
CONF_ADDED = "added"
CONF_KNOWN_LAST_SEEN = "known_last_seen"
CONF_KNOWN_LOCATION = "known_location"
CONF_ADD_ANOTHER = "add_another"
CONF_ALERT = "alert"

# --- species (wildlife) subentry ---
CONF_ALERT_SCOPE = "alert_scope"
CONF_ALERT_CAMERAS = "alert_cameras"
CONF_CRITICAL_SWITCH = "critical_switch"
CONF_PEST = "pest"
CONF_IN_SUMMARY = "in_summary"

# --- recipient subentry ---
CONF_PERSON = "person"
CONF_NOTIFY = "notify_service"
CONF_QUIET_ZONES = "quiet_zones"
CONF_MISSING_ALERTS = "missing_alerts"
CONF_CRITICAL_SOUND = "critical_sound"
CONF_CRITICAL_VOLUME = "critical_volume"

ACTION_PREFIX = "NW"  # phone notification buttons: NW|right|<id>, NW|wrong|<id>, NW|is|<id>|<slug>
EVENT_ACTION = "mobile_app_notification_action"

STORAGE_VERSION = 1
SIGHTING_DAYS = 30
SIGHTING_MAX = 4000
COMPARISON_MAX = 200
AI_TIMEOUT = 90  # seconds per model call
SNAPSHOT_TIMEOUT = 15

# Files. Frames and reference photos live in the media folder (the AI reads attachments
# from there); notification copies go to www so phones can fetch them.
MEDIA_SUBDIR = "neighborhood_watch"
PHOTO_DIR = "neighborhood_watch"  # under /config
WWW_SUBDIR = "neighborhood_watch"

QUOTA_TZ = "America/Los_Angeles"  # Gemini free-tier quotas reset at midnight Pacific


# Common wildlife offered during setup: (alert scope, scare off, critical switch, hint for the AI)
COMMON_WILDLIFE: dict[str, tuple[str, bool, bool, str]] = {
    "Raccoon": ("any", True, True, "Ringed tail, black mask across the eyes, stocky hunched body."),
    "Opossum": ("house", True, False, "Long hairless rat-like tail, long pointed pale face, grizzled grey body."),
    "Skunk": ("any", True, False, "Black with white stripes down the back and a bushy tail."),
    "Rat": ("house", True, False, "Small rodent with a long thin tail, bigger than a mouse."),
    "Fox": ("house", False, False, ""),
    "Coyote": ("any", False, False, "Dog-like, grey-brown, bushy tail held low."),
    "Bear": ("any", False, True, ""),
    "Deer": ("never", False, False, ""),
    "Rabbit": ("never", False, False, ""),
    "Groundhog": ("never", False, False, ""),
}
DEFAULT_WILDLIFE_PICKS = ["Raccoon", "Opossum", "Skunk"]
