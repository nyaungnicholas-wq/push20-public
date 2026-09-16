"""Exchange-session arithmetic: ET dates, the next session, trading-day counts.

WHY. `rotation_live` derived every date from `date.today()`, which on this
machine is PACIFIC. That was survivable while the job ran at 07:00 PT, because
the PT and ET dates agree at midday. Evening mode breaks it: a run at 18:30 PT
is 21:30 ET, and after 21:00 PT the ET date is already TOMORROW. The state key,
the hold count and the ledger would all disagree with the exchange.

This is the same defect fixed in `execution/execute.py`, where the session date
came from UTC and silently disabled the order-idempotency guard for four hours
every evening.

CALENDAR SCOPE. Weekends plus the NYSE full-day holidays are handled here.
Half-days are NOT in this table -- they still trade, so they are sessions, and
the only thing a half-day changes is the close time, which the evening mode does
not depend on. Alpaca's own `/v2/clock` remains the authority on whether the
market is open right now; this module answers "which session is it", which a
clock cannot.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# NYSE full-day closures. Extend as needed; an unknown future year degrades to
# weekends-only, which OVER-counts sessions rather than under-counting them --
# the safe direction, since it makes the hold gate stricter, never looser.
_HOLIDAYS = {
    # 2025
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27",
    "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}


def now_et(now=None):
    """Current time in exchange time. `now` may be any aware datetime."""
    if now is None:
        return datetime.now(ET)
    return now.astimezone(ET)


def et_date(now=None):
    """Today's ET calendar date -- NOT the local date, and not the UTC date."""
    return now_et(now).date()


def is_session(d):
    """True if `d` is a trading session (weekday, not a full-day holiday)."""
    return d.weekday() < 5 and d.isoformat() not in _HOLIDAYS


def next_session(after):
    """The first session strictly after `after`."""
    d = after + timedelta(days=1)
    while not is_session(d):
        d += timedelta(days=1)
    return d


def prev_session(before):
    """The last session strictly before `before`."""
    d = before - timedelta(days=1)
    while not is_session(d):
        d -= timedelta(days=1)
    return d


def last_closed_session(now=None):
    """The most recent session whose close has passed, in ET.

    Before 16:00 ET on a session day the current day has NOT closed, so the
    answer is the previous session. This is what evening mode trades on: the
    data it decides from must be a completed session, never a partial one.
    """
    t = now_et(now)
    today = t.date()
    if is_session(today) and t.hour >= 16:
        return today
    return prev_session(today)


def sessions_between(start, end):
    """Count sessions strictly after `start` up to and including `end`.

    This is the TRADING-day count. `rotation_live` used calendar days while the
    certifying backtest (`reports/opt_harness.py`) indexes trading days, so
    Friday to Monday read as 3 to live and 1 to the backtest -- live rebalanced
    where the backtest would not.
    """
    if end <= start:
        return 0
    n, d = 0, start + timedelta(days=1)
    while d <= end:
        if is_session(d):
            n += 1
        d += timedelta(days=1)
    return n


def demo():
    from datetime import timezone

    # --- ET date is not the local or UTC date ---
    # 2026-08-31 01:00Z is 2026-08-30 21:00 ET: still the 30th in ET.
    t = datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc)
    assert t.strftime("%Y-%m-%d") == "2026-08-31", "UTC says the 31st"
    assert et_date(t).isoformat() == "2026-08-30", "ET must still say the 30th"

    # DST comes from the zone, not a fixed offset
    assert et_date(datetime(2026, 7, 1, 23, 0, tzinfo=timezone.utc)).isoformat() \
        == "2026-07-01"
    assert et_date(datetime(2026, 12, 1, 23, 0, tzinfo=timezone.utc)).isoformat() \
        == "2026-12-01"

    # --- sessions ---
    assert is_session(date(2026, 8, 31))            # Monday
    assert not is_session(date(2026, 8, 29))        # Saturday
    assert not is_session(date(2026, 8, 30))        # Sunday
    assert not is_session(date(2026, 12, 25))       # Christmas
    assert not is_session(date(2026, 11, 26))       # Thanksgiving

    assert next_session(date(2026, 8, 28)) == date(2026, 8, 31), "Fri -> Mon"
    assert next_session(date(2026, 8, 30)) == date(2026, 8, 31), "Sun -> Mon"
    assert next_session(date(2026, 12, 24)) == date(2026, 12, 28), "over Christmas"
    assert prev_session(date(2026, 8, 31)) == date(2026, 8, 28), "Mon -> Fri"

    # --- last closed session: the 16:00 ET boundary ---
    mon_1530 = datetime(2026, 8, 31, 15, 30, tzinfo=ET)
    mon_1800 = datetime(2026, 8, 31, 18, 0, tzinfo=ET)
    assert last_closed_session(mon_1530) == date(2026, 8, 28), \
        "before the close, today has not closed yet"
    assert last_closed_session(mon_1800) == date(2026, 8, 31), \
        "after the close, today is the completed session"
    sun = datetime(2026, 8, 30, 18, 0, tzinfo=ET)
    assert last_closed_session(sun) == date(2026, 8, 28), "Sunday -> Friday"

    # --- trading days vs calendar days: the whole point ---
    fri, mon = date(2026, 8, 28), date(2026, 8, 31)
    assert (mon - fri).days == 3, "calendar says 3"
    assert sessions_between(fri, mon) == 1, "trading days say 1"

    # a full week
    assert sessions_between(date(2026, 8, 24), date(2026, 8, 28)) == 4
    # across a holiday: Thanksgiving week 2026-11-26 is closed
    assert sessions_between(date(2026, 11, 24), date(2026, 11, 27)) == 2
    assert sessions_between(mon, mon) == 0, "no elapsed sessions"
    assert sessions_between(mon, fri) == 0, "end before start is 0, never negative"


if __name__ == "__main__":
    demo()
    print("OK")
