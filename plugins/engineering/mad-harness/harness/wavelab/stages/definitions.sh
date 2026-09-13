# Stage fixtures. Each builds a repository state that SHOULD trip one gate, dispatches the
# agent that owns it, and checks the verdict.
#
# THE NEGATIVE CONTROLS ARE NOT OPTIONAL. A survey that answered ABSENT to everything
# would pass every "should park" case by being uniformly pessimistic, and a lens that
# always FAILs would pass every "should fail" case the same way. Each gate is therefore
# tested in both directions.

# Pull the VERDICT token rather than grepping the whole answer. Both directions need this,
# not just the positive one: "ADEQUATE — this is not ABSENT" contains the string ABSENT and
# would pass a substring check for parking, which would make the negative control
# unfalsifiable. The prompts all ask for the verdict on the first line, so the first token
# is the verdict.
verdict_of() {
  printf '%s' "$1" | grep -oiE '\b(ADEQUATE|INFERABLE|ABSENT)\b' | head -1 | tr 'a-z' 'A-Z'
}

expect_verdict() {  # expect_verdict <label> <output> <acceptable|alternatives>
  local label="$1" got; got=$(verdict_of "$2")
  echo "    verdict: ${got:-<none found>}"
  # A regex alternation rather than a case glob: extglob is a PARSE-time option, so
  # enabling it in this file cannot affect this file.
  if [[ "$got" =~ ^($3)$ ]]; then
    echo "  PASS  $label"
  else
    echo "  FAIL  $label (got ${got:-nothing})"; FAILURES=$((FAILURES+1))
  fi
}

stage_adequacy_absent() {
  echo "== adequacy-absent: an epic with a title and nothing else should park =="
  build_repo
  local epic
  epic=$(tk create "Improve notifications" -t epic)
  cat > "$SCRATCH/p.txt" <<EOF
Assess whether this epic is specified well enough to design against.

EPIC $epic — Improve notifications
(the epic carries no description and no acceptance criteria)

The repository is at $REPO. Read what is actually there before answering.

Return your SPECIFICATION-ADEQUACY VERDICT — one of ADEQUATE, INFERABLE or ABSENT — on the
first line, then your evidence.
EOF
  local out; out=$(dispatch analyst-survey "$SCRATCH/p.txt")
  expect_verdict "an unspecified epic parks" "$out" "ABSENT"
  expect "invents no scope" "$out" \
    "cannot|no acceptance|not specified|nothing|absent"
}

stage_adequacy_ok() {
  echo "== adequacy-ok: the control — a specified epic must NOT be parked =="
  build_repo
  local epic
  epic=$(tk create "Normalise contact details" -t epic --description \
"clean_contact passes its input through. Give it real normalisers.

ACCEPTANCE
- normalise_email lowercases and strips its input; '' for None or blank.
- normalise_phone removes spaces, hyphens and brackets, keeping a leading '+'.
- clean_contact applies both to the 'email' and 'phone' keys when present.
- Absent keys are not invented. clean_contact's signature does not change.")
  cat > "$SCRATCH/p.txt" <<EOF
Assess whether this epic is specified well enough to design against.

EPIC $epic. Read it with:
    $HARNESS/tracker/tk.sh show $epic

The repository is at $REPO.

Return your SPECIFICATION-ADEQUACY VERDICT — ADEQUATE, INFERABLE or ABSENT — on the first
line, then your evidence.
EOF
  local out; out=$(dispatch analyst-survey "$SCRATCH/p.txt")
  expect_verdict "a specified epic is not parked" "$out" "ADEQUATE|INFERABLE"
}

stage_lens_tests() {
  echo "== lens-tests: a test that passes with the feature deleted must FAIL the lens =="
  build_repo
  mkdir -p "$REPO/src/wavelab"
  cat > "$REPO/src/wavelab/email.py" <<'EOF'
def normalise_email(value):
    """Lowercase and strip an address; '' for None or blank."""
    if not value:
        return ""
    return value.strip().lower()
EOF
  # DECORATIVE BY CONSTRUCTION: asserts the function returns a string and that a blank
  # input is falsy. Delete the implementation entirely and both still pass.
  cat > "$REPO/tests/test_email.py" <<'EOF'
from wavelab.email import normalise_email


def test_normalise_email_returns_a_string():
    assert isinstance(normalise_email("A@B.com"), str)


def test_normalise_email_handles_blank():
    assert not normalise_email("")
EOF
  ( cd "$REPO" && git add -A && git -c user.email=w@e -c user.name=w \
      commit -q -m "feat(email): add normalise_email" )
  local sha; sha=$(cd "$REPO" && git rev-parse HEAD)
  cat > "$SCRATCH/p.txt" <<EOF
You are lens 2 of 3 — the TEST-QUALITY gate. Judge the tests in commit $sha of the
repository at $REPO.

The change adds \`normalise_email\` in src/wavelab/email.py with tests in
tests/test_email.py. Read both.

Ask the question your doctrine asks: would each test go RED if the implementation were
deleted or broken? Return VERDICT: PASS or FAIL on the first line, then your findings.
EOF
  local out; out=$(dispatch verifier-tests "$SCRATCH/p.txt")
  expect "returns FAIL" "$out" "FAIL"
  expect "names the tests as decorative" "$out" "decorative|would still pass|not assert|isinstance"
}

stage_lens_correctness() {
  echo "== lens-correctness: a change that misses a stated criterion must FAIL =="
  build_repo
  mkdir -p "$REPO/src/wavelab"
  # MISSES ONE CRITERION, DELIBERATELY: it lowercases and strips, but raises on None
  # instead of returning ''. The tests never exercise None, so the suite is green — which
  # is the whole point. A lens that only re-runs the tests cannot see this.
  cat > "$REPO/src/wavelab/email.py" <<'EOF'
def normalise_email(value):
    """Lowercase and strip an address."""
    return value.strip().lower()
EOF
  cat > "$REPO/tests/test_email.py" <<'EOF'
from wavelab.email import normalise_email


def test_lowercases_and_strips():
    assert normalise_email("  A@B.COM ") == "a@b.com"


def test_blank_stays_blank():
    assert normalise_email("") == ""
EOF
  ( cd "$REPO" && git add -A && git -c user.email=w@e -c user.name=w \
      commit -q -m "feat(email): add normalise_email" )
  local sha; sha=$(cd "$REPO" && git rev-parse HEAD)
  cat > "$SCRATCH/p.txt" <<EOF
You are lens 1 of 3 — the CORRECTNESS gate. Judge commit $sha in the repository at $REPO.

The task's acceptance criteria were:
  AC1 normalise_email lowercases and strips its input.
  AC2 It returns '' for None or a blank string, rather than raising.

Confirm each criterion is met BY THE CODE, not merely claimed. The suite is green; that is
not the question. Return VERDICT: PASS or FAIL on the first line, then AC1/AC2 with the
file and line that shows each.
EOF
  local out; out=$(dispatch verifier "$SCRATCH/p.txt")
  expect "returns FAIL" "$out" "FAIL"
  expect "names the unmet criterion" "$out" "AC2|None"
  expect "does not hide behind a green suite" "$out" "raise|TypeError|AttributeError|not met|NOT MET"
}

stage_lens_spec() {
  echo "== lens-spec: code contradicting its own doc must FAIL =="
  build_repo
  mkdir -p "$REPO/docs" "$REPO/src/wavelab"
  cat > "$REPO/docs/contacts.md" <<'EOF'
# Contact handling

## Behaviour

`clean_contact` returns a NEW dictionary and never mutates its argument. Callers rely on
this: the raw record is kept for the audit trail and must survive cleaning unchanged.
EOF
  # CONTRADICTS THE DOC: mutates in place and returns the same object.
  cat > "$REPO/src/wavelab/contact.py" <<'EOF'
def clean_contact(raw: dict) -> dict:
    """Clean a contact record."""
    if "email" in raw:
        raw["email"] = raw["email"].strip().lower()
    return raw
EOF
  ( cd "$REPO" && git add -A && git -c user.email=w@e -c user.name=w \
      commit -q -m "feat(contact): normalise the email field" )
  cat > "$SCRATCH/p.txt" <<EOF
You are lens 3 of 3 — the SPEC, DOCS and BLAST-RADIUS gate. The repository is at $REPO.

A task has just landed: "clean_contact normalises the email field". You are given the task
and the repository at HEAD, and deliberately NOT the diff.

Ask what must now be true if that task is done, and check the corpus. Return
VERDICT: PASS or FAIL on the first line, then your findings with the file that shows each.
EOF
  local out; out=$(dispatch verifier-spec "$SCRATCH/p.txt")
  expect "returns FAIL" "$out" "FAIL"
  expect "names the contradicted doc" "$out" "contacts\.md|docs/"
  expect "names the contradiction" "$out" "mutat|in place|new dict|same object"
}

stage_plan_contention() {
  echo "== plan-contention: two tasks on one file must not share a wave =="
  build_repo
  mkdir -p "$REPO/src/wavelab"
  cat > "$REPO/src/wavelab/contact.py" <<'EOF'
def clean_contact(raw: dict) -> dict:
    """Assemble a cleaned contact record."""
    return dict(raw)
EOF
  ( cd "$REPO" && git add -A && git -c user.email=w@e -c user.name=w commit -q -m "base contact" )
  local epic
  epic=$(tk create "Clean two fields" -t epic --description "Both changes edit clean_contact.")
  tk create "Normalise the email field inside clean_contact" --parent "$epic" \
    --description "Edit src/wavelab/contact.py: lowercase and strip raw['email'] when present." >/dev/null
  tk create "Normalise the phone field inside clean_contact" --parent "$epic" \
    --description "Edit src/wavelab/contact.py: strip punctuation from raw['phone'] when present." >/dev/null
  cat > "$SCRATCH/p.txt" <<EOF
Plan the wave for epic $epic in the repository at $REPO.

Read its children with:
    $HARNESS/tracker/tk.sh list --parent $epic --json

Produce the file-contention matrix and say which tasks may be dispatched in the SAME wave
and which must wait. Be explicit about any file two tasks both touch.
EOF
  local out; out=$(dispatch planner "$SCRATCH/p.txt")
  expect "identifies the shared file" "$out" "contact\.py"
  expect "serialises rather than parallelising" "$out" \
    "next wave|serial|cannot.*same wave|not.*same wave|wave 2|separate wave|sequential"
}
