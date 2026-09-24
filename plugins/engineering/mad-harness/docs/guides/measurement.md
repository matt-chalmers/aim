# Measurement

Every default in this harness that touches cost or quality moved because a measurement said
so, and the measurement is written into the release note beside it. This page is the
discipline: what counts as evidence, how to read the verdict, and the ways a number that
looks like a finding is not one.

The rig that produces these numbers is documented with the code it runs:
[`harness/wavelab/`](../../harness/wavelab/README.md). This page is about what you are
allowed to conclude from it.

## Quick start

Measure a lever and read it back:

```bash
harness/wavelab/ab.sh preload --runs 5 --lenses     # 5 runs per arm, judged
harness/wavelab/ab-report.sh preload                # medians, IQRs, the verdict
```

The report answers one question per metric: **did the spreads separate?** Anything else is
noise until more runs say otherwise.

## Why a lab and not the field

A campaign runs different work every time, and the cost analysis that started this measured
**30× token variance on identical tasks**. Against that, a field comparison of two campaigns
tells you almost nothing: the difference between them is dominated by which tasks they drew.

So the field confirms a lever's *direction*, and only the same work repeated can size it.
The rig runs the same seeded epic, N times per arm, in fresh repositories, from a frozen
copy of the plugin, with every dispatch tagged `lever:arm:run`.

That cuts the variance but does not remove it. Which is why the verdict is a spread
comparison rather than a difference of means.

## Reading the report

```
[off]  2 run(s), 20 dispatch(es), 0 budget kill(s), 1 not-ok, code c4d8a1e, cost sdk, tiers plugin
  cost / run        $6.59   (IQR $6.26–$6.92)   — writers AND lenses
  writers / run     $1.56   (IQR $1.29–$1.83)
  by pocket / run    subscription $6.59
  agent minutes/run 25.5   (IQR 24.6–26.3)
  first-pass lenses L1 4/4 pass   L2 1/4 pass   L3 4/4 pass   L4 4/4 pass

delta, on vs off (medians; a delta whose IQRs overlap is noise until more runs say otherwise):
  cost / run        -20% median, better — spreads separate
  turns             +11% median, worse — spreads OVERLAP, not a finding
```

**Medians and interquartile ranges, never means.** One wild run must not become the result:
in a five-run arm of $1.00, $1.10, $0.90, $1.00 and $30.00, the median is $1.00 and the mean
is $6.80. The 30× outlier is a real property of the system, not a data error — so the
statistic has to be one it cannot dominate.

**"Spreads separate" is the only sentence that licenses a change.** If the arms' IQRs
overlap, the report says `not a finding` however large the median gap looks, and the default
does not move.

**An arm that is not one sample says so.** The report refuses to average across a boundary
that makes two runs incomparable, and flags each in the arm's header:

| flag | means |
|---|---|
| `MIXED CODE across runs` | the runs came from different commits — not one sample |
| `MIXED COST SOURCES` | one run's cost is the SDK's estimate and another's is computed from declared rates — see [providers](../concepts/providers.md) |
| `MIXED TIER SOURCES` | some runs used the plugin's tier definitions and others a project's |

## Three traps

### 1. Cheaper by doing less

A lever that makes workers cheaper by skipping doctrine reads as a clean win on cost alone.
Measured: the arm carrying test-doctrine ran mutation testing four times as often and cost
61% more, and a headless epic run without it failed the test-quality lens for decorative
assertions.

So a cost series without a quality counterweight cannot be trusted to mean what it says.
`--lenses` judges every task the wave landed, and the report prints the first-pass rate per
lens beside the money. An arm that got cheap by doing less of the work shows up there.

### 2. Two pockets are not one number

When one arm spends from a subscription allowance and the other from an invoiced account,
their sum is a figure neither pocket paid. The report prints cost per run **per pocket** and
computes the delta per pocket; a pocket only one arm spends from is reported as new spend
rather than a percentage against a zero that was never paid.

> This was got wrong first: a headline of "$5.27 per run, −20%, spreads separate" was a
> percentage between $6.59 of subscription and a sum of two currencies. What actually
> happened was that the subscription pocket fell 22% and $0.15 of invoiced spend appeared —
> two true sentences, where the single number with a confidence claim attached was false.

See [providers](../concepts/providers.md) for what `billing` declares.

### 3. n is small, and the write-up must say so

Two runs per arm is a direction, not a size. State the n, state what the run could not see,
and name any artefact that affected both arms — a defect in the frozen tree hits both, which
makes it fair for the comparison and still wrong for the absolute numbers.

## What may move a default

Stated in [CLAUDE.md](../../CLAUDE.md) and enforced by review rather than by a check:

1. A measurement whose **spreads separate**.
2. In its **own patch release**, so the change is bisectable.
3. With the numbers in [upgrading.md](../upgrading.md), so the next person can see what the
   rule was bought with — and delete it if they re-measure and it no longer holds.

The one exception is a lever that only *removes* something (`lean_catalog` takes unusable
entries out of a worker's tool catalog); there is nothing to trade off, so it ships on.

## Where the lab cannot look

Stated with the rig, because they are properties of the instrument:
[`harness/wavelab/`](../../harness/wavelab/README.md) — no orchestrator, a lighter tool-result
profile than the field, and a cost per series worth knowing before you start one.

When a question falls outside it, the honest answer is a field measurement from real
transcripts (`harness/checks/session-cost.sh`) with its n stated, or no answer at all.

## See also

| | |
|---|---|
| [Cost](../concepts/cost.md) | the lever table: what each one measured, and the default it moved |
| [Providers](../concepts/providers.md) | pockets, cost sources, and what makes two arms comparable |
| [Contributing](contributing.md) | adding a lever, and the rig's arms |
