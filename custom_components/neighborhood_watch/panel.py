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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, NAME
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
    for command in (ws_vision, ws_vision_settings, ws_vision_train):
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
            config={"font": FONT_URL},
            require_admin=True,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Couldn't add the Neighborhood Watch page to the sidebar")
