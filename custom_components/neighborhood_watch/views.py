"""HTTP view: the saved photo of one sighting, for the card's review list.

Authenticated; the card asks Home Assistant to sign the path so a plain <img> can load it.
"""
from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.http import KEY_HASS

from . import dataset
from .const import DOMAIN
from .engine import WatchEngine


class FrameView(HomeAssistantView):
    url = "/api/neighborhood_watch/frame/{sid}"
    extra_urls = ["/api/neighborhood_watch/frame/{sid}/{variant}"]
    name = "api:neighborhood_watch:frame"
    requires_auth = True

    async def get(self, request: web.Request, sid: str, variant: str = "") -> web.Response:
        if not sid.isalnum() or len(sid) > 32:
            return web.Response(status=400)
        hass = request.app[KEY_HASS]
        watch = next(
            (
                e.runtime_data
                for e in hass.config_entries.async_loaded_entries(DOMAIN)
                if isinstance(getattr(e, "runtime_data", None), WatchEngine)
            ),
            None,
        )
        row = watch.find_sighting(sid, None) if watch else None
        if row is None or not row.get("day"):
            return web.Response(status=404)
        read = dataset.read_crop if variant == "crop" else dataset.read_frame
        frame = await hass.async_add_executor_job(read, watch.dataset_root, row["day"], sid)
        if frame is None:
            return web.Response(status=404)
        return web.Response(
            body=frame,
            content_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )
