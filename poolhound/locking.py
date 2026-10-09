"""One writer at a time, for the files that are read-modify-written.

WHY THIS EXISTS

Appending a line is very nearly atomic and was treated as good enough. It is not
good enough here, because three of the writers do not append — they read the
whole CSV, change it in memory and write it back. While one of those is in
flight, an append into the same file lands in a version that is about to be
overwritten, and the row is gone. Demonstrated: nine concurrent dose logs, all
answered 200 with the estimated effect shown to the browser, six rows on disk.
The same shape left `config.toml` unparseable in three runs out of eight, and a
corrupt config takes the whole site down in a way a container rebuild does not
fix, because the file is on the share.

WHY A LOCAL LOCK FILE, NOT A LOCK ON THE SHARE

Every writer on the server is inside one container: the server's threads, the cron
collectors (`docker compose exec` re-enters the same container) and anything run
by hand. They share a filesystem namespace, so a local lock file coordinates all
of them. Putting the lock on `/mnt/poolhound` instead would put it on CIFS, where
`flock` semantics are exactly the thing we do not want to be relying on. The lock
path is derived from the data directory, so two installs on one machine do not
block each other and one install always agrees with itself.

WHICH local directory is not a detail — see lock_dir(). The system temp
directory is periodically emptied by something that is not us, and a lock file
deleted out from under a holder silently stops excluding anything.

This does NOT make the share safe against a second host mounting it and writing
concurrently. Nothing here would; that is a property of the deployment, which
has exactly one writer by design.
"""
import contextlib, csv, errno, fcntl, hashlib, os, shutil, stat, sys, tempfile, threading, time

class LockTimeout(Exception):
    """Waited too long for another writer to finish."""

# Which locks THIS thread holds. Per-thread, not per-process: two threads
# genuinely contending is the case flock is for and must still block.
_held = threading.local()


def lock_dir(data_dir):
    """Where the lock files live.

    THE SYSTEM TEMP DIRECTORY IS THE ONE DIRECTORY SOMETHING ELSE DELETES FROM.
    systemd-tmpfiles and tmpreaper age out /tmp entries on a timer, and a
    container's /tmp is equally fair game. If the directory goes while a writer
    holds a lock, the next writer's makedirs creates a NEW inode and flocks
    that: it acquires in 0.000s while another writer is mid-rewrite, mutual
    exclusion is gone, and every writer still reports success. That is the
    pre-lock failure this module exists to end, wearing the lock's clothes.

    Measured: holder takes 'chemicals', the directory is removed, the second
    writer acquires immediately instead of blocking.

    Two answers, both applied. The directory is kept out of the cleaner's way
    where a place exists that no cleaner walks (POOLHOUND_LOCK_DIR, or /run on
    Linux, which is tmpfs and not aged out), and `exclusive()` VERIFIES the lock
    it took is still the lock on the path afterwards rather than assuming it.
    """
    tag = hashlib.sha256(os.path.abspath(data_dir).encode()).hexdigest()[:16]
    base = (os.environ.get("POOLHOUND_LOCK_DIR", "").strip()
            or _default_lock_base())
    d = os.path.join(base, "poolhound-locks-" + tag)
    os.makedirs(d, exist_ok=True)
    return d


def _default_lock_base():
    """/run where it exists and is writable, else the system temp directory.

    /run is tmpfs, is cleared only by a reboot (which clears every lock holder
    with it, so there is nothing to protect) and is not walked by the temp-file
    cleaners. On macOS and anywhere /run is not writable this falls back to the
    temp directory, which is why exclusive() revalidates regardless.
    """
    for cand in ("/run", "/var/run"):
        if os.path.isdir(cand) and os.access(cand, os.W_OK):
            return cand
    return tempfile.gettempdir()

@contextlib.contextmanager
def exclusive(data_dir, name, timeout=15.0):
    """Hold an exclusive lock named `name` for this install.

    Blocking with a deadline rather than failing fast. A timeout raises instead
    of proceeding unlocked — losing a dose silently is the failure this exists
    to prevent, and doing it anyway under load would reintroduce it at exactly
    the moment it matters.

    HOW LONG THESE ARE ACTUALLY HELD. This said "sub-millisecond critical
    sections, so a wait means genuine contention and will clear", and that
    figure was wrong by three orders of magnitude for the one that matters: the
    samples lock was held across a whole page render — 0.42s on a local SSD with
    eight rows, and the server renders 549 rows from six CSVs on a CIFS mount with
    actimeo=30 and writes 1.1 MB. Anybody sizing this timeout from that sentence
    would size it for the wrong world. The render has been moved out of the
    lock; what is left is a dedup read plus an append, which IS milliseconds —
    but the timeout is generous because the files are on a share whose latency
    is not ours to predict, not because the sections are provably tiny.
    """
    # flock is held per OPEN FILE DESCRIPTION, so taking this lock twice in one
    # thread opens a second description and blocks on ourselves — a fifteen
    # second stall ending in "another poolhound writer has held chemicals", which
    # names the wrong cause and sends the reader looking for a second process
    # that does not exist. Nesting is always a bug at the call site: the inner
    # function wants append_locked, not append_row.
    key = (os.path.abspath(data_dir), name)
    if key in getattr(_held, "keys", ()):
        raise RuntimeError(
            f"the {name} lock is already held by this thread. Nesting it would "
            f"block until the timeout. The inner call wants the _locked variant.")

    deadline = time.monotonic() + timeout
    fd, path = _acquire(data_dir, name, deadline, timeout)
    try:
        _held.keys = getattr(_held, "keys", frozenset()) | {key}
        try:
            yield
        finally:
            _held.keys -= {key}
            _warn_if_replaced(fd, path, name)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _warn_if_replaced(fd, path, name):
    """Say it out loud if our lock file stopped being the lock file.

    A holder cannot be protected from this after the fact — but it CAN refuse to
    be silent about it. Somebody reading a corrupted CSV needs to know that the
    exclusion protecting it may not have been exclusion at all, and the only
    place that fact is knowable is here.
    """
    try:
        mine, theirs = os.fstat(fd), os.stat(path)
        if (mine.st_ino, mine.st_dev) == (theirs.st_ino, theirs.st_dev):
            return
    except OSError:
        pass
    print(f"locking: the {name} lock file at {path} was REPLACED OR DELETED while "
          f"this writer held it. Another writer could have taken a lock of the "
          f"same name at the same time, so anything written under it may have "
          f"raced. Set POOLHOUND_LOCK_DIR to a directory no temp-file cleaner "
          f"walks.", file=sys.stderr, flush=True)


def _acquire(data_dir, name, deadline, timeout):
    """flock a lock file, and prove afterwards that it is still THE lock file.

    THE LOCK'S OWN EXISTENCE IS NOT ASSUMED. flock is held on an open file
    description, so it keeps working perfectly on a file that has been unlinked
    — and a writer that opens the path afterwards creates a different inode and
    locks that instead. Both hold "the" lock, neither waits, and both report
    success. The cleaner that empties /tmp is enough to cause it.

    So after acquiring, the fd's inode is compared with the inode the path names
    now. A mismatch means the file we locked is no longer the file the name
    refers to: drop it and start again on the current one. Retrying rather than
    raising is right because the state is transient and a raise would refuse a
    legitimate write over an ephemeral race.

    THIS NARROWS THE WINDOW; IT DOES NOT CLOSE IT, and saying otherwise would be
    the same kind of comment this module already had. A writer that acquired
    BEFORE the deletion cannot be told about it from here — it holds a valid
    lock on an inode nobody else will ever find. The actual fix is the lock
    directory not being somewhere a cleaner walks (see lock_dir); this check is
    what catches the orderings that remain, and the release path below SAYS SO
    out loud when it finds its own lock file was replaced while it was held.
    """
    while True:
        path = os.path.join(lock_dir(data_dir), name + ".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as e:
                    if e.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if time.monotonic() >= deadline:
                        raise LockTimeout(
                            f"another poolhound writer has held {name} for more "
                            f"than {timeout:g}s")
                    time.sleep(0.02)
            mine = os.fstat(fd)
            try:
                theirs = os.stat(path)
                same = (mine.st_ino, mine.st_dev) == (theirs.st_ino, theirs.st_dev)
            except OSError:
                same = False               # the path is gone; ours locks nothing
            if same:
                return fd, path
            fcntl.flock(fd, fcntl.LOCK_UN)
        except BaseException:
            os.close(fd)
            raise
        os.close(fd)
        if time.monotonic() >= deadline:
            raise LockTimeout(
                f"the {name} lock file kept being replaced underneath us for more "
                f"than {timeout:g}s — something is deleting {lock_dir(data_dir)}. "
                f"Set POOLHOUND_LOCK_DIR to a directory no temp-file cleaner walks.")
        time.sleep(0.02)

def replace_atomically(path, write):
    """Write `path` via a uniquely-named temp file and rename it into place.

    `write(f)` receives the open text handle. The unique suffix is not
    decoration: a fixed `path + ".tmp"` let two writers interleave INTO THE TEMP
    FILE and then rename the byte-salad over the real one, which is how a
    concurrent settings save produced a config.toml that would not parse.

    Callers still take `exclusive()` around a read-modify-write — this makes the
    write atomic, not the read-then-write.
    """
    # mkstemp, not a name built from pid and clock: two threads of one process
    # share a pid and can land in the same millisecond, which collides and then
    # fails with a FileNotFoundError as one rename beats the other to the temp.
    # In the same directory so the rename stays on one filesystem, and therefore
    # atomic.
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        # mkstemp is 0600; keep whatever the real file already had, so replacing
        # it does not quietly tighten or loosen who can read the data.
        try:
            os.fchmod(fd, stat.S_IMODE(os.stat(path).st_mode))
        except (OSError, ValueError):
            pass
        with os.fdopen(fd, "w", newline="") as f:
            write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise


# ---------------------------------------------------------------- columns
# Every CSV in this product is append-only with a header written once, at
# creation. That makes adding a column a data-corruption event unless somebody
# rewrites the existing file first, and there were THREE writers of two files
# that did not: bin/sample appended 19 fields under a 15-field header, and both
# the API and bin/chem disagreed about whether chemicals.csv has a `by` column.
# Each exited 0 and printed its usual success line while every row it wrote
# mis-parsed from then on.
#
# The repair lived inside the server's agent-ingest path, which is the one
# writer that did it correctly. It lives here now because this is the module
# every writer can reach — aqualink.py cannot import server.py, which is
# precisely how it came to be the exception.

def migrate_columns(path, cols):
    """Bring an existing CSV up to the current column set, or refuse.

    ONLY ADDITIVE. If a column that exists on disk is not in `cols`, this refuses
    rather than rewriting: that case is somebody deleting a column from the
    source, and quietly dropping recorded history to match is not a migration, it
    is data loss with a tidy name.

    It also refuses when any ROW is longer than the header, which is the state
    the un-migrated writers above left files in. Those overflow fields are
    recorded readings — a pool setpoint of 88, a freeze setpoint of 34, the
    identity of whoever logged a dose — and the rewrite this function does would
    have silently dropped them while reporting how many columns it had ADDED.
    A file in that state needs a person, not an automatic repair.

    The caller holds the lock; the write is atomic, so an interruption leaves the
    old file rather than half a new one.

    Returns None when nothing was needed, else (added, rows) for the log.
    """
    if not os.path.exists(path):
        return None
    with open(path, newline="") as f:
        try:
            old = next(csv.reader(f))
        except StopIteration:
            return None                      # empty file; the writer will head it
    cols = list(cols)

    # THE CORRUPTION CHECK RUNS FIRST, BEFORE "nothing to migrate".
    #
    # This scan used to sit BELOW `if old == cols: return None` — so it was
    # reachable only during an actual column migration, and dead in the steady
    # state, which is where every file in a deployed install lives. The header
    # on disk equals COLS on every normal day; that early return was taken on
    # every normal day; and so the refusal this function advertises, and the
    # .bak below it, never ran when it mattered.
    #
    # Measured: chemicals.csv with a current header and one row carrying an
    # extra field (what an unquoted comma in `note` produces) — migrate_columns
    # returned None, rewrite_locked proceeded, and `by=demo`, the identity of
    # whoever logged the dose, was gone. No .bak. The route is /api/chemical/
    # delete, which answered 200 and reported "remaining": 3.
    #
    # CLAUDE.md states the promise this breaks: "A row LONGER than its header is
    # a file some writer already corrupted, and those overflow fields are
    # recorded readings — it stops and asks for a person rather than dropping
    # them." That was true only while a migration was pending.
    OVER = "\x00over"                        # a key no real column can collide with
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f, restkey=OVER))
    long = [i for i, r in enumerate(rows, start=2) if r.get(OVER)]
    if long:
        raise ValueError(
            f"{os.path.basename(path)} has {len(long)} row(s) with MORE fields than "
            f"its header (first at line {long[0]}): {rows[long[0]-2][OVER]!r}. That is "
            f"a file some writer appended to without migrating it, and those extra "
            f"fields are recorded readings. Refusing to rewrite — rewriting would "
            f"delete them. Repair the header by hand against the writer's COLS.")

    if old == cols:
        return None
    lost = [c for c in old if c not in cols]
    if lost:
        raise ValueError(
            f"{os.path.basename(path)} has column(s) {lost} that the code no longer "
            f"knows about. Refusing to rewrite the file: that would delete recorded "
            f"readings. Add them back to COLS, or move the file aside deliberately.")

    # The history is irreplaceable and this is the only operation that rewrites
    # all of it. replace_atomically means a crash cannot leave half a file, but
    # it cannot undo a migration that was wrong about the shape of the old one.
    backup = f"{path}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)

    def _write(fh):
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in cols})
    replace_atomically(path, _write)
    return ([c for c in cols if c not in old], len(rows))


def append_locked(path, cols, row):
    """Migrate then append. THE CALLER MUST ALREADY HOLD THE LOCK.

    Migrate-then-append is one operation and every writer of these files needs
    both halves. Calling only the second is what corrupted samples.csv and
    chemicals.csv, and it read as ordinary correct-looking code at each call
    site — which is why the answer is a function rather than a comment.

    Returns migrate_columns' result, so a caller can say what it repaired.
    """
    did = migrate_columns(path, cols)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cols), extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)
    return did


def append_if_new(data_dir, name, path, cols, row, key):
    """Append one row unless `key` already names a row in the file.

    THE DEDUP READ HAPPENS INSIDE THE LOCK, which is the whole point. The
    collectors read the file to build a `seen` set, decided, and only then
    called an append that took the lock — so the lock covered the write and not
    the decision, and two runs both found the timestamp absent and both wrote
    it. Measured: 12 of 12 concurrent collector pairs wrote a duplicate row for
    one measurement.

    That is reachable, not theoretical: /api/refresh spawns the collector as a
    subprocess while cron fires the same one at 14:00, and the household's own
    readings.csv already carries 2026-09-08 twice.

    Returns True when the row was written, False when it was already there.
    """
    with exclusive(data_dir, name):
        seen = set()
        if os.path.exists(path):
            with open(path, newline="") as f:
                seen = {r.get(key, "") for r in csv.DictReader(f)}
        if row.get(key, "") in seen:
            return False
        append_locked(path, cols, row)
        return True


def rewrite_locked(path, cols, rows):
    """Rewrite a whole CSV. THE CALLER MUST ALREADY HOLD THE LOCK.

    The counterpart to append_locked, and it exists because the pairing was
    enforced for appends only. Four rewriters -- deleting a dose, editing one,
    saving a by-hand reading, merging Leslie's -- each rebuilt their file from a
    hardcoded COLS list with no migrate_columns call, so a column on disk that
    the code does not know was silently deleted along with every value in it.

    Measured: with an `operator_initials` column present, logging a dose was
    REFUSED ("that would delete recorded readings") while Delete on any row
    returned 200 and destroyed the column, with no .bak. The two policies lived
    on the same tab, on the same file. Worse, the destruction hid itself: once
    the column was gone the append path stopped refusing, so the only visible
    symptom disappeared with the data.

    migrate_columns first, so an unknown column refuses here exactly as it does
    on the append path, and an additive change is backed up before the rewrite.
    """
    migrate_columns(path, cols)

    def _write(fh):
        w = csv.DictWriter(fh, fieldnames=list(cols), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    replace_atomically(path, _write)


def append_row(data_dir, name, path, cols, row):
    """append_locked, taking the lock itself. For a caller that holds nothing."""
    with exclusive(data_dir, name):
        return append_locked(path, cols, row)
