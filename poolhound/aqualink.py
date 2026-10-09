#!/usr/bin/env python3
"""The shape of one controller sample. Nothing here talks to the panel.

WHAT THIS USED TO BE
  A poller. It ran on a Mac every fifteen minutes, dialled AqualinkD over HTTP
  and appended to a local CSV, and its own first line said so: "Runs on the
  MAC". That stopped being the architecture on 2026-09-12, when the server became
  the system of record — but the code stayed, and so did everything built
  around it: a Refresh button offering a "free local HTTP call" that the server
  answers with an explanation of why it cannot make one, an [aqualink] host in
  Settings that only the dead poller read, and a Collection tab that judged the
  controller on whether a job had run here.

WHY IT WAS NOT MERELY UNUSED
  A workstation that can still collect is a workstation that can write a second
  pool history. The readings live on an Azure Files share mounted only on
  the server; a poll run from a laptop appends somewhere else, and nothing
  reconciles the two. The value of removing it is not tidiness — it is that
  there is now exactly one place a sample can come from.

WHERE SAMPLES COME FROM NOW
  The Pi. `poolhound-agent.service` reads the panel on the house LAN and POSTs
  to the server, because the panel has no authentication of its own and nothing may
  reach inward toward it. The agent finds AqualinkD at POOLHOUND_AQUALINK and
  has never read the config section this module used.

WHAT IS LEFT
  COLS — the column list for samples.csv, which is a SCHEMA and belongs to
  neither end. server.py validates the agent's POST against it, bin/demo-data
  builds fixtures from it, and bin/selftest checks every device's state column
  is in it. One list, in one place, exactly as before.
"""

# pool_heat / spa_heat are the heater's ON-OFF state. The SETPOINT -- the
# temperature it is set TO -- had no column anywhere, so three of the four
# setpoints could be commanded and never read back, and the control cards said
# "setpoint not reported" permanently: set the pool heater to 80 and nothing
# could ever confirm it. The panel does report them; nothing asked.
# (The salt cell is the fourth and was already covered: swg_pct IS its setpoint.)
# spa_temp: the Pi has read Temperature/Spa from the panel on every sample since
# the agent was written, and the server threw it away at the CSV writer -- ingest uses
# extrasaction="ignore", so the POST still answered {"stored": true}. The product
# has a spa heater control with a setpoint, a spa volume calculator, a spa lane
# on "What the pool did" and a spa notification channel, and nowhere at all that
# said how warm the spa was. migrate_columns backfills it on the next write.
COLS = ["ts", "pump", "pump_rpm", "pump_watts", "swg_pct", "salt_ppm",
        "pool_temp", "air_temp", "spa_temp", "spa", "sheer", "pool_light", "spa_light",
        "pool_heat", "spa_heat", "solar_valve",
        "freeze", "pool_set", "spa_set", "freeze_set"]
