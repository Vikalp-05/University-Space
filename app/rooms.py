"""Room naming helpers and free/busy logic."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import CAMPUS_NAMES
from .ics import Event

#room information
@dataclass(frozen=True)
class Room:
    id: str
    name: str  
    campus: str  
    code: str  
    building: str  

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "campus": self.campus,
            "campus_name": CAMPUS_NAMES.get(self.campus, self.campus),
            "code": self.code,
            "building": self.building,
        }


_LETTERS = re.compile(r"^([A-Za-z]+)")


def _letter_prefix(code: str) -> str:
    m = _LETTERS.match(code)
    return m.group(1).upper() if m else "OTHER"


def build_rooms(items: list[dict]) -> list[Room]:
    parsed = []
    for item in items:
        name = str(item.get("name", "")).strip()
        rid = str(item.get("identity", "")).strip()
        if not name or not rid:
            continue
        campus, _, code = name.partition(".")
        if not code:
            campus, code = "OTHER", name
        parsed.append((rid, name, campus.upper(), code))

    prefixes: dict[str, set[str]] = {}
    for _, _, campus, code in parsed:
        prefixes.setdefault(campus, set()).add(_letter_prefix(code))

    rooms = []
    for rid, name, campus, code in parsed:
        letters = _letter_prefix(code)
        building = letters
        if len(letters) >= 2 and letters[-1] in "GB" and letters[:-1] in prefixes[campus]:
            building = letters[:-1]
        rooms.append(Room(id=rid, name=name, campus=campus, code=code, building=building))

    rooms.sort(key=lambda r: (r.campus, r.building, _natural_key(r.code)))
    return rooms


def _natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]



_LOCATION_RE = re.compile(r"^(?P<rooms>.*)\((?P<building>[^()]*),\s*(?P<campus>[^(),]*)\)\s*$")


def learn_building_name(room: Room, events: list[Event]) -> str | None:
    for ev in events:
        for part in re.split(r"\)\s*,\s*", ev.location):
            part = part if part.endswith(")") else part + ")"
            m = _LOCATION_RE.match(part.strip())
            if not m:
                continue
            codes = {c.strip().upper() for c in m.group("rooms").split(",")}
            if room.code.upper() in codes:
                return m.group("building").strip()
    return None

def _merge(events: list[Event]) -> list[tuple[datetime, datetime]]:
    blocks: list[list[datetime]] = []
    for ev in sorted(events, key=lambda e: e.start):
        if ev.end <= ev.start:
            continue
        if blocks and ev.start <= blocks[-1][1]:
            blocks[-1][1] = max(blocks[-1][1], ev.end)
        else:
            blocks.append([ev.start, ev.end])
    return [(a, b) for a, b in blocks]

#Check availability
def availability(events: list[Event], at: datetime, duration: timedelta) -> dict:
    window_end = at + duration
    current = [e for e in events if e.start <= at < e.end]
    upcoming = [e for e in events if e.start > at]
    blocks = _merge(events)

    free_now = not current
    free_until = None
    busy_until = None

    if free_now:
        nxt = next((b for b in blocks if b[0] > at), None)
        free_until = nxt[0] if nxt else None
        free_for_window = free_until is None or free_until >= window_end
    else:
        block = next(b for b in blocks if b[0] <= at < b[1])
        busy_until = block[1]
        free_for_window = False

    if free_for_window:
        status = "free"
    elif free_now:
        status = "free_short"
    else:
        status = "busy"

    return {
        "status": status,
        "free": free_for_window,
        "free_now": free_now,
        "free_until": free_until,
        "busy_until": busy_until,
        "current_event": current[0] if current else None,
        "next_event": upcoming[0] if upcoming else None,
    }