"""Diagnostics: settings, the roster and recent sightings."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import WatchConfigEntry
from .const import (
    CONF_NAME,
    CONF_NOTIFY,
    CONF_PEOPLE,
    CONF_PERSON,
    CONF_PUBLIC_URL,
    CONF_QUIET_ZONES,
    SUBENTRY_RECIPIENT,
)
from .engine import WatchEngine

TO_REDACT = {CONF_PUBLIC_URL}
# People: who they are, their phones and where they are.
PERSON_REDACT = {CONF_NAME, CONF_NOTIFY, CONF_PERSON, CONF_QUIET_ZONES}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WatchConfigEntry
) -> dict[str, Any]:
    watch = getattr(entry, "runtime_data", None)
    options = async_redact_data(dict(entry.options), TO_REDACT)
    if isinstance(options.get(CONF_PEOPLE), dict):
        options[CONF_PEOPLE] = {
            pid: async_redact_data(dict(person), PERSON_REDACT)
            for pid, person in options[CONF_PEOPLE].items()
        }
    result: dict[str, Any] = {
        "options": options,
        "subentries": [
            {"type": s.subentry_type, "title": s.title, "data": dict(s.data)}
            if s.subentry_type != SUBENTRY_RECIPIENT
            else {"type": s.subentry_type, "title": "**REDACTED**", "data": async_redact_data(dict(s.data), PERSON_REDACT)}
            for s in entry.subentries.values()
        ],
    }
    if not isinstance(watch, WatchEngine):
        return result
    data = watch.data
    result.update(
        {
            "strict_now": sorted(watch.strict_keys()),
            "references": sorted(watch.references),
            "animals": data.get("animals"),
            "cameras": data.get("cameras"),
            "missing": data.get("missing"),
            "ai": data.get("ai"),
            "summary": data.get("summary"),
            "last_summary_at": data.get("last_summary_at"),
            "sightings": data.get("sightings", [])[-200:],
        }
    )
    return result
