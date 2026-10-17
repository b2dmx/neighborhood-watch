"""Onboarding, settings (Configure), and the Add animal / Edit animal flows.

Onboarding walks through: which AI models look (and in what order), when to watch, which
cameras (each pre-filled from its own device's motion and person/vehicle sensors), then the
animals one by one. Animals are subentries (each a device with its own page); cameras and
people are managed under Configure → Cameras / People.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

try:  # Home Assistant 2026.10 replaced voluptuous with probatio (same API).
    import probatio as vol
except ImportError:  # pragma: no cover - older Home Assistant
    import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    DateTimeSelector,
    EntitySelector,
    EntitySelectorConfig,
    FileSelector,
    FileSelectorConfig,
    MediaSelector,
    MediaSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TimeSelector,
)
from homeassistant.util import dt as dt_util, slugify

from . import logic
from .const import (
    BUILTIN_KEYS,
    CONF_ALERT_AT,
    CONF_ALERTS,
    CONF_BUTTONS,
    CONF_CRITICAL_AT,
    CONF_CRITICAL_EXCEPT,
    CONF_CRITICAL_SOUND,
    CONF_CRITICAL_VOLUME,
    CONF_DATASET,
    CONF_DATASET_DIR,
    CONF_DATASET_GB,
    CONF_DETECTOR_MIN,
    CONF_DETECTOR_MODE,
    CONF_DETECTOR_URL,
    DETECTOR_MODES,
    CONF_DETER_LIGHTS,
    CONF_DETER_MINUTES,
    CONF_DETER_SOUND,
    CONF_AI_NOTES,
    CONF_ALWAYS,
    COMMON_WILDLIFE,
    DEFAULT_WILDLIFE_PICKS,
    CONF_INTERVAL,
    CONF_MIN_CONFIDENCE,
    CONF_MISSING_ALERTS,
    CONF_MUTED,
    CONF_NOTIFY,
    CONF_PERSON,
    CONF_PESTS,
    CONF_CAMERAS,
    CONF_PEOPLE,
    CONF_QUIET_END,
    CONF_QUIET_MODE,
    CONF_QUIET_START,
    CONF_QUIET_ZONES,
    QUIET_NONE,
    QUIET_NORMAL,
    CONF_ABSENT_DAYS,
    CONF_ADDED,
    CONF_ALWAYS_STRICT,
    CONF_BASE,
    CONF_CAMERA,
    CONF_COOLDOWN,
    CONF_DESCRIPTION,
    CONF_EXTRA_RULES,
    CONF_FIRST_LOOK,
    CONF_INSTRUCTIONS,
    CONF_MAIN_END,
    CONF_MAIN_START,
    CONF_KNOWN_LAST_SEEN,
    CONF_KNOWN_LOCATION,
    CONF_MISSING_COOLDOWN,
    CONF_NAME,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_ON_CAMERA_MINUTES,
    CONF_PAID,
    CONF_PATTERN,
    CONF_PRIORITY,
    CONF_PRIORITY_COOLDOWN,
    CONF_PUBLIC_URL,
    CONF_REFERENCE_PHOTOS,
    CONF_REFERENCE_UPLOAD,
    CONF_REMOVE_REFERENCE,
    CONF_REOPENED_BY,
    CONF_SECOND_OPINION,
    CONF_SKIP_WHEN,
    CONF_SLUG,
    CONF_SPECIES,
    CONF_SUMMARY_TIME,
    CONF_TIPS,
    CONF_TRIGGERS,
    CONF_USE_DARK,
    CONF_WILDLIFE,
    DEFAULT_OPTIONS,
    DOMAIN,
    NAME,
    SUBENTRY_ANIMAL,
)
from .engine import remove_file, reference_file, save_reference

# --- selectors ------------------------------------------------------------------------

AI_MODELS = EntitySelector(EntitySelectorConfig(domain="ai_task", multiple=True, reorder=True))
CAMERA = EntitySelector(EntitySelectorConfig(domain="camera"))
CAMERAS = EntitySelector(EntitySelectorConfig(domain="camera", multiple=True))
BINARY_SENSORS = EntitySelector(
    EntitySelectorConfig(domain=["binary_sensor", "input_boolean"], multiple=True)
)
LONG_TEXT = TextSelector(TextSelectorConfig(multiline=True))
TEXT = TextSelector()
TIME = TimeSelector()
BOOL = BooleanSelector()
PATTERN = SelectSelector(
    SelectSelectorConfig(options=list(logic.PATTERNS), translation_key="pattern")
)
BASE = SelectSelector(SelectSelectorConfig(options=list(logic.BASES), translation_key="base"))
WILDLIFE = SelectSelector(
    SelectSelectorConfig(options=list(logic.DEFAULT_WILDLIFE), multiple=True, custom_value=True)
)
PHOTO = FileSelector(FileSelectorConfig(accept="image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif"))
LIGHTS = EntitySelector(EntitySelectorConfig(domain=["light", "switch"], multiple=True))
SOUND = MediaSelector(MediaSelectorConfig())
PERSON = EntitySelector(EntitySelectorConfig(domain="person"))
ZONES = EntitySelector(EntitySelectorConfig(domain="zone", multiple=True))
CONFIDENCE = SelectSelector(
    SelectSelectorConfig(options=[logic.LOW, logic.MEDIUM, logic.HIGH], translation_key="confidence")
)
PESTS = SelectSelector(
    SelectSelectorConfig(
        options=[n for n, (_, pest, _) in COMMON_WILDLIFE.items() if pest] + ["Coyote", "Bear"],
        multiple=True,
        custom_value=True,
    )
)
QUIET_MODE = SelectSelector(
    SelectSelectorConfig(options=[QUIET_NORMAL, QUIET_NONE], translation_key="quiet_mode")
)
VOLUME = NumberSelector(
    NumberSelectorConfig(min=0.1, max=1, step=0.1, mode=NumberSelectorMode.SLIDER)
)


def _minutes(high: float) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=0, max=high, step=0.5, unit_of_measurement="min", mode=NumberSelectorMode.BOX
        )
    )


def _number(low: float, high: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=low, max=high, step=1, unit_of_measurement=unit, mode=NumberSelectorMode.BOX)
    )


# --- shared schema pieces ----------------------------------------------------------------

# Collapsible groups on the long forms. Only the form is grouped: the values are flattened
# again before they are checked and saved, so stored settings keep their flat keys.
CAMERA_SECTIONS = ("timing", "critical_alerts", "deterrent")
PERSON_SECTIONS = ("critical_alerts", "mutes", "quiet")
ANIMAL_SECTIONS = ("looks", "rules", "last_seen")
MODELS_SECTIONS = ("first_model_hours",)
SCHEDULE_SECTIONS = ("more",)
CATCH_ALL_SECTIONS = tuple(BUILTIN_KEYS.values())  # pest, other, unknown, motion


def _section(fields: dict[Any, Any], collapsed: bool = True) -> section:
    return section(vol.Schema(fields), {"collapsed": collapsed})


def _flatten(user_input: Mapping[str, Any], sections: tuple[str, ...]) -> dict[str, Any]:
    """A form's answers with its sections' fields lifted back to the top level."""
    flat = {k: v for k, v in user_input.items() if k not in sections}
    for key in sections:
        flat.update(user_input.get(key) or {})
    return flat


def _where(cameras: list[SelectOptionDict]) -> SelectSelector:
    """A camera picker for Alert at / Critical at: "All cameras" or chosen ones; empty = none."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=logic.EVERY, label="All cameras"), *cameras],
            multiple=True,
        )
    )


def _names(names: list[str]) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=names, multiple=True, custom_value=True))


def _alert_fields(values: Mapping[str, Any], cameras: list[SelectOptionDict], prefix: str = "") -> dict:
    return {
        vol.Optional(f"{prefix}{CONF_ALERT_AT}", default=list(values.get(f"{prefix}{CONF_ALERT_AT}", [logic.EVERY]))): _where(cameras),
        vol.Optional(f"{prefix}{CONF_CRITICAL_AT}", default=list(values.get(f"{prefix}{CONF_CRITICAL_AT}", []))): _where(cameras),
    }


def _alert_data(user_input: Mapping[str, Any], prefix: str = "") -> dict[str, list[str]]:
    """Critical overrides "don't alert", so its cameras are also alert cameras."""
    alert = list(user_input.get(f"{prefix}{CONF_ALERT_AT}") or [])
    critical = list(user_input.get(f"{prefix}{CONF_CRITICAL_AT}") or [])
    if logic.EVERY in critical:
        critical = [logic.EVERY]
    if logic.EVERY in alert or logic.EVERY in critical:
        alert = [logic.EVERY]
    else:
        alert += [c for c in critical if c not in alert]
    return {f"{prefix}{CONF_ALERT_AT}": alert, f"{prefix}{CONF_CRITICAL_AT}": critical}


NEW = "add_new"  # the "+ Add ..." choice; camera and person ids are uuid hex


def _camera_label(hass: HomeAssistant, camera: str) -> str:
    """A camera entity's friendly name, or its entity id when it has no state."""
    state = hass.states.get(camera)
    return state.name if state else camera


def _camera_options(entry: ConfigEntry, exclude: str | None = None) -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=d[CONF_SLUG], label=d[CONF_NAME])
        for cid, d in (entry.options.get(CONF_CAMERAS) or {}).items()
        if cid != exclude
    ]


def _subjects(entry: ConfigEntry) -> list[str]:
    """Everything an alert can be about: named animals, pests and the catch-alls."""
    named = [
        s.data.get(CONF_NAME) or s.title
        for s in entry.subentries.values()
        if s.subentry_type == SUBENTRY_ANIMAL
    ]
    pests = [p for p in entry.options.get(CONF_PESTS) or [] if p.lower() not in {n.lower() for n in named}]
    return [*named, *pests, *BUILTIN_KEYS]


def _pick(values: list[str], names: list[str]) -> list[str]:
    """Stored lower-case names shown with their proper spelling."""
    by_key = {n.lower(): n for n in names}
    return [by_key.get(v.lower(), v) for v in values]


def _models_schema(values: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_FIRST_LOOK, default=values.get(CONF_FIRST_LOOK, [])): AI_MODELS,
            vol.Optional(CONF_SECOND_OPINION, default=values.get(CONF_SECOND_OPINION, [])): AI_MODELS,
            vol.Optional(CONF_PAID, default=values.get(CONF_PAID, [])): AI_MODELS,
            vol.Required("first_model_hours", default={}): _section(
                {
                    vol.Optional(
                        CONF_MAIN_START, description={"suggested_value": values.get(CONF_MAIN_START) or None}
                    ): TIME,
                    vol.Optional(
                        CONF_MAIN_END, description={"suggested_value": values.get(CONF_MAIN_END) or None}
                    ): TIME,
                }
            ),
        }
    )


def _schedule_schema(values: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NIGHT_START, default=values[CONF_NIGHT_START]): TIME,
            vol.Required(CONF_NIGHT_END, default=values[CONF_NIGHT_END]): TIME,
            vol.Required(CONF_USE_DARK, default=values[CONF_USE_DARK]): BOOL,
            vol.Required(CONF_ALWAYS, default=values.get(CONF_ALWAYS, False)): BOOL,
            vol.Required("more", default={}): _section(
                {
                    vol.Required(CONF_SUMMARY_TIME, default=values[CONF_SUMMARY_TIME]): TIME,
                    vol.Required(CONF_ON_CAMERA_MINUTES, default=values[CONF_ON_CAMERA_MINUTES]): _number(1, 120, "min"),
                }
            ),
        }
    )


def _validate_models(user_input: Mapping[str, Any]) -> dict[str, str]:
    if not user_input.get(CONF_FIRST_LOOK):
        return {CONF_FIRST_LOOK: "first_look_required"}
    return {}


def suggest_camera(hass: HomeAssistant, camera_entity: str) -> dict[str, Any]:
    """Name and sensors from the camera's own device: motion/animal sensors trigger a
    check; person/vehicle sensors skip it."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    entry = ent_reg.async_get(camera_entity)
    name = ""
    triggers: list[str] = []
    skip: list[str] = []
    if entry and entry.device_id:
        device = dev_reg.async_get(entry.device_id)
        if device:
            name = device.name_by_user or device.name or ""
        for other in er.async_entries_for_device(ent_reg, entry.device_id):
            if other.domain != "binary_sensor" or other.disabled_by:
                continue
            label = f"{other.entity_id} {other.original_name or ''} {other.translation_key or ''}".lower()
            if "person" in label or "vehicle" in label:
                skip.append(other.entity_id)
            elif "animal" in label or "motion" in label:
                triggers.append(other.entity_id)
    if not name:
        state = hass.states.get(camera_entity)
        name = state.name if state else camera_entity.split(".", 1)[1].replace("_", " ").title()
    return {CONF_NAME: name, CONF_TRIGGERS: triggers, CONF_SKIP_WHEN: skip}


def _sensors_note(values: Mapping[str, Any]) -> dict[str, str]:
    """The camera page's {sensors} placeholder: a hint when nothing starts a check."""
    if values.get(CONF_TRIGGERS) or values.get(CONF_INTERVAL):
        return {"sensors": ""}
    return {
        "sensors": "\n\nNo motion or animal sensors were found for this camera. Pick one below, "
        "or open Timing and set Also check every so it is checked on a timer."
    }


def _camera_schema(
    values: Mapping[str, Any],
    others: list[SelectOptionDict],
    include_camera: bool,
    subjects: list[str] | None = None,
) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if include_camera:
        fields[vol.Required(CONF_CAMERA, default=values.get(CONF_CAMERA))] = CAMERA
    fields.update(
        {
            vol.Required(CONF_NAME, default=values.get(CONF_NAME, "")): TEXT,
            vol.Optional(CONF_TRIGGERS, default=values.get(CONF_TRIGGERS, [])): BINARY_SENSORS,
            vol.Optional(CONF_SKIP_WHEN, default=values.get(CONF_SKIP_WHEN, [])): BINARY_SENSORS,
            vol.Optional(
                CONF_AI_NOTES, description={"suggested_value": values.get(CONF_AI_NOTES) or None}
            ): LONG_TEXT,
        }
    )
    timing: dict[Any, Any] = {
        vol.Required(CONF_COOLDOWN, default=values.get(CONF_COOLDOWN, 3)): _minutes(120),
        vol.Required(CONF_PRIORITY_COOLDOWN, default=values.get(CONF_PRIORITY_COOLDOWN, 0)): _minutes(240),
        vol.Required(CONF_MISSING_COOLDOWN, default=values.get(CONF_MISSING_COOLDOWN, 0)): _minutes(60),
    }
    if others:
        timing[vol.Optional(CONF_REOPENED_BY, default=values.get(CONF_REOPENED_BY, []))] = SelectSelector(
            SelectSelectorConfig(options=others, multiple=True)
        )
    timing[vol.Required(CONF_INTERVAL, default=values.get(CONF_INTERVAL, 0))] = _minutes(240)
    fields[vol.Required("timing", default={})] = _section(timing)
    if subjects:
        fields[vol.Required("critical_alerts", default={})] = _section(
            {
                vol.Optional(
                    CONF_CRITICAL_EXCEPT,
                    default=_pick(list(values.get(CONF_CRITICAL_EXCEPT) or []), subjects),
                ): _names(subjects)
            }
        )
    fields[vol.Required("deterrent", default={})] = _section(
        {
            vol.Optional(CONF_DETER_LIGHTS, default=values.get(CONF_DETER_LIGHTS, [])): LIGHTS,
            vol.Required(CONF_DETER_MINUTES, default=values.get(CONF_DETER_MINUTES, 2)): _minutes(60),
            vol.Optional(
                CONF_DETER_SOUND, description={"suggested_value": values.get(CONF_DETER_SOUND)}
            ): SOUND,
        }
    )
    return vol.Schema(fields)


def _camera_data(user_input: Mapping[str, Any], slug: str, camera: str) -> dict[str, Any]:
    return {
        CONF_CAMERA: camera,
        CONF_NAME: user_input[CONF_NAME].strip(),
        CONF_SLUG: slug,
        CONF_TRIGGERS: user_input.get(CONF_TRIGGERS, []),
        CONF_SKIP_WHEN: user_input.get(CONF_SKIP_WHEN, []),
        CONF_COOLDOWN: user_input.get(CONF_COOLDOWN, 3),
        CONF_PRIORITY_COOLDOWN: user_input.get(CONF_PRIORITY_COOLDOWN, 0),
        CONF_MISSING_COOLDOWN: user_input.get(CONF_MISSING_COOLDOWN, 0),
        CONF_REOPENED_BY: user_input.get(CONF_REOPENED_BY, []),
        CONF_INTERVAL: user_input.get(CONF_INTERVAL, 0),
        CONF_CRITICAL_EXCEPT: [n.lower() for n in user_input.get(CONF_CRITICAL_EXCEPT) or []],
        CONF_AI_NOTES: (user_input.get(CONF_AI_NOTES) or "").strip(),
        CONF_DETER_LIGHTS: user_input.get(CONF_DETER_LIGHTS, []),
        CONF_DETER_MINUTES: user_input.get(CONF_DETER_MINUTES, 2),
        CONF_DETER_SOUND: user_input.get(CONF_DETER_SOUND) or None,
    }


def _species(known: list[str]) -> SelectSelector:
    """Cat, Dog and any kind already in use; any other kind can be typed."""
    kinds = list(dict.fromkeys(["cat", "dog", *(k.strip().lower() for k in known if k and k.strip())]))
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=k, label=k.title()) for k in kinds],
            custom_value=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _animal_schema(
    values: Mapping[str, Any],
    cameras: list[SelectOptionDict],
    *,
    reconfigure: bool,
    onboarding: bool,
    species: list[str] | None = None,
) -> vol.Schema:
    """Name, kind, look and photo up top; the finer points in sections. In onboarding the
    name may be left empty, which finishes the step."""
    kind = (values.get(CONF_SPECIES) or "cat").strip().lower()
    fields: dict[Any, Any] = {
        (vol.Optional if onboarding else vol.Required)(
            CONF_NAME, description={"suggested_value": values.get(CONF_NAME)}
        ): TEXT,
        vol.Required(CONF_SPECIES, default=kind): _species([*(species or []), kind]),
        vol.Optional(CONF_DESCRIPTION, description={"suggested_value": values.get(CONF_DESCRIPTION)}): LONG_TEXT,
        vol.Optional(CONF_REFERENCE_UPLOAD): PHOTO,
    }
    if reconfigure:
        fields[vol.Required(CONF_REMOVE_REFERENCE, default=False)] = BOOL
    fields[vol.Required("looks", default={})] = _section(
        {
            vol.Required(CONF_PATTERN, default=values.get(CONF_PATTERN, "na")): PATTERN,
            vol.Required(CONF_BASE, default=values.get(CONF_BASE, "na")): BASE,
            vol.Optional(CONF_TIPS, description={"suggested_value": values.get(CONF_TIPS)}): LONG_TEXT,
        }
    )
    fields[vol.Required("rules", default={})] = _section(
        {
            vol.Required(CONF_PRIORITY, default=values.get(CONF_PRIORITY, False)): BOOL,
            vol.Required(CONF_ALWAYS_STRICT, default=values.get(CONF_ALWAYS_STRICT, False)): BOOL,
            **_alert_fields(values, cameras),
        }
    )
    fields[vol.Required("last_seen", default={})] = _section(
        {
            vol.Optional(
                CONF_KNOWN_LAST_SEEN, description={"suggested_value": values.get(CONF_KNOWN_LAST_SEEN)}
            ): DateTimeSelector(),
            vol.Optional(
                CONF_KNOWN_LOCATION, description={"suggested_value": values.get(CONF_KNOWN_LOCATION)}
            ): TEXT,
        }
    )
    return vol.Schema(fields)


def _animal_data(user_input: Mapping[str, Any], slug: str, added: str) -> dict[str, Any]:
    return {
        CONF_NAME: user_input[CONF_NAME].strip(),
        CONF_SLUG: slug,
        CONF_SPECIES: (user_input.get(CONF_SPECIES) or "").strip().lower() or "cat",
        CONF_DESCRIPTION: (user_input.get(CONF_DESCRIPTION) or "").strip(),
        CONF_TIPS: (user_input.get(CONF_TIPS) or "").strip(),
        CONF_PATTERN: user_input.get(CONF_PATTERN, "na"),
        CONF_BASE: user_input.get(CONF_BASE, "na"),
        CONF_PRIORITY: bool(user_input.get(CONF_PRIORITY)),
        CONF_ALWAYS_STRICT: bool(user_input.get(CONF_ALWAYS_STRICT)),
        **_alert_data(user_input),
        CONF_ADDED: added,
        CONF_KNOWN_LAST_SEEN: user_input.get(CONF_KNOWN_LAST_SEEN) or None,
        CONF_KNOWN_LOCATION: (user_input.get(CONF_KNOWN_LOCATION) or "").strip() or None,
    }


async def _store_upload(hass: HomeAssistant, file_id: str, slug: str) -> dict[str, str]:
    """Save an uploaded reference photo; an error for the form if it can't be read (an
    iPhone HEIC photo, for example)."""
    from homeassistant.components.file_upload import process_uploaded_file  # noqa: PLC0415

    def _process() -> None:
        with process_uploaded_file(hass, file_id) as path:
            save_reference(path, reference_file(hass, slug))

    try:
        await hass.async_add_executor_job(_process)
    except Exception:  # noqa: BLE001 - any unreadable picture
        return {CONF_REFERENCE_UPLOAD: "photo_unreadable"}
    return {}


def _validate_animal(user_input: Mapping[str, Any], taken: set[str]) -> dict[str, str]:
    name = (user_input.get(CONF_NAME) or "").strip()
    if not name:
        return {CONF_NAME: "name_required"}
    if name.lower() in taken:
        return {CONF_NAME: "name_taken"}
    if logic.is_unknown(name) or name.lower() in ("none", "nothing"):
        return {CONF_NAME: "name_reserved"}
    if "|" in name:
        return {CONF_NAME: "name_pipe"}
    if user_input.get(CONF_PRIORITY) and user_input.get(CONF_ALWAYS_STRICT):
        return {CONF_ALWAYS_STRICT: "priority_not_strict"}
    return {}


# --- onboarding ----------------------------------------------------------------------------


class NeighborhoodWatchConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 3

    def __init__(self) -> None:
        self._options: dict[str, Any] = dict(DEFAULT_OPTIONS)
        self._pending_cameras: list[str] = []
        self._camera_values: dict[str, Any] = {}
        self._cameras: dict[str, dict[str, Any]] = {}
        self._people: dict[str, dict[str, Any]] = {}
        self._animals: list[ConfigSubentryData] = []
        self._phones_found = False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return WatchOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ANIMAL: AnimalSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not self.hass.states.async_entity_ids("ai_task"):
            return self.async_abort(reason="no_ai_task")
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _flatten(user_input, MODELS_SECTIONS)
            errors = _validate_models(user_input)
            if not errors:
                self._options.update(user_input)
                return await self.async_step_schedule()
        return self.async_show_form(
            step_id="user",
            data_schema=_models_schema(user_input or self._options),
            errors=errors,
        )

    async def async_step_schedule(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._options.update(_flatten(user_input, SCHEDULE_SECTIONS))
            return await self.async_step_cameras()
        return self.async_show_form(step_id="schedule", data_schema=_schedule_schema(self._options))

    async def async_step_cameras(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._pending_cameras = list(user_input.get("cameras", []))
            return await self.async_step_camera()
        return self.async_show_form(
            step_id="cameras",
            data_schema=vol.Schema({vol.Optional("cameras", default=[]): CAMERAS}),
        )

    async def async_step_camera(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm one camera at a time, pre-filled from its device."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _flatten(user_input, CAMERA_SECTIONS)
            camera = self._camera_values[CONF_CAMERA]
            slug = slugify(user_input[CONF_NAME])
            taken = {c[CONF_SLUG] for c in self._cameras.values()}
            if not slug:
                errors[CONF_NAME] = "name_required"
            elif slug in taken:
                errors[CONF_NAME] = "name_taken"
            else:
                self._cameras[uuid4().hex] = _camera_data(user_input, slug, camera)
                self._camera_values = {}
                user_input = None
        if not errors:
            if not self._pending_cameras:
                return await self.async_step_wildlife()
            camera = self._pending_cameras.pop(0)
            self._camera_values = {CONF_CAMERA: camera, **suggest_camera(self.hass, camera)}
        others = self._onboarding_cameras()
        return self.async_show_form(
            step_id="camera",
            data_schema=_camera_schema(user_input or self._camera_values, others, include_camera=False),
            description_placeholders={
                "camera": _camera_label(self.hass, self._camera_values[CONF_CAMERA]),
                **_sensors_note(self._camera_values),
            },
            errors=errors,
        )

    async def async_step_wildlife(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Which animals are pests. Every other animal is tracked as wildlife without setup."""
        if user_input is not None:
            self._options[CONF_PESTS] = [p.strip().title() for p in user_input.get(CONF_PESTS, []) if p.strip()]
            return await self.async_step_people()
        return self.async_show_form(
            step_id="wildlife",
            data_schema=vol.Schema({vol.Optional(CONF_PESTS, default=DEFAULT_WILDLIFE_PICKS): PESTS}),
        )

    async def async_step_people(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Phones that get alerts. Quiet zones and sounds are set per person later, under
        Configure → People."""
        phones = [s for s in _notify_services(self.hass) if s.startswith("mobile_app_")]
        self._phones_found = bool(phones)
        names = _phone_names(self.hass)
        if user_input is not None or not phones:
            for service in (user_input or {}).get("phones", []):
                name = names.get(service) or service.removeprefix("mobile_app_").replace("_", " ").title()
                self._people[uuid4().hex] = _recipient_data(
                    {CONF_NAME: name, CONF_NOTIFY: service, CONF_MISSING_ALERTS: True}
                )
            return await self.async_step_animal()
        return self.async_show_form(
            step_id="people",
            data_schema=vol.Schema(
                {
                    vol.Optional("phones", default=phones): SelectSelector(
                        SelectSelectorConfig(
                            options=[SelectOptionDict(value=s, label=names.get(s, s)) for s in phones],
                            multiple=True,
                        )
                    )
                }
            ),
        )

    async def async_step_animal(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add animals one by one; leave the name empty to finish."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _flatten(user_input, ANIMAL_SECTIONS)
            if not (user_input.get(CONF_NAME) or "").strip():
                return self._finish()
            taken = {a["data"][CONF_NAME].lower() for a in self._animals}
            errors = _validate_animal(user_input, taken)
            if not errors:
                slug = slugify(user_input[CONF_NAME])
                if (upload := user_input.get(CONF_REFERENCE_UPLOAD)) and (
                    errors := await _store_upload(self.hass, upload, slug)
                ):
                    return self.async_show_form(
                        step_id="animal",
                        data_schema=_animal_schema(
                            user_input, self._onboarding_cameras(), reconfigure=False, onboarding=True,
                            species=self._species(),
                        ),
                        description_placeholders=self._animal_placeholders(),
                        errors=errors,
                    )
                self._animals.append(
                    ConfigSubentryData(
                        data=_animal_data(user_input, slug, dt_util.utcnow().isoformat()),
                        subentry_type=SUBENTRY_ANIMAL,
                        title=user_input[CONF_NAME].strip(),
                        unique_id=user_input[CONF_NAME].strip().lower(),
                    )
                )
                user_input = None
        return self.async_show_form(
            step_id="animal",
            data_schema=_animal_schema(
                user_input or {}, self._onboarding_cameras(), reconfigure=False, onboarding=True,
                species=self._species(),
            ),
            description_placeholders=self._animal_placeholders(),
            errors=errors,
        )

    def _animal_placeholders(self) -> dict[str, str]:
        """The animal step is the last one, so it also says what was left out earlier."""
        notes = []
        if not self._cameras:
            notes.append("No cameras were picked, so no checks run until one is added under Configure → Cameras.")
        if not self._people:
            notes.append(
                ("Nobody was picked to get alerts." if self._phones_found else
                 "No phones with the Home Assistant app were found, so nobody gets alerts yet.")
                + " Add people later under Configure → People."
            )
        return {"count": str(len(self._animals)), "notes": " ".join(notes)}

    def _species(self) -> list[str]:
        return [a["data"].get(CONF_SPECIES) or "" for a in self._animals]

    def _onboarding_cameras(self) -> list[SelectOptionDict]:
        return [SelectOptionDict(value=c[CONF_SLUG], label=c[CONF_NAME]) for c in self._cameras.values()]

    def _finish(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=NAME,
            data={},
            options={**self._options, CONF_CAMERAS: self._cameras, CONF_PEOPLE: self._people},
            subentries=self._animals,
        )


# --- settings --------------------------------------------------------------------------------


class WatchOptionsFlow(OptionsFlow):
    @property
    def _values(self) -> dict[str, Any]:
        return {**DEFAULT_OPTIONS, **self.config_entry.options}

    def _save(self, user_input: Mapping[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self._values, **user_input})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "cameras",
                "people",
                "alerts",
                "catch_alls",
                "schedule",
                "models",
                # The advanced ones last (their labels say so).
                "identification",
                "training",
                "detector",
                "notifications",
            ],
        )

    # --- cameras -------------------------------------------------------------------------

    async def async_step_cameras(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a camera to edit, or add one."""
        cams = self._values.get(CONF_CAMERAS) or {}
        if user_input is not None:
            self._editing = None if user_input["camera"] == NEW else user_input["camera"]
            return await (self.async_step_camera_add() if self._editing is None else self.async_step_camera())
        choices = [
            SelectOptionDict(value=cid, label=d[CONF_NAME])
            for cid, d in sorted(cams.items(), key=lambda kv: kv[1][CONF_NAME].lower())
        ] + [SelectOptionDict(value=NEW, label="+ Add a camera")]  # label: fallback for the translation
        return self.async_show_form(
            step_id="cameras",
            data_schema=vol.Schema(
                {
                    vol.Required("camera"): SelectSelector(
                        SelectSelectorConfig(options=choices, mode=SelectSelectorMode.LIST, translation_key="camera_choice")
                    )
                }
            ),
        )

    async def async_step_camera_add(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._new_camera = user_input[CONF_CAMERA]
            return await self.async_step_camera()
        return self.async_show_form(
            step_id="camera_add", data_schema=vol.Schema({vol.Required(CONF_CAMERA): CAMERA})
        )

    async def async_step_camera(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One camera's settings (pre-filled from its device when it's new)."""
        cams = dict(self._values.get(CONF_CAMERAS) or {})
        editing = self._editing
        current = cams[editing] if editing else {CONF_CAMERA: self._new_camera, **suggest_camera(self.hass, self._new_camera)}
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _flatten(user_input, CAMERA_SECTIONS)
            if editing and user_input.get("remove"):
                cams.pop(editing)
                return self._save({CONF_CAMERAS: cams})
            slug = current[CONF_SLUG] if editing else slugify(user_input.get(CONF_NAME) or "")
            taken = {d[CONF_SLUG] for cid, d in cams.items() if cid != editing}
            if not slug:
                errors[CONF_NAME] = "name_required"
            elif slug in taken:
                errors[CONF_NAME] = "name_taken"
            else:
                cams[editing or uuid4().hex] = _camera_data(
                    user_input, slug, user_input.get(CONF_CAMERA) or current[CONF_CAMERA]
                )
                return self._save({CONF_CAMERAS: cams})
        schema = _camera_schema(
            user_input or current,
            _camera_options(self.config_entry, editing),
            include_camera=bool(editing),
            subjects=_subjects(self.config_entry),
        )
        if editing:
            schema = schema.extend({vol.Required("remove", default=False): BOOL})
        return self.async_show_form(
            step_id="camera",
            data_schema=schema,
            description_placeholders={
                "camera": _camera_label(self.hass, current[CONF_CAMERA]),
                **_sensors_note(current),
            },
            errors=errors,
        )

    # --- people --------------------------------------------------------------------------

    async def async_step_people(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a person to edit, or add one."""
        people = self._values.get(CONF_PEOPLE) or {}
        if user_input is not None:
            self._editing = None if user_input["person"] == NEW else user_input["person"]
            return await self.async_step_person()
        choices = [
            SelectOptionDict(value=pid, label=d[CONF_NAME])
            for pid, d in sorted(people.items(), key=lambda kv: kv[1][CONF_NAME].lower())
        ] + [SelectOptionDict(value=NEW, label="+ Add a person")]
        return self.async_show_form(
            step_id="people",
            data_schema=vol.Schema(
                {
                    vol.Required("person"): SelectSelector(
                        SelectSelectorConfig(options=choices, mode=SelectSelectorMode.LIST, translation_key="person_choice")
                    )
                }
            ),
        )

    async def async_step_person(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Someone who gets alerts: phone, sound, mutes, quiet zones and hours."""
        people = dict(self._values.get(CONF_PEOPLE) or {})
        editing = self._editing
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _flatten(user_input, PERSON_SECTIONS)
            if editing and user_input.get("remove"):
                people.pop(editing)
                return self._save({CONF_PEOPLE: people})
            data = _recipient_data(user_input)
            if not data[CONF_NAME]:
                errors[CONF_NAME] = "name_required"
            else:
                people[editing or uuid4().hex] = data
                return self._save({CONF_PEOPLE: people})
        schema = _recipient_schema(
            self.hass, user_input or (people.get(editing) if editing else {}), _subjects(self.config_entry)
        )
        if editing:
            schema = schema.extend({vol.Required("remove", default=False): BOOL})
        return self.async_show_form(step_id="person", data_schema=schema, errors=errors)

    async def async_step_models(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _flatten(user_input, MODELS_SECTIONS)
            errors = _validate_models(user_input)
            if not errors:
                user_input.setdefault(CONF_SECOND_OPINION, [])
                user_input.setdefault(CONF_PAID, [])
                user_input.setdefault(CONF_MAIN_START, "")
                user_input.setdefault(CONF_MAIN_END, "")
                return self._save(user_input)
        return self.async_show_form(
            step_id="models", data_schema=_models_schema(user_input or self._values), errors=errors
        )

    async def async_step_schedule(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(_flatten(user_input, SCHEDULE_SECTIONS))
        return self.async_show_form(step_id="schedule", data_schema=_schedule_schema(self._values))

    async def async_step_identification(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        values = self._values
        if user_input is not None:
            if user_input.pop("reset_instructions", False):
                user_input[CONF_INSTRUCTIONS] = logic.DEFAULT_INSTRUCTIONS
            text = user_input.get(CONF_INSTRUCTIONS) or ""
            if any(p not in text for p in logic.REQUIRED_PLACEHOLDERS):
                errors[CONF_INSTRUCTIONS] = "missing_animals"
            else:
                user_input.setdefault(CONF_EXTRA_RULES, "")
                return self._save(user_input)
            values = {**values, **user_input}
        return self.async_show_form(
            step_id="identification",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_INSTRUCTIONS, default=values[CONF_INSTRUCTIONS]): LONG_TEXT,
                    vol.Required("reset_instructions", default=False): BOOL,
                    vol.Optional(
                        CONF_EXTRA_RULES, description={"suggested_value": values[CONF_EXTRA_RULES]}
                    ): LONG_TEXT,
                    vol.Required(CONF_WILDLIFE, default=values[CONF_WILDLIFE]): WILDLIFE,
                    vol.Required(CONF_ABSENT_DAYS, default=values[CONF_ABSENT_DAYS]): _number(1, 365, "d"),
                    vol.Required(CONF_REFERENCE_PHOTOS, default=values[CONF_REFERENCE_PHOTOS]): BOOL,
                }
            ),
            errors=errors,
            description_placeholders={
                key: "{" + key + "}" for key in ("animals", "location", "absent", "extra_rules", "wildlife")
            },
        )

    async def async_step_alerts(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        values = self._values
        return self.async_show_form(
            step_id="alerts",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ALERTS, default=values[CONF_ALERTS]): BOOL,
                    vol.Required(CONF_MIN_CONFIDENCE, default=values[CONF_MIN_CONFIDENCE]): CONFIDENCE,
                    vol.Required(CONF_BUTTONS, default=values[CONF_BUTTONS]): BOOL,
                }
            ),
        )

    async def async_step_catch_alls(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Unknown animals, other wildlife and unsure motion: where each alerts."""
        if user_input is not None:
            user_input = _flatten(user_input, CATCH_ALL_SECTIONS)
            data: dict[str, Any] = {
                CONF_PESTS: [p.strip().title() for p in user_input.get(CONF_PESTS, []) if p.strip()]
            }
            for key in BUILTIN_KEYS.values():
                data.update(_alert_data(user_input, f"{key}_"))
            return self._save(data)
        cameras = _camera_options(self.config_entry)
        # One folded group each, in the order the page describes them.
        fields: dict[Any, Any] = {}
        for key in ("pest", "unknown", "other", "motion"):
            group = _alert_fields(self._values, cameras, f"{key}_")
            if key == "pest":
                group = {vol.Optional(CONF_PESTS, default=list(self._values.get(CONF_PESTS) or [])): PESTS, **group}
            fields[vol.Required(key, default={})] = _section(group)
        return self.async_show_form(step_id="catch_alls", data_schema=vol.Schema(fields))

    async def async_step_training(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            user_input.setdefault(CONF_DATASET_DIR, "")
            return self._save(user_input)
        values = self._values
        return self.async_show_form(
            step_id="training",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DATASET, default=values[CONF_DATASET]): BOOL,
                    vol.Optional(
                        CONF_DATASET_DIR, description={"suggested_value": values[CONF_DATASET_DIR] or None}
                    ): TEXT,
                    vol.Required(CONF_DATASET_GB, default=values[CONF_DATASET_GB]): _number(1, 500, "GB"),
                }
            ),
        )

    async def async_step_detector(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            user_input.setdefault(CONF_DETECTOR_URL, "")
            return self._save(user_input)
        values = self._values
        return self.async_show_form(
            step_id="detector",
            description_placeholders={"example": "http://nas.local:8765"},
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DETECTOR_URL, description={"suggested_value": values[CONF_DETECTOR_URL] or None}
                    ): TEXT,
                    vol.Required(CONF_DETECTOR_MODE, default=values[CONF_DETECTOR_MODE]): SelectSelector(
                        SelectSelectorConfig(options=DETECTOR_MODES, translation_key="detector_mode")
                    ),
                    vol.Required(CONF_DETECTOR_MIN, default=values[CONF_DETECTOR_MIN]): NumberSelector(
                        NumberSelectorConfig(min=0.05, max=0.9, step=0.05, mode=NumberSelectorMode.BOX)
                    ),
                }
            ),
        )

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            user_input.setdefault(CONF_PUBLIC_URL, "")
            return self._save(user_input)
        return self.async_show_form(
            step_id="notifications",
            description_placeholders={"example": "https://example.duckdns.org:8123"},
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_PUBLIC_URL, description={"suggested_value": self._values[CONF_PUBLIC_URL]}
                    ): TEXT
                }
            ),
        )


# --- animals (Add animal / Edit animal) -------------------------------------------------------


class AnimalSubentryFlow(ConfigSubentryFlow):
    def _reference_markdown(self, subentry_id: str) -> str:
        """The saved reference photo, shown above the form (the upload box can't show it)."""
        unique_id = f"{self._get_entry().entry_id}_{subentry_id}_reference_photo"
        entity_id = er.async_get(self.hass).async_get_entity_id("image", DOMAIN, unique_id)
        state = self.hass.states.get(entity_id) if entity_id else None
        picture = state.attributes.get("entity_picture") if state and state.state != "unavailable" else None
        if not picture:
            return "No reference photo yet."
        return "Current reference photo:\n\n" + f"![reference photo]({picture})"

    def _species(self) -> list[str]:
        return [
            s.data.get(CONF_SPECIES) or ""
            for s in self._get_entry().subentries.values()
            if s.subentry_type == SUBENTRY_ANIMAL
        ]

    def _taken(self, exclude: str | None = None) -> set[str]:
        return {
            s.data[CONF_NAME].lower()
            for s in self._get_entry().subentries.values()
            if s.subentry_type == SUBENTRY_ANIMAL and s.subentry_id != exclude
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _flatten(user_input, ANIMAL_SECTIONS)
            errors = _validate_animal(user_input, self._taken())
            if not errors:
                name = user_input[CONF_NAME].strip()
                slug = slugify(name)
                if upload := user_input.get(CONF_REFERENCE_UPLOAD):
                    errors = await _store_upload(self.hass, upload, slug)
            if not errors:
                return self.async_create_entry(
                    title=name,
                    data=_animal_data(user_input, slug, dt_util.utcnow().isoformat()),
                    unique_id=name.lower(),
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_animal_schema(
                user_input or {}, _camera_options(self._get_entry()), reconfigure=False, onboarding=False,
                species=self._species(),
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _flatten(user_input, ANIMAL_SECTIONS)
            errors = _validate_animal(user_input, self._taken(subentry.subentry_id))
            if not errors:
                slug = subentry.data[CONF_SLUG]
                if user_input.get(CONF_REMOVE_REFERENCE):
                    await self.hass.async_add_executor_job(remove_file, reference_file(self.hass, slug))
                elif upload := user_input.get(CONF_REFERENCE_UPLOAD):
                    errors = await _store_upload(self.hass, upload, slug)
            if not errors:
                data = _animal_data(user_input, slug, subentry.data.get(CONF_ADDED) or dt_util.utcnow().isoformat())
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=data[CONF_NAME],
                    data=data,
                    unique_id=data[CONF_NAME].lower(),
                )
        return self.async_show_form(
            step_id="reconfigure",
            description_placeholders={"reference": self._reference_markdown(subentry.subentry_id)},
            data_schema=_animal_schema(
                user_input or subentry.data,
                _camera_options(self._get_entry()),
                reconfigure=True,
                onboarding=False,
                species=self._species(),
            ),
            errors=errors,
        )


# --- people (alerts) -------------------------------------------------------------------------


def _notify_services(hass: HomeAssistant) -> list[str]:
    return sorted(
        name
        for name in hass.services.async_services().get("notify", {})
        if name not in ("notify", "send_message", "persistent_notification")
    )


def _phone_names(hass: HomeAssistant) -> dict[str, str]:
    """Each Home Assistant app's notify service, mapped to its phone's device name."""
    dev_reg = dr.async_get(hass)
    names: dict[str, str] = {}
    for entry in hass.config_entries.async_entries("mobile_app"):
        device_name = entry.data.get("device_name") or entry.title
        if not device_name:
            continue
        devices = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
        device = devices[0] if devices else None
        names[f"mobile_app_{slugify(device_name)}"] = (
            device and (device.name_by_user or device.name)
        ) or device_name
    return names


def _recipient_schema(hass: HomeAssistant, values: Mapping[str, Any], subjects: list[str]) -> vol.Schema:
    names = _phone_names(hass)
    return vol.Schema(
        {
            vol.Required(CONF_NAME, description={"suggested_value": values.get(CONF_NAME)}): TEXT,
            vol.Optional(CONF_PERSON, description={"suggested_value": values.get(CONF_PERSON)}): PERSON,
            vol.Required(CONF_NOTIFY, description={"suggested_value": values.get(CONF_NOTIFY)}): SelectSelector(
                SelectSelectorConfig(
                    options=[SelectOptionDict(value=s, label=names.get(s, s)) for s in _notify_services(hass)],
                    custom_value=True,
                )
            ),
            vol.Required("critical_alerts", default={}): _section(
                {
                    vol.Required(CONF_MISSING_ALERTS, default=values.get(CONF_MISSING_ALERTS, True)): BOOL,
                    vol.Required(CONF_CRITICAL_SOUND, default=values.get(CONF_CRITICAL_SOUND, "default")): TEXT,
                    vol.Required(CONF_CRITICAL_VOLUME, default=values.get(CONF_CRITICAL_VOLUME, 0.7)): VOLUME,
                }
            ),
            vol.Required("mutes", default={}): _section(
                {
                    vol.Optional(
                        CONF_MUTED, default=_pick(list(values.get(CONF_MUTED) or []), subjects)
                    ): _names(subjects),
                }
            ),
            vol.Required("quiet", default={}): _section(
                {
                    vol.Optional(CONF_QUIET_ZONES, default=values.get(CONF_QUIET_ZONES, [])): ZONES,
                    vol.Optional(
                        CONF_QUIET_START, description={"suggested_value": values.get(CONF_QUIET_START)}
                    ): TIME,
                    vol.Optional(
                        CONF_QUIET_END, description={"suggested_value": values.get(CONF_QUIET_END)}
                    ): TIME,
                    vol.Required(CONF_QUIET_MODE, default=values.get(CONF_QUIET_MODE, QUIET_NORMAL)): QUIET_MODE,
                }
            ),
        }
    )


def _recipient_data(user_input: Mapping[str, Any]) -> dict[str, Any]:
    notify = str(user_input[CONF_NOTIFY]).strip().removeprefix("notify.")
    return {
        CONF_NAME: user_input[CONF_NAME].strip(),
        CONF_PERSON: user_input.get(CONF_PERSON) or None,
        CONF_NOTIFY: notify,
        CONF_QUIET_ZONES: user_input.get(CONF_QUIET_ZONES, []),
        CONF_MUTED: [n.lower() for n in user_input.get(CONF_MUTED) or []],
        CONF_QUIET_START: user_input.get(CONF_QUIET_START) or None,
        CONF_QUIET_END: user_input.get(CONF_QUIET_END) or None,
        CONF_QUIET_MODE: user_input.get(CONF_QUIET_MODE, QUIET_NORMAL),
        CONF_MISSING_ALERTS: bool(user_input.get(CONF_MISSING_ALERTS)),
        CONF_CRITICAL_SOUND: (user_input.get(CONF_CRITICAL_SOUND) or "default").strip(),
        CONF_CRITICAL_VOLUME: user_input.get(CONF_CRITICAL_VOLUME, 0.7),
    }
