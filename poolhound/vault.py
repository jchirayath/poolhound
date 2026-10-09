#!/usr/bin/env python3
"""Encrypted credential store.

WHY THE KEY LIVES IN THE KEYCHAIN AND NOT NEXT TO THE FILE
  Encrypting a file and leaving its key beside it on the same disk is theatre.
  Anything that can read the ciphertext can read the key, so it protects against
  nothing except a casual `cat`, while looking like real security — which is
  worse than an honest plaintext file, because it invites people to relax about
  where the file ends up.

  What makes this real is where the key goes: the macOS login keychain, which is
  encrypted with the login password and unlocked by the OS on login. The
  ciphertext can then sit in a backup, a Time Machine snapshot, a synced folder
  or an accidental copy without carrying its own key along.

  That is the actual threat model here. Nobody is attacking this machine's RAM.
  What realistically happens to a credentials file is that it gets backed up,
  copied to another machine, or committed by accident — and against all three,
  ciphertext without the key is genuinely safe where a 600-mode plaintext file
  is not.

WHAT THIS DOES NOT DEFEND AGAINST
  Anything running as this user while the keychain is unlocked, which includes
  poolhound itself. That is unavoidable: a collector that must log in to
  WaterGuru at two o'clock in the morning needs the password without a human
  present. Stated plainly rather than glossed over.

FALLBACK
  On a machine with no keychain the key goes in a 0600 file and the module says
  so, loudly and every time. It is still better than plaintext for the backup
  case — barely — and the warning exists so nobody mistakes it for the real thing.
"""
import base64, json, os, secrets, shutil, stat, subprocess, sys, tempfile
import urllib.error, urllib.request

SERVICE = "poolhound"
ACCOUNT = "vault-key"

# Where the encrypted store lives. POOLHOUND_VAULT moves it; everything else
# keeps the historical location.
#
# This used to be an unconditional ~/.poolhound, which made it the ONE piece of
# state that did not follow POOLHOUND_DATA/POOLHOUND_CONFIG. An install pointed
# at a scratch directory still read and wrote the operating user's real
# credentials — so a "sandboxed" test run resolved live logins and the
# collectors made real API calls against the household's accounts. Deliberately
# NOT derived from POOLHOUND_DATA by default: on the server that is the CIFS share,
# mounted file_mode=0660, which is both the wrong permissions for a secret and
# the wrong place for one.
HOME = os.path.expanduser(os.environ.get("POOLHOUND_VAULT") or "~/.poolhound")
VAULT = os.path.join(HOME, "vault.enc")
KEYFILE = os.path.join(HOME, "vault.key")     # fallback only

# Setting POOLHOUND_VAULT is a statement that THIS store is the one to use, so
# the implicit ~/.waterguru-style fallbacks below are not consulted. Without
# this, relocating the vault moved only half the lookup: an install pointed
# elsewhere still found the operating user's plaintext files and reported their
# logins as stored. A path given explicitly in config.toml is still honoured —
# that is a deliberate choice by whoever wrote the config, not a fallback.
ISOLATED = bool(os.environ.get("POOLHOUND_VAULT"))


def _kc_account(account):
    """The keychain account name for this install.

    POOLHOUND_VAULT RELOCATES EVERYTHING THE STORE HOLDS, OR IT RELOCATES
    NOTHING. It moved vault.enc and it did not move the two items that are not
    in vault.enc — the master key under `vault-key` and the Pi's shared secret
    under `agent-token`. Both were looked up by a FIXED keychain service/account
    pair, so they were not namespaced by HOME at all: a review server pointed at
    an empty scratch directory was handed the household's live agent token in
    one call, and the same `security` lookup returns the key that decrypts every
    stored password. An isolation that covers the file and not the key is not
    isolation, it is a file move.

    So when the store is relocated the keychain items are too, under a name
    derived from where it was relocated TO. Relocating it back finds them again;
    a different directory is a different install and finds nothing, which is
    what isolation is supposed to mean. The unrelocated case keeps the
    historical bare names, so no existing install has to be migrated.
    """
    if not ISOLATED:
        return account
    import hashlib
    tag = hashlib.sha256(os.path.abspath(HOME).encode()).hexdigest()[:12]
    return f"{account}@{tag}"

# The services this holds, and what each field is called where a person can see
# it. Order is the order they appear on the page.
#
# `config` IS THE CONFIG KEY THAT NAMES THIS SERVICE'S CREDENTIAL FILE, and it
# is here because it was nowhere. save_credential looked the SMTP path up under
# cfg["smtp"]["credentials"] — a section that exists in no config.toml, in no
# DEFAULTS and nowhere else in the repository — while watch.py reads it from
# notify.smtp_credentials, which is the key the Settings form itself writes. So
# the one service whose file location the product lets you EDIT was the one the
# writer could not see: every file-store save of the mail password landed on the
# built-in ~/.poolhound_smtp whatever the form said, and the reader went on
# looking where the form pointed.
#
# Two spellings of one fact is the defect; one accessor, credential_file(), is
# the fix. Derived from this table rather than typed at each call site.
SERVICES = {
    "waterguru": {"label": "WaterGuru", "user": "Email", "secret": "Password",
                  "note": "The account the pod is registered to.",
                  "legacy": "~/.waterguru",
                  "config": ("waterguru", "credentials")},
    "leslies":   {"label": "Leslie's", "user": "Email", "secret": "Password",
                  "note": "Your lesliespool.com login.",
                  "legacy": "~/.leslies",
                  "config": ("leslies", "credentials")},
    # The assistant's provider key. No legacy file: this service never existed
    # before the vault did, so there is no plaintext copy to migrate and none
    # should be invented. The username is the provider's name — carried only so
    # the Settings row can say WHOSE key it is, since a bare secret gives a
    # reader no way to tell an OpenAI key from a local endpoint's ignored one.
    "ai":        {"label": "AI assistant", "user": "Provider", "secret": "API key",
                  "note": "Any OpenAI-compatible endpoint. The base URL and "
                          "model are set below; this is the key alone.",
                  "config": ("ai", "credentials")},
    # The watchdog's own heartbeat. A push URL carries a token in its path, so
    # it is a credential and lives here rather than in SETTABLE -- the same
    # judgement the assistant's key gets, and for the same reason: a secret
    # that is editable on a settings form is a secret in a page, a log and a
    # backup. No legacy file; this never existed before the vault.
    #
    # WHY THE WATCHDOG NEEDS WATCHING. Nothing inside this system can report
    # its own absence. MEASURED: the share failed to mount after a reboot, the
    # container refused to start, and collect.sh skipped every job for forty
    # hours -- including `watch` every thirty minutes, 86 times. The chemistry
    # alarm that should have fired at 07:37 UTC fired seventeen hours late,
    # because the thing that raises alarms was one of the casualties. A push
    # monitor alarms on the ping it did NOT receive, which is the only shape of
    # check that survives its subject being dead.
    "heartbeat": {"label": "Watchdog heartbeat", "user": "Monitor",
                  "secret": "Push URL",
                  "note": "A push URL (uptime-kuma or similar). bin/watch pings "
                          "it on every completed run; the monitor alarms when a "
                          "ping does not arrive.",
                  "config": ("notify", "heartbeat_credentials")},
    "smtp":      {"label": "Outgoing email", "user": "Username", "secret": "App password",
                  "note": "Gmail needs an app password, not the account password.",
                  "legacy": "~/.poolhound_smtp",
                  "config": ("notify", "smtp_credentials")},
}

def _implicit_legacy(service):
    """The historical ~/.<service> file, or "" when this install is isolated."""
    if ISOLATED:
        return ""
    return os.path.expanduser(SERVICES.get(service, {}).get("legacy", ""))

def config_key(service):
    """Which config section and key names this service's credential FILE.

    One place, so a reader and a writer cannot look under different names. They
    did: see the note on SERVICES above.
    """
    spec = SERVICES.get(service)
    return spec.get("config") if spec else None

def credential_file(service, cfg=None):
    """Where this service's plaintext credential file lives, or "".

    THE ONE ANSWER TO THAT QUESTION. Config first — a path somebody wrote down
    is a deliberate choice — then the historical ~/.<service>, and nothing at
    all on an isolated install, which is the same order and the same guard every
    other route to a credential in this module uses.
    """
    key = config_key(service)
    if cfg and key:
        sec = cfg.get(key[0]) or {}
        p = (sec.get(key[1]) or "").strip() if isinstance(sec, dict) else ""
        if p:
            # The isolation guard applies to a configured path too, for the
            # reason put_file() spells out at length: config.DEFAULTS supplies
            # one on every install, so "explicitly configured" is not the
            # deliberate act it sounds like.
            return "" if ISOLATED else os.path.expanduser(p)
    return _implicit_legacy(service)

class VaultError(Exception):
    pass

class VaultUnreadable(VaultError):
    """The file is there and the key does not open it.

    Its own class because it is the ONE failure that a write may answer by
    starting a fresh store. The other VaultErrors — a key file other people can
    read, a missing cryptography package — are conditions to fix, not files to
    move aside, and a blanket `except VaultError` on the write path would have
    thrown away a perfectly good vault over an unimported library.
    """

def _ensure_home():
    os.makedirs(HOME, exist_ok=True)
    os.chmod(HOME, 0o700)

def _keychain_available():
    return sys.platform == "darwin" and shutil.which("security")

def _key_from_keychain():
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE,
                            "-a", _kc_account(ACCOUNT), "-w"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return base64.b64decode(r.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None

def _key_to_keychain(key):
    # -U updates in place rather than erroring when one already exists, and the
    # value goes via -w so it never appears in a process listing as an argument
    # anybody can see... which it does anyway on macOS, so the key is generated
    # here and written once rather than being a password a human chose.
    try:
        r = subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE,
                            "-a", _kc_account(ACCOUNT), "-w",
                            base64.b64encode(key).decode(),
                            "-D", "poolhound vault key",
                            "-j", "Encrypts poolhound's stored service passwords"],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

def key_location():
    """Where the master key is, so the page can be honest about it."""
    if keyvault_name():
        return "keyvault"
    if _keychain_available() and _key_from_keychain() is not None:
        return "keychain"
    if os.path.exists(KEYFILE):
        return "file"
    return "none"

def _get_or_make_key():
    _ensure_home()
    if _keychain_available():
        k = _key_from_keychain()
        if k and len(k) == 32:
            return k, "keychain"
        k = secrets.token_bytes(32)
        if _key_to_keychain(k):
            return k, "keychain"
    # Fallback.
    if os.path.exists(KEYFILE):
        if os.stat(KEYFILE).st_mode & 0o077:
            raise VaultError(f"{KEYFILE} is readable by others — chmod 600 it")
        with open(KEYFILE, "rb") as f:
            k = base64.b64decode(f.read().strip())
        if len(k) == 32:
            return k, "file"
    k = secrets.token_bytes(32)
    fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(base64.b64encode(k))
    return k, "file"

def _aead(key):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise VaultError("the cryptography package is needed to encrypt the vault; "
                         "install it into .venv")
    return AESGCM(key)

def _load_raw():
    if not os.path.exists(VAULT):
        return {}
    key, _ = _get_or_make_key()
    with open(VAULT, "rb") as f:
        blob = f.read()
    if len(blob) < 13:
        return {}
    nonce, ct = blob[:12], blob[12:]
    try:
        plain = _aead(key).decrypt(nonce, ct, b"poolhound-vault-v1")
    except Exception:
        raise VaultUnreadable(
            "the vault could not be decrypted — the key does not match. "
            "If the keychain entry was removed, the stored passwords are "
            "unrecoverable and will have to be entered again.")
    return json.loads(plain.decode())


def _load_for_write():
    """The stored records, or a fresh empty store when the file cannot be read.

    AN ERROR THAT INSTRUCTS A RE-ENTRY HAS TO ACCEPT ONE. An undecryptable
    vault.enc tells the reader, in the message above, that the passwords are
    gone and will have to be entered again — and then put() called _load_raw()
    before writing, so typing one returned that same sentence, and drop() raised
    out of the handler entirely: the socket closed with NO HTTP status and the
    page's message never changed. Measured on a sandboxed server with one byte
    of vault.enc flipped: save answered 400 with the re-entry instruction it had
    just refused, and Forget answered nothing at all (curl exit 52). The only
    remedy left was deleting the file by hand — inside a 0700 docker volume on
    the server, where nobody running the page can reach it.

    So a write starts over, and the unreadable file is BACKED UP first, with a
    timestamp, the way locking.migrate_columns backs a CSV up before the one
    operation that rewrites all of it. The ciphertext is worth keeping: it is
    still the only copy of those passwords, and a keychain entry that was
    removed can be restored from a backup while a deleted file cannot.

    Reads are untouched — get() and status() still report the failure rather
    than silently presenting an empty store as a healthy one.
    """
    try:
        return _load_raw()
    except VaultUnreadable:
        import time as _time
        stamp = _time.strftime("%Y%m%d-%H%M%S")
        aside = f"{VAULT}.unreadable-{stamp}.bak"
        # A second failure inside the same second must not overwrite the first
        # backup and then delete its source. The whole point of the copy is that
        # the ciphertext is the only copy of those passwords.
        n = 1
        while os.path.exists(aside):
            n += 1
            aside = f"{VAULT}.unreadable-{stamp}-{n}.bak"
        shutil.copy2(VAULT, aside)
        os.remove(VAULT)
        print(f"vault: {VAULT} could not be decrypted; it has been copied to "
              f"{aside} and a fresh store started. The passwords it held are "
              f"not recoverable without the old key.",
              file=sys.stderr, flush=True)
        return {}

def _save_raw(data):
    _ensure_home()
    key, where = _get_or_make_key()
    nonce = secrets.token_bytes(12)
    ct = _aead(key).encrypt(nonce, json.dumps(data).encode(), b"poolhound-vault-v1")
    tmp = VAULT + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(nonce + ct)
    os.replace(tmp, VAULT)
    return where

def put(service, username, password):
    """Store one login. A blank password KEEPS the stored one.

    THE FIELD SAYS `unchanged` AND MEANT `unchangeable`. status() returns the
    username on purpose — "it is how somebody recognises which account is in
    there, and getting that wrong is a common and confusing failure" — and the
    password box carries the placeholder `unchanged`, which promises exactly
    this. Requiring both refused it: the one field the product deliberately
    shows was the one field it could not correct, and fixing a typo in an email
    address meant finding the app password again. A blank password with no
    record to keep is still refused, because that would store a login that
    cannot log in.
    """
    if service not in SERVICES:
        raise VaultError(f"unknown service {service!r}")
    if not username:
        raise VaultError("a username is needed")
    # Refuse rather than write something that will never be read. Where Key Vault
    # answers for this service, credentials_for() returns from it before the
    # encrypted file is consulted — so this used to store the new password,
    # report success, flip the badge to "stored", and leave the collector using
    # the old one. Failing here with the place to go is the honest outcome.
    if keyvault_name():
        try:
            u, p = kv_credentials(service)
        except Exception:
            u = p = None
        if u and p:
            raise VaultError(
                f"{SERVICES[service]['label']} is read from Azure Key Vault "
                f"({keyvault_name()}) on this host, which is checked before this "
                f"store — saving here would change nothing. Update the secret in "
                f"Key Vault instead.")
        # "NOTHING THERE" AND "COULD NOT ASK" ARE NOT THE SAME ANSWER.
        #
        # kv_get never raises: it records the reason in _kv_errors and returns
        # None. So when the vault was unreachable -- a network blip, an expired
        # managed identity, a 403 -- this saw u=p=None, concluded the secret was
        # absent, wrote the new password to the local store and reported success.
        # Key Vault is checked FIRST by credentials_for(), so once it came back
        # the collector went on using the old password while Settings showed a
        # green "stored" badge. That is exactly the failure this refusal exists
        # to prevent, reached through the one door left open.
        why = [f"{n}: {e}" for n in kv_names(service)
               if (e := _kv_errors.get(n))]  # noqa: E501
        if why:
            raise VaultError(
                f"Azure Key Vault ({keyvault_name()}) could not be read for "
                f"{SERVICES[service]['label']} — {'; '.join(why)}. It is checked "
                f"before this store, so a secret saved here would be shadowed the "
                f"moment the vault answers again, and nothing would say so. Fix "
                f"the vault access, or add poolhound-{service}-user and "
                f"poolhound-{service}-password there.")
    data = _load_for_write()
    if not password:
        password = (data.get(service) or {}).get("password") or ""
        if not password:
            raise VaultError(
                f"there is no {SERVICES[service]['label']} password stored yet, so "
                f"this one cannot be left unchanged — enter it as well.")
    data[service] = {"username": username, "password": password}
    return _save_raw(data)

def drop(service):
    data = _load_for_write()
    if service in data:
        del data[service]
        _save_raw(data)
        return True
    return False

def get(service):
    """Username and password, or None. Never logged, never returned to a page."""
    try:
        rec = _load_raw().get(service)
    except VaultError:
        return None
    if not rec:
        return None
    return rec.get("username"), rec.get("password")

def status():
    """What is stored, without revealing any of it.

    The username is shown because it is how somebody recognises which account is
    in there, and getting that wrong is a common and confusing failure. The
    password is never returned in any form — not masked, not truncated, not its
    length.
    """
    try:
        data = _load_raw()
        err = None
    except VaultError as e:
        data, err = {}, str(e)
    # Where a credential would actually be READ from, which is not the same
    # question as what the encrypted file happens to contain. On the server Key Vault
    # answers first, so driving these badges off the file alone reported "not
    # set" for every login while the collectors were using them perfectly well —
    # a status display contradicting the thing it describes.
    kv = keyvault_name()
    out = {"key_location": key_location(), "error": err,
           "keyvault": kv or None, "services": {}}
    for key, spec in SERVICES.items():
        rec = data.get(key)
        legacy = _implicit_legacy(key)
        source, username = None, (rec or {}).get("username", "")
        if kv:
            try:
                u, p = kv_credentials(key)
                if u and p:
                    source, username = "keyvault", u
            except Exception:
                pass                       # unreachable Key Vault is not "absent"
        if source is None and rec:
            source = "vault"
        if source is None and legacy and os.path.exists(legacy):
            source = "file"
        out["services"][key] = {
            "label": spec["label"], "user_label": spec["user"],
            "secret_label": spec["secret"], "note": spec["note"],
            "stored": source is not None,
            "source": source,
            # Why a Key Vault lookup failed, when it failed for a reason other
            # than the secret being absent. "Not stored" and "the vault did not
            # answer" looked identical on this page and have entirely different
            # fixes -- one is a password to enter, the other is a role
            # assignment or an IMDS outage.
            "kv_error": (_kv_errors.get(kv_names(key)[0])
                         or _kv_errors.get(kv_names(key)[1])),
            # True when this host resolves the credential somewhere the Settings
            # form cannot write. Saving here would report success and change
            # nothing that is ever read.
            "read_only": source == "keyvault",
            "username": username,
            "legacy_file": legacy if (legacy and os.path.exists(legacy)) else None,
        }
    return out

def import_legacy(remove=False):
    """Pull existing plaintext credential files into the vault.

    Not removed unless asked. Deleting the only copy of a password because a new
    store appeared to work is the kind of helpfulness nobody wants, and the
    import is verifiable afterwards by reading it back.
    """
    # HONOURS THE ISOLATION, LIKE EVERY OTHER LOOKUP. This called
    # os.path.expanduser(spec["legacy"]) directly rather than going through
    # _implicit_legacy(), so it was the one path that still reached into the
    # operating user's home when POOLHOUND_VAULT said "this store is the one to
    # use". Demonstrated during review: a server pointed at a scratch directory
    # answered POST /api/credential/import with 200 and a list naming the
    # household's real WaterGuru and Leslie's logins, which it had just copied
    # into the scratch vault. `remove` would then have MOVED the originals
    # aside. An isolated install has nothing to import; saying so is the whole
    # of the fix.
    if ISOLATED:
        return []
    moved = []
    for key, spec in SERVICES.items():
        p = _implicit_legacy(key)
        if not p or not os.path.exists(p):
            continue
        try:
            lines = [l.strip() for l in open(p) if l.strip()]
        except OSError:
            continue
        if len(lines) < 2:
            continue
        put(key, lines[0], lines[1])
        moved.append({"service": key, "from": p, "username": lines[0]})
        if remove:
            os.replace(p, p + ".imported")
            os.chmod(p + ".imported", 0o600)
    return moved

def put_file(service, user, secret, path=None):
    """Write one credential to a plain file, the way the collectors used to read them.

    OFFERED, AND ARGUED AGAINST ON THE PAGE.

    A plaintext file is a real downgrade from the vault: anything that can read
    your home directory can read the password, where the vault at least requires
    a key the operating system is defending. It exists because some deployments
    genuinely need it — a collector running as another user, a box with no
    keychain and no managed identity — and because a person who wants this will
    otherwise write the file by hand and get the permissions wrong.

    Two lines, username then secret, mode 0600, and the directory checked too:
    a 0600 file inside a world-writable directory can be replaced wholesale.
    """
    # THE SAME GUARD AS credentials_for(), FOR THE SAME REASON — and it was
    # missing here, which is the whole of the escape.
    #
    # The comment that used to sit here said the implicit fallback honours the
    # isolation while "an explicit path does not, because a path written into
    # config.toml is a deliberate choice by whoever wrote it". That exemption
    # describes a BUILT-IN DEFAULT: config.DEFAULTS supplies "~/.waterguru" on
    # every install, so `path` is never empty for waterguru and the guard below
    # it was unreachable for exactly the services it protects.
    #
    # credentials_for() made this argument, in these words, and was fixed. The
    # READ side was fixed and the WRITE side was not — and the selftest section
    # titled "isolation covers every route to a credential" enumerates five
    # reads and no writes, so it passed eleven checks while this stood open.
    #
    # It was then demonstrated the expensive way: a sandboxed review server,
    # with POOLHOUND_VAULT set and ISOLATED asserted True, answered POST
    # /api/credential {"store":"file"} by overwriting this household's real
    # ~/.waterguru with a synthetic credential, and named the path it had just
    # written in its own response. put_file takes no backup, so the previous
    # contents were gone. Third escape from this boundary, second by this exact
    # mechanism.
    if ISOLATED:
        raise VaultError(
            f"this install has POOLHOUND_VAULT set, so it will not write a "
            f"credential file outside that store — not even to a path named in "
            f"config.toml, because the default config names one on every "
            f"install. Save {service} to the vault instead.")
    p = os.path.expanduser(path) if path else _implicit_legacy(service)
    if not p:
        raise VaultError(
            f"no file path configured for {service}"
            + (" — this install has POOLHOUND_VAULT set, so the historical "
               "~/.<service> location is deliberately not used. Give a path in "
               "config.toml, or save to the vault instead." if ISOLATED else ""))
    d = os.path.dirname(p) or "."
    if not os.path.isdir(d):
        raise VaultError(f"{d} does not exist")
    if os.stat(d).st_mode & 0o002:
        raise VaultError(f"{d} is world-writable; the file could be replaced under you")
    # Written via a temporary file in the same directory and renamed, so a
    # reader never sees a half-written credential and a crash never leaves one.
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".ph-", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"{user}\n{secret}\n")
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(p, 0o600)
    return p


def credentials_for(service, legacy_path=None):
    """What every collector should call.

    Vault first, then the old plaintext file, so an install that has not migrated
    keeps working and one that has never touches disk in the clear. The legacy
    path keeps its permission check — a file that was safe to read yesterday can
    stop being safe today.
    """
    # Key Vault first where one is configured — that is the server, and on the
    # server there is no Keychain and no reason to keep a second copy on disk.
    if keyvault_name():
        u, p = kv_credentials(service)
        if u and p:
            return u, p, "keyvault"
    got = get(service)
    if got and got[0] and got[1]:
        return got[0], got[1], "vault"
    # THE GUARD BELONGS HERE, NOT ONLY IN _implicit_legacy().
    #
    # POOLHOUND_VAULT is a statement that THIS store is the one to use, and the
    # guard was written into _implicit_legacy() alone — which this reaches only
    # when the caller passes no path. Every real caller passes one:
    # waterguru.py and panels.py both read config's `credentials` key, and
    # config.DEFAULTS SUPPLIES "~/.waterguru" on every install. So the "a path
    # given explicitly is a deliberate choice by whoever wrote the config"
    # exemption was describing a built-in default, and the guard was unreachable
    # for the two collectors it exists to protect.
    #
    # Measured with POOLHOUND_VAULT set and ISOLATED True: import_legacy()
    # returned [], agent_token() returned None, and credentials_for() still
    # resolved this household's real login off ~/.waterguru. A sandbox that is
    # isolated in three places and not the fourth is not a sandbox.
    if ISOLATED:
        return None, None, None
    p = (os.path.expanduser(legacy_path) if legacy_path else _implicit_legacy(service))
    if p and os.path.exists(p):
        if os.stat(p).st_mode & stat.S_IRWXG | os.stat(p).st_mode & stat.S_IRWXO:
            raise VaultError(f"{p} is readable by others — chmod 600 it, or move it "
                             f"into the vault from the Settings tab")
        lines = [l.strip() for l in open(p) if l.strip()]
        if len(lines) >= 2:
            return lines[0], lines[1], "file"
    return None, None, None



# ============================================ AZURE KEY VAULT
# The server deployment keeps its secrets in Azure Key Vault instead of an
# encrypted file, for one reason: the encrypted file needs a key, and on a
# server the key has to live somewhere on the same disk as the thing it
# protects. That is a lock with the key taped to the door. A managed identity
# has no key at all — the platform vouches for the VM, the VM never holds a
# credential, and rebuilding the host from scratch does not mean re-keying
# anything.
#
# Deliberately spoken over plain urllib rather than azure-identity. The whole
# exchange is two GETs, and pulling in the Azure SDK would add ~40 MB and a
# dependency tree to a container that is otherwise stdlib plus six packages
# the WaterGuru collector needs.

IMDS = ("http://169.254.169.254/metadata/identity/oauth2/token"
        "?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net")
KV_API = "7.4"
# name -> (value, fetched_at). NOT for the life of the process: this server runs
# under `restart: unless-stopped` for weeks, so a secret rotated in Key Vault was
# never seen again until somebody recreated the container -- while put() refused
# the local save with "Update the secret in Key Vault instead", advice which,
# followed, changed nothing in the process that gave it. _imds_token() already
# caches with an expiry; a cached secret should too.
KV_TTL = 600.0
_kv_cache = {}
_kv_token = [None, 0]   # [token, expires_at]

def keyvault_name():
    return os.environ.get("POOLHOUND_KEYVAULT", "").strip()

def _imds_token():
    """An access token for Key Vault, from the instance metadata service.

    Cached until a minute before it expires. IMDS is rate-limited and the
    token is valid for hours, so fetching one per secret read would be both
    slow and liable to throttle during a collector run.
    """
    import time as _time
    tok, exp = _kv_token
    if tok and _time.time() < exp - 60:
        return tok
    req = urllib.request.Request(IMDS, headers={"Metadata": "true"})
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.load(r)
    _kv_token[0] = d["access_token"]
    _kv_token[1] = float(d.get("expires_on") or (_time.time() + 3000))
    return _kv_token[0]

# Why a lookup failed, when it failed for a reason other than "no such secret".
# Read by status(), so a page can distinguish a vault that said no from a vault
# that could not be reached at all.
_kv_errors = {}

def kv_get(name):
    """One secret by name, or None. Never raises — a missing secret is a
    configuration state the caller has to handle anyway (SMTP is optional),
    and an exception here would take down a collector over a feature it was
    not using."""
    import time as _time
    hit = _kv_cache.get(name)
    if hit and _time.time() - hit[1] < KV_TTL:
        return hit[0]
    vault = keyvault_name()
    if not vault:
        return None
    try:
        url = f"https://{vault}.vault.azure.net/secrets/{name}?api-version={KV_API}"
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + _imds_token()})
        with urllib.request.urlopen(req, timeout=15) as r:
            val = json.load(r).get("value")
    except urllib.error.HTTPError as e:
        # 404 is the ONE answer that means what the bare `return None` used to
        # imply: there is no such secret. Everything else -- 401 on an expired
        # token, 403 on a role assignment somebody removed, 429, a 5xx -- says
        # the vault could not answer, which is a different problem with a
        # different fix and was reported identically. Still not raised: a
        # collector must not die over a feature it is not using. But recorded,
        # so status() can say "the vault did not answer" instead of "not stored".
        if e.code != 404:
            _kv_errors[name] = f"HTTP {e.code}"
            print(f"vault: Key Vault {vault!r} answered HTTP {e.code} for {name!r} "
                  f"— this is NOT the same as the secret being absent",
                  file=sys.stderr, flush=True)
        else:
            _kv_errors.pop(name, None)
        return None
    except Exception as e:
        _kv_errors[name] = type(e).__name__
        print(f"vault: could not reach Key Vault {vault!r} for {name!r}: "
              f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return None
    _kv_errors.pop(name, None)
    _kv_cache[name] = (val, _time.time())
    return val

def kv_names(service):
    """The two secret names a service lives under. Spelled in one place: it was
    written out by hand in three, and a name that disagrees with the one that
    was WRITTEN is a lookup that silently finds nothing."""
    return (f"poolhound-{service}-user", f"poolhound-{service}-password")


def forget_cache(service=None):
    """Drop cached secrets, so the next read asks Key Vault again.

    Called after a credential write and from status(), because the page that
    reports whether a rotated secret is in use must not be answering from a
    copy taken before the rotation.
    """
    if service is None:
        _kv_cache.clear()
    else:
        for n in kv_names(service):
            _kv_cache.pop(n, None)


def kv_credentials(service):
    u, p = (kv_get(n) for n in kv_names(service))
    return (u, p) if (u and p) else (None, None)


# ============================================ THE AGENT TOKEN
# A shared secret between the server and the Pi, used only on the /api/agent/* routes.
# It is not a person's credential and does not belong in the encrypted vault with
# the service logins: it has to be retrievable in one piece to be copied onto two
# other machines, and mixing "things a human types" with "things a machine
# presents" in one store makes both harder to reason about.

TOKEN_ACCOUNT = "agent-token"
TOKEN_SECRET = "poolhound-agent-token"

# The file the server and the Pi can be handed a token in, for a host with no
# keychain and no managed identity.
#
# IT LIVED IN server.py, AND SO DID THE LOOKUP, WHICH PUT ONE ROUTE TO THE TOKEN
# OUTSIDE THIS MODULE'S BOUNDARY. server.agent_token() read this file BEFORE
# calling vault.agent_token(), the path is absolute and POOLHOUND_VAULT does not
# move it, so a review server started on the server or the Pi with the vault
# relocated — every isolation assertion in the suite passing — authenticated
# agents with the household's real shared secret. That is the third time a
# credential route has been found outside the guard, and each time the route was
# one this module did not own. So it owns this one now: every way to the token
# is in this function.
#
# The DEFAULT is not consulted under isolation, for the reason put_file() sets
# out: a built-in default is not the deliberate choice the exemption is written
# for. An explicitly exported POOLHOUND_AGENT_TOKEN_FILE is one — nothing
# supplies it but a person, per process — so that is honoured.
TOKEN_FILE_DEFAULT = "/etc/poolhound/agent.token"
TOKEN_FILE = (os.environ.get("POOLHOUND_AGENT_TOKEN_FILE") or "").strip()
TOKEN_FILE_IS_DEFAULT = not TOKEN_FILE
if not TOKEN_FILE:
    TOKEN_FILE = TOKEN_FILE_DEFAULT


def token_file():
    """The token file this install may read, or "" when it may read none."""
    if ISOLATED and TOKEN_FILE_IS_DEFAULT:
        return ""
    return os.path.expanduser(TOKEN_FILE)


def _token_from_file(p=None):
    p = token_file() if p is None else p
    if not p or not os.path.exists(p):
        return None
    if os.stat(p).st_mode & 0o077:
        # Not silently. This returned None inside server.py, so a token file
        # somebody had made world-readable closed every /api/agent/* route and
        # the Pi simply went quiet, with nothing anywhere saying why.
        print(f"vault: {p} is readable by others — chmod 600 it. Ignoring it, so "
              f"the agent routes are closed until it is fixed.",
              file=sys.stderr, flush=True)
        return None
    with open(p) as f:
        return f.read().strip() or None


def agent_token(create=True):
    """Fetch the agent token, generating one in the keychain if asked.

    Returned rather than printed, and never logged. Getting it onto the Pi and
    onto the server is a deliberate act by a person with a terminal — which is the
    point, because a secret that appears in scrollback has been shared with
    every log, backup and screen recording in the room.

    File, then Key Vault, then the keychain — the order server.py used, kept, so
    no deployment changes which copy it answers with.
    """
    tok = _token_from_file()
    if tok:
        return tok
    # On the server the token comes from Key Vault, which is also where the Pi's
    # copy is provisioned from — so there is exactly one authority for it and
    # rotating it is a single write rather than a hunt across three machines.
    if keyvault_name():
        tok = kv_get(TOKEN_SECRET)
        if tok:
            return tok
        # "NOTHING THERE" AND "COULD NOT ASK" ARE NOT THE SAME ANSWER, and this
        # is the door that was still open after put() was taught the difference.
        # kv_get() never raises — it records the true reason in _kv_errors and
        # returns None — so an unreachable vault, an expired managed identity, a
        # 403 on a role assignment somebody removed or a 429 all arrived here as
        # "no token". server.agent_token() then returned None, agent_ok() read
        # that as "unconfigured, fail closed", and every /api/agent/* route shut:
        # the Pi goes silent, which is the same symptom as the house being off
        # the air. Worse with create=True, where the advice was to write a SECOND
        # token into a vault that already holds one — rotating the shared secret
        # out from under the machine that cannot currently be asked about it.
        why = _kv_errors.get(TOKEN_SECRET)
        if why:
            raise VaultError(
                f"Azure Key Vault ({keyvault_name()}) could not be read for the "
                f"agent token — {why}. That is NOT the same as no token being "
                f"stored, so this will not report one missing or write a second "
                f"one. Fix the vault access and ask again.")
        if not create:
            return None
        raise VaultError(f"no {TOKEN_SECRET} in Key Vault; write one there "
                         f"rather than generating a second token on this host")
    if not _keychain_available():
        # create=False ASKS A QUESTION; it does not request a store. "Is there a
        # token here" has a true answer on a machine with no keychain, and that
        # answer is no — so raising was the wrong shape twice over. server.py
        # papered over it with a bare `except Exception: return None`, which is
        # why nobody noticed that agent_token_fingerprint() has no such wrapper
        # and blew up on any host with neither a keychain nor a Key Vault. The
        # image build is one: it runs bin/selftest on Linux with the example
        # config, and that is where this surfaced.
        if not create:
            return None
        raise VaultError("no keychain here; set the token by hand on both ends")
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE,
                            "-a", _kc_account(TOKEN_ACCOUNT), "-w"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if not create:
        return None
    # 32 bytes of urlsafe base64. Long enough that guessing is not a strategy,
    # short enough to paste into a file over ssh without wrapping.
    tok = secrets.token_urlsafe(32)
    r = subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE,
                        "-a", _kc_account(TOKEN_ACCOUNT), "-w", tok,
                        "-D", "poolhound agent token",
                        "-j", "Shared secret between the server and the pool agent"],
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise VaultError("could not write the token to the keychain")
    return tok

# ---------------------------------------------------- rotating that token
#
# WHY THERE ARE TWO SLOTS
#
# docs/THREAT_MODEL.md carried this as its first open item: the agent token is
# "static, with no expiry and no rotation runbook". The missing runbook was the
# visible half. The reason it was missing is the other half — WITH ONE SLOT
# THERE IS NO CORRECT ORDER TO ROTATE IN. Write the new token to Key Vault
# first and the Pi presents the old one until somebody reaches it, so every
# /api/agent/* call is refused and the pool loses its only sampler. Write it to
# the Pi first and the server refuses it, identically. Either way the gap is
# however long the person takes, `bin/watch` alarms, and the samples inside it
# are gone — the panel is not a store and nothing re-reads it.
#
# So the server accepts two: the current one and, for a stated window, the
# previous one. Rotation becomes an ordered sequence with no outage in it, and
# each step is independently reversible. The runbook is in
# docs/AGENT-PROTOCOL.md.
#
# THE WINDOW IS MANDATORY, which is the part that answers "no expiry". A
# previous token with no deadline is not a rotation in progress, it is two live
# credentials, and the weaker direction to fail in is the one where a stored
# value this module cannot date is REFUSED rather than honoured indefinitely.
# It says so rather than going quiet, because a rotation that silently never
# finishes looks exactly like one that did.
TOKEN_ACCOUNT_PREVIOUS = "agent-token-previous"
TOKEN_SECRET_PREVIOUS = "poolhound-agent-token-previous"

# `<token> <deadline>`, whitespace separated, the deadline in RFC-3339 UTC.
# ONE FORMAT, because the runbook has to be a line somebody can type over ssh
# and because two accepted formats is the drift this project keeps finding.
#
#     printf '%s %s\n' "$OLD" "2026-10-14T00:00:00Z" > /etc/poolhound/agent.token.previous
PREVIOUS_FORMAT = "<token> <YYYY-MM-DDTHH:MM:SSZ>"


def previous_token_file():
    """The second slot's file, beside the first and under the same rules."""
    p = token_file()
    return (p + ".previous") if p else ""


def _parse_previous(raw):
    """(token, deadline) from a stored second slot, or (None, reason).

    Refuses a bare token on purpose: see the comment above. Also refuses a
    deadline it cannot read, rather than treating an unparseable date as
    absent — "I could not tell when this expires" and "this does not expire"
    must not arrive at the same answer, which is the mistake kv_get() was
    taught not to make about "nothing there" and "could not ask".
    """
    import datetime as _dt
    parts = (raw or "").split()
    if len(parts) < 2:
        return None, (f"it is not {PREVIOUS_FORMAT} — a previous token with no "
                      f"deadline is a second live credential, not a rotation")
    tok, when = parts[0], parts[1]
    try:
        # fromisoformat takes +00:00 but not the Z every runbook writes.
        d = _dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None, f"{when!r} is not a date this can read ({PREVIOUS_FORMAT})"
    return (tok, d), None


# ASKED OFTEN, SO CACHED. /api/health is polled every few seconds and the
# agent heartbeats every twenty, and both now want to know whether a rotation
# is in progress — which would be a Key Vault read per poll. server.py already
# has a comment about exactly this hazard beside the token-error field it
# reports instead of re-asking. Sixty seconds, so the cost of the cache is that
# a second slot written by hand takes up to a minute to take effect, which the
# runbook says.
_PREV_TTL_S = 60.0
_prev_cache = [0.0, None]


def agent_token_previous():
    """The outgoing token and its deadline, or None — and never raises.

    NONE IS THE NORMAL STATE. No rotation is in progress most of the time, and
    an absent second slot must be as cheap and as quiet as that fact is. Every
    other outcome is reported to stderr once, because a rotation that is not
    working is a rotation somebody believes has finished.
    """
    import time as _time
    now = _time.monotonic()
    if now - _prev_cache[0] < _PREV_TTL_S:
        return _prev_cache[1]
    got = _agent_token_previous_uncached()
    # Monotonic, because this is a question about how long ago this process
    # asked, not about wall-clock time — the one place the opposite of the
    # agent guard's reasoning applies, since nothing here reads across a
    # restart.
    _prev_cache[0], _prev_cache[1] = now, got
    return got


def _agent_token_previous_uncached():
    raw = None
    try:
        raw = _token_from_file(previous_token_file())
    except OSError:
        raw = None
    if not raw and keyvault_name():
        raw = kv_get(TOKEN_SECRET_PREVIOUS)
    if not raw and not keyvault_name() and _keychain_available():
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", SERVICE,
                 "-a", _kc_account(TOKEN_ACCOUNT_PREVIOUS), "-w"],
                capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                raw = r.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            raw = None
    if not raw:
        return None
    parsed, why = _parse_previous(raw)
    if why:
        _warn_previous(f"the previous agent token is being ignored: {why}")
        return None
    tok, deadline = parsed
    import datetime as _dt
    if deadline <= _dt.datetime.now(_dt.timezone.utc):
        _warn_previous(f"the previous agent token expired at "
                       f"{deadline.isoformat()} — remove the second slot")
        return None
    if tok == agent_token(create=False):
        # A no-op rotation that reads as a finished one. Both slots holding the
        # same value means somebody copied rather than replaced, and the window
        # would then be the only thing standing between here and nothing.
        _warn_previous("the previous agent token is identical to the current "
                       "one — that is a copy, not a rotation")
        return None
    return tok, deadline


_previous_warned = [None]


def _warn_previous(msg):
    """Said once per distinct reason, not once per request."""
    if _previous_warned[0] != msg:
        print(f"vault: {msg}", file=sys.stderr, flush=True)
        _previous_warned[0] = msg


def agent_token_previous_fingerprint():
    """The outgoing token's short hash, for checking the Pi mid-rotation."""
    import hashlib
    got = agent_token_previous()
    if not got:
        return None
    return hashlib.sha256(got[0].encode()).hexdigest()[:12]


def agent_token_fingerprint():
    """A short hash, so two machines can be checked against each other without
    either end revealing the secret."""
    import hashlib
    tok = agent_token(create=False)
    if not tok:
        return None
    return hashlib.sha256(tok.encode()).hexdigest()[:12]
