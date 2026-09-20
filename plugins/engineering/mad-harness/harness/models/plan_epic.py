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


def _dispatch(agent: str, prompt: str, epic: str, out: Path, runner, cwd: str, dispatch_fn=None) -> tuple[Raw, str]:
    """One fresh dispatch; the whole result at `out`. Returns (raw, result text)."""
    pfile = out.with_suffix(".prompt.md")
    pfile.parent.mkdir(parents=True, exist_ok=True)
    pfile.write_text(prompt)
    if dispatch_fn:
        raw = dispatch_fn(agent, pfile, out)
    else:
        raw = execute([str(DISPATCH), agent, "--prompt-file", str(pfile), "--task", epic, "--out", str(out), "--digest", "0"], cwd=cwd, timeout=DISPATCH_TIMEOUT, runner=runner)
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
        view = self.render_view() if triage != "UNPLANNED" else None
        base = (f"Epic {self.epic} — {self.title()}. The SPEC INDEX is at `{self.state.art('spec_index') or '(none staged; read the epic)'}`; the survey at `{self.state.art('survey') or '(reused)'}`. Start there; open what it points at.\n"
                + (f"The epic's existing tasks are rendered, in full, at `{view}` — read that file; do not fetch them one by one, and never in a shell loop (a compound command is denied).\n" if view else ""))
        if triage == "READY":
            prompt = base + ("SANITY-CHECK the existing design and tasks: 1) is the recorded or implied design still correct given everything that has landed since the tasks were written — check the feature docs and the ADRs, including ones written after these tasks; 2) has the ground moved underneath it — run `tk.sh memories` and look for a documented framework that is a veneer; 3) confirm, or flag the drift precisely. Begin your output with an `ARCHITECTURE:` block. "
                             "If you cannot proceed without inventing scope, put `ADEQUACY: ABSENT` first and stop. Put every open question on its own line as `DECISION: <question>`.\n")
        else:
            prompt = base + ("DESIGN it: the recommended approach (modules, service functions, schema, API contract), rejected alternatives with reasons, the impact list, a draft decision record where the call is non-obvious. Begin your output with an `ARCHITECTURE:` block. "
                             "If you cannot design without inventing scope, put `ADEQUACY: ABSENT` first and stop. Put every open question on its own line as `DECISION: <question>`; put a missing requirement as `REQUIREMENT: <what is unspecified>`.\n")
        out = self.out_dir / "design.md"
        raw, text = _dispatch("architect", prompt, self.epic, out, self.runner, self.cwd, self.dispatch_fn)
        if not self.judged("dispatch architect", raw, text, None):
            return
        m = ADEQUACY.search(text)
        v = m.group("v").upper() if m else "ADEQUATE"
        self.results.append(Result("dispatch architect", OK if v != "ABSENT" else FAIL, f"{'sanity-check' if triage == 'READY' else 'design'} — ADEQUACY: {v}{' (disputed)' if m else ''}\nfull: {out}", raw))
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
        adrs = (self.project.paths or {}).get("adrs") if self.project else None
        from tracker.staging import adr_next

        next_adr = f"{adr_next(Path(self.cwd) / adrs):04d}" if adrs else None
        lanes = self.project.lanes() if self.project else {}
        lane_text = ", ".join(f"{k} (cap {v.get('cap', '?')})" for k, v in lanes.items()) or "(none declared)"
        prompt = (f"PLAN epic {self.epic} — {self.title()}. Its current tasks: `{TK} --readonly list --parent {self.epic}`. The design is at `{self.state.art('staged_design') or self.state.art('design') or '(the ARCHITECTURE: note)'}`; the SPEC INDEX at `{self.state.art('spec_index') or '(none)'}`; the survey at `{self.state.art('survey') or '(reused)'}`.\n"
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
        plan = self.state.art("plan")
        prompt = f"AUDIT the plan for epic {self.epic} at `{plan}` — its task set, acceptance criteria and SURFACE: lines — against the standard. Return the AUDIT return contract: `VERDICT: PASS` or `VERDICT: FAIL` first, findings tagged blocking|filed. A gap written down (an open question, a decision task, a stated deferral) is a PASS; the same gap silent is a FAIL.\n"
        n = self.state.data["audit_attempts"] + 1
        out = self.out_dir / f"audit-{n}.md"
        raw, text = _dispatch("analyst", prompt, self.epic, out, self.runner, self.cwd, self.dispatch_fn)
        if not self.judged("dispatch analyst (audit)", raw, text, verdict_mod.VERDICT):
            return
        v = verdict_mod.parse(text)
        self.state.data["audit_attempts"] = n
        self.state.save()
        self.results.append(Result(f"dispatch analyst (audit {n})", OK if v.status == verdict_mod.PASS else FAIL, f"{v.status} — {v.blocking} blocking, {v.filed} filed\nfull: {out}", raw))
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
        if code == EXIT_PARKED:
            self.sync_park()
        return self.report(code), code

    def sync_park(self) -> None:
        """A park leaves tracker state behind — the gate, the PARKED note, the `decision`
        or `REQUIREMENT:` tasks it filed, the staged spec index or proposal — and the
        next session (or the owner answering the decision) reads it from the export.
        Committed and pushed HERE, as `halt.sh pause` does, because the sequencer knows it
        parked. Measured (the first orchestrated wavelab run of 0.10.28): told only "4
        parked — move to the next epic", the orchestrator spent 16 of its 26 turns reading
        preflight.py, campaign_auto.py and tracker_sync.py to decide what to commit and
        whether autosync would be restored. autosync is NOT restored here: `campaign.sh`
        (auto) and §5 (interactive) own that, as for every wave."""
        from . import tracker_sync

        export, raw = tracker_sync.export_path(self.runner, self.cwd)
        if raw.error or (raw.ran and raw.returncode != 0):
            self.results.append(Result("tk.sh backend --json", FAIL, "cannot learn the tracked export path — the park is recorded but NOT committed:\n" + failure_detail(raw), raw))
            return
        extra = []
        folder = self.folder()
        if folder and folder.is_dir():
            extra.append(str(folder.relative_to(self.cwd)) if folder.is_absolute() else str(folder))
        self.results += tracker_sync.sync(
            message=f"chore(tracker): park {self.epic} at {self.stop[1]}", epics=[self.epic], export=export, extra_paths=extra,
            push=self.push, stop_if_upstream_moved=False, restore_autosync=False, project=self.project, runner=self.runner, cwd=self.cwd,
        )

    def report(self, code: int) -> str:
        out = [f"plan-epic {self.epic} (MODE={self.mode})", render(self.results)]
        if code == EXIT_OK:
            out.append("\nPLANNED — the DAG is in the tracker; `tk.sh validate` and the view ran with the apply. Next: §4, `wave-plan.sh <lane> --parent " + self.epic + "`.")
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
            out.append("\nCOULD NOT JUDGE — a dispatch did not return a verdict. Nothing was approved; re-run the same stage (`--from`) once the cause is fixed.")
        else:
            out.append(f"\nSTOPPED at {self.stop[1]} — resolve the [FAIL] line and re-run `plan-epic.sh {self.epic} --from {self.stop[1]}`.")
        return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .project import ProjectError, load

    ap = argparse.ArgumentParser(prog="plan-epic.sh", description="campaign-loop §3 as a sequencer: survey → fold-in ① → architect → stage → planner → audit (≤2) → gate → apply. Interactive stops at the two approvals (exit 6); auto self-approves and parks on absent scope or an open decision.")
    ap.add_argument("epic")
    ap.add_argument("--mode", choices=["auto", "interactive"], default="interactive")
    ap.add_argument("--from", dest="start", choices=STAGES, default="survey", help="resume at this stage (the state file carries the earlier artefacts)")
    ap.add_argument("--triage", choices=["UNPLANNED", "PARTIAL", "READY"], default=None, help="what epic-queue.sh said; READY asks the architect for a sanity-check")
    ap.add_argument("--reset", action="store_true", help="forget the state file first — plan from nothing")
    ap.add_argument("--no-push", action="store_true", help="a park commits the tracker state but does not push it")
    args = ap.parse_args(argv)
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


if __name__ == "__main__":
    raise SystemExit(main())
