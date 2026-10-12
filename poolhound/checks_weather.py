"""Weather: what is sent, which day is a day, and which source a row believes.

Every case here is a way the weather data could be WRONG while looking right —
a precise location leaving the house, half a day's sun stored as a day's, a
forecast standing in for an observation, a revision that never lands, or a
Celsius figure filed under a column named for Fahrenheit. The fit that will
read this table cannot tell any of those from the truth, so they are refused
here, against a saved response, without spending a call.

The fixtures under poolhound/fixtures/ are SYNTHETIC: a point at sea, invented
figures in Open-Meteo's real response shape (taken from a live response for an
unrelated public location). No household coordinate is in this repository.
"""

import copy
import datetime as dt
import json
import os
import shutil
import tempfile

from .selftest import check, in_deploy_tz

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
# The fixtures' "today": 2026-03-06 is in progress and 2026-03-07 is a forecast.
_TODAY = dt.date(2026, 3, 6)


def _load(name):
    with open(os.path.join(_FIX, name), encoding="utf-8") as f:
        return json.load(f)


def _parsed(name, today=_TODAY):
    from . import weather
    blob = _load(name)
    p = blob["params"]
    return weather.parse(blob["response"], blob["source"], blob["fetched"],
                         float(p["latitude"]), float(p["longitude"]), today)


def t_a_saved_response_parses_into_ended_days_only():
    """The parser, against a saved response, with the day in progress refused.

    THE DAY STILL IN PROGRESS IS NOT A DAY. trajectory() counted today's
    part-run pump hours as a day and talked a salt cell that was over-producing
    into looking correct; a weather row for today would be the same mistake in
    the input the fit trusts most — half an afternoon's sun stored as a day's.
    And a forecast of tomorrow is not an observation of anything.
    """
    from . import weather
    print("\n  weather — a saved response, ended days only")
    rows = _parsed("weather-forecast.json")
    check("today and tomorrow are not rows",
          [r["date"] for r in rows],
          ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"])
    r = {x["date"]: x for x in rows}["2026-03-03"]
    check("every column is one weather.COLS names",
          sorted(set(r) - set(weather.COLS)), [])
    check("sunshine arrives in seconds and is stored in hours",
          r["sunshine_h"], "0.5")
    check("a wet day keeps its rain", r["precip_in"], "0.42")
    check("the forecast model's UV index is read", r["uv_max"], "1.2")
    check("the row says which source it is", r["source"], "forecast")
    check("and where: the rounded pair that was sent",
          (r["lat"], r["lon"]), ("12.3", "45.6"))

    arch = _parsed("weather-archive.json")
    check("an archive day with no figure at all is not an observation",
          [x["date"] for x in arch],
          ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04"])
    check("and reanalysis's absent UV index is blank, not zero",
          {x["uv_max"] for x in arch}, {""})


def t_a_unit_that_is_not_the_one_asked_for_is_refused():
    """A Celsius figure under air_max_f is wrong at the confidence of a right one.

    The request asks for Fahrenheit and inches and the response states its
    units. If they ever disagree, the run fails with the raw file on disk —
    recoverable — instead of writing numbers that are plausible in either unit.
    """
    from . import weather
    print("\n  weather — units are checked, not assumed")
    blob = _load("weather-forecast.json")
    bad = copy.deepcopy(blob["response"])
    bad["daily_units"]["temperature_2m_max"] = "°C"
    refused = ""
    try:
        weather.parse(bad, "forecast", "t", 12.3, 45.6, _TODAY)
    except ValueError as e:
        refused = str(e)
    check("a temperature in °C is refused", "°C" in refused, True)

    gone = copy.deepcopy(blob["response"])
    del gone["daily"]["rain_sum"]
    refused = ""
    try:
        weather.parse(gone, "forecast", "t", 12.3, 45.6, _TODAY)
    except ValueError as e:
        refused = str(e)
    check("a measure missing from the response is refused, not left blank",
          "rain_sum" in refused, True)

    refused = ""
    try:
        weather.parse({"error": True, "reason": "Latitude must be in range"},
                      "archive", "t", 0, 0, _TODAY)
    except ValueError as e:
        refused = str(e)
    check("Open-Meteo's own refusal is carried into the failure",
          "Latitude must be in range" in refused, True)


def t_only_the_rounded_location_can_leave():
    """What is sent is 0.1 degree, and nothing can send more.

    location() is the only producer of a coordinate pair, and it returns the
    rounded one; params() rounds again rather than trusting its caller. So a
    precise coordinate has no path to the request, the raw file or the page.
    """
    from . import weather
    print("\n  weather — what leaves is rounded, and is all that leaves")
    precise = {"weather": {"latitude": 37.42199, "longitude": -122.08405}}
    loc, why = weather.location(precise)
    check("the pair is rounded to 0.1 degree", loc, (37.4, -122.1))
    p = weather.params(37.42199, -122.08405, "forecast")
    check("params() rounds even a precise pair handed to it",
          (p["latitude"], p["longitude"]), ("37.4", "-122.1"))
    check("and sends exactly these keys — no identity, no key",
          sorted(p), sorted(["latitude", "longitude", "daily", "timezone",
                             "temperature_unit", "precipitation_unit",
                             "past_days", "forecast_days"]))
    a = weather.params(*loc, "archive", "2026-03-01", "2026-03-05")
    check("the archive request differs only by its date range",
          sorted(set(a) ^ set(p)),
          sorted(["start_date", "end_date", "past_days", "forecast_days"]))
    told = weather.describe(precise)
    check("the Collection card's sentence carries the rounded pair",
          "latitude 37.4, longitude -122.1" in told, True)
    check("and none of the precision it was given",
          any(s in told + json.dumps(p) for s in ("37.42", "122.08")), False)
    check("a coordinate just under zero is 0.0, not -0.0",
          weather.params(-0.04, 0.04, "forecast")["latitude"], "0.0")
    check("the day is the pool's: the timezone sent is config.DEPLOY_TZ",
          p["timezone"], __import__("poolhound.config", fromlist=["x"]).DEPLOY_TZ)

    check("unset is not set up, and says nothing is sent",
          "nothing is sent" in (weather.location({})[1] or ""), True)
    check("text where a number belongs is refused",
          weather.location({"weather": {"latitude": "home", "longitude": 1}})[0], None)
    check("a latitude past the pole is refused",
          weather.location({"weather": {"latitude": 91, "longitude": 1}})[0], None)


def t_the_documents_state_the_rounding_the_code_applies():
    """The example config, the Guide and the privacy tab promise 0.1 degree.

    The Guide and the privacy tab read weather.ROUND_DEG, so they cannot
    drift. The example config is TOML, which no Python reads for prose, so it
    is the copy that could: a promise of coarser rounding than is applied is
    the worst direction for a privacy statement to be wrong in.
    """
    from . import config, help as helpdoc, policies, weather
    print("\n  weather — every statement of the rounding is the rounding")
    want = f"{weather.ROUND_DEG:g}"
    ex = os.path.join(config.root(), "config", "config.example.toml")
    with open(ex, encoding="utf-8") as f:
        body = f.read()
    check("config.example.toml states the precision weather.py rounds to",
          f"ROUNDED TO {want} DEGREE" in body, True)
    check("the privacy tab states it", f"rounded to {want}°" in policies.panel(), True)
    check("the Guide states it",
          f"rounded to {want}&deg;" in helpdoc.help_panel(public=True), True)


def t_the_archive_supersedes_and_the_forecast_fills_a_blank():
    """Which source a date believes, and that nothing it lost is lost silently.

    Archive outranks forecast whole; at equal rank the newer fetch wins,
    because a revision is a correction. A field the winner left blank is taken
    from the loser and NAMED in `carried`, so a UV index on an archive day says
    it is the forecast model's.
    """
    from . import weather
    print("\n  weather — dedupe on the date, and which source wins")
    fc, ar = _parsed("weather-forecast.json"), _parsed("weather-archive.json")
    m, n = weather.merge([], fc)
    m, n = weather.merge(m, ar)
    by = {r["date"]: r for r in m}
    check("one row per date", len(m), len({r["date"] for r in m}))
    check("the archive wins a date both describe", by["2026-03-03"]["source"], "archive")
    check("with the archive's own figure", by["2026-03-03"]["precip_in"], "0.57")
    check("and the forecast's UV index is carried, not lost",
          by["2026-03-03"]["uv_max"], "1.2")
    check("and the row says so", by["2026-03-03"]["carried"], "uv_max:forecast")
    check("a day the archive does not have yet stays the forecast's",
          by["2026-03-05"]["source"], "forecast")
    check("the counts are against the file: three revised, one added",
          (n["revised"], n["added"]), (3, 1))

    again, n2 = weather.merge(m, fc + ar)
    check("a re-run of the same responses changes nothing", again, m)
    check("and says so", (n2["added"], n2["revised"]), (0, 0))

    late_fc, _ = weather.merge(m, fc)
    check("a forecast arriving after the archive does not displace it",
          {r["date"]: r["source"] for r in late_fc}["2026-03-02"], "archive")

    revised = [dict(r, precip_in="0.61", fetched="later")
               for r in ar if r["date"] == "2026-03-03"]
    after, n3 = weather.merge(m, revised)
    check("an archive revision replaces the earlier archive figure",
          {r["date"]: r for r in after}["2026-03-03"]["precip_in"], "0.61")
    check("and keeps the UV index it carried, still credited to the forecast",
          {r["date"]: r for r in after}["2026-03-03"]["carried"], "uv_max:forecast")
    check("one revision counted", n3["revised"], 1)

    moved = [dict(r, lat="12.5", uv_max="", fetched="later")
             for r in ar if r["date"] == "2026-03-03"]
    after, _ = weather.merge(m, moved)
    row = {r["date"]: r for r in after}["2026-03-03"]
    check("a moved location replaces the row and carries nothing from the old place",
          (row["lat"], row["uv_max"], row["carried"]), ("12.5", "", ""))


def t_the_day_is_the_pools_day():
    """The calendar date is the pool's, whatever zone the host is in.

    07:30 UTC on 6 March is still 5 March at a Pacific pool. A host on UTC
    that took its own date would call the 5th ended and store a day whose
    evening had not happened — the partial-day defect by the timezone door.
    """
    from . import render, weather
    print("\n  weather — the day is the pool's, and a reader refuses a partial one")
    utc = dt.datetime(2026, 3, 6, 7, 30, tzinfo=dt.timezone.utc)
    check("07:30Z on the 6th is the 5th at the pool", weather.today_local(utc),
          dt.date(2026, 3, 5))
    check("so the 5th is not yet a day",
          [r["date"] for r in _parsed("weather-forecast.json",
                                      today=weather.today_local(utc))][-1],
          "2026-03-04")
    with in_deploy_tz():
        got = render.weather_by_day(
            [{"date": "2026-03-04", "source": "archive", "sun_mj_m2": "15.7",
              "precip_in": ""},
             {"date": "2026-03-06", "source": "forecast", "sun_mj_m2": "9.1"},
             {"date": "not a date"}],
            today=_TODAY)
    check("the loader keys ended days by date", sorted(got), ["2026-03-04"])
    check("a figure is a float", got["2026-03-04"]["sun_mj_m2"], 15.7)
    check("and an absent one is None, never zero",
          got["2026-03-04"]["precip_in"], None)
    check("a day row says provisional for the forecast's figure",
          render.weather_line({"sun_mj_m2": 9.0, "air_max_f": 60.0,
                               "air_min_f": 45.0, "source": "forecast"}).endswith(
              "provisional"), True)
    check("and names rain only when there was any",
          "rain" in render.weather_line({"sun_mj_m2": 9.0, "precip_in": 0.0,
                                          "source": "archive"}), False)


def t_the_archive_is_asked_for_what_is_missing_and_what_may_be_revised():
    """Idempotent backfill: the whole history once, then only what can change."""
    from . import weather
    print("\n  weather — the archive window")
    today = dt.date(2026, 3, 20)
    start = dt.date(2026, 1, 1)
    check("an empty file asks from the pool's first day to yesterday",
          weather.archive_window([], start, today), ("2026-01-01", "2026-03-19"))
    full = [{"date": (start + dt.timedelta(days=i)).isoformat(), "source": "archive"}
            for i in range((today - start).days)]
    check("a complete file asks only for the days that may still be revised",
          weather.archive_window(full, start, today), ("2026-03-10", "2026-03-19"))
    holed = [r for r in full if r["date"] != "2026-02-01"]
    check("a hole is asked for again, from the hole",
          weather.archive_window(holed, start, today)[0], "2026-02-01")
    fc_only = [dict(r, source="forecast") for r in full]
    check("a forecast row does not count as the archive having the day",
          weather.archive_window(fc_only, start, today)[0], "2026-01-01")
    check("no history asks for the settling window alone",
          weather.archive_window([], None, today), ("2026-03-10", "2026-03-19"))


def t_a_run_from_saved_files_writes_the_pools_days_once():
    """The whole collector, offline: --from-file, clipped to the pool, re-runnable.

    Run against a scratch data directory holding one sample, so the history
    begins on 2 March: the archive's 1 March is weather over a pool this
    product never saw, and is not written.
    """
    from . import config, locking, weather
    print("\n  weather — bin/weather --from-file, twice")
    d = tempfile.mkdtemp()
    prev = os.environ.get("POOLHOUND_DATA")
    try:
        os.environ["POOLHOUND_DATA"] = d
        with open(os.path.join(d, "samples.csv"), "w") as f:
            f.write("ts,pump\n2026-03-02T09:00:00-0800,1\n")
        files = [os.path.join(_FIX, "weather-archive.json"),
                 os.path.join(_FIX, "weather-forecast.json")]
        with in_deploy_tz():
            # The fixtures' today, whatever today is: the 7th is a forecast in
            # them and would otherwise be an ended day by the time this runs.
            real = weather.today_local
            weather.today_local = lambda now=None: _TODAY
            try:
                rc, s = weather.collect(["--from-file", *files])
                rc2, s2 = weather.collect(["--from-file", *files])
            finally:
                weather.today_local = real
        with open(os.path.join(d, weather.FILE), newline="") as f:
            import csv
            got = list(csv.DictReader(f))
            header = list(got[0].keys()) if got else []
        check("it ran", (rc, rc2), (0, 0))
        check("the header is weather.COLS", header, weather.COLS)
        check("nothing before the pool's first day, nothing not yet ended",
              [r["date"] for r in got],
              ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"])
        check("the first run added four days", s["added"], 4)
        check("the second changed nothing", (s2["added"], s2["revised"]), (0, 0))
        check("and weather.csv is not one of the history guard's files",
              weather.FILE in config.HISTORY_FILES, False)
    finally:
        if prev is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev
        shutil.rmtree(d, ignore_errors=True)


def t_an_optional_source_nobody_set_up_is_not_broken():
    """No coordinates is "not set up", never "never run" — and never a banner."""
    from . import collection, weather
    print("\n  weather — the Collection card when nobody has opted in")
    real = weather.location
    try:
        weather.location = lambda cfg=None: (None, "not set up — nothing is sent")
        card = {c["tool"]: c for c in collection.summary([])}["weather"]
        check("the card says not set up", card["state"], "off")
        check("which needs nobody's attention",
              card["state"] in collection.NEEDS_ATTENTION, False)
        check("and its pill carries the words, not only a colour",
              "not set up" in collection._pill(card["state"], card["text"]), True)
        check("and it claims to send nothing", collection._sends(card), "")

        weather.location = lambda cfg=None: ((36.0, -120.0), None)
        card = {c["tool"]: c for c in collection.summary([])}["weather"]
        check("set up and never run is graded like any source",
              card["state"], "never")
        check("and the card prints what it sends",
              "latitude 36.0, longitude -120.0" in collection._sends(card), True)
    finally:
        weather.location = real
