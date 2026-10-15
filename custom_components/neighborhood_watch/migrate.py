"""Moving older setups to the current settings (pure, so it can be tested on its own)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import logic
from .const import (
    CONF_ALERT,
    CONF_ALERT_AT,
    CONF_ALERT_CAMERAS,
    CONF_ALERT_SCOPE,
    CONF_CRITICAL_AT,
    CONF_CRITICAL_EXCEPT,
    CONF_CRITICAL_SWITCH,
    CONF_HOUSE,
    CONF_NAME,
    CONF_PRIORITY,
    CONF_SLUG,
    CONF_UNKNOWN_ALERTS,
    CONF_UNSURE_ALERTS,
    SUBENTRY_ANIMAL,
    SUBENTRY_CAMERA,
    SUBENTRY_SPECIES,
)


def alert_rules_v2(
    options: Mapping[str, Any], subentries: Mapping[str, tuple[str, Mapping[str, Any]]]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """0.6 → 0.7: "close to the house" cameras, alert scopes and critical switches become
    each animal's Alert at / Critical at and each camera's "critical, except".

    `subentries` maps id → (type, data). Returns the new options and the changed data by id.
    The behaviour stays the same: what alerted where still does, and the priority animals
    (never critical before) are the exceptions on every camera.
    """
    cams = {i: d for i, (t, d) in subentries.items() if t == SUBENTRY_CAMERA}
    house = [d[CONF_SLUG] for d in cams.values() if d.get(CONF_HOUSE)]
    unsure = [d[CONF_SLUG] for d in cams.values() if d.get(CONF_UNSURE_ALERTS)]
    priority = [
        d[CONF_NAME].lower()
        for t, d in subentries.values()
        if t == SUBENTRY_ANIMAL and d.get(CONF_PRIORITY)
    ]
    changed: dict[str, dict[str, Any]] = {}
    for sid, (kind, d) in subentries.items():
        data = dict(d)
        if kind == SUBENTRY_CAMERA:
            data.pop(CONF_HOUSE, None)
            data.pop(CONF_UNSURE_ALERTS, None)
            data.setdefault(CONF_CRITICAL_EXCEPT, priority)
        elif kind == SUBENTRY_ANIMAL:
            on = data.pop(CONF_ALERT, True)
            data.setdefault(CONF_ALERT_AT, [logic.EVERY] if on else [])
            data.setdefault(CONF_CRITICAL_AT, [])
        elif kind == SUBENTRY_SPECIES:
            scope = data.pop(CONF_ALERT_SCOPE, "never")
            chosen = list(data.pop(CONF_ALERT_CAMERAS, None) or [])
            data.pop(CONF_CRITICAL_SWITCH, None)  # every kind has the switch now
            where = {"any": [logic.EVERY], "house": house, "cameras": chosen}.get(scope, [])
            data.setdefault(CONF_ALERT_AT, list(where))
            data.setdefault(CONF_CRITICAL_AT, [])  # empty: rings wherever it alerts
        else:
            continue
        if data != dict(d):
            changed[sid] = data

    opts = dict(options)
    unknown = opts.pop(CONF_UNKNOWN_ALERTS, True)
    opts.setdefault(f"unknown_{CONF_ALERT_AT}", [logic.EVERY] if unknown else [])
    opts.setdefault(f"unknown_{CONF_CRITICAL_AT}", [])
    opts.setdefault(f"other_{CONF_ALERT_AT}", house)
    opts.setdefault(f"other_{CONF_CRITICAL_AT}", [])
    opts.setdefault(f"motion_{CONF_ALERT_AT}", unsure)
    opts.setdefault(f"motion_{CONF_CRITICAL_AT}", [])
    return opts, changed
