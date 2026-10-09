# The chemistry, and why this is fittable

The claim behind this project is that pool chemistry is not mysterious — it is a
pair of near-linear differential equations with measurable inputs. What makes it
*feel* mysterious is that the dominant terms are usually unmeasured: nobody
records how long the pump ran, how long the water feature aerated, or how much
sun fell on the water.

We can measure all three.

---

## Free chlorine

```
ΔFC/day  =  production  −  UV loss  −  oxidant demand
```

### Production

A salt cell electrolyses chloride to hypochlorite continuously **while water is
flowing through it**. Output is close to linear in both dial setting and runtime:

```
production  ≈  k · (SWG% / 100) · pump_hours
```

`k` is a property of this specific cell — its rated output, its age, and the salt
concentration it is working with. Cell efficiency falls as salt drops, which is
why `salt_ppm` is sampled rather than assumed. **`k` is what the fit recovers**,
and once known, the dial becomes a calculated setting rather than a guess.

**Spa mode raises the cell percentage**, so spa sessions produce chlorine faster.
This needs no separate term: the sampler records the *live* dial every 15 minutes
rather than assuming a configured value, so the boost already appears in
`SWG% · pump_hours` when that product is integrated over the samples. It would
only need special handling if the dial were assumed constant — which is exactly
why it is not.

This is why the pump matters twice over. It is not merely circulation: with the
pump off, chlorine production is exactly zero.

### UV loss — the dominant term, and the one CYA governs

Sunlight photolyses hypochlorite. Unprotected, an outdoor pool can lose **half
its free chlorine in a few hours** of strong sun. Cyanuric acid changes this
completely: chlorine binds reversibly to cyanurate, and the bound fraction is
shielded from UV while remaining available as the free fraction is consumed.

```
UV loss  ≈  k · sun_exposure · g(CYA)      g decreasing, strongly so at low CYA
```

Two consequences that matter for this pool:

**CYA is the largest confounder in the whole model.** Fit FC loss without it and
the sun coefficient absorbs CYA's effect, so the model silently mispredicts
whenever stabiliser drifts. It is measured only every few weeks, so it enters as
a slow-moving input — held constant between lab readings, never interpolated as
if it were observed daily.

**Measured CYA here is 38 against a target of 65.** Lower CYA means less UV
shielding, so chlorine burns off faster and the sun term should come out large.
It also means FC and sun will correlate strongly, which helps identification.

### Oxidant demand

Bathers, leaves, pollen, rain-borne organics. Largely unmeasured and mostly
noise — but it is the residual, so a persistent unexplained loss is itself a
signal worth reading.

### The ratio that actually matters

Sanitising power comes from **hypochlorous acid**, not total FC. With CYA
present, HOCl tracks roughly the **FC/CYA ratio** rather than FC alone. The
usual working figure for salt pools is around 7.5% of CYA.

```
CYA 38  ->  ~2.9 ppm FC is the equivalent target
measured FC 6.2, with a stated target of 3.0
```

So FC is running high relative to the stabiliser present. That is a hypothesis
for the data to confirm, not an instruction — but if it holds, the SWG dial is
doing more work than it needs to.

---

## pH

```
ΔpH/day  =  salt-cell rise  +  aeration rise  −  acid  ± TA buffering
```

### Why a salt pool climbs

Electrolysis produces hydroxide at the cathode, and the hydrogen it liberates
off-gasses instead of recombining. The net effect is a persistent upward drift
tied to cell runtime — **the same variable that produces chlorine**. Production
and pH rise are therefore coupled, which is exactly the trade the model should
quantify.

### Aeration — two sources, not one

Pool water is supersaturated with CO₂ relative to air. Anything that agitates the
surface drives CO₂ out, carbonic acid falls, and **pH rises with no change in
total alkalinity**.

There are two aeration sources here and they behave very differently:

**The spa spillover — the baseline.** It runs the entire time the pool is
running. So baseline aeration is not a separate variable at all: it tracks
`pump_hours`. This is almost certainly the larger of the two terms simply because
it runs constantly.

**The sheer descent — the increment.** Switched on `Aux_2`, recorded every 15
minutes, adding aeration on top of the spillover whenever it runs.

```
aeration  ≈  k_spill · pump_hours  +  k_sheer · sheer_hours
```

### The collinearity this creates, and how to break it

Because the spillover runs with the pump, **pump hours drive pH through two
separate channels**: cell electrolysis and spillover aeration. Both scale with
the same variable, so from pump runtime alone they are perfectly collinear and
cannot be separated. A naive fit would attribute all of it to whichever term
happens to be in the model.

The way out is that the **cell** term also scales with the dial, while the
**spillover** term does not:

```
ΔpH/day  =  k_cell  · (SWG% · pump_hours)     electrolysis — scales with dial
         +  k_spill · (pump_hours)            spillover — dial-independent
         +  k_sheer · (sheer_hours)           water feature
         −  k_acid  · (acid, strength-adjusted)
```

**So the two become identifiable precisely when SWG% varies independently of
pump runtime.** If the dial is left at one setting for months, they stay
confounded no matter how much data accumulates.

That is worth knowing in advance, because it is cheap to arrange and impossible
to fix retrospectively: deliberately running a few weeks at a different SWG%
with similar pump hours would separate them cleanly. Otherwise `k_cell` and
`k_spill` can only ever be reported as a combined per-pump-hour figure.

**The spa is a third aeration source**, and a strong one — jets and the return
into a small volume agitate far harder than the spillover. It also diverts
circulation, changing how the spillover behaves. So spa hours earn their own
term:

```
ΔpH/day  =  k_cell  · (SWG% · pump_hours)
         +  k_spill · (pump_hours)
         +  k_sheer · (sheer_hours)
         +  k_spa   · (spa_hours)
         −  k_acid  · (acid, strength-adjusted)
```

### Spa sessions are the natural experiment

The collinearity above needs SWG% to vary independently of pump hours to
resolve — and **spa mode does exactly that**, because the cell percentage is
higher in spa mode. Every spa session is a period where the dial moves while the
pump keeps running.

So the identification problem may solve itself through ordinary use, provided spa
sessions are frequent and varied enough. If after a couple of months `k_cell` and
`k_spill` still will not separate, the fix is the deliberate one: a few weeks at a
different dial setting with similar pump hours.

### Heaters — real, but acting through temperature

Both heaters affect both measures, and by the same route: **temperature**.

**On chlorine.** Reaction rates rise with temperature, so warmer water consumes
chlorine faster. A heated pool has a higher standing demand than a cold one at
identical sun and CYA.

**On pH.** CO₂ is less soluble in warm water, so heating drives it out of
solution — the same mechanism as aeration, arriving by a different door. Carbonic
acid falls and pH rises. Gas combustion products never touch the water, so there
is no direct chemical addition; the effect is entirely thermal.

**Water temperature is the mediator, and we measure it directly**, which is
better than using heater runtime as a proxy — temperature also captures solar
gain, and on a sunny day the sun heats this pool more than the heater does.

That said, heater hours may carry information temperature alone does not:
*actively heating* drives harder off-gassing than sitting at a steady warm
temperature, because the transient matters, not just the level. Both
`pool_heat` and `spa_heat` are sampled alongside `pool_temp`, so the fit can
report whether heater runtime adds anything beyond temperature — rather than
either of us having to assume.

### Acid, and what it costs

**The first measured dose, 12 September 2026.** 0.25 gal of 31.45% muriatic into
a nominal 15,000 gal at TA 73, read 2.6 hours later:

```
predicted   -0.26 pH   (alkalinity-scaled estimate)
measured    -0.40 pH   against the previous reading
            -0.30 pH   against a four-reading baseline
ratio        1.4x to 1.9x stronger than predicted
```

That single point is not a fit, and it is contaminated by two known problems: the
pump ran only ~4 hours that day, so a full turnover may not have completed, and
the pod sits in the skimmer where it reads whatever is nearest it. But it is the
first evidence the estimates have ever been checked against, and it points one
way.

Three explanations produce the same signature, and only more doses separate them:

1. **The pH-per-alkalinity step was wrong** — now partly corrected, see below.
2. **The pool holds less water than 15,000 gal.** Every dose would be stronger in
   proportion. This is the coefficient that measures the volume, and it is worth
   remembering that the configured figure is an estimate.
3. **Alkalinity has drifted below the 73 ppm last measured**, which does the same
   thing for the same reason.

### pH response depends on alkalinity, and is not a constant

The old model used a flat "0.1 pH per 4 ppm of TA". That figure is quoted for
pools around TA 90, and a carbonate system resists pH change in proportion to how
much bicarbonate it holds — so the same acid moves pH further in a
low-alkalinity pool.

```
ΔpH  =  ΔTA / 4 × 0.1 × (90 / TA)
```

At this pool's TA 73 that changes the prediction for a 0.25 gal dose from −0.21
to −0.26, closing most of the gap to what was measured **without fitting anything
to a single observation**. The scaling is physics; only the anchor is a
convention.


Muriatic acid neutralises carbonate alkalinity, lowering both pH and TA. Strength
is not a detail: this pool's acid is **31.45%**, against a common alternative of
14.5% — more than a factor of two. That is why `chem` refuses to log a volume
without one.

A useful prior, to be replaced by the fitted value:

```
25.6 fl oz of 31.45% muriatic  ->  -10 ppm TA per 10,000 gal
this pool, ~15,000 gal at TA 73  ->  order of 15 fl oz per 0.1 pH
```

The alkalinity relation is solid; the pH-per-alkalinity step is the soft part,
because it depends on where on the buffer curve the pool is sitting. Treat the
second line as a starting estimate. Recovering the real figure for *this* pool is
the point — and it is the one coefficient that also measures the pool volume,
which is currently a guess.

These constants live in `chemicals.py`, once, and are exported to the browser so
the dose form previews the same numbers rather than reimplementing them.

### The CO₂ equilibrium ceiling, and why alkalinity sets it

Aeration cannot raise pH forever. Water exchanges CO₂ with the air until the two
balance, and at that point there is nothing left to drive off — the spillover
keeps running and pH stops moving.

From Henry's law and the first dissociation of carbonic acid, with bicarbonate
standing in for alkalinity (fair between pH 7 and 8.5):

```
pH_ceiling = pK₁ + log₁₀( [HCO₃⁻] / [CO₂*] )
```

```
TA  60 ppm  ->  8.30        TA  90 ppm  ->  8.47
TA  73 ppm  ->  8.38        TA 100 ppm  ->  8.52
TA  80 ppm  ->  8.42        TA 120 ppm  ->  8.60
```

**The ceiling is a function of alkalinity.** This is a second and far more
concrete argument for the alkalinity a salt pool wants than "it buffers less":
it says exactly where pH will stop. Lowering TA from 73 to 58 moves the ceiling
from 8.4 to 8.3.

It also explains a plateau rather than a climb. This pool read pH 8.2 on 1 August
and 8.2 again on 8 August at TA 73 — sitting just under its ceiling, which is
what running out of CO₂ to expel looks like.

**Above the ceiling, only the cell is still pushing.** That matters for
attribution: below it, aeration and the cell are both at work and the fitted
coefficients have to separate them; near it, aeration has largely stopped
contributing and what remains is electrolysis.

### Turning the cell down slows the rise; it does not reverse it

The cell raises pH because the hydrogen made at the cathode leaves as gas, so the
hydroxide that came with it is never neutralised, and because those bubbles
aerate. Both scale with output × pump hours, so halving the dial halves the push.

But removing a riser is not the same as adding an acid. On its own, a smaller
cell lets pH level off; something acidic — rain, fresh fill water, or acid poured
in — is still what brings a number down.

**A caution on attribution.** High free chlorine biases a phenol-red pH reading
*upward*: the dye is bleached and the colour over-reads. So when chlorine comes
down, part of any apparent pH drop can be the measurement correcting rather than
the water changing. Distinguishing them needs a reading taken once free chlorine
is back in range, or a lab cross-check.

The record already contains a warning against the easy conclusion. The lab has
pH 8.2 on 8 August and 7.9 on 21 August — a genuine fall with no acid logged —
while free chlorine **rose** from 2.2 to 4.9 ppm over the same stretch, which is
the opposite of what cutting cell output would do. Whatever moved pH then, it was
not a smaller cell.

### TA is the buffer, and it cuts both ways

High TA resists pH movement — but it also means more dissolved CO₂ available to
off-gas, so aeration drives pH up faster. Lowering TA makes pH easier to hold but
more volatile.

**A salt pool wants a lower TA than a chlorine pool** — roughly 60–90 rather than
80–120 — precisely because the cell is a permanent upward push and TA is the
reservoir feeding the rebound. Judging a salt pool against the chlorine-pool band
calls a correctly-run pool "low" and invites bicarbonate, which is the opposite
of what it needs. `targets()` therefore reads the sanitiser type rather than
carrying one band for every pool.

**No measured value is quoted here on purpose.** An earlier version of this
section said "measured TA here is 95, target 80", and both halves went wrong: the
next lab result read 73, and 80 was the chlorine-pool aim. Prose that states a
number is prose that goes stale silently. Current values belong on the dashboard,
which derives them; this document is for the relationships.

---

## After a change, the pool approaches a new equilibrium

Chlorine does not step to a new level when the cell output changes. Production is
roughly constant for a given output and runtime, while loss is proportional to
how much chlorine is present — UV destroys a fraction, not a fixed amount. So:

```
d(FC)/dt  =  P  −  k·FC          settles at  FC_eq = P/k
```

An exponential approach, not a step. Fitting P and k from consecutive daily
readings turns "it is still falling" into a number and a date.

**Why this matters operationally.** The natural move four days after an
adjustment is to make another one, because the number is still not where it
should be. That stacks a second change on an unconverged first: neither can be
attributed afterwards, and the usual outcome is overshoot in the other direction
followed by a correction, which is how people end up permanently chasing a pool.

**Runtime is the larger lever and it must be held still.** Production scales with
output × pump hours, so a fit whose window spans a changing schedule is
projecting from an average that no longer applies. Worked example from this pool:
after cutting cell output, the decay fit gave k = 0.23/day and P = 0.90 ppm/day,
implying equilibrium 3.9 ppm — but pump hours fell from 10.8 to 4.5 across the
same window. At the newer runtime, production is 0.65× and equilibrium is 2.6 ppm
instead. Same fit, same cell setting, a full 1.3 ppm apart on runtime alone.

## What the fit should recover

| Coefficient | Meaning | Use |
|---|---|---|
| `k_prod` | ppm FC per SWG-% per pump-hour | set the dial from a target |
| `k_uv` | FC lost per unit sun, at this CYA | predict summer vs winter demand |
| `k_cya` | how much protection stabiliser buys | justify raising CYA to target |
| `k_spill` | pH rise per pump-hour from spillover | the standing cost of circulating |
| `k_sheer` | pH rise per sheer-descent hour | price the water feature |
| `k_acid` | pH drop per fl oz at 31.45% | dose by calculation |
| `k_cell` | pH rise per pump-hour of cell runtime | the production/pH trade |
| `k_temp` | FC demand and pH rise per degree | what heating costs in chemistry |

---

## Honest limits

**One FC and pH reading per day.** One observation per day, so resolution is
bounded by the sensor, not the sampling.

**Sun and temperature move together.** Both rise in summer and their coefficients
are hard to separate until the data spans varied weather — cloudy warm days and
clear cool ones are the informative ones. Expect **two to three months** before
coefficients stabilise.

**Lab values are sparse.** CYA, TA and salt update every few weeks. Between
readings they are assumed constant, which is wrong in detail — CYA is diluted by
rain and backwashing, TA drifts with acid.

**Bather load is unmeasured** and lands in the residual.

**The cell and spillover pH terms are collinear** unless SWG% is varied
independently of pump hours. Left at a fixed dial, they can only be reported
together as a combined per-pump-hour figure — see the aeration section.

**Dosing is not instantaneous.** Acid needs a full turnover to mix; a reading
taken too soon after dosing measures a gradient, not the pool. Log the time,
which `chem` does, so late-day additions can be attributed to the following day.
