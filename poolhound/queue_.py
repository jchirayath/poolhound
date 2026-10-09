#!/usr/bin/env python3
"""The command queue that lives on the server.

WHY IT IS IN MEMORY
  Commands expire after five minutes. A queue that survived a restart would only
  ever hand the agent something already too old to run, so persisting it buys
  nothing and costs a disk write on the path that must stay fast. Losing the
  queue on restart is the correct behaviour, not a limitation: the pool keeps
  doing whatever it was doing, and a person who still wants the change presses
  the button again.

WHY EACH AGENT GETS ITS OWN FAN-OUT
  One agent is expected, but a reconnect briefly overlaps two — the old
  connection has not yet noticed it is dead while the new one is already up.
  Handing each subscriber its own buffer means a command issued during that
  overlap reaches whichever survives, and the agent's idempotency check discards
  it if both happen to deliver.

WHAT IT REMEMBERS
  Acknowledgements, briefly, so the page can say what became of a command rather
  than only that it was sent. "Refused: pool heater has a 300s cooldown" is the
  answer to the question somebody is about to ask, and it comes from the agent
  rather than from anything the server assumed.
"""
import collections, datetime as dt, queue, threading, time

from . import commands as C

MAX_SUBSCRIBERS = 4
ACK_RETENTION = 900          # long enough to answer "what happened to that?"
HISTORY = 200                # the audit log kept in memory for the page

class CommandQueue:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs = []
        self._acks = collections.OrderedDict()
        self._log = collections.deque(maxlen=HISTORY)
        self._recent = []              # command timestamps, for the rate limit
        self._evicted = 0              # subscribers dropped at the cap

    # ----------------------------------------------------------- subscribers
    # Handed to an evicted subscriber so its stream loop can end. Anything the
    # loop can distinguish from a command would do; a sentinel object cannot be
    # confused with one that happens to have the same shape.
    CLOSED = object()

    def subscribe(self):
        q = queue.Queue(maxsize=32)
        with self._lock:
            # A stale subscriber that nobody is draining must not accumulate
            # forever; the oldest goes when the limit is reached.
            #
            # EVICTION USED TO BE SILENT, AND THAT IS TWO PROBLEMS. The queue
            # was dropped from the list and nothing else happened: the evicted
            # agent's connection stayed open, its stream loop went on blocking
            # on a queue no longer registered, and it sat there believing it was
            # connected and receiving commands FOREVER. Meanwhile
            # agents_connected still read 4, so the page said four agents were
            # listening when one of them had been cut off — and a command issued
            # to that pool went to four queues, none of them the one the Pi was
            # holding.
            #
            # Waking the evicted queue lets its loop close the stream, which is
            # what makes the agent reconnect. And the eviction is announced: a
            # cap being hit means either several agents are genuinely connected
            # or connections are leaking, and both are things somebody needs to
            # be able to find out.
            while len(self._subs) >= MAX_SUBSCRIBERS:
                victim = self._subs.pop(0)
                self._evicted += 1
                try:
                    victim.put_nowait(self.CLOSED)
                except queue.Full:
                    # Nobody is draining it, which is what made it the oldest.
                    # The loop will notice on its next heartbeat write.
                    pass
                print(f"  queue: evicting the oldest agent subscriber "
                      f"({MAX_SUBSCRIBERS} is the cap, {self._evicted} evicted "
                      f"since start) — it will reconnect", flush=True)
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)
            # WHAT THE AGENT IS RUNNING IS ONLY TRUE WHILE ONE IS CONNECTED.
            # agent_revision was set on the handshake and never cleared, so
            # after the last agent disconnected /api/health went on reporting
            # the revision of an agent that was no longer there -- and
            # update-pi.sh --check compares exactly that field against the
            # commit it shipped. A Pi that had dropped off entirely would be
            # reported as running the right code.
            if not self._subs:
                self.agent_revision = None

    # What the connected agent says it is running. Set on the SSE handshake,
    # read by /api/health so deploy/update-pi.sh --check can compare it to the
    # commit that was shipped -- the Pi's equivalent of the REVISION stamp
    # the server's image already carries.
    agent_revision = None

    @property
    def agents_connected(self):
        with self._lock:
            return len(self._subs)

    # --------------------------------------------------------------- issuing
    def issue(self, action, device=None, value=None, by=None):
        """Validate here as well as at the agent.

        Not because the agent's check can be skipped — it cannot — but because
        refusing a bad command at the point somebody pressed the button gives
        them an answer immediately, instead of a round trip and a silence.
        """
        cmd = C.new_command(action, device, value, by)
        cmd = C.validate(cmd)          # raises Refused, which the caller shows
        delivered = 0
        with self._lock:
            # The agent enforces the same ceiling, and that is the check that
            # actually protects the panel. This one protects the link: without
            # it a stuck button or a hijacked session can fan unbounded traffic
            # down the SSE stream to the house, where every command is dropped
            # but every command still had to be carried. The internet-facing
            # side should not rely on the far end to stop a flood.
            now = time.time()
            self._recent = [t for t in self._recent if now - t < C.RATE_WINDOW_S]
            if len(self._recent) >= C.RATE_LIMIT:
                raise C.Refused(
                    f"more than {C.RATE_LIMIT} commands in {C.RATE_WINDOW_S} seconds. "
                    f"Something is pressing buttons faster than a person would, so "
                    f"the rest are being held back.")
            self._recent.append(now)

            for q in list(self._subs):
                try:
                    q.put_nowait(cmd)
                    delivered += 1
                except queue.Full:
                    pass
            self._log.appendleft({"at": dt.datetime.now().astimezone().isoformat(
                                      timespec="seconds"),
                                  "cmd": cmd, "what": C.describe(cmd),
                                  "by": cmd.get("by"), "delivered": delivered,
                                  "result": None})
        return cmd, delivered

    # ------------------------------------------------------------------ acks
    def knows(self, cmd_id):
        """Did THIS process issue that command?

        An ack for an id we never issued is not refused — the server may have
        restarted since, and the agent is right to report what it did — but it
        must not reach the permanent record as a command, because nothing can
        ever pair it with one. audit.csv outlives the restart that emptied this
        log, so a row written there is forever.
        """
        with self._lock:
            # The log stores the whole command under "cmd" — the same shape
            # ack() walks, so the two agree about what "this id" means.
            return any((e.get("cmd") or {}).get("id") == cmd_id
                       for e in self._log)

    def ack(self, cmd_id, result):
        with self._lock:
            self._acks[cmd_id] = {"at": time.time(), "result": result}
            while len(self._acks) > 400:
                self._acks.popitem(last=False)
            for entry in self._log:
                if entry["cmd"]["id"] == cmd_id:
                    entry["result"] = result
                    break

    def result_for(self, cmd_id, wait=0.0):
        """Wait briefly for the agent to report back.

        The round trip is normally well under a second, so a short wait turns
        "sent" into "done" or "refused, and here is why" while the person is
        still looking at the button. Past that it returns without an answer
        rather than holding the request open.
        """
        deadline = time.time() + wait
        while True:
            with self._lock:
                got = self._acks.get(cmd_id)
            if got or time.time() >= deadline:
                return got["result"] if got else None
            time.sleep(0.05)

    # ----------------------------------------------------------------- audit
    def history(self, limit=40):
        with self._lock:
            return list(self._log)[:limit]

    def purge_acks(self):
        cutoff = time.time() - ACK_RETENTION
        with self._lock:
            for k in [k for k, v in self._acks.items() if v["at"] < cutoff]:
                del self._acks[k]

QUEUE = CommandQueue()
