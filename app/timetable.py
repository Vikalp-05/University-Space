#Async client for DCU Redbrick API

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx

from . import config
from .ics import Event, parse_ics
from .rooms import Room, build_rooms, learn_building_name


class UpstreamError(Exception):
    """API error exception"""


class TTLCache:
    #async cache

    def __init__(self) -> None:
        self._data: dict[Any, tuple[float, Any]] = {}
        self._inflight: dict[Any, asyncio.Future] = {}

    async def get_or_fetch(self, key: Any, ttl: float, fetch: Callable[[], Awaitable[Any]]) -> Any:
        hit = self._data.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        if key in self._inflight:
            return await asyncio.shield(self._inflight[key])

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            value = await fetch()
        except BaseException as exc:
            fut.set_exception(exc)
            fut.exception()  # mark retrieved so asyncio doesn't warn if nobody else awaits it
            raise
        else:
            self._data[key] = (time.monotonic() + ttl, value)
            fut.set_result(value)
            return value
        finally:
            self._inflight.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


class TimetableClient:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(
            base_url=config.TIMETABLE_API_BASE,
            timeout=config.REQUEST_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
            follow_redirects=True,
        )
        self._cache = TTLCache()
        self._sem = asyncio.Semaphore(config.MAX_CONCURRENCY)
        self.building_names: dict[tuple[str, str], str] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, **kwargs) -> httpx.Response:
        async with self._sem:
            try:
                resp = await self._http.get(path, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                raise UpstreamError(f"Timetable API returned {exc.response.status_code} for {path}") from exc
            except httpx.HTTPError as exc:
                raise UpstreamError(f"Could not reach the timetable API: {exc}") from exc

    #rooms
    async def rooms(self) -> list[Room]:
        async def fetch() -> list[Room]:
            resp = await self._get("/category/location/items")
            return build_rooms(resp.json())

        return await self._cache.get_or_fetch("rooms", config.ROOM_LIST_TTL, fetch)

    async def rooms_by_id(self) -> dict[str, Room]:
        return {r.id: r for r in await self.rooms()}

    #events
    async def room_events(self, room: Room, day: date) -> list[Event]:
        #all room events

        async def fetch() -> list[Event]:
            start_local = datetime.combine(day, dtime.min, tzinfo=config.LOCAL_TZ)
            end_local = start_local + timedelta(days=1)
            resp = await self._get(
                f"/category/location/items/{room.id}/events",
                params={
                    "start": start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                headers={"media-type": "text/calendar"},
            )
            events = [
                e for e in parse_ics(resp.text) if e.end > start_local and e.start < end_local
            ]
            name = learn_building_name(room, events)
            if name:
                self.building_names[(room.campus, room.building)] = name
            return events

        return await self._cache.get_or_fetch(("events", room.id, day), config.EVENTS_TTL, fetch)