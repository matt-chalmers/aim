---
name: design-fidelity
description: How to implement or audit a screen against the Claude Design handover package — the handover source is the spec, never a screenshot; the one command that makes both mandatory steps un-skippable; and the evidence a screen must produce before it counts as done. Declared by fidelity-auditor (which judges) and fullstack-engineer (which builds).
---

# Design fidelity

**The handover source is the spec.** Its JSX, inline styles and token sheets carry
the exact layout, sizes, spacing and placement. Every fidelity miss this repo has
recorded — a dense icon grid, a control placed in the wrong corner, content overflowing
its card —
came from the same cause: *not reading the handover code that already specified it*.

## One command, because two steps must not be skippable independently

```bash
${CLAUDE_PLUGIN_ROOT}/harness/verify/fidelity-check.sh <name> <HandoverComponent> <ourPath> [width]
```

It prints the handover **source** for that component and the sub-components to
read, **and** generates the side-by-side composite at `$SHOTS_DIR/cmp/<name>.png`
— in a single action, so neither half can be quietly dropped. Run it **before**
touching code and **again after** to confirm. `${CLAUDE_PLUGIN_ROOT}/harness/verify/fidelity-noauth.mjs`
is the logged-out variant. The handover lives at `$HANDOVER_DIR`
(required — no default; it names your design handover package).

## The method, per screen

1. **Read the source and copy exact values** — tile size, icon scale, gap, ring
   weight, placement, `visibility:hidden` used to hold width. Read the component's
   **sub-components** too; the command lists them. Do not approximate.
2. **Compare side by side at 390px and 1280px.** Never from memory.
3. **Every data state**, not a flattering populated mock: the default landing state and
   every other state the screen can be in, including empty and error — with the **longest
   real names** your data allows.
4. **Zoom the smallest repeated unit at native resolution** — one cell, one card,
   one control. Full-screen shots downscale and hide icon fill, border weight,
   padding and alignment.
5. **Defect-hunt.** Assume there is one: content clipping, wrong sizes,
   thin-vs-bold borders, sparse-vs-dense, wrong placement, width jitter.

A screen is **not verified** until you have done an explicit defect pass at 100%
per component and can name what you checked. "Same sections present" is not a
pass, and no fidelity task closes on a glance.

## Evidence, or it isn't done

Paste **both** in your response: (a) the handover **source excerpt** you
replicated, and (b) the **re-run composite** showing ours matching. No source
excerpt and no confirming composite means not done — do not commit, do not close
the task. This is the forcing function; the owner gates on exactly this evidence.

## When you think the handover is wrong

**Stop and say so — do not silently decide.** If you believe you have a better
idea than Claude Design, or the design appears to have forgotten or contradicted
something (a spec, an ADR, an accessibility or privacy rule), call it out for the
owner to review during handover. Do not quietly implement your own version, and do
not quietly build a design you believe is wrong. Claude Design may be wrong; you
may be wrong; the owner decides.

## What this does not cover

Which tokens and components to use is your project's design-system doc and its
`ui-patterns.md`. Where each screen is specified is the feature folder's `## UI`
section, indexed by your project's screen catalogue. Find both through `paths.docs`.
