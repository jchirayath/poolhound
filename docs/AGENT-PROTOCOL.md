# Reaching a service that must not be reachable

How poolhound gets instructions to a pool controller that has no password, and
how the next service should do the same thing without inventing it again.

> **Status: design note.** Nothing here is built as a separate library yet. It
> describes what poolhound already does, names it, fills the gaps in it, and says
> what a second service would have to implement. Read
> [deploy/README.md](../deploy/README.md) for the deployment this comes from.

---

## The question

We have a device on a house LAN — a Raspberry Pi speaking RS485 to a Jandy panel
— and a server on the internet. The server has to be able to say *turn the spa
heater on*, and the device has to be able to say *here is what the panel reads*.

The device's neighbour, AqualinkD, **has no authentication of any kind**.
Anything that can reach it can burn gas. So the requirement is not "secure the
inbound path", it is **there is no inbound path**: no port forward, no tunnel, no
proxy, and no route from the server into the house.

Is there a standard protocol for that? And is there a framework we should be
using rather than writing?

## The short answer

**There is no single standard that covers it end to end, and there are three
that cover it in layers — which is what we are already using.**

The pattern has a name in the standards world: it is a *client-initiated
bidirectional HTTP* connection, and the IETF wrote down its issues and best
practices in **[RFC 6202](https://www.rfc-editor.org/rfc/rfc6202)** fifteen years
ago. The transport we use for the server-to-device direction, **Server-Sent
Events**, is part of the [HTML Standard](https://html.spec.whatwg.org/multipage/server-sent-events.html)
and has been for longer than that. The identity layer is **OAuth 2.0** and
**OpenID Connect**, both of which have a profile for exactly the two cases we
have — a machine that cannot see a browser, and a person who can.

So the answer to "should we adopt a framework" is **no, and yes**:

- **No** to adopting a new protocol or a broker. What we run is already a
  conformant use of existing standards, and the alternatives that come as a
  finished product (MQTT with a broker, a managed IoT hub, a reverse-tunnel
  service) each cost us either a third party in the path or a daemon to operate,
  in exchange for features this system does not use.
- **Yes** to writing the profile down. What we lack is not a protocol, it is a
  **specification**: which parts of those standards we use, what the delivery
  guarantees are, how a credential is issued and rotated, and what the edge does
  before the application sees a request. Three of those are currently decisions
  living only in the code.

The rest of this document is that specification.

---

## What already exists

Every one of these is a real answer to "device behind NAT needs instructions".
The verdict column is about *this* system, not the option's quality.

| Option | What it is | Verdict here |
|---|---|---|
| **Server-Sent Events** — [HTML Standard](https://html.spec.whatwg.org/multipage/server-sent-events.html) | One long-lived HTTP response, `text/event-stream`, server writes frames when it has something. Client opens it; reconnection and resumption are specified. | **What we use.** One direction, text, no dependency, and it is already what an HTTP server can do. |
| **HTTP long polling** — [RFC 6202](https://www.rfc-editor.org/rfc/rfc6202) | Client asks, server holds the request open until there is news or a timeout. | Equivalent for our purposes; SSE is the same idea with the reconnect logic standardised instead of hand-written. Worth keeping as the documented fallback for a middlebox that will not pass a stream. |
| **WebSocket** — [RFC 6455](https://www.rfc-editor.org/rfc/rfc6455), [RFC 8441](https://www.rfc-editor.org/rfc/rfc8441) | Full duplex over one client-initiated connection. | Correct, and more than we need. Our upstream direction is a periodic POST of a few hundred bytes, which does not want a frame protocol, a ping/pong state machine or a subprotocol negotiation. Revisit if the device ever needs to stream. |
| **MQTT** — [OASIS](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html) | *The* IoT answer. Device connects out to a broker, subscribes to a command topic, publishes telemetry. QoS 0/1/2, retained messages, last-will. | The strongest alternative, and the one to take if this grows to many devices or many services. Costs a broker to run and secure, and puts a second daemon on the board whose SD card we are trying not to write to. |
| **AMQP 1.0** — [OASIS / ISO 19464](https://www.amqp.org/) | Broker-based messaging with real queue semantics. | Same trade as MQTT, heavier. What Azure IoT Hub speaks underneath. |
| **CoAP + Observe** — [RFC 7252](https://www.rfc-editor.org/rfc/rfc7252), [RFC 7641](https://www.rfc-editor.org/rfc/rfc7641) | REST over UDP for constrained devices, with a subscription verb. | For devices far smaller than a Pi. We are not constrained. |
| **gRPC server streaming** | A typed stream over HTTP/2. | Good if the fleet were polyglot and the schema large. Ours is one Python file with no dependencies, deliberately. |
| **WebSub** — [W3C](https://www.w3.org/TR/websub/), and webhooks generally | Publisher POSTs to a subscriber's URL. | **Backwards for us.** It requires the subscriber to be reachable, which is the precise thing we refuse. Worth knowing as the shape to reject. |
| **Managed IoT hub** (Azure IoT Hub, AWS IoT Core) | Device-to-cloud and cloud-to-device messaging, outbound only, with device identity and rotation built in. | Genuinely solves this, including the credential problem below. Rejected for now: it puts a vendor in the path of "turn the heater on" and bills per message for a system with one device. Reconsider at ten. |
| **Reverse tunnel** (Cloudflare Tunnel, Tailscale, ngrok, Azure Relay) | A daemon dials out and the service becomes reachable through the vendor. | **Rejected on the security property.** These exist to make an internal service reachable. AqualinkD must not become reachable, by us or by anyone holding the vendor's control plane. The agent forwards *validated commands from a catalogue*, not packets. |
| **Job queues** (Celery, RabbitMQ, Redis, Faktory, Beanstalk) | A worker pulls tasks from a broker. | The right model for server-side work, and it is worth noting our agent *is* a worker pulling tasks. Not worth a broker for four command types with a five-minute expiry. |

**The one to remember is MQTT.** If a second and third service arrive with
devices of their own, a broker stops being overhead and starts being the thing
that saves us writing this twice. Until then the profile below is smaller than
the broker's configuration file.

---

## What we are actually doing, named

Three separable pieces. A new service may need one, two or all four.

| Piece | Problem it solves | Direction |
|---|---|---|
| **The tether** | An internal service that must never be reachable still has to receive instructions. | Device dials out and holds the line. |
| **The collector** | A third-party service has data we want and offers no push. | Server dials out on a schedule. |
| **The edge** | People and machines both arrive at the same hostname and must not be authenticated the same way. | Everything arrives here first. |

Everything below uses **tether** as the working name for the first. It is a
placeholder: a tether is a line held from one end, which is the entire mechanism
— the house holds it, and nothing can pull on it from outside.

---

## The tether

### Roles

| Role | Is | In poolhound |
|---|---|---|
| **agent** | The process on the protected side. Opens every connection. Holds no inbound listener. | `poolhound-agent.service` on the Pi |
| **hub** | The reachable side. Holds the queue, serves the stream, accepts events. | the `poolhound` container |
| **edge** | Terminates TLS and classifies the request before the hub sees it. | Caddy |
| **catalogue** | The closed set of commands both sides validate against. | [`commands.py`](../poolhound/commands.py) |

### The two channels

```
agent ──── POST /api/agent/sample ───────────►  hub      upstream, every 15 min
agent ──── GET  /api/agent/commands ─────────►  hub      downstream, held open
      ◄─── text/event-stream, frames + heartbeat ───
agent ──── POST /api/agent/ack ──────────────►  hub      what became of a command
```

Those are poolhound's paths. A generic implementation would name the upstream one
for whatever it carries; the shape is what is being specified, not the spelling.
What is **not** negotiable is the prefix: one path prefix for the whole machine
class, so the edge can match it in a single rule.

Both are opened by the agent. The hub never initiates anything; it leaves a note
and waits to be collected. That single sentence is the security property, and
every other decision here is downstream of it.

### Why a held stream and not polling

The question in the title of this document was *polling*, and it is worth being
precise about why we do not.

A command should arrive in about a second. Polling for that at fifteen-second
intervals costs roughly **140 MB a month** in request overhead on a link that
carries almost no payload. One held connection with a heartbeat costs about
**seven**, and delivers immediately rather than on average half an interval late.
The frequency-versus-cost trade only exists if you poll; holding the line removes
the trade instead of tuning it.

The heartbeat is not decoration. It keeps the NAT mapping alive on the house
router and gives the agent a way to notice a connection that is dead but not
closed — which is the normal failure, not the exception.

### Wire format

Standard `text/event-stream`. One JSON object per frame.

```
event: command
id: 01JB2K7Q8Z0000000000000000
data: {"id":"01JB2K…","action":"set","device":"Spa_Heater","value":1,"exp":1757790000}

: heartbeat
```

| Field | Use |
|---|---|
| `event:` | `command`, or a control frame. Absent means `message`. |
| `id:` | The command id, so the browser-standard `Last-Event-ID` header can resume. **Not sent today — see Gaps.** |
| `data:` | One JSON object, one line. |
| `retry:` | Reconnect hint in ms, sent once on connect. **Not sent today.** |
| `:` comment | The heartbeat. Cheapest legal frame, ignored by every conformant reader. |

### Delivery semantics, stated rather than implied

This is the part that was previously only in the code, and it is the part a
second service will get wrong.

- **At-most-once, deliberately.** A command that does not arrive is not retried
  by the hub. It expires. The pool keeps doing what it was doing and a person
  presses the button again. Retrying an instruction that may already have been
  obeyed is worse than dropping it.
- **Everything expires.** Five minutes. A queue that survived a hub restart could
  only ever hand the agent something already too old to run, which is why the
  queue is in memory and losing it on restart is correct behaviour rather than a
  limitation.
- **The agent is the second validator, not the first.** The hub validates against
  the catalogue, and the agent validates again before the panel sees it. The hub
  sits behind a human identity provider that authenticates people well and
  protects nothing if the host itself is taken; the controller has no password at
  all. So the hub's approval is **necessary, not sufficient**.
- **Idempotency is the agent's job, and it has to survive the process.**
  Executed command ids, per-device cooldowns and a rate limit live on the agent,
  in `/run/poolhound/guard.json` — tmpfs, so still RAM and still no write to the
  SD card. A reconnect can briefly overlap two streams; both may deliver; the
  second delivery is dropped on the id. For months this sentence was true of the
  directory and false of the data: nothing was ever written into it, so the
  guard held all three in process memory, and because every deploy restarts the
  unit, **every deploy cleared the 300 s cooldown on the gas heater and forgot
  which ids had run.** What the file survives is the *process*, not a reboot:
  expiry is five minutes, so every command from before a reboot is expired on
  arrival anyway, and a cooldown is a claim about the last few minutes. Wall
  clock rather than monotonic, because monotonic restarts with the process and
  the process restarting is the event this exists to read across — which also
  means a timestamp in the future is dropped rather than believed, since
  `now - future` is negative and would read as "changed ages ago". Neither
  loading nor saving it may stop the agent: it is the pool's only sampler, and
  trading that for a cooldown is the wrong way round.
- **Confirmation comes from a reading, never from an acknowledgement.** `ack`
  tells the page what the agent *did*. What the panel *is* comes from the next
  sample. A command is "pending" until a reading disagrees with the old state —
  not until a sample merely arrives, and only the newest command per device
  counts.
- **Fan-out is per subscriber.** Each held stream gets its own bounded buffer; a
  stalled reader is dropped rather than allowed to grow.

### Connection management

| Concern | Rule |
|---|---|
| Heartbeat | Hub writes a comment frame on an interval; agent treats silence longer than the timeout as a dead link (poolhound: 120 s). |
| Reconnect | Exponential backoff with jitter, capped. Never a tight loop: the unit's restart policy and the agent's own backoff must not fight each other. |
| Resumption | `Last-Event-ID` on reconnect, hub replays only unexpired commands after that id. |
| Backpressure | Bounded queue per subscriber, oldest subscriber evicted past the cap. |
| Errors | [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem details on the POST paths; `Retry-After` on 429 and 503. |

### Keeping the agent alive is three questions, not one

A held stream makes the agent's liveness the whole system's liveness: nothing
samples the panel and no command can be delivered while it is down. Each of
these was added after an outage the others could not see, and a second service
on this profile needs all three.

| Mechanism | Answers | Why the others miss it |
|---|---|---|
| `Restart=always` | *it died* | Covers a crash and nothing else. |
| A one-shot healer on a 5-minute timer | *it is not running* | systemd deleted the unit's start job to break an ordering cycle, so the process never existed and there was nothing to restart. It read `inactive (dead)`, `Result=success`, `NRestarts=0` for 71 hours while `is-enabled` said "enabled". |
| `WatchdogSec` + a link-staleness test | *it is running and not working* | After a reboot with DNS down the agent sat `active` for eight minutes logging "stream lost; retrying" and reaching nothing. `is-active` asks whether a process exists, and the process was the only thing that was fine. |

**What the watchdog ping means is the whole design.** A ping sent on a timer
proves a thread is scheduled, which is never the question. The agent pings only
while it has had contact with the hub inside `LINK_STALE_S` (600 s) and
withholds it otherwise, so systemd replaces a process whose view of the network
is stuck and a fresh one re-resolves DNS and opens a new socket. *Contact* means
the stream opened or any line arrived on it, **heartbeats included** — an idle
pool is the normal case, and judging liveness on commands alone would call a
quiet night a dead link.

Ten minutes rather than two, because a hub outage is not the agent's fault and
restarting cannot fix it: the threshold sits well past any blip, since the hub
heartbeats inside its own timeout and reconnect backoff tops out at 60 s. During
a long outage this does churn a restart every few minutes, which is accepted
deliberately — and is only affordable because the interlocks above now survive a
restart. Before that, this mechanism would have cleared the gas heater's
cooldown every time it fired.

---

## The collector

The other half of "polling to trigger jobs", and the half where we really do
poll — because WaterGuru and Leslie's offer no push and never will.

Rules that cost us something to learn:

- **A vendor's rate budget is a constraint, not a preference.** One call a day,
  because the upstream project asks for one or two. Scheduling is not the place
  to be generous with somebody else's service.
- **Schedule against the source's clock, not ours.** 14:00 because the pod tests
  at 12:45; a job that runs at 06:00 fetches yesterday's number with today's
  timestamp and nothing reports an error.
- **Re-running must be a no-op.** Both labs return their whole history on every
  pull, so rows are keyed on the measurement's own timestamp and a re-run
  corrects rather than duplicates. One run backfills; a missed day heals itself.
- **Never delete, correct.** Because the source returns everything every time, a
  row deleted from our copy is restored by the next run — a fix that appears to
  work and undoes itself overnight. Corrections are an append-only list applied
  by the loader at read time.
- **Save the raw response before parsing it.** When their markup changes the
  failure should be a parser that found nothing with the offending page on disk,
  not a silent gap in the history.
- **Develop against a saved response.** `--from-file` and `--dry-run` exist so
  that writing a parser does not spend the day's budget.
- **A collector that stops is invisible.** Something has to notice silence. Ours
  is a watchdog on its own schedule that mails when a stream goes quiet.

## The edge, and social login

Every request to the hostname is one of three things, and the classification
happens at the edge **before the application sees it**:

| Class | Who | How | Failure mode if you get it wrong |
|---|---|---|---|
| **public** | anyone | nothing | A reference nobody can open explains the system to nobody. |
| **private** | a person | OIDC at the edge | An unauthenticated write. |
| **agent** | a machine | bearer token checked by the application | oauth2-proxy answers a machine with an HTML sign-in page, which it cannot satisfy and will retry for ever. |

Three rules that are load-bearing:

1. **The machine path must bypass the human gate.** Not as an exception — as a
   separate class with its own credential, matched by path prefix at the edge.
2. **The private list is written out path by path**, never as "everything except
   the public ones". A path the list does not name is still proxied through —
   the last rule is a catch-all — and is refused by poolhound itself, which
   issues no session token to a caller the proxy did not vouch for. Forgetting
   to list one costs a layer rather than opening a door. (This sentence
   previously claimed the proxy fails closed; it does not, and the vhost says
   so in its own comments.)
3. **The edge strips the identity headers it sets, on every request**, so that
   only the edge can set them. Check this against the proxy's *adapted*
   configuration, not its source: directive ordering is not always the order you
   wrote.

### Social login is OIDC, and it belongs at the edge

"Social login" is not a separate thing to build. It is
[OpenID Connect](https://openid.net/specs/openid-connect-core-1_0.html) with a
different issuer — Entra ID today; Google, Apple or GitHub are a configuration
change to the same proxy, not a change to the application. The application
receives an identity header and never sees a token, an assertion or a password.

That is the whole point of putting it at the edge: **a new service inherits
sign-in by sitting behind the same proxy**, and a bug in the application cannot
become an authentication bug.

### Machine identity is the real gap

A static bearer token in a root-owned file is defensible for one device on one
LAN, and it does not survive a fleet. The standards for what comes next already
exist, in the order we would adopt them:

| Need | Standard |
|---|---|
| Enrol a device that has no browser | [RFC 8628](https://www.rfc-editor.org/rfc/rfc8628) device authorization grant |
| Machine-to-machine credential | OAuth 2.0 client credentials ([RFC 6749](https://www.rfc-editor.org/rfc/rfc6749)), bearer use per [RFC 6750](https://www.rfc-editor.org/rfc/rfc6750) |
| Stop a stolen token being replayable | [RFC 8705](https://www.rfc-editor.org/rfc/rfc8705) mTLS binding, or [RFC 9449](https://www.rfc-editor.org/rfc/rfc9449) DPoP |
| Long-lived identity without a shared secret | [RFC 7523](https://www.rfc-editor.org/rfc/rfc7523) JWT assertion; [SPIFFE](https://spiffe.io/) if this ever runs in a cluster |
| Narrow a token for one downstream call | [RFC 8693](https://www.rfc-editor.org/rfc/rfc8693) token exchange |
| A token the hub can validate offline | [RFC 9068](https://www.rfc-editor.org/rfc/rfc9068) JWT access token profile |

The minimum worth doing before a second device exists: **one credential per
agent, an expiry, and a rotation path that does not require touching the board.**
Of those three, the rotation path exists now and the other two do not.

### Rotating the token

**Why this needed code before it could need a runbook.** With one accepted
value there is no correct order. Write the new token to Key Vault first and the
Pi presents the old one into a closed route; write the Pi first and the server
refuses it. Either way the pool's only sampler is off the air for as long as
the person takes, `bin/watch` alarms about it, and the samples inside that
window do not exist anywhere else — the panel is not a store and nothing
re-reads it.

So the server accepts **two**: the current token, and for a stated window the
previous one. The second slot is `poolhound-agent-token-previous` in Key Vault,
or `/etc/poolhound/agent.token.previous` (0600) on a host without one, and its
format is one line:

```
<token> <YYYY-MM-DDTHH:MM:SSZ>
```

The deadline is **mandatory**, which is the half of this that answers "no
expiry". `vault.agent_token_previous()` refuses four things, each of which
would otherwise be a second permanent credential wearing a rotation's clothes:

| Refused | Because |
|---|---|
| a slot with no deadline | that is two live tokens, not a rotation |
| a deadline it cannot parse | "I cannot tell when this expires" must not arrive at the same answer as "this does not expire" |
| a deadline that has passed | this is the expiry |
| a value identical to the current token | copying both ends is a no-op that reads as a finished rotation |

The lookup is cached for 60 seconds, so a slot written by hand takes up to a
minute to take effect.

**The sequence.** Every step is reversible on its own, and no step has an
outage in it.

1. **Open the window.** Put the *current* token into the second slot with a
   deadline a few days out. Nothing changes yet: the server now accepts the
   same value twice, which it refuses — so do this and step 2 together.
2. **Write the new token** as the current one, in Key Vault (or the token
   file). The server now accepts the new token and the outgoing one. The Pi is
   still on the outgoing one and keeps working.
3. **Check the server agrees.** `/api/health` reports
   `agent_token_rotating: "<deadline>"` and `agent_on_previous_token: true`.
   That second field is the point of the whole mechanism: it says the Pi has
   not moved yet.
4. **Update the Pi.** Write the new token to `/etc/poolhound/agent.token`
   (0600, root), then restart the agent — and remember the timer:
   `systemctl restart poolhound-agent poolhound-agent-ensure.timer`.
5. **Confirm it moved.** `agent_on_previous_token` goes back to `false` on the
   next push, and
   `python -c "from poolhound import vault; print(vault.agent_token_fingerprint())"`
   on both ends prints the same twelve characters. Compare fingerprints, never
   tokens.
6. **Close the window.** Delete the second slot. `bootstrap-server.sh --check`
   warns on every run until you do, because the step people skip is this one
   and a rotation nobody finishes looks exactly like one that is done.

**If step 4 goes wrong**, the Pi can be put back on the outgoing token and
keeps working until the deadline. That is what the window is for, and it is why
the deadline should be days rather than hours.

---

## What a new service has to implement

The checklist, if this becomes a shared thing rather than a description.

**Hub**

- [ ] A stream endpoint (poolhound: `GET /api/agent/commands`) — `text/event-stream`, heartbeat, `id:` per frame, honours `Last-Event-ID`
- [ ] An upstream endpoint (poolhound: `POST /api/agent/sample`) — idempotent on the event's own timestamp
- [ ] `POST /api/agent/ack` — outcome, retained long enough to answer "what happened to that?"
- [ ] A queue with an expiry, a per-subscriber buffer and a subscriber cap
- [ ] A catalogue module shared verbatim with the agent
- [ ] `/api/health` reporting live state — whether an agent is connected, what is pending — **fetched by the browser, never baked into a render**

**Agent**

- [ ] Opens both connections outward; no listener, no inbound port
- [ ] Validates every command against the catalogue before acting
- [ ] Idempotency by command id, per-device cooldowns, a rate limit — all in memory
- [ ] Backoff with jitter; heartbeat timeout; resume with `Last-Event-ID`
- [ ] Writes nothing to persistent storage it does not have to
- [ ] Hardened unit: `NoNewPrivileges`, `ProtectSystem=strict`, `RestrictAddressFamilies`, credential from an `EnvironmentFile` rather than `Environment=` (which `systemctl show` will print)

**Edge**

- [ ] Three classes, matched by path, private enumerated explicitly
- [ ] Identity headers deleted before the auth hop, verified against the adapted config
- [ ] The agent path excluded from the human gate
- [ ] The hub's port exposed to the proxy and **never published**

**Collector** (only if pulling from somebody else)

- [ ] A stated call budget and a schedule that respects the source's own clock
- [ ] Dedup on the source's natural key; re-runs are no-ops
- [ ] Raw response written before parsing
- [ ] `--from-file` and `--dry-run`
- [ ] A watchdog that notices silence

---

## Gaps in what we run today

Honest list. None is urgent for one device; all three are why this document
exists before a second one.

| Gap | Consequence | Fix |
|---|---|---|
| **No `id:` on frames, no `Last-Event-ID` on reconnect.** We send `data:` only. | A reconnect cannot resume. In practice unexpired commands are lost across a drop, which is consistent with at-most-once but is a silent behaviour rather than a chosen one. | Emit the command id as `id:`, replay unexpired commands after it on reconnect. |
| **No `retry:` hint.** | Reconnect timing is entirely the agent's, so a hub that wants to shed load cannot ask for a longer interval. | Send `retry:` once on connect. |
| **Static bearer token, and nothing binds it to the device.** Rotation now has a window and a runbook (above), and the outgoing half of a rotation now expires — but the token in steady state still does not, and a stolen one is replayable by anything that can reach the server. | A copy of the token is a copy of the agent's whole authority: it can push fabricated samples and acknowledge commands. | Client credentials with an expiry first; mTLS or DPoP binding when there is more than one agent. |
| **A restart during an outage is normal, not exceptional.** | The watchdog churns a restart every few minutes through a long hub outage. Harmless now that the interlocks persist, but it means `NRestarts` is not a fault count. | Read `NRestarts` against `WatchdogTimestamp` and the hub's own uptime, not on its own. |
| **The profile lives in three files and this document.** | A second service would reimplement it from reading poolhound rather than from a spec. | If a second service appears: extract the catalogue, the queue and the agent loop, keeping the *policy* (expiry, cooldowns, validation) as the caller's. |

## Non-goals

- **Making the internal service reachable.** Not with a tunnel, not with a
  proxy, not "just for a minute". The agent forwards validated commands from a
  closed catalogue; it is not a transport.
- **Guaranteed delivery.** Stated above and chosen, not missing.
- **A broker, until there is a fleet.** The moment there are several devices or
  several services, MQTT stops being overhead. It is not yet.
- **Anything in the browser that decides who may do what.** Client-side hiding is
  not a boundary; the split happens at build and at the edge.

## Open questions

1. **At what fleet size do we switch to MQTT or a hub?** A written trigger beats
   a judgement call made under pressure. Suggested: the second device, or the
   first one that is not on a LAN we control.
2. **Does the tether want a second transport?** Long polling as a documented
   fallback costs little and covers the middlebox that will not pass a stream.
3. **Where does a shared implementation live** — a package this repository
   depends on, or a file copied deliberately? Copying has served the catalogue
   well precisely because both sides must not drift *silently*; a shared package
   makes drift impossible but couples the release of two things that deploy
   separately.

## References

Transport: [RFC 6202](https://www.rfc-editor.org/rfc/rfc6202) ·
[Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html) ·
[RFC 6455](https://www.rfc-editor.org/rfc/rfc6455) ·
[MQTT 5.0](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html) ·
[RFC 7252](https://www.rfc-editor.org/rfc/rfc7252)

Identity: [RFC 6749](https://www.rfc-editor.org/rfc/rfc6749) ·
[RFC 6750](https://www.rfc-editor.org/rfc/rfc6750) ·
[RFC 8628](https://www.rfc-editor.org/rfc/rfc8628) ·
[RFC 7523](https://www.rfc-editor.org/rfc/rfc7523) ·
[RFC 8705](https://www.rfc-editor.org/rfc/rfc8705) ·
[RFC 9449](https://www.rfc-editor.org/rfc/rfc9449) ·
[RFC 8693](https://www.rfc-editor.org/rfc/rfc8693) ·
[RFC 9068](https://www.rfc-editor.org/rfc/rfc9068) ·
[OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html)

HTTP: [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) ·
[RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) ·
[Idempotency-Key header](https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/) (draft)

In this repository: [deploy/README.md](../deploy/README.md) ·
[poolhound/agent.py](../poolhound/agent.py) ·
[poolhound/queue_.py](../poolhound/queue_.py) ·
[poolhound/commands.py](../poolhound/commands.py) ·
the Help tab's *How this page reaches the pool*
