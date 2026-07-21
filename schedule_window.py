"""Restrict the watcher to certain days and hours.

Why this exists as Python rather than just a cron expression: GitHub Actions
cron is UTC-only and has no concept of daylight saving. A window written as
"00:00-05:00 America/Los_Angeles" shifts by an hour in UTC twice a year, so a
fixed UTC cron would silently drift off the intended window every spring and
autumn. Doing the final check here keeps the window correct year round.

The cron in the workflow still narrows things roughly, so most out-of-window
runs never start at all; this catches the edges.

Windows that cross midnight are supported: start 22:00, end 05:00 means
"10pm until 5am the following morning", and the day filter applies to the day
the window STARTED.
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime
from typing import Optional

log = logging.getLogger(__name__)

DAY_NAMES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def _parse_days(days) -> set[int]:
    if not days:
        return set(range(7))
    parsed = set()
    for entry in days:
        key = str(entry).strip().lower()
        if key in DAY_NAMES:
            parsed.add(DAY_NAMES[key])
        else:
            log.warning("Unrecognised day %r in schedule - ignoring it", entry)
    return parsed or set(range(7))


def _parse_time(value: str, fallback: dtime) -> dtime:
    try:
        hours, _, minutes = str(value).partition(":")
        return dtime(int(hours), int(minutes or 0))
    except (TypeError, ValueError):
        log.warning("Could not read time %r in schedule - using %s", value, fallback)
        return fallback


def now_in_zone(timezone: str) -> datetime:
    """Local time in the configured zone, falling back to UTC if unavailable."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(timezone))
    except Exception as exc:  # noqa: BLE001 - unknown zone, or missing tzdata
        log.warning(
            "Timezone %r unusable (%s) - falling back to UTC, so the window may be off. "
            "On a slim container, `pip install tzdata` fixes this.",
            timezone,
            exc,
        )
        return datetime.utcnow()


def check_window(config: dict, moment: Optional[datetime] = None) -> tuple[bool, str]:
    """Return (should_run, human explanation)."""
    schedule = config.get("schedule") or {}

    if not schedule.get("enabled"):
        return True, "no run window configured - checking around the clock"

    timezone = schedule.get("timezone", "UTC")
    now = moment or now_in_zone(timezone)

    start = _parse_time(schedule.get("start", "00:00"), dtime(0, 0))
    end = _parse_time(schedule.get("end", "23:59"), dtime(23, 59))
    days = _parse_days(schedule.get("days"))

    current = now.time()
    overnight = start > end

    if overnight:
        # e.g. 22:00 -> 05:00. Before midnight the day is today's; after
        # midnight it belongs to the window that opened yesterday.
        if current >= start:
            in_hours, window_day = True, now.weekday()
        elif current < end:
            in_hours, window_day = True, (now.weekday() - 1) % 7
        else:
            in_hours, window_day = False, now.weekday()
    else:
        in_hours = start <= current < end
        window_day = now.weekday()

    stamp = now.strftime("%a %H:%M %Z").strip()
    window = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')} {timezone}"

    if not in_hours:
        return False, f"outside the run window ({stamp}; window is {window})"
    if window_day not in days:
        return False, f"not a scheduled day ({stamp}; window is {window})"

    return True, f"inside the run window ({stamp}; window is {window})"