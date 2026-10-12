"""Daily weather at the pool, from Open-Meteo -> data/weather.csv.

WHY A WEATHER SOURCE AT ALL
  THEORY.md's honest limits name two holes in the chemistry fit that no pool
  instrument can close. Sun and temperature move together, so `k_uv` cannot be
  told apart from `k_temp` without a measure of the sun that is not also a
  measure of heat — and UV loss is the dominant free-chlorine term, so that is
  the coefficient it matters most to get right. And cyanuric acid is "diluted
  by rain and backwashing" but is held constant between lab results, because
  nothing recorded when it rained. Evaporation runs the other way: the pool
  loses water and concentrates salt, CYA and calcium until a refill dilutes
  them. Each of those is a daily quantity, so this is a daily table.

  This module is the DATA LAYER for a fit that does not exist yet. What it has
  to get right is that each row is a real, complete, attributed day; the page
  shows a line of it and no more.

WHAT IS SENT, EXACTLY
  Latitude and longitude ROUNDED to ROUND_PLACES decimal places — 0.1 degree,
  about eleven kilometres north-south — plus the pool's timezone, the names of
  the daily measures and the units. Nothing else: no key (there is none), no
  identity, no address. params() is the one place a request is spelled, and the
  Collection tab prints its output rather than a description of it, on the
  principle the Ask AI tab already follows: what leaves is on the screen.

  Rounding is not decoration. Weather does not change across a street; a
  four-decimal coordinate identifies a house. The grid Open-Meteo reads from is
  coarser than 0.1 degree anyway, so the rounding costs the data nothing.

TWO ENDPOINTS, AND WHICH ONE WINS
  archive   archive-api: reanalysis. The authority for any day it covers. Its
            last few days are preliminary and get revised, so they are re-asked
            for until SETTLE_DAYS have passed.
  forecast  api: the forecast model's own record of days just gone. Arrives the
            morning after; the archive may not have that day yet. It also carries
            UV index, which reanalysis does not, so it is asked for the last
            PAST_DAYS every run.

  A row is keyed on its DATE, like the lab collectors key on their measurement
  time. When two rows describe one date the higher-ranked SOURCE wins whole
  (archive over forecast); at equal rank the newer fetch wins, because a
  revision is a correction. The loser is not thrown away field by field: a
  measure the winner left BLANK is filled from it, and the row's `carried`
  column names each field so filled and where it came from. That is how a UV
  index survives the archive superseding a forecast-era day — and how a reader
  can tell that one number on an archive row was not the archive's.

  Forecasts of days to come are never stored. Only days that have ENDED in
  config.DEPLOY_TZ are written — "the day still in progress is not a day", the
  rule trajectory() learned the expensive way — so today's half-measured sun
  and tomorrow's predicted rain never reach a table the fit will read as
  observation.

WHY THIS FILE IS NOT IN config.HISTORY_FILES
  That list is what seed_config() asks to decide whether a data directory is a
  pool or a lost share. This collector writes into an EMPTY directory happily —
  its first run is a backfill — so on a share that failed to mount it would
  create weather.csv on local disk, and the next container start would read
  that as a history and seed over it. Exactly the second-pool-history defect
  that guard exists for, arriving through a new door. Weather is also
  re-fetchable, which no reading is.

  It IS in the nightly backup (bin/backup's EXTRA): it is small, the UV values
  carried from the forecast model are not re-fetchable once they fall out of
  its window, and a fit should be reproducible from the inputs it was given.
"""
import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config
from . import locking

FILE = "weather.csv"

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# 0.1 degree. The precision is the privacy decision, so it is a named number
# that the Guide, the Safety & privacy tab and the config example all read
# rather than restate.
ROUND_PLACES = 1
ROUND_DEG = 10 ** -ROUND_PLACES

# The archive's newest days are preliminary. Anything this close to yesterday
# is asked for again on each run, so a revision replaces the early figure.
SETTLE_DAYS = 10
# How far back the forecast model is asked, every run. 92 is the API's own
# ceiling; asked for in full because it is one small response and it is the
# only place a UV index for a recent day can come from.
PAST_DAYS = 92
# The longest backfill one run will ask the archive for. A history older than
# this is unusual and the next run continues from where this one stopped.
MAX_SPAN_DAYS = 3 * 366

# Open-Meteo's daily field -> (our column, the unit it must arrive in, scale).
#
# UNITS ARE CHECKED, NOT ASSUMED. The request asks for Fahrenheit and inches
# because this pool is measured in gallons and degrees F; the response states
# its units in `daily_units`, and a mismatch refuses the whole response rather
# than storing a Celsius figure under a column named _f. `None` accepts any
# unit — the UV index has none, and the archive spells that "undefined".
DAILY = {
    "shortwave_radiation_sum":    ("sun_mj_m2",  "MJ/m²", 1.0),
    "sunshine_duration":          ("sunshine_h", "s",     1 / 3600.0),
    "uv_index_max":               ("uv_max",     None,    1.0),
    "cloud_cover_mean":           ("cloud_pct",  "%",     1.0),
    "temperature_2m_max":         ("air_max_f",  "°F",    1.0),
    "temperature_2m_min":         ("air_min_f",  "°F",    1.0),
    "temperature_2m_mean":        ("air_mean_f", "°F",    1.0),
    "precipitation_sum":          ("precip_in",  "inch",  1.0),
    "rain_sum":                   ("rain_in",    "inch",  1.0),
    "et0_fao_evapotranspiration": ("et0_in",     "inch",  1.0),
}
MEASURES = [col for col, _u, _s in DAILY.values()]

# THE COLUMN LIST, ONCE. Every reader and the demo generator take it from here.
#
# date      the pool's calendar day, in config.DEPLOY_TZ
# source    archive | forecast — which endpoint this row's figures are from
# fetched   when we asked, local with offset
# lat, lon  the ROUNDED coordinates that were sent, so a row says where it is
#           about and a moved config is visible in the file
# carried   fields filled from a superseded row, as field:source;...
COLS = ["date", "source", "fetched", "lat", "lon"] + MEASURES + ["carried"]

RANK = {"forecast": 1, "archive": 2}
SOURCE_LABEL = {"archive": "archive (reanalysis)",
                "forecast": "forecast model, provisional"}

# Where the pool's history begins: the earliest date in any file that only a
# running pool produces. config.HISTORY_FILES is that list, and each file's
# timestamp column is named here.
_HISTORY_TIME = {"samples.csv": "ts", "readings.csv": "measured",
                 "leslies.csv": "measured", "lab.csv": "measured",
                 "chemicals.csv": "ts", "manual.csv": "measured"}


# ------------------------------------------------------------------ location
def location(cfg=None):
    """((lat, lon), None) rounded for sending, or (None, why it cannot be).

    The ROUNDED pair is the only one that leaves this function, so no caller
    can send the precise one by accident — there is nothing else to send.
    """
    cfg = cfg if cfg is not None else config.load()
    sec = cfg.get("weather") or {}
    lat, lon = sec.get("latitude"), sec.get("longitude")
    if lat in (None, "") and lon in (None, ""):
        return None, ("not set up — no [weather] latitude and longitude in "
                      "config.toml, so nothing is sent")
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, "[weather] latitude and longitude must both be numbers"
    if not (math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None, "[weather] latitude must be -90..90 and longitude -180..180"
    return (rounded(lat), rounded(lon)), None


def configured(cfg=None):
    return location(cfg)[0] is not None


def rounded(v):
    """To ROUND_PLACES, and never "-0.0" — a sign on zero is noise on the page."""
    r = round(float(v), ROUND_PLACES)
    return 0.0 if r == 0 else r


def params(lat, lon, source, start=None, end=None):
    """The query string's contents, and the whole of what is sent.

    lat/lon must already be rounded — location() is the only thing that
    produces them — and this rounds again anyway, because a function that
    states "exactly what is sent" should not depend on its caller to make it
    true.
    """
    p = {"latitude": f"{rounded(lat):.{ROUND_PLACES}f}",
         "longitude": f"{rounded(lon):.{ROUND_PLACES}f}",
         "daily": ",".join(DAILY),
         "timezone": config.DEPLOY_TZ,
         "temperature_unit": "fahrenheit",
         "precipitation_unit": "inch"}
    if source == "archive":
        p["start_date"], p["end_date"] = start, end
    else:
        p["past_days"] = str(PAST_DAYS)
        p["forecast_days"] = "1"
    return p


def describe(cfg=None):
    """What a run sends, as a sentence for the Collection card."""
    loc, why = location(cfg)
    if loc is None:
        return why
    return (f"latitude {loc[0]:.{ROUND_PLACES}f}, longitude "
            f"{loc[1]:.{ROUND_PLACES}f} (rounded to {ROUND_DEG:g}°), the "
            f"timezone {config.DEPLOY_TZ}, and the names of "
            f"{len(DAILY)} daily measures. No key, no identity, no address.")


# ---------------------------------------------------------------------- time
def today_local(now=None):
    """The pool's calendar date. A day before this one has ended."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(config.DEPLOY_TZ)
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.astimezone()
    return now.astimezone(tz).date()


def history_start(data):
    """The first date the pool has any record for, or None."""
    from .render import when
    first = None
    for name, col in _HISTORY_TIME.items():
        p = os.path.join(data, name)
        if not os.path.exists(p):
            continue
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = when(r.get(col) or "")
                if d and (first is None or d.date() < first):
                    first = d.date()
    return first


def archive_window(existing, start, today, settle=SETTLE_DAYS):
    """(start_date, end_date) to ask the archive for, or None.

    Every ended day from `start` that has no ARCHIVE row, plus the last
    `settle` days whatever they hold (their archive figures are preliminary).
    One contiguous range from the earliest of those to yesterday — the archive
    answers a range in one call, and a hole in the middle is cheaper to re-read
    than to describe.
    """
    yesterday = today - dt.timedelta(days=1)
    if start is None or start > yesterday:
        start = yesterday - dt.timedelta(days=settle - 1)
    start = max(start, yesterday - dt.timedelta(days=MAX_SPAN_DAYS))
    have = {r.get("date") for r in existing if r.get("source") == "archive"}
    want = None
    d = start
    while d <= yesterday:
        if d.isoformat() not in have or (yesterday - d).days < settle:
            want = d
            break
        d += dt.timedelta(days=1)
    if want is None:
        return None
    return want.isoformat(), yesterday.isoformat()


# ------------------------------------------------------------------- parsing
def parse(payload, source, fetched, lat, lon, today):
    """Rows for every ENDED day in one Open-Meteo response.

    Raises ValueError on a response whose shape or units are not the ones
    asked for — a refusal is a run that failed with the raw file on disk, which
    is recoverable; a wrong unit stored under a column named for another is not.

    A day with no figure at all is not a row: the archive answers null for days
    it does not have yet, and an all-blank row would claim a day was observed.
    """
    if source not in RANK:
        raise ValueError(f"unknown source {source!r}")
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError(f"Open-Meteo refused: {(payload or {}).get('reason', payload)!r}"[:300])
    daily = payload.get("daily")
    units = payload.get("daily_units") or {}
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        raise ValueError("no daily block in the response")
    for field, (col, unit, _scale) in DAILY.items():
        if field not in daily:
            raise ValueError(f"the response has no {field}")
        if unit is not None and units.get(field) != unit:
            raise ValueError(f"{field} arrived in {units.get(field)!r}, not {unit!r} "
                             f"— refusing rather than storing it under {col}")
    out = []
    for i, day in enumerate(daily["time"]):
        try:
            d = dt.date.fromisoformat(str(day))
        except ValueError:
            continue
        if d >= today:
            continue                     # not over yet, or not happened yet
        row = {"date": d.isoformat(), "source": source, "fetched": fetched,
               "lat": f"{rounded(lat):.{ROUND_PLACES}f}",
               "lon": f"{rounded(lon):.{ROUND_PLACES}f}", "carried": ""}
        any_value = False
        for field, (col, _unit, scale) in DAILY.items():
            vals = daily[field]
            v = vals[i] if isinstance(vals, list) and i < len(vals) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                row[col] = _fmt(v * scale)
                any_value = True
            else:
                row[col] = ""
        if any_value:
            out.append(row)
    return out


def _fmt(v):
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# ------------------------------------------------------------------- merging
def _parse_carried(s):
    out = {}
    for part in (s or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def combine(old, new):
    """The one row a date keeps when two describe it, and why.

    Returns (row, verdict): "added" for a new date, "superseded" when the new
    row won and differs, "filled" when the old row won but took a blank from
    the new one, "unchanged" or "kept" when nothing visible moved. See the
    module docstring for the rule.
    """
    if old is None:
        return dict(new), "added"
    same_place = (old.get("lat"), old.get("lon")) == (new.get("lat"), new.get("lon"))
    # A MOVED LOCATION IS NOT A REVISION. If the config now names a different
    # place, the old row describes somewhere else and nothing of it is carried.
    if not same_place:
        return dict(new), "superseded"
    r_old, r_new = RANK.get(old.get("source"), 0), RANK.get(new.get("source"), 0)
    win, lose = (new, old) if r_new >= r_old else (old, new)
    row = dict(win)
    carried = _parse_carried(win.get("carried"))
    for col in MEASURES:
        if (row.get(col) or "") == "" and (lose.get(col) or "") != "":
            row[col] = lose[col]
            # Where the value ORIGINALLY came from, not merely the row it was
            # last copied out of — a UV index carried twice is still the
            # forecast model's.
            carried[col] = _parse_carried(lose.get("carried")).get(col) or lose.get("source", "")
    row["carried"] = ";".join(f"{k}:{v}" for k, v in sorted(carried.items()))
    if all((row.get(c) or "") == (old.get(c) or "")
           for c in MEASURES + ["source", "carried"]):
        # Nothing a reader could see has changed; keep the old row, fetched
        # stamp and all, so a re-run is not a rewrite of the whole file.
        return dict(old), ("kept" if win is old else "unchanged")
    # The old row won but took a blank from the new one — a UV index from the
    # forecast landing on an archive day. A change, and the file is rewritten.
    return row, ("filled" if win is old else "superseded")


_VISIBLE = MEASURES + ["source", "carried", "lat", "lon"]


def merge(existing, incoming):
    """(merged rows sorted by date, counts). Pure.

    THE COUNTS ARE AGAINST THE FILE, not against the fold. Within one run the
    forecast lands first and the archive then supersedes it, so counting each
    combine() reported a first backfill of 73 days as "73 new, 46 revised" and
    the run log's `added` as 119 — more rows than the file holds. What a reader
    of runs.csv wants is how the FILE changed: dates it did not have, dates
    whose row now reads differently, and dates that came back the same.
    """
    before = {r["date"]: r for r in existing if r.get("date")}
    by_date = {k: dict(v) for k, v in before.items()}
    for r in incoming:
        by_date[r["date"]], _verdict = combine(by_date.get(r["date"]), r)
    counts = {"added": 0, "revised": 0, "unchanged": 0}
    for d in {r["date"] for r in incoming}:
        if d not in before:
            counts["added"] += 1
        elif any((by_date[d].get(c) or "") != (before[d].get(c) or "")
                 for c in _VISIBLE):
            counts["revised"] += 1
        else:
            counts["unchanged"] += 1
    return [by_date[k] for k in sorted(by_date)], counts


# ---------------------------------------------------------------- the network
def fetch(source, p, timeout=30):
    url = (ARCHIVE_URL if source == "archive" else FORECAST_URL) + "?" + \
        urllib.parse.urlencode(p, safe=",/")
    req = urllib.request.Request(url, headers={"User-Agent": "poolhound",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # Open-Meteo answers a bad request with 400 and a JSON reason; that
        # reason is the useful half of the failure, so it is kept.
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:                                   # noqa: BLE001
            body = {}
        raise ValueError(f"{source}: HTTP {e.code} "
                         f"{body.get('reason', '')}".strip()[:300]) from None


# ---------------------------------------------------------------------- main
def collect(argv=None):
    """(exit status, summary dict). bin/weather records the summary."""
    ap = argparse.ArgumentParser(
        prog="bin/weather",
        description="Collect daily weather for the pool from Open-Meteo.")
    ap.add_argument("--from-file", nargs="+", metavar="JSON",
                    help="parse saved responses (raw/weather-*.json) instead of fetching")
    ap.add_argument("--source", choices=sorted(RANK),
                    help="for --from-file on a bare Open-Meteo response with no "
                         "wrapper: which endpoint it came from")
    ap.add_argument("--dry-run", action="store_true", help="parse and print, write nothing")
    a = ap.parse_args(argv)

    cfg = config.load()
    data = config.data_dir(cfg)
    today = today_local()
    now = dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    summary = {"configured": True, "rows": 0, "added": 0, "revised": 0,
               "unchanged": 0, "detail": ""}
    start = history_start(data)

    responses = []                      # (source, payload, lat, lon, fetched)
    if a.from_file:
        for path in a.from_file:
            with open(path, encoding="utf-8") as f:
                blob = json.load(f)
            if isinstance(blob, dict) and "response" in blob:
                src, sent = blob.get("source"), blob.get("params") or {}
                payload, fetched = blob["response"], blob.get("fetched") or now
            else:
                src, sent, payload, fetched = a.source, {}, blob, now
                if not src:
                    sys.exit(f"{path}: a bare response needs --source archive|forecast")
            lat = sent.get("latitude", (payload or {}).get("latitude"))
            lon = sent.get("longitude", (payload or {}).get("longitude"))
            responses.append((src, payload, float(lat), float(lon), fetched))
            print(f"  (offline, {src}, from {os.path.basename(path)})")
    else:
        loc, why = location(cfg)
        if loc is None:
            print(f"  weather: {why}")
            summary.update(configured=False, detail=why)
            return 0, summary
        lat, lon = loc
        existing = _read(os.path.join(data, FILE))
        window = archive_window(existing, start, today)
        plan = [("forecast", params(lat, lon, "forecast"))]
        if window:
            plan.append(("archive", params(lat, lon, "archive", *window)))
        raw = os.path.join(data, "raw")
        if not a.dry_run:
            os.makedirs(raw, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M")
        for src, p in plan:
            print(f"  asking {src} for "
                  + (f"{p['start_date']} to {p['end_date']}" if src == "archive"
                     else f"the last {PAST_DAYS} days")
                  + f" at {p['latitude']}, {p['longitude']}")
            payload = fetch(src, p)
            # Saved BEFORE parsing, like every collector here: a parser that
            # breaks on a changed response must leave the response on disk.
            # The wrapper records what was sent beside what came back, so a
            # saved file answers "exactly what was sent" on its own.
            if not a.dry_run:
                path = os.path.join(raw, f"weather-{src}-{stamp}.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"source": src, "fetched": now, "params": p,
                               "response": payload}, f, indent=1)
            responses.append((src, payload, lat, lon, now))

    # Forecast first, archive second: the archive then supersedes the forecast
    # within this one run, carrying across the UV index it does not have.
    responses.sort(key=lambda t: RANK.get(t[0], 0))
    incoming = []
    for src, payload, lat, lon, fetched in responses:
        incoming += parse(payload, src, fetched, lat, lon, today)
    # NOTHING FROM BEFORE THE POOL. The forecast model answers for 92 days
    # whatever the history holds, and its oldest day comes back half-filled at
    # the edge of what it keeps. A day before the first sample is weather over
    # a pool this product never saw, and nothing will ever be fitted to it.
    if start is not None:
        incoming = [r for r in incoming if r["date"] >= start.isoformat()]
    summary["rows"] = len(incoming)

    if a.dry_run:
        merged, counts = merge(_read(os.path.join(data, FILE)), incoming)
        _report(incoming, counts)
        print("  (dry run — nothing written)")
        summary.update(counts)
        return 0, summary

    out = os.path.join(data, FILE)
    # THE READ IS INSIDE THE LOCK, for the reason leslies.py gives at length:
    # two runs that each read, merge and replace lose whichever finished first.
    with locking.exclusive(data, "weather"):
        merged, counts = merge(_read(out), incoming)
        if counts["added"] or counts["revised"] or not os.path.exists(out):
            locking.rewrite_locked(out, COLS, merged)
    _report(incoming, counts)
    summary.update(counts)
    return 0, summary


def _report(incoming, counts):
    days = sorted({r["date"] for r in incoming})
    span = f"{days[0]} to {days[-1]}" if days else "no ended days"
    print(f"  {len(days)} ended day(s) returned, {span}: {counts['added']} new, "
          f"{counts['revised']} revised, {counts['unchanged']} unchanged")


def _read(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    return collect(sys.argv[1:])[0]


if __name__ == "__main__":
    sys.exit(main())
