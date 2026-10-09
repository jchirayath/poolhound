#!/usr/bin/env python3
"""Collect Leslie's in-store water tests.

WHY A SECOND CHEMISTRY SOURCE IS WORTH THE TROUBLE
  The WaterGuru pod reads free chlorine and pH in the water, every day.
  Everything else — alkalinity, calcium, cyanuric acid, salt, phosphates — is
  laboratory work on a discrete sample: WaterGuru analyse one posted to them,
  Leslie's run a bench photometer on one carried into the store. Two labs, both
  credible, on different days.

  They are stored in SEPARATE files on purpose. Where they differ there are two
  possible reasons and no way to tell them apart from the numbers alone: the labs
  measure differently, or the pool moved between the two samples. Alkalinity 95
  on one date and 73 seven days later is exactly what one dose of muriatic acid
  does to this pool. Averaging them would produce a figure neither lab measured
  and bury the question.

HOW THE DATA IS ACTUALLY REACHED
  Leslie's runs on Salesforce Commerce Cloud. WaterTest-Landing answers 302 to
  /login without a session, and once logged in it returns an empty shell — the
  results arrive through a three-call XHR chain that their waterTest.js performs,
  which this reproduces:

    GET  WaterTest-Home           seeds the session with the current pool
    GET  WaterTest-ProfileById    -> {poolProfile: {...}}
    POST WaterTest-GetSanitizer   {poolProfile, count:1} -> {name, sanitizer}
    POST WaterTest-GetWaterTest   {poolProfileName, poolSanitizer} -> {response: HTML}

  Two details cost real time to find and will not be obvious later:

  WaterTest-Home is not optional. The XHR endpoints read the current pool profile
  out of the session, and that page is what puts it there. Called without it they
  answer 500 — not 401, not an empty result, a 500 that looks like a server fault
  rather than a missing precondition.

  The POST bodies are jQuery $.param() output, which serialises a nested object
  as poolProfile[pool_address][city]=... A plain urlencode of the same dict is
  not equivalent and also draws a 500.

WHAT COMES BACK
  The whole history, not just the newest test — four tests back to May on the
  first run. So a single call backfills, and re-running is idempotent because
  rows are keyed on the test date.
"""
import argparse, csv, datetime as dt, html, http.cookiejar, json, os, re, sys
import urllib.error, urllib.parse, urllib.request

from . import config
from . import locking

BASE = "https://lesliespool.com"
SITE = "/on/demandware.store/Sites-lpm_site-Site/en_US"
LOGIN_PAGE = f"{BASE}/login?rurl=1"
LOGIN_POST = f"{BASE}{SITE}/Account-Login?rurl=1"
WT_HOME    = f"{BASE}{SITE}/WaterTest-Home"
WT_PROFILE = f"{BASE}{SITE}/WaterTest-ProfileById"
WT_SANI    = f"{BASE}{SITE}/WaterTest-GetSanitizer"
WT_TESTS   = f"{BASE}{SITE}/WaterTest-GetWaterTest"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

COLS = ["measured", "fetched", "score", "free_cl", "total_cl", "ph", "ta", "ch",
        "cya", "iron", "copper", "phosphates", "salt", "tds", "total_hardness",
        "borate", "saturation_index", "issues", "pool", "sanitizer", "source_file"]

# Leslie's column headings, normalised to alphanumerics so that spacing,
# punctuation and capitalisation changes cannot break the mapping. Extra
# aliases cover panels this account has not been given yet.
HEADERS = {
    "overallscore": "score",
    "freechlorine": "free_cl",  "freeavailablechlorine": "free_cl",
    "totalchlorine": "total_cl",
    "ph": "ph",
    "alkalinity": "ta",         "totalalkalinity": "ta",
    "calcium": "ch",            "calciumhardness": "ch",
    "cyanuricacid": "cya",      "stabilizer": "cya",  "conditioner": "cya",
    "iron": "iron",
    "copper": "copper",
    "phosphates": "phosphates", "phosphate": "phosphates",
    "salt": "salt",             "saltlevel": "salt",
    "totaldissolvedsolids": "tds", "tds": "tds",
    "totalhardness": "total_hardness",
    "borate": "borate",         "borates": "borate",
    "saturationindex": "saturation_index", "langelierindex": "saturation_index",
    "issuesreported": "issues", "issues": "issues",
}
# Columns that are text, not numbers — they must not be pushed through float().
TEXT_COLS = {"issues"}

def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()

# --------------------------------------------------------------------- session
def credentials(path):
    """From the encrypted vault, or the old plaintext file if that is all there
    is. Keeping both means migrating is optional rather than a flag day."""
    from . import vault
    try:
        user, pw, src = vault.credentials_for("leslies", path)
    except vault.VaultError as e:
        sys.exit(str(e))
    if user and pw:
        return user, pw
    sys.exit(f"no Leslie's credentials — add them on the Settings tab, or write two "
             f"lines (email, password) to {path} and chmod 600 it")

def opener():
    jar = http.cookiejar.CookieJar()
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    o.addheaders = [("User-Agent", UA), ("Accept-Language", "en-US,en;q=0.9"),
                    ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")]
    return o, jar

def get(o, url):
    with o.open(urllib.request.Request(url), timeout=45) as r:
        return r.read().decode("utf-8", "replace"), r.geturl()

def jq_param(data, prefix=None):
    """Reproduce jQuery's $.param(): a nested object becomes a[b][c]=v.

    This is not decoration. WaterTest-GetSanitizer is handed a whole poolProfile
    object by their JS, and a flat urlencode of the same dict draws a 500."""
    out = []
    if isinstance(data, dict):
        for k, v in data.items():
            out += jq_param(v, f"{prefix}[{k}]" if prefix else k)
    elif isinstance(data, (list, tuple)):
        for i, v in enumerate(data):
            out += jq_param(v, f"{prefix}[{i}]")
    else:
        out.append((prefix, "" if data is None else str(data)))
    return out

def xhr(o, url, data, referer=WT_HOME):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(jq_param(data)).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                 "X-Requested-With": "XMLHttpRequest",
                 "Accept": "application/json, text/javascript, */*; q=0.01",
                 "Referer": referer, "Origin": BASE})
    with o.open(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def csrf(page):
    m = (re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)', page)
         or re.search(r'value=["\']([^"\']+)["\'][^>]*name=["\']csrf_token["\']', page))
    return m.group(1) if m else None

def login(o, email, password):
    page, _ = get(o, LOGIN_PAGE)
    tok = csrf(page)
    if not tok:
        sys.exit("no csrf_token on the login page — the form has changed")
    body, final = post_form(o, LOGIN_POST, {
        "loginEmail": email, "loginPassword": password,
        "loginRememberMe": "true", "csrf_token": tok}, LOGIN_PAGE)
    # A failed SFCC login answers 200 with the login page again, not a 4xx, so
    # the status code proves nothing. Where the POST lands is the reliable tell.
    if "/login" in final:
        err = re.search(r'class=["\'][^"\']*error[^"\']*["\'][^>]*>\s*([^<]{4,160})', body)
        sys.exit("login rejected" + (f": {html.unescape(err.group(1)).strip()}" if err else ""))
    return True

def post_form(o, url, fields, referer):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Referer": referer, "Origin": BASE})
    with o.open(req, timeout=45) as r:
        return r.read().decode("utf-8", "replace"), r.geturl()

def fetch(o):
    """Walk the XHR chain and return (results HTML fragment, profile, sanitizer)."""
    get(o, WT_HOME)                      # seeds the session; see module docstring
    try:
        prof = json.loads(get(o, WT_PROFILE)[0]).get("poolProfile") or {}
    except urllib.error.HTTPError as e:
        sys.exit(f"WaterTest-ProfileById returned {e.code} — the session has no "
                 f"current pool profile; WaterTest-Home did not seed it")
    if not prof:
        sys.exit("no pool profile on the account")
    s = xhr(o, WT_SANI, {"poolProfile": prof, "count": 1})
    if not s.get("success"):
        sys.exit(f"GetSanitizer refused: {s}")
    w = xhr(o, WT_TESTS, {"poolProfileName": s.get("poolProfileName"),
                          "poolSanitizer": s.get("poolSanitizer")})
    if not w.get("success") or not w.get("response"):
        sys.exit(f"GetWaterTest returned no results: {list(w)}")
    return w["response"], prof, s.get("poolSanitizer", "")

# ---------------------------------------------------------------------- parsing
def value(col, raw):
    """'N/A' is an absent measurement, not a zero. Storing it as 0 would drag
    every average down and imply the pool has no iron in it."""
    raw = (raw or "").strip()
    if not raw or raw.upper() in ("N/A", "NA", "-", "—"):
        return ""
    if col in TEXT_COLS:
        return raw
    m = re.search(r"-?\d+(?:\.\d+)?", raw.replace(",", ""))
    return m.group(0) if m else raw

def parse(fragment):
    """Read the results table.

    Column meaning comes from the header row rather than from fixed positions,
    so Leslie's adding or reordering a measure changes which column a value
    lands in without changing this code. Dates sit in <th> inside each row while
    the values are <td>, which is why the two are collected separately.
    """
    rows, unknown = [], set()
    table = re.search(r"<table[^>]*>.*?</table>", fragment, re.S)
    if not table:
        return rows, unknown, "no <table> in the response"
    t = table.group(0)

    head = re.search(r"<thead[^>]*>(.*?)</thead>", t, re.S)
    head_html = head.group(1) if head else t
    heads = [strip_tags(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", head_html, re.S)]
    # Date and the PDF link are not measures; everything else maps by heading.
    cols = []
    for h in heads:
        n = norm(h)
        if n in ("date", "viewpdf", "instoretest", ""):
            continue
        if n in HEADERS:
            cols.append(HEADERS[n])
        else:
            cols.append(None)
            unknown.add(h)
    if not cols:
        return rows, unknown, "no recognised column headings"

    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        tds = [strip_tags(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if not tds:
            continue
        ths = [strip_tags(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", tr, re.S)]
        date = next((d for d in (parse_date(x) for x in ths) if d), None)
        if not date:
            continue        # a row with no date cannot be joined to anything
        row = {"measured": date}
        for col, raw in zip(cols, tds):
            if col:
                v = value(col, raw)
                if v != "":
                    row[col] = v
        rows.append(row)
    return rows, unknown, None

def parse_date(s):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            d = dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
        if dt.date(2010, 1, 1) <= d <= dt.date.today() + dt.timedelta(days=1):
            return d.isoformat()
    return None

# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(
        description="Collect Leslie's in-store water tests.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-file", help="parse a saved fragment instead of logging in")
    ap.add_argument("--dry-run", action="store_true", help="parse and print, write nothing")
    a = ap.parse_args()

    cfg = config.load()
    data = config.data_dir(cfg)
    raw = os.path.join(data, "raw"); os.makedirs(raw, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    prof, sanitizer = {}, ""

    if a.from_file:
        fragment = open(a.from_file, encoding="utf-8", errors="replace").read()
        src = os.path.basename(a.from_file)
    else:
        # vault.py owns the credential-file locations; this retyped one.
        from . import vault as _v
        email, password = credentials(cfg.get("leslies", {}).get("credentials")
                                      or _v.credential_file("leslies", cfg))
        o, _ = opener()
        login(o, email, password)
        fragment, prof, sanitizer = fetch(o)
        src = f"leslies-{stamp}.html"
        # Saved before parsing: when their markup changes, the failure should be
        # a parser that finds nothing with the offending page on disk, not a
        # silent gap in the history.
        with open(os.path.join(raw, src), "w") as f:
            f.write(fragment)

    rows, unknown, err = parse(fragment)
    if err or not rows:
        sys.exit(f"parsed nothing from {src}: {err or 'no dated rows'} — "
                 f"inspect the saved page and extend HEADERS")

    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
    for r in rows:
        r["fetched"] = now
        r["source_file"] = src
        if prof: r["pool"] = prof.get("pool_name", "")
        if sanitizer: r["sanitizer"] = sanitizer

    print(f"  {len(rows)} test{'s' if len(rows) != 1 else ''} from Leslie's"
          + (f" — {prof.get('pool_name')}, {prof.get('size_in_gallons')} gal on file" if prof else ""))
    for r in sorted(rows, key=lambda r: r["measured"], reverse=True)[:6]:
        print("   ", r["measured"], " ".join(
            f"{k}={r[k]}" for k in ("free_cl", "ph", "ta", "ch", "cya", "salt", "phosphates")
            if r.get(k)))
    if unknown:
        print(f"  unmapped columns: {', '.join(sorted(unknown))}", file=sys.stderr)

    if a.dry_run:
        print("  (dry run — nothing written)")
        return 0

    out = os.path.join(data, "leslies.csv")
    # THE LOCK COVERS THE READ AS WELL AS THE WRITE, because it is the gap
    # BETWEEN them that loses rows. The write below is already atomic and uses a
    # per-process temp name, which stops two runs interleaving into one file --
    # but two runs could still each read `existing`, each compute their own
    # merge, and the second replace() would drop whatever the first had added.
    # Atomicity makes each write whole; only the lock makes the pair correct.
    with locking.exclusive(data, "leslies"):
        return _merge_and_write(out, rows, a)


def _merge_and_write(out, rows, a):
    """The read-modify-write half. THE CALLER HOLDS THE LOCK."""
    # Keyed on the test date: a re-run corrects an existing row rather than
    # appending a duplicate, so the daily job is safe to run when nothing is new.
    existing = list(csv.DictReader(open(out))) if os.path.exists(out) else []
    dates = {r["measured"] for r in rows}
    merged = [r for r in existing if r.get("measured") not in dates] + rows
    merged.sort(key=lambda r: r.get("measured", ""))
    # Written to a temp file and renamed into place, like every other
    # read-modify-write here (server.py, vault.py, pool_shape.py, watch.py).
    # Truncating this file in place left it momentarily EMPTY on every single
    # run — and a render landing in that window succeeds silently and publishes
    # "Leslie's lab has never returned anything" onto the public page, with the
    # lab values that drive the advice swapped out from under it. Renders fire
    # from cron, from every browser write and three times per agent sample, so
    # the window is hit rather than merely reachable. The unique suffix matters
    # too: a fixed name lets two collector runs interleave into one temp file.
    # Migrate, then rewrite, under the lock the caller already holds. This
    # rebuilt leslies.csv from COLS alone, so a column on disk the code does not
    # know was deleted with every value in it -- while the append path one file
    # away refused the same situation outright.
    locking.rewrite_locked(out, COLS, merged)
    print(f"  wrote {out}  ({len(merged)} test{'s' if len(merged) != 1 else ''} on file)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
