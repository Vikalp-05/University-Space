#iCalender parser

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import LOCAL_TZ


@dataclass(frozen=True)
class Event:
    uid: str
    start: datetime 
    end: datetime  
    title: str
    description: str
    location: str


def _unfold(text: str) -> list[str]:
    #Join folded lines 
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _split_property(line: str) -> tuple[str, dict[str, str], str]:
        #Split into (name, params, value)
    in_quotes = False
    for idx, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ":" and not in_quotes:
            head, value = line[:idx], line[idx + 1 :]
            break
    else:
        return line.upper(), {}, ""
    name, *raw_params = head.split(";")
    params = {}
    for p in raw_params:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return name.upper(), params, value


def _parse_dt(value: str, params: dict[str, str]) -> datetime:
    value = value.strip()
    if params.get("VALUE") == "DATE" or len(value) == 8:
        d = date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        return datetime(d.year, d.month, d.day, tzinfo=LOCAL_TZ)
    if value.endswith("Z"):
        return datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    tz = LOCAL_TZ
    if "TZID" in params:
        try:
            tz = ZoneInfo(params["TZID"])
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return naive.replace(tzinfo=tz)


def parse_ics(text: str) -> list[Event]:
    events: list[Event] = []
    current: dict[str, tuple[dict[str, str], str]] | None = None

    for line in _unfold(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = {}
            continue
        if upper == "END:VEVENT":
            if current is not None and "DTSTART" in current:
                start = _parse_dt(current["DTSTART"][1], current["DTSTART"][0])
                if "DTEND" in current:
                    end = _parse_dt(current["DTEND"][1], current["DTEND"][0])
                elif current["DTSTART"][0].get("VALUE") == "DATE":
                    end = start + timedelta(days=1)
                else:
                    end = start
                get = lambda k: _unescape(current[k][1]) if k in current else ""  # noqa: E731
                events.append(
                    Event(
                        uid=get("UID"),
                        start=start,
                        end=end,
                        title=get("SUMMARY"),
                        description=get("DESCRIPTION"),
                        location=get("LOCATION"),
                    )
                )
            current = None
            continue
        if current is not None:
            name, params, value = _split_property(line)
            current[name] = (params, value)

    events.sort(key=lambda e: (e.start, e.end))
    return events