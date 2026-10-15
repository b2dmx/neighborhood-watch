"""Diagnostics: settings, the roster, recent sightings and shadow comparisons."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import WatchConfigEntry
from .const import CONF_PUBLIC_URL
from .engine import WatchEngine

TO_REDACT = {CONF_PUBLIC_URL}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WatchConfigEntry
) -> dict[str, Any]:
    watch = getattr(entry, "runtime_data", None)
    result: dict[str, Any] = {
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "subentries": [
            {"type": s.subentry_type, "title": s.title, "data": dict(s.data)}
            for s in entry.subentries.values()
        ],
    }
    if not isinstance(watch, WatchEngine):
        return result
    data = watch.data
    result.update(
        {
            "mode": watch.mode,
            "strict_now": sorted(watch.strict_keys()),
            "references": sorted(watch.references),
            "animals": data.get("animals"),
            "cameras": data.get("cameras"),
            "missing": data.get("missing"),
            "ai": data.get("ai"),
            "summary": data.get("summary"),
            "last_summary_at": data.get("last_summary_at"),
            "sightings": data.get("sightings", [])[-200:],
            "comparisons": data.get("comparisons", [])[-100:],
        }
    )
    return result
