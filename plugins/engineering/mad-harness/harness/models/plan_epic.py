"""Design and plan one epic — `campaign-loop` §3 as a sequencer around its five judgements.

WHAT THE ORCHESTRATOR DID BY HAND, twenty to thirty tool calls per epic: `spec-index-status`
→ a full or DELTA survey (or reuse) → parse `ADEQUACY:` and branch → on ABSENT a
spec-editor draft and an analyst audit of it → the `ADEQUACY:` note, retyped from the
survey's file → `check-decision-register` and its Open-table parse → fold-in ① via
spec-editor → the architect → its verdict (a second copy of the same table, which drifted)
→ `AUTO-ACCEPTED` or the interactive stop → the design staged by hand into a folder the
close-out gate later refuses if the slug is wrong → the ADR number by `ls` + max + 1 → the
planner → the analyst audit → on FAIL a fresh planner with the findings, a counter of
attempts held in the orchestrator's head, a second FAIL parking the epic → the `AUDIT:`
note → the interactive stop → `apply-plan.sh`. Every hop was: write a prompt file naming
the previous artefact's path, dispatch, read a verdict line, branch. The judgement is
entirely inside the five agents; between them the orchestrator was routing, at its
context price, and the skipped `ADEQUACY:`/`AUDIT:` notes are exactly what signal ①d
exists to count.

THE SHAPE. Each stage is a function that runs, records what it produced in a state file
(`.harness/run/plan-epic-<epic>.json`), and returns a verdict the sequencer branches on.
The file is what makes `--from <stage>` a resume rather than a restart, and what the two
interactive stops hand back: `MODE=interactive` exits 6 at the design gate and at the DAG
gate with the artefact's path, and the owner re-runs `--from planner` / `--from apply`
after approving (or edits the artefact and re-runs the same stage). `MODE=auto`
self-approves — recording `AUTO-ACCEPTED` — and parks on the two things auto-accept
never covers: an ABSENT specification (invented scope) and an open `decision`.

DISPATCHES ARE FRESH, NEVER RESUMED. A planner revision after an audit FAIL is a new
dispatch carrying the audit's file path — measured, resuming a lens rebuilt its whole
prior conversation at the cache-write rate ($1.95 for two round-trips against $0.16).

Exit 0 planned and applied · 1 a stage failed (the line says which) · 2 could not judge
(a dispatch that hung, was denied, or returned no verdict) · 4 parked (the epic is out of
the queue; the report says on what) · 6 interactive stop (approval owed; the report says
the artefact and the resume command).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path

from . import verdict as verdict_mod
from .resolve import HARNESS, REPO
from .steps import FAIL, INFO, OK, Raw, Result, execute, failure_detail, render, tail

DISPATCH = HARNESS / "models" / "dispatch.sh"
RENDER = HARNESS / "tracker" / "render-epic.sh"
TK = HARNESS / "tracker" / "tk.sh"
CHECKS = HARNESS / "checks"
SWARM = HARNESS / "swarm"

STAGES = ("survey", "foldin", "architect", "stage", "planner", "audit", "gate", "apply")
EXIT_OK, EXIT_FAILED, EXIT_NO_JUDGE, EXIT_PARKED, EXIT_STOP = 0, 1, 2, 4, 6
DISPATCH_TIMEOUT = 3600

# MARKDOWN AROUND THE WORD IS NOT A DIFFERENT VERDICT. `ADEQUACY: **ADEQUATE**` cost a
# survey re-dispatch (measured, an orchestrated wavelab run: three attempts, one of them
# this) before the parser tolerated emphasis, as `verdict.VERDICT` already did.
_EM = r"[*_`]*"
ADEQUACY = re.compile(rf"^\s*{_EM}ADEQUACY{_EM}:?{_EM}\s*{_EM}(?P<v>ADEQUATE|INFERABLE|ABSENT)(?![A-Za-z])", re.I | re.M)
DECISION_LINE = re.compile(rf"^\s*{_EM}DECISION{_EM}:{_EM}\s*(?P<q>.+?)\s*$", re.M)
REQUIREMENT_LINE = re.compile(rf"^\s*{_EM}REQUIREMENT{_EM}:{_EM}\s*(?P<q>.+?)\s*$", re.M)
SPEC_INDEX = re.compile(r"^\s*SPEC INDEX:\s*(?P<body>.*?)(?=^\s*[A-Z][A-Z /]+:\s|\Z)", re.M | re.S)
OPEN_ROWS = re.compile(r"epic cannot fold in or close")


class State:
    def __init__(self, epic: str, path: Path):
        self.epic, self.path = epic, path
        self.data: dict = {"epic": epic, "done": [], "artefacts": {}, "audit_attempts": 0, "mode": None, "notes": []}
        try:
            self.data.update(json.loads(path.read_text()))
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")

    def done(self, stage: str, **artefacts) -> None:
        if stage not in self.data["done"]:
            self.data["done"].append(stage)
        self.data["artefacts"].update({k: str(v) for k, v in artefacts.items()})
        self.save()

    def art(self, key: str) -> str | None:
        return self.data["artefacts"].get(key)


def _read(path: str | None) -> str:
    try:
        return Path(path).read_text() if path else ""
    except OSError:
        return ""


def _note(epic: str, text: str, runner, cwd: str) -> Result:
    raw = execute([str(TK), "update", epic, "--append-notes", text], cwd=cwd, runner=runner)
    ok = raw.ran and raw.returncode == 0
    return Result(f"tk.sh update {epic} --append-notes", OK if ok else FAIL, text.splitlines()[0][:120] if ok else f"NOT written — {failure_detail(raw)}", raw)


def _dispatch(agent: str, prompt: str, epic: str, out: Path, runner, cwd: str, dispatch_fn=None, tier: str | None = None) -> tuple[Raw, str]:
    """One fresh dispatch; the whole result at `out`. Returns (raw, result text).
    `tier` overrides the agent's declared tier (`dispatch.sh --tier`) — the lever below."""
    pfile = out.with_suffix(".prompt.md")
    pfile.parent.mkdir(parents=True, exist_ok=True)
    pfile.write_text(prompt)
    if dispatch_fn:
        try:
            raw = dispatch_fn(agent, pfile, out, tier=tier)
        except TypeError:
            raw = dispatch_fn(agent, pfile, out)
    else:
        argv = [str(DISPATCH), agent, "--prompt-file", str(pfile), "--task", epic, "--out", str(out), "--digest", "0"]
        if tier:
            argv += ["--tier", tier]
        raw = execute(argv, cwd=cwd, timeout=DISPATCH_TIMEOUT, runner=runner)
    return raw, _read(str(out))


def _park(epic: str, reason: str, runner, cwd: str) -> Result:
    raw = execute([str(TK), "park", epic, "--reason", reason], cwd=cwd, runner=runner)
    ok = raw.ran and raw.returncode == 0
    return Result(f"tk.sh park {epic}", OK if ok else FAIL, reason if ok else f"NOT parked — {failure_detail(raw)}\nby hand: {TK} park {epic} --reason \"{reason}\"", raw)


def _create_decisions(text: str, epic: str, runner, cwd: str, *, prefix: str = "") -> list[tuple[str, str]]:
    """Every `DECISION:` line → a `decision` task. Returns (id, question) pairs."""
    made = []
    for m in DECISION_LINE.finditer(text):
        q = m.group("q")
        raw = execute([str(TK), "create", f"{prefix}{q}"[:200], "-t", "decision", "-p", "1", "--description", f"Raised while planning {epic}.\n\n{q}"], cwd=cwd, runner=runner)
        tid = (raw.stdout.strip().splitlines() or ["?"])[-1].strip() if raw.ran and raw.returncode == 0 else "?"
        made.append((tid, q))
    return made


class Sequencer:
    def __init__(self, epic: str, *, mode: str, project, runner=None, cwd: str | None = None, dispatch_fn=None, store=None, push: bool = True):
        self.epic, self.mode, self.project = epic, mode, project
        self.runner, self.cwd, self.dispatch_fn = runner, cwd or str(REPO), dispatch_fn
        self.push = push
        self.results: list[Result] = []
        self.state = State(epic, Path(self.cwd) / ".harness" / "run" / f"plan-epic-{epic}.json")
        self.state.data["mode"] = mode
        self.out_dir = Path(self.cwd) / ".harness" / "run" / "plan" / epic
        self._store = store
        self.epic_task = None
        self.stop: tuple[int, str] | None = None

    # --- helpers ---------------------------------------------------------------------------

    def store(self):
        if self._store is None:
            import tracker

            self._store = tracker.task_store()
        return self._store

    def title(self) -> str:
        if self.epic_task is None:
            try:
                self.epic_task = self.store().show(self.epic)
            except Exception:  # noqa: BLE001
                self.epic_task = None
        return (self.epic_task.title if self.epic_task else "") or ""

    # --- proportionality -------------------------------------------------------------------
    # PLANNING MUST COST LESS THAN THE WORK IT PLANS. Measured (0.10.28): a 3-task epic that
    # already had its three tasks spent 29 minutes and $7.33 in §3 — survey, architect,
    # planner, audit, denied audit, revised planner, audit again, each Opus dispatch ~4
    # minutes in strict sequence — and $1.07 in 3.5 minutes building. A pre-step does NOT
    # get quicker for a smaller epic on its own: the architect ran 12 turns at ~23 s each,
    # the planner 23 at ~15 s — per-turn thinking latency at the tier's fixed effort, times
    # a reading procedure whose floor is the same however little there is to read. What
    # moves it is (a) the effort, the `plan_tiers` lever, and (b) not dispatching at all
    # when the answer is already on disk or computable: a design staged while the spec
    # index reads REUSE is reused, and a READY epic whose tasks pass the mechanical checks
    # gets no planner and no audit — the planner's review is what `validate --paths` and
    # the acceptance/SURFACE checks now compute. State, never a task-count threshold.

    def children(self) -> list:
        try:
            return [c for c in self.store().list(parent=self.epic) if c.status != "closed"]
        except Exception:  # noqa: BLE001 — a tracker that cannot answer reads as "no children"
            return []

    def size_line(self, role: str) -> str:
        """WRITE IN PROPORTION TO THE EPIC. Measured: for an epic whose whole source was 239
        words, the architect wrote a 1,621-word design (20k output tokens, 273 s), the
        planner a 3,876-word plan for three tasks that already existed (28k, 342 s), the
        audit 17k tokens about it — each stage's time was its output, and its output was the
        deliverable's prescribed SHAPE, not what there was to say. The count is known here."""
        n = len(self.children())
        what = f"{n} open task(s)" if n else "no tasks yet"
        if role == "sanity-check":
            return (f"SIZE: this epic has {what}. Confirm or flag, in proportion — a paragraph per task at most, one page in all; "
                    "no rejected-alternatives section, no impact list, no draft decision record unless a real fork exists. A design that says nothing has changed is a short one.\n")
        if role == "design":
            return (f"SIZE: this epic has {what}. Write in proportion to the change — a page per module touched at most, sections only where there is something to decide; "
                    "a small change gets a short design, not a full template.\n")
        if role == "plan":
            return (f"SIZE: this epic has {what}. Emit only what changes: a task that stands as recorded is named, not re-issued; the matrix and the wave plan are as long as their edges and waves; "
                    "a three-task epic's plan is a page, not a document.\n")
        if role == "audit":
            return (f"SIZE: the plan covers {what}. One line per task — PASS, or the finding — then the blocking/filed findings; never a restatement of the plan. "
                    "A short plan gets a short audit.\n")
        return ""

    def tier_for(self, role: str) -> str | None:
        """The lever: the READY sanity-check and the audit at `strong` (the architect's
        declared tier is strategic — Opus at max effort); None keeps the declared tier.
        The design of an unplanned epic and the planner are never moved: they write the DAG."""
        from . import levers

        if not levers.lever("plan_tiers"):
            return None
        return "strong" if role in ("sanity-check", "audit") else None

    def mechanically_ready(self) -> tuple[bool, str]:
        """READY in the tracker's terms AND in the planner's: every open child carries
        acceptance criteria and a `SURFACE:` line, `tk.sh validate --paths` is OK with no
        contention edge. Returns (ready, why)."""
        kids = self.children()
        if not kids:
            return False, "no open children"
        lacking = [c.id for c in kids if not (c.acceptance or "").strip() or "SURFACE:" not in ((c.description or "") + (c.acceptance or ""))]
        if lacking:
            return False, f"{len(lacking)} task(s) without acceptance criteria or a SURFACE: line: {', '.join(lacking[:5])}"
        raw = execute([str(TK), "validate", self.epic, "--paths"], cwd=self.cwd, runner=self.runner)
        if not raw.ran or raw.returncode != 0:
            return False, "validate --paths is not OK: " + (tail(raw.stderr) or tail(raw.stdout) or failure_detail(raw))
        try:
            doc = json.loads(raw.stdout or "{}")
        except ValueError:
            return False, "validate --paths did not answer in JSON"
        edges = [e for w in ((doc.get("contention") or {}).get("waves") or []) for e in (w.get("edges") or [])]
        if edges:
            return False, f"{len(edges)} file-contention edge(s) the planner must resolve: " + "; ".join(f"{' × '.join(e['tasks'])}: {e['path']}" for e in edges[:3])
        return True, f"{len(kids)} task(s), every one with acceptance and a SURFACE: line; validate --paths clean"

    def folder(self) -> Path | None:
        from tracker.staging import ensure_folder, known_prefix

        proposed = (self.project.paths or {}).get("proposed") if self.project else None
        if not proposed:
            return None
        return ensure_folder(self.epic, Path(self.cwd) / proposed, self.title(), known_prefix())

    def fail(self, name: str, detail: str, code: int, raw: Raw | None = None) -> None:
        self.results.append(Result(name, FAIL, detail, raw))
        self.stop = (code, name)

    def judged(self, name: str, raw: Raw, text: str, needle: re.Pattern | None, label: str = "VERDICT") -> bool:
        """A dispatch that did not run cleanly, or whose result carries no verdict line, is
        NONE — the sequencer stops with 'could not judge', never proceeds on a guess."""
        if not raw.ran or raw.returncode != 0:
            self.fail(name, f"could not judge — the dispatch did not return ok ({'hung' if not raw.ran else f'exit {raw.returncode}'}: a denial, a refusal or a budget kill); the work did not happen whatever the text claims:\n{failure_detail(raw)}", EXIT_NO_JUDGE, raw)
            return False
        if needle is not None and not needle.search(text):
            self.fail(name, f"could not judge — no `{label}:` line in the result; a missing verdict is never a pass", EXIT_NO_JUDGE, raw)
            return False
        return True

    # --- stages ----------------------------------------------------------------------------

    def survey(self) -> None:
        """§3a: reuse / delta / rebuild, the survey, its verdict, the ADEQUACY note, the
        spec index staged with its frontmatter, the ABSENT path."""
        raw = execute([str(CHECKS / "spec-index-status.sh"), self.epic], cwd=self.cwd, runner=self.runner)
        status_text = (raw.stdout or "") + (raw.stderr or "")
        status = "REUSE" if "REUSE" in status_text else ("DELTA" if "DELTA" in status_text else "REBUILD")
        self.state.data["spec_status"] = status
        self.state.save()
        changed = [ln.strip("- ").strip() for ln in status_text.splitlines() if ln.strip().startswith("- ")] if status == "DELTA" else []
        self.results.append(Result("spec-index-status.sh", OK if raw.ran else FAIL, f"{status}" + (f" — {len(changed)} cited path(s) moved" if changed else "") + (" (nothing it cites has moved; the survey is not re-dispatched)" if status == "REUSE" else ""), raw))
        text = ""
        verdict = None
        if status == "REUSE":
            self.title()  # loads the epic record
            notes = (self.epic_task.notes if self.epic_task else "") or ""
            m = ADEQUACY.search(notes)
            if m:
                verdict = m.group("v").upper()
                self.results.append(Result("ADEQUACY (from the epic's note)", OK, f"{verdict} — reused; the index's date is on the file"))
            else:
                status = "REBUILD"
                self.results.append(Result("ADEQUACY", INFO, "the index is current but the epic carries no ADEQUACY: note — surveying"))
        if status != "REUSE":
            delta = f"\nThis is a DELTA survey. The existing index is at `{self.state.art('spec_index') or 'the staging folder'}`; these cited paths moved since it was generated — verify and extend, do not rebuild:\n" + "\n".join(f"- {c}" for c in changed) if status == "DELTA" else ""
            prompt = (f"SURVEY epic {self.epic} — {self.title()}.\nRead `{TK} --readonly show {self.epic}` and its children, then sweep the corpus for everything that already constrains it. "
                      f"Return the SURVEY return contract: the ADEQUACY: verdict first, the AUTHORITATIVE SPEC, and a POINTER-ONLY SPEC INDEX.{delta}\n")
            out = self.out_dir / "survey.md"
            raw, text = _dispatch("analyst-survey", prompt, self.epic, out, self.runner, self.cwd, self.dispatch_fn)
            if not self.judged("dispatch analyst-survey", raw, text, ADEQUACY, "ADEQUACY"):
                return
            verdict = ADEQUACY.search(text).group("v").upper()
            self.results.append(Result("dispatch analyst-survey", OK, f"ADEQUACY: {verdict}\nfull: {out}", raw))
            self.state.done("survey", survey=out)
            folder = self.folder()
            if folder is not None:
                from tracker.staging import cites_in, stage_spec_index

                m = SPEC_INDEX.search(text)
                body = m.group("body").strip() if m else text
                idx = stage_spec_index(folder, self.epic, verdict=verdict, body=body, cites=cites_in(text, Path(self.cwd)), cwd=Path(self.cwd), title=self.title())
                self.results.append(Result("spec-index.md", OK, f"{idx.relative_to(self.cwd)} — generated_sha, cites, verdict in frontmatter"))
                self.state.done("survey", spec_index=idx)
                if status == "DELTA":
                    st = execute([str(CHECKS / "spec-index-status.sh"), self.epic, "--stamp"], cwd=self.cwd, runner=self.runner)
                    self.results.append(Result("spec-index-status.sh --stamp", OK if st.ran and st.returncode == 0 else FAIL, "baseline → HEAD — a DELTA that does not stamp re-fires forever" if st.ran and st.returncode == 0 else failure_detail(st), st))
            else:
                self.results.append(Result("spec-index.md", INFO, "not staged — harness.yaml declares no paths.proposed"))
            self.results.append(_note(self.epic, f"ADEQUACY: {verdict} {_dt.date.today().isoformat()} — see {self.state.art('spec_index') or out}", self.runner, self.cwd))
        self.state.data["adequacy"] = verdict
        self.state.save()

        if verdict == "ABSENT":
            self.absent(text)
            return
        self.state.done("survey")

    def absent(self, survey_text: str) -> None:
        """The ABSENT path: draft from the corpus, audit the draft, then either continue
        (CORPUS-DERIVED) or file the requirement and park."""
        folder = self.folder()
        prompt = (f"DRAFT the proposal for epic {self.epic} — {self.title()} — under the staging folder `{folder}` as `proposal.md`, `status: draft`, "
                  f"from the SURVEY at `{self.state.art('survey')}`. Every drafted criterion carries the doc and the quoted opening words it derives from; a criterion that cannot be cited goes under `## Open questions`. Assemble, never invent.\n")
        out = self.out_dir / "draft.md"
        raw, text = _dispatch("spec-editor", prompt, self.epic, out, self.runner, self.cwd, self.dispatch_fn)
        if not self.judged("dispatch spec-editor (draft)", raw, text, None):
            return
        self.results.append(Result("dispatch spec-editor (draft)", OK, f"full: {out}", raw))
        prompt = f"AUDIT the drafted proposal for epic {self.epic} at `{folder / 'proposal.md' if folder else '(see the epic)'}`. Return the AUDIT return contract with `VERDICT: PASS` or `VERDICT: FAIL` and, for a spec, an `ADEQUACY:` line.\n"
        out = self.out_dir / "draft-audit.md"
        raw, text = _dispatch("analyst", prompt, self.epic, out, self.runner, self.cwd, self.dispatch_fn)
        if not self.judged("dispatch analyst (audit the draft)", raw, text, verdict_mod.VERDICT):
            return
        v = verdict_mod.parse(text)
        adequate = v.status == verdict_mod.PASS or bool(re.search(r"ADEQUACY:\s*ADEQUATE", text, re.I))
        self.results.append(Result("dispatch analyst (audit the draft)", OK if adequate else FAIL, f"{v.status}\nfull: {out}", raw))
        if adequate:
            self.results.append(_note(self.epic, f"CORPUS-DERIVED — no owner input {_dt.date.today().isoformat()}: the proposal was assembled from the corpus and audited ADEQUATE", self.runner, self.cwd))
            self.state.data["adequacy"] = "INFERABLE"
            self.state.done("survey")
            return
        req = REQUIREMENT_LINE.search(text)
        what = req.group("q") if req else f"the specification for {self.epic} is absent and the corpus does not supply it"
        raw = execute([str(TK), "create", f"REQUIREMENT: {what}"[:200], "-t", "decision", "-p", "1", "--description", f"Epic {self.epic} — {self.title()}. The draft at {folder / 'proposal.md' if folder else '?'} carries what the corpus supports; the audit at {out} names what it does not. Route: /requirements."], cwd=self.cwd, runner=self.runner)
        self.results.append(Result("tk.sh create REQUIREMENT:", OK if raw.ran and raw.returncode == 0 else FAIL, tail(raw.stdout, 1) if raw.ran else failure_detail(raw), raw))
        self.results.append(_park(self.epic, f"REQUIREMENT owed: {what[:120]} — route /requirements with the partial draft", self.runner, self.cwd))
        self.stop = (EXIT_PARKED, "absent")

    def foldin(self) -> None:
        """Fold-in ①: the register gate, then spec-editor applies the proposal (if one)."""
        raw = execute([str(CHECKS / "check-decision-register.sh"), self.epic], cwd=self.cwd, runner=self.runner)
        text = (raw.stdout or "") + (raw.stderr or "")
        if not raw.ran or raw.returncode != 0:
            self.fail("check-decision-register.sh", failure_detail(raw), EXIT_FAILED, raw)
            return
        from tracker.check_register import OPEN_MARKER

        if OPEN_MARKER in text:
            rows = [ln.strip() for ln in text.splitlines() if OPEN_MARKER in ln]
            self.results.append(Result("check-decision-register.sh", FAIL, "open decision(s) in the register — an epic does not fold in or design over a decision it raised:\n" + "\n".join(rows), raw))
            self.results.append(_park(self.epic, "open decision(s) in the register — /decision", self.runner, self.cwd))
            self.stop = (EXIT_PARKED, "register")
            return
        self.results.append(Result("check-decision-register.sh", OK, tail(raw.stdout, 1) or "clean", raw))
        folder = self.folder()
        proposal = folder / "proposal.md" if folder else None
        if proposal is None or not proposal.is_file():
            self.results.append(Result("fold-in ①", OK, "no proposal.md staged for this epic — nothing to fold in; the epic predates the flow or was specified in place"))
            self.state.done("foldin")
            return
        inferences = "\nRecord every INFERENCE the survey listed as an unchecked acceptance criterion in the owning feature doc.\n" if self.state.data.get("adequacy") == "INFERABLE" else ""
        prompt = (f"FOLD-IN ① for epic {self.epic}: apply the staged proposal at `{proposal}` to the corpus, guided by the SPEC INDEX at `{self.state.art('spec_index') or folder}`. "
                  f"Acceptance criteria land in the owning feature doc as unchecked criteria, verbatim; index rows where the corpus index needs them. Then set the proposal's `status: folded-in`.{inferences}")
        out = self.out_dir / "foldin.md"
        raw, text = _dispatch("spec-editor", prompt, self.epic, out, self.runner, self.cwd, self.dispatch_fn)
        if not self.judged("dispatch spec-editor (fold-in ①)", raw, text, None):
            return
        self.results.append(Result("dispatch spec-editor (fold-in ①)", OK, f"full: {out}", raw))
        self.state.done("foldin", foldin=out)

    def render_view(self) -> Path | None:
        """The epic's tasks as one file, for an agent that would otherwise read them one
        `tk.sh show` at a time. Measured (an orchestrated wavelab run): the architect looped
        `for id in …; do tk.sh show $id; done` — a compound command, denied, identically
        on both attempts — and the sequencer stopped with nothing designed. The view is
        deterministic; the sequencer renders it and names the path."""
        folder = self.folder()
        if not folder:
            return None
        out = folder / "tasks.md"
        raw = execute([str(RENDER), self.epic, "--write", str(out)], cwd=self.cwd, runner=self.runner)
        if not raw.ran or raw.returncode != 0:
            self.results.append(Result("render-epic.sh", INFO, "the task view could not be rendered — the architect reads the tracker itself:\n" + failure_detail(raw), raw))
            return None
        return out

    def architect(self) -> None:
        """§3b: the design (or the sanity-check), its verdict, the note, the gate."""
        triage = self.state.data.get("triage") or "UNPLANNED"
        # A DESIGN STAGED WHILE NOTHING IT RESTS ON MOVED IS THE DESIGN. The spec index at
        # REUSE says every cited path is as it was when the index was generated; a staged
        # design.md from that time needs no architect to say so again.
        folder = self.folder()
        staged = folder / "design.md" if folder else None
        if staged and staged.is_file() and self.state.data.get("spec_status") == "REUSE":
            self.results.append(Result("architect", OK, f"reused — `{staged}` is staged and the spec index reads REUSE (nothing it cites has moved); not re-dispatched"))
            self.results.append(_note(self.epic, f"ARCHITECTURE: reused {_dt.date.today().isoformat()} — design staged at {staged}, spec index REUSE", self.runner, self.cwd))
            self.state.done("architect", design=staged)
            self.state.done("stage", staged_design=staged)
            return
        view = self.render_view() if triage != "UNPLANNED" else None
        tier = self.tier_for("sanity-check") if triage == "READY" else None
        base = (f"Epic {self.epic} — {self.title()}. The SPEC INDEX is at `{self.state.art('spec_index') or '(none staged; read the epic)'}`; the survey at `{self.state.art('survey') or '(reused)'}`. Start there; open what it points at.\n"
                + (f"The epic's existing tasks are rendered, in full, at `{view}` — read that file; do not fetch them one by one, and never in a shell loop (a compound command is denied).\n" if view else ""))
        if triage == "READY":
            prompt = base + self.size_line("sanity-check") + ("SANITY-CHECK the existing design and tasks: 1) is the recorded or implied design still correct given everything that has landed since the tasks were written — check the feature docs and the ADRs, including ones written after these tasks; 2) has the ground moved underneath it — run `tk.sh memories` and look for a documented framework that is a veneer; 3) confirm, or flag the drift precisely. Begin your output with an `ARCHITECTURE:` block. "
                             "If you cannot proceed without inventing scope, put `ADEQUACY: ABSENT` first and stop. Put every open question on its own line as `DECISION: <question>`.\n")
        else:
            prompt = base + self.size_line("design") + ("DESIGN it: the recommended approach (modules, service functions, schema, API contract), rejected alternatives with reasons, the impact list, a draft decision record where the call is non-obvious. Begin your output with an `ARCHITECTURE:` block. "
                             "If you cannot design without inventing scope, put `ADEQUACY: ABSENT` first and stop. Put every open question on its own line as `DECISION: <question>`; put a missing requirement as `REQUIREMENT: <what is unspecified>`.\n")
        out = self.out_dir / "design.md"
        raw, text = _dispatch("architect", prompt, self.epic, out, self.runner, self.cwd, self.dispatch_fn, tier=tier)
        if not self.judged("dispatch architect", raw, text, None):
            return
        m = ADEQUACY.search(text)
        v = m.group("v").upper() if m else "ADEQUATE"
        self.results.append(Result("dispatch architect", OK if v != "ABSENT" else FAIL, f"{'sanity-check' if triage == 'READY' else 'design'}{f' at tier {tier}' if tier else ''} — ADEQUACY: {v}{' (disputed)' if m else ''}\nfull: {out}", raw))
        note = execute([str(TK), "update", self.epic, "--append-notes-file", str(out)], cwd=self.cwd, runner=self.runner)
        self.results.append(Result(f"tk.sh update {self.epic} --append-notes-file", OK if note.ran and note.returncode == 0 else FAIL, "the architect's output, by file — never retyped" if note.ran and note.returncode == 0 else failure_detail(note), note))
        self.state.done("architect", design=out)
        if v == "ABSENT":
            req = REQUIREMENT_LINE.search(text)
            what = req.group("q") if req else "the architect cannot design without inventing scope"
            raw = execute([str(TK), "create", f"REQUIREMENT: {what}"[:200], "-t", "decision", "-p", "1", "--description", f"Epic {self.epic}: {what}. See {out}. Route: /requirements."], cwd=self.cwd, runner=self.runner)
            self.results.append(Result("tk.sh create REQUIREMENT:", OK if raw.ran and raw.returncode == 0 else FAIL, tail(raw.stdout, 1) if raw.ran else failure_detail(raw), raw))
            self.results.append(_park(self.epic, f"REQUIREMENT owed: {what[:120]} — auto-accept covers design, never invented scope", self.runner, self.cwd))
            self.stop = (EXIT_PARKED, "architect")
            return
        decisions = _create_decisions(text, self.epic, self.runner, self.cwd)
        self.state.data["decisions"] = decisions
        self.state.save()
        if self.mode == "auto":
            self.results.append(_note(self.epic, f"AUTO-ACCEPTED {_dt.date.today().isoformat()}: the design at {out} was accepted without an owner, MODE=auto", self.runner, self.cwd))
            if decisions:
                self.results.append(_park(self.epic, f"open decision(s) from the architect: {', '.join(t for t, _ in decisions)} — the hard line", self.runner, self.cwd))
                self.stop = (EXIT_PARKED, "architect")
                return
        else:
            self.stop = (EXIT_STOP, "architect")

    def stage(self) -> None:
        """Stage the design and a draft decision record per decision the architect raised."""
        folder = self.folder()
        design = self.state.art("design")
        if folder is None:
            self.results.append(Result("stage design", INFO, "not staged — harness.yaml declares no paths.proposed; the ARCHITECTURE: note is the record"))
            self.state.done("stage")
            return
        from tracker.staging import draft_adr, stage_design

        proposed = (Path(self.cwd) / (self.project.paths or {}).get("proposed")) if self.project else None
        path = stage_design(folder, self.epic, _read(design), title=self.title(), proposed=proposed)
        drafts = [draft_adr(folder, self.epic, q, tid, proposed=proposed) for tid, q in self.state.data.get("decisions") or []]
        self.results.append(Result("stage design", OK, f"{path.relative_to(self.cwd)}" + (f" + {len(drafts)} draft decision record(s)" if drafts else "")))
        self.results.append(_note(self.epic, f"ARCHITECTURE: design staged at {path.relative_to(self.cwd)} — folds in at §5", self.runner, self.cwd))
        self.state.done("stage", staged_design=path)

    def planner(self, findings: str | None = None) -> None:
        """§3c: a FRESH planner dispatch — with the audit's findings path on a revision."""
        if findings is None and (self.state.data.get("triage") or "UNPLANNED") == "READY":
            ready, why = self.mechanically_ready()
            if ready:
                self.results.append(Result("planner", OK, f"not dispatched — the tasks are READY by the mechanical checks: {why}. The planner's review is what those checks compute; the audit gates a plan, and there is none to audit."))
                self.results.append(_note(self.epic, f"PLAN: reused {_dt.date.today().isoformat()} — READY; {why}", self.runner, self.cwd))
                self.state.data["plan_reused"] = True
                self.state.done("planner")
                self.state.done("audit")
                self.state.done("gate")
                self.state.done("apply")
                return
            self.results.append(Result("planner", INFO, f"dispatched — READY in the tracker but not by the mechanical checks: {why}"))
        adrs = (self.project.paths or {}).get("adrs") if self.project else None
        from tracker.staging import adr_next

        next_adr = f"{adr_next(Path(self.cwd) / adrs):04d}" if adrs else None
        lanes = self.project.lanes() if self.project else {}
        lane_text = ", ".join(f"{k} (cap {v.get('cap', '?')})" for k, v in lanes.items()) or "(none declared)"
        # THE CHILDREN AS ONE FILE, as for the architect: handed `list --parent`, the planner
        # read each record with `for t in …; do tk.sh show; done` — denied, three of three
        # attempts, a complete plan refused each time (measured, $4.13 of planner).
        view = self.render_view()
        tasks_line = f"Its current tasks are rendered, in full, at `{view}` — read that file; do not fetch them one by one, and never in a shell loop (a compound command is denied)." if view else f"Its current tasks: `{TK} list --parent {self.epic}`, then `{TK} show <id>` one at a time — never in a shell loop (a compound command is denied)."
        prompt = (self.size_line("plan") + f"PLAN epic {self.epic} — {self.title()}. {tasks_line} The design is at `{self.state.art('staged_design') or self.state.art('design') or '(the ARCHITECTURE: note)'}`; the SPEC INDEX at `{self.state.art('spec_index') or '(none)'}`; the survey at `{self.state.art('survey') or '(reused)'}`.\n"
                  f"Lanes and caps: {lane_text}. " + (f"The next free decision-record number is {next_adr}; a task that needs one names it. " if next_adr else "") +
                  "Produce the DAG with a SURFACE: line per task, the file-contention matrix with every edge resolved, the wave plan, and the tracker commands in fenced bash blocks with `T1:` labels for apply-plan.sh. Open questions as `DECISION: <question>` lines outside the blocks.\n")
        if findings:
            prompt += f"\nThis is a REVISION. The audit of your previous plan FAILed; its findings are at `{findings}` — read them and revise the plan. Fresh dispatch: nothing of the previous conversation is here.\n"
        n = self.state.data["audit_attempts"]
        out = self.out_dir / (f"plan-{n + 1}.md" if n else "plan.md")
        raw, text = _dispatch("planner", prompt, self.epic, out, self.runner, self.cwd, self.dispatch_fn)
        if not self.judged("dispatch planner", raw, text, None):
            return
        if "```" not in text:
            self.fail("dispatch planner", f"could not judge — the plan carries no fenced command block for apply-plan.sh\nfull: {out}", EXIT_NO_JUDGE, raw)
            return
        self.results.append(Result("dispatch planner" + (" (revision)" if findings else ""), OK, f"{'ADR ' + next_adr + ' allocated; ' if next_adr else ''}full: {out}", raw))
        self.state.done("planner", plan=out)

    def audit(self) -> None:
        """§3d: the analyst audits the plan; FAIL → a fresh planner once; a second FAIL parks."""
        if self.state.data.get("plan_reused"):
            return
        plan = self.state.art("plan")
        # THE EXISTING TASKS AS ONE FILE, as for the architect and the planner: the analyst
        # looped `for … tk.sh show …` over the epic's children to compare the plan with the
        # records — denied, a re-dispatch at $1.06 (measured).
        view = self.render_view()
        view_line = f" The epic's existing tasks are rendered, in full, at `{view}` — read that file; do not fetch them one by one, and never in a shell loop (a compound command is denied)." if view else ""
        prompt = self.size_line("audit") + f"AUDIT the plan for epic {self.epic} at `{plan}` — its task set, acceptance criteria and SURFACE: lines — against the standard.{view_line} Return the AUDIT return contract: `VERDICT: PASS` or `VERDICT: FAIL` first, findings tagged blocking|filed. A gap written down (an open question, a decision task, a stated deferral) is a PASS; the same gap silent is a FAIL.\n"
        n = self.state.data["audit_attempts"] + 1
        out = self.out_dir / f"audit-{n}.md"
        tier = self.tier_for("audit")
        raw, text = _dispatch("analyst", prompt, self.epic, out, self.runner, self.cwd, self.dispatch_fn, tier=tier)
        if not self.judged("dispatch analyst (audit)", raw, text, verdict_mod.VERDICT):
            return
        v = verdict_mod.parse(text)
        self.state.data["audit_attempts"] = n
        self.state.save()
        self.results.append(Result(f"dispatch analyst (audit {n})", OK if v.status == verdict_mod.PASS else FAIL, f"{v.status}{f' at tier {tier}' if tier else ''} — {v.blocking} blocking, {v.filed} filed\nfull: {out}", raw))
        self.results.append(_note(self.epic, f"AUDIT: {v.status} {_dt.date.today().isoformat()} — {v.blocking} blocking, {v.filed} filed; see {out}", self.runner, self.cwd))
        if v.status == verdict_mod.PASS:
            self.state.done("audit", audit=out)
            return
        if n >= 2:
            raw = execute([str(TK), "create", f"REQUIREMENT: the plan for {self.epic} cannot be made dispatchable in two audit passes"[:200], "-t", "decision", "-p", "1", "--description", f"Epic {self.epic}: two audits FAILed — an absent specification wearing a DAG. The findings at {out} are the agenda for /requirements."], cwd=self.cwd, runner=self.runner)
            self.results.append(Result("tk.sh create REQUIREMENT:", OK if raw.ran and raw.returncode == 0 else FAIL, tail(raw.stdout, 1) if raw.ran else failure_detail(raw), raw))
            self.results.append(_park(self.epic, "the plan failed its audit twice — underspecified at the epic level; /requirements", self.runner, self.cwd))
            self.stop = (EXIT_PARKED, "audit")
            return
        self.planner(findings=str(out))
        if self.stop:
            return
        self.audit()

    def gate(self) -> None:
        """§3d's gate: interactive stops here; auto parks on a decision or an unresolved edge."""
        if self.state.data.get("plan_reused"):
            return
        plan_text = _read(self.state.art("plan"))
        # Every open question the planner raised is a `decision` task in both modes — an
        # open question is the owner's whatever the mode; auto parks on it, interactive
        # surfaces it first at the stop.
        decisions = _create_decisions(plan_text, self.epic, self.runner, self.cwd)
        unresolved = bool(re.search(r"\bunresolved\b.*\b(edge|contention)\b", plan_text, re.I))
        if self.mode == "auto":
            if decisions or unresolved:
                why = ("open decision(s) from the planner: " + ", ".join(t for t, _ in decisions if t)) if decisions else "an unresolved contention edge in the plan"
                self.results.append(_park(self.epic, why + " — auto-approve covers a plan, never a fork", self.runner, self.cwd))
                self.stop = (EXIT_PARKED, "gate")
                return
            self.results.append(_note(self.epic, f"AUTO-ACCEPTED {_dt.date.today().isoformat()}: the plan at {self.state.art('plan')} was approved without an owner, MODE=auto", self.runner, self.cwd))
            self.state.done("gate")
            return
        self.state.data["plan_decisions"] = [f"{t} {q}" for t, q in decisions]
        self.state.save()
        self.stop = (EXIT_STOP, "gate")

    def apply(self) -> None:
        """§3e/§3f: one call."""
        if self.state.data.get("plan_reused"):
            self.results.append(Result("apply-plan.sh", OK, "nothing to apply — the tasks in the tracker are the plan"))
            return
        folder = self.folder()
        argv = [str(SWARM / "apply-plan.sh"), self.state.art("plan") or "", "--epic", self.epic]
        if folder is not None:
            argv += ["--render", str(folder / "tasks.md")]
        raw = execute(argv, cwd=self.cwd, timeout=1800, runner=self.runner)
        if not raw.ran or raw.returncode != 0:
            self.fail("apply-plan.sh", failure_detail(raw) + "\nA refused plan writes nothing; a plan that failed part-way resumes by label on the same command.", EXIT_FAILED, raw)
            return
        self.results.append(Result("apply-plan.sh", OK, tail(raw.stdout, 2), raw))
        self.state.done("apply")

    # --- the loop ----------------------------------------------------------------------------

    ORDER = {"survey": survey, "foldin": foldin, "architect": architect, "stage": stage, "planner": planner, "audit": audit, "gate": gate, "apply": apply}

    def run(self, start: str = "survey") -> tuple[str, int]:
        """`start` and everything after it. The stages before it are assumed done — their
        artefacts are in the state file — which is what `--from` means."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        begin = STAGES.index(start)
        if begin:
            missing = [k for k, st in (("survey", "survey"), ("design", "architect"), ("plan", "planner")) if STAGES.index(st) < begin and not self.state.art(k)]
            if missing:
                self.results.append(Result("state", INFO, f"--from {start} with no {', '.join(missing)} artefact recorded — the earlier stages' outputs are read from the tracker and the staging folder where they exist"))
        for name in STAGES[begin:]:
            self.ORDER[name](self)
            if self.stop:
                break
        code = self.stop[0] if self.stop else EXIT_OK
        if code in (EXIT_PARKED, EXIT_OK):
            self.sync_staging(code)
        return self.report(code), code

    def sync_staging(self, code: int) -> None:
        """What §3 leaves on disk is committed by §3. A park leaves the gate, the PARKED
        note, the `decision` or `REQUIREMENT:` tasks it filed and the staged spec index;
        a SUCCESS leaves the staged `spec-index.md`, `design.md`, the draft decision
        records and the applied plan's export and view — and `merge-wave.sh` refuses a
        dirty tree before it takes the slot, so an uncommitted staging folder blocked the
        first wave's merge until the orchestrator worked out what to commit (measured:
        seven turns, `--help` on three scripts). Committed and pushed HERE, as `halt.sh
        pause` does, because the sequencer knows what it wrote. Measured earlier: told
        only "4 parked — move to the next epic", the orchestrator spent 16 of its 26 turns
        reading preflight.py, campaign_auto.py and tracker_sync.py to decide what to
        commit. autosync is NOT restored here: `campaign.sh` (auto) and §5 (interactive)
        own that, as for every wave."""
        from . import tracker_sync

        export, raw = tracker_sync.export_path(self.runner, self.cwd)
        if raw.error or (raw.ran and raw.returncode != 0):
            self.results.append(Result("tk.sh backend --json", FAIL, "cannot learn the tracked export path — the park is recorded but NOT committed:\n" + failure_detail(raw), raw))
            return
        extra = []
        folder = self.folder()
        if folder and folder.is_dir():
            extra.append(str(folder.relative_to(self.cwd)) if folder.is_absolute() else str(folder))
        what = f"park {self.epic} at {self.stop[1]}" if code == EXIT_PARKED else f"plan {self.epic} — staged survey, design and plan"
        self.results += tracker_sync.sync(
            message=f"chore(tracker): {what}", epics=[self.epic], export=export, extra_paths=extra,
            push=self.push, stop_if_upstream_moved=False, restore_autosync=False, project=self.project, runner=self.runner, cwd=self.cwd,
        )

    def report(self, code: int) -> str:
        out = [f"plan-epic {self.epic} (MODE={self.mode})", render(self.results)]
        if code == EXIT_OK:
            out.append("\nPLANNED — the DAG is in the tracker; `tk.sh validate` and the view ran with the apply; the staging folder and the export are committed and pushed (the sync lines above). Next: §4, `wave-plan.sh <lane> --parent " + self.epic + "`.")
        elif code == EXIT_STOP:
            stage = self.stop[1]
            art = self.state.art("staged_design") or self.state.art("design") if stage == "architect" else self.state.art("plan")
            nxt = "planner" if stage == "architect" else "apply"
            what = "the design" if stage == "architect" else "the DAG, the contention matrix and the revision plan"
            qs = self.state.data.get("plan_decisions") or [q for _, q in (self.state.data.get("decisions") or [])]
            out.append(f"\nAPPROVAL OWED — {what} is at `{art}`. Render it verbatim to the owner" + (f", surfacing these decision(s) first: {'; '.join(qs)}" if qs else "") + f". Approved → `plan-epic.sh {self.epic} --from {nxt}`. Revise → edit or re-dispatch, then `--from {stage}`. Reject → `tk.sh park {self.epic}`.")
        elif code == EXIT_PARKED:
            out.append(f"\nPARKED at {self.stop[1]} — the epic is out of the queue; the [FAIL] line above says on what and which command un-parks it. The tracker export and the staging folder are committed and pushed (the sync lines above); autosync stays off for the run. Nothing else is owed here: record the outcome (`campaign-signals.sh {self.epic} --outcome parked`, or the return contract's first line in MODE=auto) and move to the next epic.")
        elif code == EXIT_NO_JUDGE:
            # stop[1] is the failed step's NAME ("dispatch architect"); --from wants the stage.
            stage = next((st for st in STAGES if st in self.stop[1]), self.stop[1])
            out.append(f"\nCOULD NOT JUDGE — a dispatch did not return a verdict. Nothing was approved. If the cause is yours to fix, fix it and re-run the same stage (`plan-epic.sh {self.epic} --from {stage}`). If it is not (a harness defect, a denial you cannot grant): file it, then `halt.sh pause {self.epic}` — the park, the export, the commit and the push as one call — and move to the next epic. Do not re-run the stage unchanged; a deterministic failure repeats.")
        else:
            out.append(f"\nSTOPPED at {self.stop[1]} — resolve the [FAIL] line and re-run `plan-epic.sh {self.epic} --from {self.stop[1]}`.")
        return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .project import ProjectError, load

    ap = argparse.ArgumentParser(prog="plan-epic.sh", description="campaign-loop §3 as a sequencer: survey → fold-in ① → architect → stage → planner → audit (≤2) → gate → apply. Interactive stops at the two approvals (exit 6); auto self-approves and parks on absent scope or an open decision.")
    ap.add_argument("epic", nargs="?", help="the epic to plan (not needed with --wait)")
    ap.add_argument("--mode", choices=["auto", "interactive"], default="interactive")
    ap.add_argument("--from", dest="start", choices=STAGES, default="survey", help="resume at this stage (the state file carries the earlier artefacts)")
    ap.add_argument("--triage", choices=["UNPLANNED", "PARTIAL", "READY"], default=None, help="what epic-queue.sh said; READY asks the architect for a sanity-check")
    ap.add_argument("--reset", action="store_true", help="forget the state file first — plan from nothing")
    ap.add_argument("--no-push", action="store_true", help="a park commits the tracker state but does not push it")
    ap.add_argument("--detach", action="store_true", help="run in the background through fanout; prints the run id to --wait on")
    ap.add_argument("--wait", metavar="RUN_ID", help="block up to --timeout for a detached run; exit 5 while it is still running")
    ap.add_argument("--timeout", type=int, default=540, help="seconds --wait blocks (default 540, under the 10-minute Bash cap)")
    args = ap.parse_args(argv)
    # TEN TO TWENTY-FIVE MINUTES, longer than one Bash call may run. Measured: an
    # orchestrator whose plan-epic.sh call was backgrounded by the cap spent 22 of its
    # 110 turns polling for it — `ls`, `date`, `pgrep`, a hand-written wait script. The
    # detach-and-poll shape fanout already has (exit 5 = still running) applies to this
    # one long job too; the orchestrator types two commands and reads one report.
    if args.wait:
        return _wait(args.wait, args.timeout)
    if not args.epic:
        ap.error("the following arguments are required: epic")
    if args.detach:
        return _detach(argv if argv is not None else sys.argv[1:], args.epic)
    try:
        project = load()
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_FAILED
    seq = Sequencer(args.epic, mode=args.mode, project=project, push=not args.no_push)
    if args.reset:
        seq.state.data.update({"done": [], "artefacts": {}, "audit_attempts": 0})
        seq.state.save()
    if args.triage:
        seq.state.data["triage"] = args.triage
        seq.state.save()
    text, code = seq.run(args.start)
    print(text)
    return code


def _detach(argv: list[str], epic: str) -> int:
    from . import fanout

    plain = [a for a in argv if a != "--detach"]
    wrapper = HARNESS / "swarm" / "plan-epic.sh"
    job = fanout.Job(name=f"plan-epic {epic}", argv=(str(wrapper), *plain), cwd=str(REPO), timeout=4 * 3600, task=epic)
    run_id = fanout.detach([job], 1)
    print(f"detached: plan-epic {epic} → run {run_id}")
    print(f"next: `plan-epic.sh --wait {run_id} --timeout 540` — repeat while it exits 5; the report is printed when it lands")
    return EXIT_OK


def _wait(run_id: str, timeout: int) -> int:
    from . import fanout

    try:
        done, results, jobs = fanout.wait(run_id, timeout)
    except (OSError, ValueError) as exc:
        print(f"no such run {run_id}: {exc}", file=sys.stderr)
        return EXIT_FAILED
    if not done:
        print(f"still running: plan-epic run {run_id} — `plan-epic.sh --wait {run_id} --timeout {timeout}` again")
        return 5
    r = results[0] if results else None
    if r is None:
        print(f"run {run_id} finished with no result recorded", file=sys.stderr)
        return EXIT_NO_JUDGE
    print(r.stdout.rstrip())
    if r.stderr.strip():
        print(r.stderr.rstrip(), file=sys.stderr)
    return r.rc if r.rc is not None else EXIT_NO_JUDGE


if __name__ == "__main__":
    raise SystemExit(main())
