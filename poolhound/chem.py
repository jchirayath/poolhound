#!/usr/bin/env python3
"""Log a chemical addition from the command line.

  chem acid 32 floz               muriatic acid, default strength
  chem acid 1 gal --pct 14.5      weaker acid
  chem shock 1 lb                 cal-hypo, default 73%
  chem chlorine 2 gal --pct 12.5  liquid chlorine
  chem salt 40 lb
  chem --list                     recent entries
  chem --help                     the full catalogue

  chem bought acid 2 gal --paid 19.49     a purchase, and what it cost
  chem bought salt 160 lb                 the price is optional
  chem stocktake acid 2.5 gal             what is actually on the shelf now
  chem stocktake shock 0 lb               "none left" is a count too
  chem --stock                            on hand, and how long it lasts

WHY A STOCKTAKE AND NOT ONLY PURCHASES
  On hand is the last count, plus what was bought since, minus the doses
  logged since. Purchases minus doses alone drifts for ever -- a dose nobody
  logged is a jug that never empties on paper -- and a count is the only thing
  that pulls it back. Both are corrected in place on the Chemicals tab, like a
  dose: they are our record of something we did.

WHY PERCENTAGE IS NOT OPTIONAL DETAIL
  "A gallon of acid" is not a dose. Muriatic ships at 31.45% (20 Baume) and at
  14.5%, which differ by more than a factor of two, and chlorine runs from 10%
  liquid to 73% cal-hypo. Without strength a volume cannot be converted into a
  pH or FC change, and the regression would be fitting noise.

THE CATALOGUE LIVES IN chemicals.py
  This file used to carry its own list of chemicals and default strengths, and
  the web form and the reference page each carried another. Three copies of the
  same arithmetic drift, so all three now read chemicals.py — which also means
  this command rejects "a gallon of salt" instead of computing a number for it.
"""
import csv, os, sys, time

from . import chemicals as CH
from . import stock as STOCK
from . import access, config
from . import locking

CFG = config.load()
OUT = os.path.join(config.data_dir(CFG), "chemicals.csv")
# Imported, not restated. bin/chem wrote six columns and the API wrote seven, so
# whichever of them created chemicals.csv decided its header and the other
# corrupted every row it appended.
COLS = CH.CHEM_COLS
# None when nobody has said how big the pool is. The CLI used to default to
# 15,000 here and print "estimated effect on 15,000 gal" as though it were about
# the operator's water — the exact sentence config.volume()'s docstring cites as
# the defect it exists to prevent. The dose is still logged; only the invented
# effect is withheld.
GAL = config.volume(CFG)

def catalogue():
    out = ["", "  chemicals:"]
    for k, c in CH.CHEMICALS.items():
        units = " ".join(sorted(CH.units_for(k)))
        out.append(f"    {k:<10} {c['label']:<28} default {c['default_pct']:g}%   units: {units}")
    return "\n".join(out)

def usage(msg=""):
    if msg:
        print(f"  {msg}\n")
    print(__doc__.strip())
    print(catalogue())
    sys.exit(1 if msg else 0)

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        usage()

    if args[0] in ("bought", "stocktake"):
        return stock(args[0], args[1:])
    if args[0] == "--stock":
        return show_stock()

    if args[0] == "--list":
        if not os.path.exists(OUT):
            print("  no entries yet")
            return 0
        rows = list(csv.DictReader(open(OUT)))
        print(f"  {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}:")
        for r in rows[-15:]:
            note = f"  {r['note']}" if r.get("note") else ""
            label = CH.CHEMICALS.get(r["chemical"], {}).get("label", r["chemical"])
            print(f"    {r['ts'][:16]}  {label:<26} {r['amount']:>7} {r['unit']:<5} "
                  f"@ {r['pct']}%{note}")
        return 0

    if len(args) < 3:
        usage("need: chemical amount unit")
    chem, raw_amount, unit = args[0].lower(), args[1], args[2].lower()
    if chem not in CH.CHEMICALS:
        usage(f"unknown chemical {chem!r}")
    try:
        amount = float(raw_amount)
    except ValueError:
        usage(f"amount {raw_amount!r} is not a number")
    if amount <= 0:
        usage("amount must be greater than zero")
    if unit not in CH.units_for(chem):
        phase = CH.CHEMICALS[chem]["phase"]
        usage(f"{unit!r} is not a unit for a {phase} — use one of: "
              f"{', '.join(sorted(CH.units_for(chem)))}")
    # The same bound the two HTTP routes apply. `bin/chem acid 9000 gal` used to
    # exit 0 and print an estimated alkalinity change of -364,520 ppm.
    ok, why = CH.plausible_amount(chem, amount, unit)
    if not ok:
        usage(why)

    pct, note, i = CH.CHEMICALS[chem]["default_pct"], "", 3
    while i < len(args):
        if args[i] == "--pct" and i + 1 < len(args):
            try:
                pct = float(args[i + 1])
            except ValueError:
                usage(f"--pct {args[i+1]!r} is not a number")
            i += 2
        elif args[i].startswith("--"):
            # AN UNKNOWN FLAG IS A MISTAKE, NOT A NOTE.
            #
            # The else below takes everything remaining as the note, so a
            # mistyped or imagined flag was silently recorded as the reason for
            # a dose — and the dose was logged. That is how this very file came
            # to carry a row reading `acid,12,floz,31.45,--dry-run,local`: a
            # reviewer ran `bin/chem acid 12 floz --dry-run` believing the flag
            # would prevent a write, and logged a fabricated dose into the
            # household's real record instead. bin/leslies and bin/wg-collect
            # both HAVE --dry-run, which is exactly why somebody would expect it
            # here.
            #
            # chemicals.csv is "our record of something we did". A flag that
            # does not exist must stop the command, not become its note.
            usage(f"{args[i]!r} is not an option for bin/chem. The only option "
                  f"is --pct. Everything after the unit is the note, and a note "
                  f"does not begin with --.")
        else:
            note = " ".join(args[i:])
            break
    if not (0 < pct <= 100):
        usage("--pct must be between 0 and 100")

    # MINTED HERE, not backfilled on first edit. A dose's identity used to be
    # its timestamp, and two doses in one minute made both rows permanently
    # uneditable; the server now writes an id with every dose it logs, and a
    # CLI dose that arrives without one is a row that cannot be addressed until
    # something else happens to touch it.
    row = {"id": CH.new_id(),
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "chemical": chem,
           "amount": f"{amount:g}", "unit": unit, "pct": f"{pct:g}", "note": note,
           # The CLI runs on the machine, which is the same thing whoami() calls
           # "local" for the API. Blank made every CLI dose look like a row
           # written before attribution existed.
           # access.py names it; a second literal here is how "local" and
           # "anonymous" drifted apart in the first place.
           "by": access.CONSOLE}
    # Under the same lock and the same migration the server takes. This was a
    # bare append, so a dose logged here while the browser logged one could be
    # dropped -- and it wrote the pre-`by` header shape.
    locking.append_row(config.data_dir(CFG), "chemicals", OUT, COLS, row)

    eff = CH.effects(chem, amount, unit, pct, GAL) if GAL else None
    shown = ", ".join(
        f"{CH.EFFECT_LABELS[k][0]} {CH.EFFECT_LABELS[k][1].format(v)}"
        for k, v in (eff or {}).items() if k in CH.EFFECT_LABELS)
    print(f"  logged: {amount:g} {unit} of {CH.CHEMICALS[chem]['label']} @ {pct:g}%"
          + (f" — {note}" if note else ""))
    if shown:
        print(f"  estimated effect on {GAL:,.0f} gal: {shown}")
    elif CH.CHEMICALS[chem].get("no_effect"):
        # NOT THE SAME AS AN EFFECT OF ZERO. Printing nothing here would read
        # as "this does nothing", which is the opposite of what a phosphate
        # remover does.
        print(f"  no estimate: {CH.CHEMICALS[chem]['no_effect']}")
    elif GAL is None:
        print("  estimated effect: not shown — this install has not been told the pool's")
        print("  volume, and every figure multiplies through it. Set pool.volume_gallons")
        print("  in config.toml, or use the Pool Volume Calculator on the site.")
    return 0

def stock(verb, args):
    """`bought` or `stocktake`: one row of stock.csv, through stock.validate().

    The same validator the API runs, so "a gallon of salt" is refused here for
    the reason a dose of it is, and --pct and --paid are the only options --
    an unknown flag stops the command rather than becoming the note, for the
    reason the dose path gives at length.
    """
    if len(args) < 3:
        usage(f"need: {verb} chemical amount unit")
    data = {"kind": "purchase" if verb == "bought" else "stocktake",
            "chemical": args[0].lower(), "amount": args[1], "unit": args[2].lower()}
    if data["chemical"] not in CH.CHEMICALS:
        usage(f"unknown chemical {data['chemical']!r}")
    i, opts = 3, ("--pct", "--paid") if verb == "bought" else ("--pct",)
    while i < len(args):
        if args[i] in opts and i + 1 < len(args):
            data[args[i][2:]] = args[i + 1]
            i += 2
        elif args[i].startswith("--"):
            usage(f"{args[i]!r} is not an option for bin/chem {verb}. The options "
                  f"are {' and '.join(opts)}; everything after the unit is the "
                  f"note, and a note does not begin with --.")
        else:
            data["note"] = " ".join(args[i:])
            break
    import datetime as dt
    from . import render
    row, why = STOCK.validate(data, dt.datetime.now(), render.when, render.CLOCK_SKEW)
    if why:
        usage(why)
    row.update({"by": access.CONSOLE, "id": CH.new_id()})
    path = os.path.join(config.data_dir(CFG), STOCK.FILE)
    locking.append_row(config.data_dir(CFG), "stock", path, STOCK.COLS, row)
    print(f"  recorded: {STOCK.describe(row)}"
          + (f" — {row['note']}" if row.get("note") else ""))
    return show_stock()


def show_stock():
    """What is on hand, the same summary the Chemicals tab draws."""
    from . import render
    rows = render.stock_now()
    if not rows:
        print("  nothing recorded yet — `bin/chem bought ...` or "
              "`bin/chem stocktake ...` starts it")
        return 0
    for line in STOCK.context_lines(rows):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
