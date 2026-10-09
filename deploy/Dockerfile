# poolhound on the server.
#
# python:3.12-slim rather than alpine: the WaterGuru collector needs
# `cryptography`, and on alpine that means compiling Rust — a multi-minute build
# and a toolchain in the image — because there are no musl wheels. On slim the
# manylinux wheel installs in seconds and the final image is smaller than the
# alpine one would have been once the build deps were added.
#
# Everything except the WaterGuru path is stdlib. That is deliberate and worth
# preserving: it is why this container can be rebuilt from scratch at any time
# without a lockfile archaeology exercise.
FROM python:3.12-slim

# tini because the server spawns threads for the SSE fan-out and we want signals
# delivered properly on `docker compose down`; without an init, PID 1 ignores
# SIGTERM and every restart waits the full 10s timeout.
# nodejs is not decoration: bin/render runs `node --check` over every inline
# script, and that is the ENTIRE automated gate this project has. Without node in
# the image the check silently returned, so the one thing standing between an
# apostrophe in a JS string literal and a dead page had never executed on the
# machine that renders after every sample, every write and every six hours.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini ca-certificates tzdata nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -u 1010 -m -s /usr/sbin/nologin poolhound

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=America/Los_Angeles

# Dependencies as their own layer, so a code change does not re-resolve them.
COPY deploy/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

WORKDIR /app
COPY poolhound/ /app/poolhound/
COPY bin/       /app/bin/
COPY config/config.example.toml /app/config/config.example.toml
# THE CRONTAB, because the Collection tab READS the schedule rather than
# restating it — and that tab renders in here. Without this the page would show
# no cadence for any source in production, and the alternative (a second copy
# of the schedule written into the code) is the exact drift the tab exists to
# expose: CRON_TZ was inert on the deploy host for five days while a comment
# beside it promised the times matched.
#
# It is four schedule lines and cannot execute anything from in here; cron runs
# on the host, from /etc/cron.d, which bootstrap installs separately.
COPY deploy/poolhound-cron /app/deploy/poolhound-cron

# THE LANDING PAGE'S SCREENSHOTS, which are a build INPUT and not a build
# output. bin/screenshots drives a real browser to take them; there is no
# browser in this image and there should not be one, so they cannot be
# regenerated here the way the pages are. bin/render copies this directory into
# the site volume on every render, which is how they reach the served page in
# production and on a workstation alike.
#
# They are pictures of a synthetic pool built by bin/demo-data, not of this one
# — that is asserted by the generator before it renders anything, and it is the
# reason a public page is allowed to carry screenshots at all.
COPY docs/screenshots/cards/ /app/docs/screenshots/cards/
# THE EMAIL LOGO, which this image did not carry and should have from the
# start. mail.py attaches it by Content-ID -- the only form Gmail renders, as
# against an <svg> or a `data:` URI, which it drops -- and when the file is
# absent send() posts the rest rather than failing. So every alert went out
# looking unbranded and nothing anywhere reported a fault. config.RUNTIME_ASSETS
# declares it now and bin/selftest fails this build if it is not here.
COPY docs/poolhound-mark-email.png /app/docs/poolhound-mark-email.png
RUN chmod +x /app/bin/* && chown -R poolhound:poolhound /app

# Data and site are BIND MOUNTS at runtime, never baked in. The data mount is an
# Azure Files share, which is the whole point: the container and the VM are
# disposable, the CSVs are not.
# Create both mount points AS poolhound before declaring them volumes. Docker
# seeds a fresh named volume from whatever the image has at that path, ownership
# included — so if these do not exist here, the volume is created root-owned and
# the process cannot write its own site directory. The bind-mounted /data takes
# its ownership from the host instead, but it still needs to exist.
# /vault must exist here, owned by poolhound, or Docker creates the named volume
# root:root and the container -- which runs as poolhound -- cannot write to it.
# A volume's ownership is copied from the image's mountpoint only when that
# mountpoint exists; /data and /site always did, /vault did not, so the
# encrypted credential store was mounted somewhere it could not be saved.
# 0700 because it holds secrets: the other two are readable by design.
RUN mkdir -p /data /site /vault \
 && chown poolhound:poolhound /data /site /vault \
 && chmod 700 /vault
ENV POOLHOUND_DATA=/data POOLHOUND_SITE=/site POOLHOUND_PORT=8787

# Which commit is this image? A deployed copy has no .git — the rsync excludes
# it — so version_string() reads /app/REVISION instead, and the footer is the
# only way a reader can tell a fresh page from a month-old one. It has to live
# in the IMAGE, not on the host: the render happens inside the container, and
# anything written into a running container is lost on the next recreate, after
# which the hourly cron render would quietly un-stamp the page again.
# Last layer on purpose, so changing it does not invalidate the expensive ones.
# THE GATES RUN HERE, NOT ONLY ON A LAPTOP. bin/selftest is the regression net
# for the pure functions -- every case in it is something that was actually
# wrong once -- and until this line it ran nowhere in the deploy at all: not at
# build, not in cron, not in bootstrap. An image could be built, pushed and
# serving from code the suite rejects.
#
# It needs no config and no data: the cases that would read them say so and
# skip -- loudly, in a roster printed at the end of the run, because "did not
# run" and "passed" print identically once that distinction is dropped. Sixteen
# cases are unanswerable in here and every one of them says which file it
# wanted.
#
# THIS LINE WAS NOT A GATE FOR A WHILE. `python -m` runs selftest.py as
# `__main__`, a second module object with its own empty FAILURES list, while
# every checks_*.py appends to the package copy -- so this command printed FAIL
# lines and exited 0, and no failure in any of the twelve checks_*.py modules
# could fail a build. The __main__ block delegates now. Do not "simplify" it
# back to sys.exit(main()). bin/contrast cannot run here, because it checks a RENDERED page against
# the palette and there is no data in the image -- bootstrap runs that one
# inside the container after the render, where the share is mounted.
# Against the SHIPPED EXAMPLE config, which is both a necessity and a bonus:
# render.py calls config.load() at import time, so the suite cannot run without
# one and the image has no config.toml by design -- and pointing it at the
# example means every build also proves the file a new install is told to copy
# actually loads. The first version of this line omitted it and failed the build
# with "no config found", which is the gate working.
RUN POOLHOUND_CONFIG=/app/config/config.example.toml python -m poolhound.selftest

ARG REVISION=unknown
RUN printf '%s\n' "$REVISION" > /app/REVISION && chown poolhound:poolhound /app/REVISION
VOLUME ["/data", "/site"]

USER poolhound
EXPOSE 8787
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/bin/serve"]
