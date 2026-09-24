from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import CAMPUS_NAMES, LOCAL_TZ, MAX_ROOMS_PER_SEARCH
from .ics import Event
from .rooms import Room, availability
from .timetable import TimetableClient, UpstreamError

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "timetable"):
        app.state.timetable = TimetableClient()
    yield
    await app.state.timetable.aclose()


app = FastAPI(
    title="DCU Free Rooms",
    description="Find free rooms at DCU using the Redbrick timetable API.",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(UpstreamError)
async def upstream_error_handler(request: Request, exc: UpstreamError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


def tt(request: Request) -> TimetableClient:
    return request.app.state.timetable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def current_time() -> datetime:
    return datetime.now(LOCAL_TZ)


def resolve_moment(day: date | None, at: str | None) -> datetime:
    """Combine an optional date + HH:MM (Irish time) into an aware datetime; default = now."""
    now = current_time()
    d = day or now.date()
    if at:
        try:
            t = dtime.fromisoformat(at)
        except ValueError:
            raise HTTPException(422, "time must be HH:MM")
    elif d == now.date():
        t = now.time().replace(second=0, microsecond=0)
    else:
        t = dtime(9, 0)
    return datetime.combine(d, t, tzinfo=LOCAL_TZ)


def fmt(dt: datetime | None) -> str | None:
    return dt.astimezone(LOCAL_TZ).isoformat() if dt else None


def event_dict(ev: Event | None) -> dict | None:
    if ev is None:
        return None
    return {
        "title": ev.title,
        "start": fmt(ev.start),
        "end": fmt(ev.end),
        "description": ev.description,
        "location": ev.location,
    }


def availability_dict(events: list[Event], moment: datetime, duration: timedelta) -> dict:
    a = availability(events, moment, duration)
    return {
        "status": a["status"],
        "free": a["free"],
        "free_now": a["free_now"],
        "free_until": fmt(a["free_until"]),
        "busy_until": fmt(a["busy_until"]),
        "current_event": event_dict(a["current_event"]),
        "next_event": event_dict(a["next_event"]),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/campuses", summary="Campuses and their buildings")
async def campuses(request: Request):
    client = tt(request)
    rooms = await client.rooms()
    out: dict[str, dict] = {}
    for r in rooms:
        c = out.setdefault(
            r.campus,
            {"code": r.campus, "name": CAMPUS_NAMES.get(r.campus, r.campus), "room_count": 0, "buildings": {}},
        )
        c["room_count"] += 1
        b = c["buildings"].setdefault(
            r.building,
            {"code": r.building, "name": client.building_names.get((r.campus, r.building)), "room_count": 0},
        )
        b["room_count"] += 1
    result = []
    for c in out.values():
        c["buildings"] = sorted(c["buildings"].values(), key=lambda b: b["code"])
        result.append(c)
    result.sort(key=lambda c: -c["room_count"])
    return result


@app.get("/api/rooms", summary="List or search rooms")
async def list_rooms(
    request: Request,
    q: str | None = Query(None, description="Text to search for, e.g. 'L101' or 'C1'"),
    campus: str | None = None,
    building: str | None = None,
):
    rooms = await tt(request).rooms()
    if campus:
        rooms = [r for r in rooms if r.campus == campus.upper()]
    if building:
        rooms = [r for r in rooms if r.building == building.upper()]
    if q:
        needle = q.strip().lower().replace(" ", "")
        rooms = [r for r in rooms if needle in r.name.lower().replace(" ", "")]
    return [r.to_dict() for r in rooms]


@app.get("/api/rooms/{room_id}", summary="Status and schedule for one room")
async def room_detail(
    request: Request,
    room_id: str,
    date_: date | None = Query(None, alias="date", description="YYYY-MM-DD (Irish time). Default: today"),
    time_: str | None = Query(None, alias="time", description="HH:MM (Irish time). Default: now"),
    duration: int = Query(60, ge=5, le=12 * 60, description="How long you need the room, in minutes"),
):
    client = tt(request)
    room = (await client.rooms_by_id()).get(room_id)
    if room is None:
        raise HTTPException(404, "Room not found")
    moment = resolve_moment(date_, time_)
    events = await client.room_events(room, moment.date())
    return {
        "room": room.to_dict(),
        "building_name": client.building_names.get((room.campus, room.building)),
        "at": fmt(moment),
        "duration": duration,
        **availability_dict(events, moment, timedelta(minutes=duration)),
        "events": [event_dict(e) for e in events],
    }


@app.get("/api/status", summary="Is each room free or busy right now?")
async def room_status(
    request: Request,
    q: str | None = Query(None, description="Search text, e.g. 'L101' or 'C1'"),
    campus: str | None = Query(None, description="Campus code, e.g. GLA"),
    building: str | None = Query(None, description="Building code, e.g. L (use together with campus)"),
):
    """Current free/busy status for every room matching the search and building filter."""
    client = tt(request)
    rooms: list[Room] = await client.rooms()
    if campus:
        rooms = [r for r in rooms if r.campus == campus.upper()]
    if building:
        rooms = [r for r in rooms if r.building == building.upper()]
    if q:
        needle = q.strip().lower().replace(" ", "")
        rooms = [r for r in rooms if needle in r.name.lower().replace(" ", "")]
    if not q and not building:
        raise HTTPException(400, "Search for a room or pick a building.")
    if len(rooms) > MAX_ROOMS_PER_SEARCH:
        raise HTTPException(
            400, f"{len(rooms)} rooms match - type a bit more or pick a building (max {MAX_ROOMS_PER_SEARCH})."
        )

    now = current_time()

    async def check(room: Room) -> dict:
        try:
            events = await client.room_events(room, now.date())
        except UpstreamError:
            return {"room": room.to_dict(), "status": "unknown"}
        free = availability(events, now, timedelta(minutes=1))["free_now"]
        return {"room": room.to_dict(), "status": "free" if free else "busy"}

    results = await asyncio.gather(*(check(r) for r in rooms))
    if results and all(r["status"] == "unknown" for r in results):
        raise UpstreamError("Could not load timetables from the timetable API")

    order = {"free": 0, "busy": 1, "unknown": 2}
    results.sort(key=lambda r: order[r["status"]])  # stable: keeps room-code order within each group
    return {
        "at": fmt(now),
        "counts": {"total": len(results), "free": sum(r["status"] == "free" for r in results)},
        "rooms": results,
    }