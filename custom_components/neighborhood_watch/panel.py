"""Sidebar panel: the detector and its training, read and changed through the integration so
the browser never has to reach the vision service itself (it may be http on the LAN while
Home Assistant is https, or the user may be away from home)."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import aiohttp

try:  # Home Assistant 2026.10 replaced voluptuous with probatio (same API).
    import probatio as vol
except ImportError:  # pragma: no cover - older Home Assistant
    import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import BUILTIN_KEYS, CONF_SECOND_OPINION, DOMAIN, NAME
from .engine import WatchEngine

PANEL_URL = f"/{DOMAIN}/neighborhood-watch-panel.js"
FONT_URL = f"/{DOMAIN}/GoogleSans.woff2"
PANEL_PATH = "neighborhood-watch"

_LOGGER = logging.getLogger(__name__)


def _engine(hass: HomeAssistant) -> WatchEngine | None:
    return next(
        (
            e.runtime_data
            for e in hass.config_entries.async_loaded_entries(DOMAIN)
            if isinstance(getattr(e, "runtime_data", None), WatchEngine)
        ),
        None,
    )


class VisionError(Exception):
    """The vision service is not set up, did not answer, or refused."""

    def __init__(self, code: str, message: str = "", status: int = 0) -> None:
        super().__init__(message or code)
        self.code, self.status = code, status


async def _vision(hass: HomeAssistant, method: str, path: str, body: Any = None) -> Any:
    watch = _engine(hass)
    url = watch.detector_url if watch else ""
    if not url:
        raise VisionError("not_configured")
    try:
        async with asyncio.timeout(15):
            response = await async_get_clientsession(hass).request(method, f"{url}{path}", json=body)
            text = await response.text()
    except (TimeoutError, aiohttp.ClientError) as err:
        raise VisionError("unreachable", str(err) or type(err).__name__) from err
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if response.status >= 400:
        error = data.get("error") if isinstance(data, dict) else None
        raise VisionError("refused", error or f"The service answered {response.status}", response.status)
    if data is None:
        raise VisionError("unreachable", "The service didn't answer with JSON")
    return data


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/vision"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_vision(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Everything the panel shows, in one answer (errors included, so the page can explain)."""
    watch = _engine(hass)
    result: dict[str, Any] = {"loaded": watch is not None, "url": watch.detector_url if watch else ""}
    rows = watch.detector_week() if watch else []  # the Detector agreement sensor's numbers
    if rows:
        result["agreement"] = {
            "percent": round(100 * sum(r["kind"] == "agree" for r in rows) / len(rows)),
            "misses": sum(r["kind"] == "miss" for r in rows),
        }
    try:
        result["health"], result["train"] = await asyncio.gather(
            _vision(hass, "GET", "/health"), _vision(hass, "GET", "/train")
        )
        try:
            result["history"] = await _vision(hass, "GET", "/train/history")
        except VisionError as err:
            if err.status != 404:
                raise
            result["outdated"] = True  # a vision service from before the settings page
    except VisionError as err:
        result["error"] = {"code": err.code, "message": str(err)}
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/roster"})
@websocket_api.require_admin
@callback
def ws_roster(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Who and what is set up, for the page's lists (changes go through the settings flows)."""
    watch = _engine(hass)
    if watch is None:
        connection.send_result(msg["id"], {"loaded": False})
        return
    entry_id = watch.entry.entry_id
    entities = er.async_get(hass)

    def critical(owner: str, key: str = "critical_alerts") -> str | None:
        """The entity id of a critical-alerts switch (switch.py's unique ids)."""
        return entities.async_get_entity_id("switch", DOMAIN, f"{entry_id}_{owner}_{key}")

    strict = watch.strict_keys()
    connection.send_result(
        msg["id"],
        {
            "loaded": True,
            "entry_id": entry_id,
            "second_opinion": bool(watch.options.get(CONF_SECOND_OPINION)),
            "catch_all_critical": {key: critical("hub", f"{key}_critical") for key in BUILTIN_KEYS.values()},
            "cameras": [
                {"id": c.subentry_id, "name": c.name, "camera": c.camera, "critical": critical(c.subentry_id)}
                for c in sorted(watch.cameras.values(), key=lambda c: c.name.lower())
            ],
            "people": [
                {"id": r.subentry_id, "name": r.name, "person": r.person}
                for r in sorted(watch.recipients, key=lambda r: r.name.lower())
            ],
            "animals": [
                {
                    "id": a.subentry_id,
                    "name": a.name,
                    "species": a.species,
                    "appearance": ({"preset": a.sprite} if a.sprite else {}) | (a.appearance or {}) or None,
                    "critical": critical(a.subentry_id),
                    # Priority, Always strict, new or not seen lately: only a second model confirms it.
                    "needs_second_opinion": a.priority or a.key in strict,
                }
                for a in sorted(watch.animals.values(), key=lambda a: a.name.lower())
            ],
        },
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/vision/settings", vol.Required("changes"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_vision_settings(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    try:
        answer = await _vision(hass, "POST", "/settings", msg["changes"])
    except VisionError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], answer)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/vision/train"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_vision_train(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    try:
        answer = await _vision(hass, "POST", "/train", {})
    except VisionError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], answer)


async def async_register_panel(hass: HomeAssistant, version: str) -> None:
    for command in (ws_roster, ws_vision, ws_vision_settings, ws_vision_train):
        websocket_api.async_register_command(hass, command)
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return
    from homeassistant.components import panel_custom  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    folder = Path(__file__).parent / "frontend"
    try:  # the page is an extra: never let it stop the integration from loading
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(PANEL_URL, str(folder / "neighborhood-watch-panel.js"), False),
                StaticPathConfig(FONT_URL, str(folder / "GoogleSans.woff2"), True),
            ]
        )
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_PATH,
            webcomponent_name="neighborhood-watch-panel",
            sidebar_title=NAME,
            sidebar_icon="mdi:cat",
            module_url=f"{PANEL_URL}?v={version}",
            # The review list is the dashboard card's; the page loads it if it isn't yet.
            config={"font": FONT_URL, "card": f"/{DOMAIN}/neighborhood-watch-card.js?v={version}"},
            require_admin=True,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Couldn't add the Neighborhood Watch page to the sidebar")
