from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import config
from app.ics import parse_ics
from app import main
from app.main import app
from app.rooms import availability, build_rooms
from app.timetable import TimetableClient

BASE = config.TIMETABLE_API_BASE
L101 = "fdb87a11-e1c3-8c3b-6d27-a54b7ea8cdd5"
L114 = "9fa13e6b-b4eb-2960-e21e-3597b9ba1e31"
LG25 = "11111111-1111-1111-1111-111111111111"
C101 = "22222222-2222-2222-2222-222222222222"

ROOMS = [
    {"name": "GLA.L101", "identity": L101},
    {"name": "GLA.L114", "identity": L114},
    {"name": "GLA.LG25", "identity": LG25},
    {"name": "GLA.C101", "identity": C101},
    {"name": "GLA.C117 & C122", "identity": "33333333-3333-3333-3333-333333333333"},
    {"name": "GLA.SB12 & SB12-A", "identity": "44444444-4444-4444-4444-444444444444"},
    {"name": "GLA.S143", "identity": "55555555-5555-5555-5555-555555555555"},
    {"name": "SPC.B103", "identity": "66666666-6666-6666-6666-666666666666"},
    {"name": "AHC.ODG01", "identity": "77777777-7777-7777-7777-777777777777"},
]

#mock api
L101_ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
METHOD:PUBLISH\r
PRODID:-//timetable.redbrick.dcu.ie//TimetableSync 3.0.0//EN\r
BEGIN:VEVENT\r
UID:b44c9adc-e19b-4bb1-9459-d329b97508d4\r
DTSTART:20260924T080000Z\r
DTEND:20260924T090000Z\r
SUMMARY:CSC1012 Problem-solving\\,Creativity & Critical Thinking (Lab)\r
DESCRIPTION:Details: Lab\\nStaff: McKenna J\r
LOCATION:LG25\\, LG26\\, L101\\, L114\\, L125\\, L128\\, L129 (McNulty Building\\, Glasnevin)\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:38f46396-e694-43a2-9aa4-81f6ff9ff904\r
DTSTART:20260924T120000Z\r
DTEND:20260924T140000Z\r
SUMMARY:CSC1048 Computability & Complexity (Lab)\r
DESCRIPTION:Details: Lab\\nStaff: Sinclair D\r
LOCATION:L101\\, L125 (McNulty Building\\, Glasnevin)\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:7d166b93-bc8a-47f2-809a-a16c8cd23018\r
DTSTART:20260924T150000Z\r
DTEND:20260924T170000Z\r
SUMMARY:CSC1168 Programming for Mathematics (Lab)\r
DESCRIPTION:Details: Lab\\nStaff: Davis B\r
LOCATION:L101\\, L114 (McNulty Building\\, Glasnevin)\r
END:VEVENT\r
END:VCALENDAR\r
"""

L114_ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:x1
DTSTART:20260924T080000Z
DTEND:20260924T090000Z
SUMMARY:CSC1012 Problem-solving\\,Creativity & Critical Thinking (Lab)
LOCATION:LG25\\, LG26\\, L101\\, L114\\, L125\\, L128\\, L129 (McNulty Building\\, Glasnevin)
END:VEVENT
BEGIN:VEVENT
UID:x2
DTSTART:20260924T150000Z
DTEND:20260924T170000Z
SUMMARY:CSC1168 Programming for Mathematics (Lab)
DESCRIPTION:A long description that has been folded onto
  a second line
LOCATION:L101\\, L114 (McNulty Building\\, Glasnevin)
END:VEVENT
END:VCALENDAR
"""

EMPTY_ICS = "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"


@pytest.fixture
def mock_api():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/category/location/items").respond(json=ROOMS)
        mock.get(f"/category/location/items/{L101}/events").respond(text=L101_ICS)
        mock.get(f"/category/location/items/{L114}/events").respond(text=L114_ICS)
        mock.get(url__regex=r".*/items/[0-9a-f-]+/events.*").respond(text=EMPTY_ICS)
        yield mock


@pytest.fixture
def client(mock_api):
    app.state.timetable = TimetableClient(httpx.AsyncClient(base_url=BASE))
    with TestClient(app) as c:
        yield c
    del app.state.timetable


# ---------------------------------------------------------------- unit tests


def test_parse_ics():
    events = parse_ics(L101_ICS)
    assert len(events) == 3
    e = events[0]
    assert e.title == "CSC1012 Problem-solving,Creativity & Critical Thinking (Lab)"
    assert e.description == "Details: Lab\nStaff: McKenna J"
    assert e.start == datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
    assert e.location.endswith("(McNulty Building, Glasnevin)")
    folded = parse_ics(L114_ICS)[1]
    assert folded.description == "A long description that has been folded onto a second line"


def test_building_codes():
    rooms = {r.name: r for r in build_rooms(ROOMS)}
    assert rooms["GLA.L101"].building == "L"
    assert rooms["GLA.LG25"].building == "L"  # ground floor of L
    assert rooms["GLA.SB12 & SB12-A"].building == "S"  # basement of S
    assert rooms["GLA.C117 & C122"].building == "C"
    assert rooms["AHC.ODG01"].building == "ODG"  # no "OD" building, so keep as-is
    assert rooms["SPC.B103"].campus == "SPC"


def test_availability_logic():
    ev = parse_ics(L101_ICS)
    utc = lambda h, m=0: datetime(2026, 9, 24, h, m, tzinfo=timezone.utc)  # noqa: E731
    hour = timedelta(hours=1)

    a = availability(ev, utc(10), hour)  # between classes, next at 12:00
    assert a["status"] == "free" and a["free_until"] == utc(12)

    a = availability(ev, utc(11, 30), hour)  # free now but class at 12:00
    assert a["status"] == "free_short"

    a = availability(ev, utc(13), hour)
    assert a["status"] == "busy" and a["busy_until"] == utc(14)

    a = availability(ev, utc(17), hour)  # after last class
    assert a["status"] == "free" and a["free_until"] is None


# ---------------------------------------------------------------- API tests


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200 and "DCU Free Rooms" in r.text


def test_campuses(client):
    data = client.get("/api/campuses").json()
    gla = next(c for c in data if c["code"] == "GLA")
    assert gla["name"] == "Glasnevin"
    assert {b["code"] for b in gla["buildings"]} == {"L", "C", "S"}


def test_search_rooms(client):
    names = [r["name"] for r in client.get("/api/rooms", params={"q": "l1"}).json()]
    assert names == ["GLA.L101", "GLA.L114"]


def test_room_detail(client):
    # 12:30 Irish time = 11:30 UTC -> free, but a class starts at 13:00 Irish time
    r = client.get(f"/api/rooms/{L101}", params={"date": "2026-09-24", "time": "12:30", "duration": 60})
    d = r.json()
    assert r.status_code == 200, d
    assert d["status"] == "free_short"
    assert d["free_until"] == "2026-09-24T13:00:00+01:00"
    assert d["next_event"]["title"].startswith("CSC1048")
    assert len(d["events"]) == 3
    assert d["building_name"] == "McNulty Building"


def test_status_for_building(client, mock_api, monkeypatch):
    monkeypatch.setattr(main, "current_time", lambda: datetime(2026, 9, 24, 14, 0, tzinfo=config.LOCAL_TZ))
    r = client.get("/api/status", params={"campus": "GLA", "building": "L"})
    d = r.json()
    assert r.status_code == 200, d
    assert [(x["room"]["code"], x["status"]) for x in d["rooms"]] == [
        ("L114", "free"), ("LG25", "free"), ("L101", "busy"),
    ]
    assert d["counts"] == {"total": 3, "free": 2}
    #free or busy
    assert set(d["rooms"][0]) == {"room", "status"}

    #cache call
    calls = mock_api.calls.call_count
    client.get("/api/status", params={"campus": "GLA", "building": "L"})
    assert mock_api.calls.call_count == calls


def test_status_search(client, monkeypatch):
    monkeypatch.setattr(main, "current_time", lambda: datetime(2026, 9, 24, 9, 30, tzinfo=config.LOCAL_TZ))
    d = client.get("/api/status", params={"q": "l1"}).json()
    assert [(x["room"]["code"], x["status"]) for x in d["rooms"]] == [("L101", "busy"), ("L114", "busy")]


def test_status_needs_a_filter(client):
    assert client.get("/api/status").status_code == 400


def test_upstream_down(mock_api):
    mock_api.get("/category/location/items").mock(side_effect=httpx.ConnectError("boom"))
    app.state.timetable = TimetableClient(httpx.AsyncClient(base_url=BASE))
    with TestClient(app) as c:
        r = c.get("/api/campuses")
    del app.state.timetable
    assert r.status_code == 502


def test_unknown_room(client):
    assert client.get("/api/rooms/nope").status_code == 404