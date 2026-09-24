import os
from zoneinfo import ZoneInfo

# Base URL of the Redbrick timetable API (docs: https://timetable.redbrick.dcu.ie/api/docs)
TIMETABLE_API_BASE = os.getenv("TIMETABLE_API_BASE", "https://timetable.redbrick.dcu.ie/api/v3/timetable")


LOCAL_TZ = ZoneInfo("Europe/Dublin")

# Caching 
ROOM_LIST_TTL = int(os.getenv("ROOM_LIST_TTL", 6 * 60 * 60))  # 6 hours
EVENTS_TTL = int(os.getenv("EVENTS_TTL", 10 * 60))  # 10 minutes

# Max parallel requests scanning a building.
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", 8))

# Safety cap 
MAX_ROOMS_PER_SEARCH = int(os.getenv("MAX_ROOMS_PER_SEARCH", 150))

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", 20))
USER_AGENT = "dcu-free-rooms/1.0 (+FastAPI student project)"

CAMPUS_NAMES = {
    "GLA": "Glasnevin",
    "SPC": "St Patrick's",
    "AHC": "All Hallows",
}