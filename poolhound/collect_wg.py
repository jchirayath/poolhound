#!/usr/bin/env python3
"""Daily WaterGuru pull -> data/readings.csv (+ raw JSON kept verbatim).

TWO DIFFERENT CADENCES IN ONE RESPONSE, AND THEY MUST NOT BE CONFLATED
  The SENSE pod measures FREE_CL, PH and SKIMMER_FLOW in the water, daily.
  Everything else — TA, CH, CYA, SALT, PHOSPHATES, COPPER, IRON,
  SATURATION_INDEX — is laboratory analysis of a sample posted to WaterGuru, so
  it arrives whenever a sample was last sent, currently four weeks old. Writing
  a stale CYA into today's row as if measured today would silently fabricate a
  time series, so each value is stored with the timestamp it was actually
  measured at, and the lab values go to their own file.

  CYA matters more than its update rate suggests: it sets how much UV protection
  the chlorine has, so it is the main confounder in any FC-loss model. It has to
  be carried as a slow-moving input, not ignored.

The raw response is always saved. The upstream project asks for no more than
one or two calls a day, so a parser bug must never cost a re-fetch.
"""
import csv, json, os, sys, time

from . import config
from . import locking

DATA = config.data_dir()

DAILY = {"FREE_CL": "free_cl", "PH": "ph", "SKIMMER_FLOW": "skimmer_flow"}
LAB = {"TA": "ta", "CH": "ch", "CYA": "cya", "SALT": "salt",
       "PHOSPHATES": "phosphates", "COPPER": "copper", "IRON": "iron",
       "SATURATION_INDEX": "saturation_index"}

def val(m):
    for k in ("floatValue", "intValue"):
        if k in m: return m[k]
    return m.get("value")

def append(path, row, cols):
    """One row, under the lock, after migrating the header.

    This was a bare append. Three collectors and the agent ingest write into the
    same data directory on the same schedule -- cron fires this at 14:00 while
    the Pi pushes a sample every fifteen minutes and every browser write renders
    -- so "nobody else is writing right now" was an assumption, not a fact. And
    without the migration step, adding a column to COLS here would have written
    long rows under a short header, which is the corruption locking.py exists to
    refuse.

    The lock is named for the file, so readings.csv, lab.csv and wg_targets.csv
    serialise independently rather than blocking each other.
    """
    name = os.path.splitext(os.path.basename(path))[0]
    locking.append_row(os.path.dirname(path) or ".", name, path, cols, row)

def main():
    # --from-file lets the parser be developed and re-run against a saved
    # response. The API is rate-limited to once or twice a day, so a parser
    # change must never require spending a call to test it.
    if "--from-file" in sys.argv:
        src = sys.argv[sys.argv.index("--from-file") + 1]
        d = json.load(open(src))
        print(f"  (offline, from {os.path.basename(src)})")
    else:
        from . import waterguru
        d = waterguru.fetch()
        os.makedirs(os.path.join(DATA, "raw"), exist_ok=True)
        raw = os.path.join(DATA, "raw", "wg-%s.json" % time.strftime("%Y%m%d-%H%M"))
        json.dump(d, open(raw, "w"), indent=2); os.chmod(raw, 0o600)

    wb = d["waterBodies"][0]
    meas = {m["type"]: m for m in wb.get("measurements", [])}

    daily = {"fetched": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             "measured": wb.get("latestMeasureTime", ""),
             "water_temp": wb.get("waterTemp", ""),
             "status": wb.get("status", ""),
             "alerts": "; ".join(a.get("text", "") for a in wb.get("alerts", []))}
    for t, col in DAILY.items():
        daily[col] = val(meas[t]) if t in meas else ""
    cols = ["fetched", "measured", "free_cl", "ph", "water_temp",
            "skimmer_flow", "status", "alerts"]
    # Deduped on the measurement timestamp, exactly as the lab rows eleven lines
    # below already were. Without it a second pull the same day — a manual run, a
    # Refresh press, a cron retry — appended the same pod measurement again. The
    # household's own file carries the 2026-09-08 reading twice because of it,
    # and everything that counts rows (including "Model readiness", which decides
    # when there is enough data to fit the coefficients) counted it twice.
    # THE DEDUP READ MUST BE INSIDE THE LOCK. This read the file, decided, and
    # then called an append that took the lock — so the lock covered the write
    # and not the decision, and two runs both found the timestamp absent and
    # both wrote it. /api/refresh spawns this as a subprocess while cron fires
    # the same collector at 14:00. Measured before the change: 12 of 12
    # concurrent pairs duplicated one measurement.
    readings_path = os.path.join(DATA, "readings.csv")
    wrote_reading = bool(daily["measured"]) and locking.append_if_new(
        DATA, "readings", readings_path, cols, daily, "measured")

    # Lab values, written only when the measurement timestamp is one we have not
    # already recorded — otherwise a daily cron would repeat month-old lab work.
    lab_path = os.path.join(DATA, "lab.csv")
    lab_cols = ["measured", "fetched"] + list(LAB.values())
    # labResultsTime is the payload's own name for when the lab ran. Deriving it
    # from CYA's measureTime worked only because CYA happens to be a lab measure;
    # if WaterGuru ever moves CYA to the pod, that would start dating lab rows by
    # a daily reading and silently create one lab row per day.
    lab_time = wb.get("labResultsTime") or meas.get("CYA", {}).get("measureTime", "")
    wrote_lab = False
    if lab_time:
        lab = {"measured": lab_time, "fetched": daily["fetched"]}
        for t, col in LAB.items():
            lab[col] = val(meas[t]) if t in meas else ""
        # Same read-inside-the-lock as the pod row above.
        wrote_lab = locking.append_if_new(DATA, "lab", lab_path, lab_cols,
                                          lab, "measured")

    # WaterGuru publishes the targets it judges this pool against. They are a
    # third opinion alongside our computed bands and Leslie's stated salt range,
    # and they are free — already in a response we have paid for. Recorded only
    # when they change, because they change about never.
    tgt_path = os.path.join(DATA, "wg_targets.csv")
    tgt_cols = ["fetched", "free_cl", "ph", "ta", "ch", "cya", "salt", "flow", "sanitizer"]
    tgt = {"fetched": daily["fetched"],
           "free_cl": wb.get("freeClTargetEffective", ""),
           "ph": wb.get("phTargetEffective", ""),
           "ta": wb.get("taTargetEffective", ""),
           "ch": wb.get("chTargetEffective", ""),
           "cya": wb.get("cyaTargetEffective", ""),
           "salt": wb.get("saltTargetEffective", ""),
           "flow": wb.get("flowGpmTargetEffective", ""),
           "sanitizer": wb.get("sanitizerType", "")}
    # Also read-decide-append, so also inside the lock. This one dedups on the
    # VALUES rather than a timestamp -- the vendor's targets only change when
    # they change -- so it cannot use append_if_new and takes the lock itself.
    with locking.exclusive(DATA, "wg_targets"):
        def same_as_last(path, row, cols):
            if not os.path.exists(path): return False
            rows = list(csv.DictReader(open(path)))
            if not rows: return False
            return all(str(rows[-1].get(c, "")) == str(row.get(c, ""))
                       for c in cols if c != "fetched")
        if not same_as_last(tgt_path, tgt, tgt_cols):
            locking.append_locked(tgt_path, tgt_cols, tgt)

    body = wb.get("waterBody", {})
    print(f"  measured {daily['measured'][:16]}  FC {daily['free_cl']}  pH {daily['ph']}  "
          f"temp {daily['water_temp']}F  status {daily['status']}")
    if daily["alerts"]: print(f"  alerts: {daily['alerts']}")
    print(f"  pod row: {'written' if wrote_reading else 'already recorded'}")
    print(f"  lab row: {'written (' + lab_time[:10] + ')' if wrote_lab else 'already recorded'}")
    print(f"  vendor targets: FC {tgt['free_cl']} · pH {tgt['ph']} · TA {tgt['ta']} · "
          f"CH {tgt['ch']} · CYA {tgt['cya']} · salt {tgt['salt']}")
    print(f"  pool: {body.get('sizeGallons')} gal · {body.get('userCl')} · "
          f"acid {body.get('userAcidMuriaticPct')}%")
    return 0




if __name__ == "__main__":
    sys.exit(main())
