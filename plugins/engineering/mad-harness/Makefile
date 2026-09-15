# MAD harness — the plugin's own targets.
#
# These operate on the HARNESS ITSELF. A consuming project drives the harness
# through its own Makefile, pointing HARNESS at this directory; see README.md.
#
# The harness is its own first consumer: harness.yaml describes this repository,
# so `make project` and the test suite exercise the real schema rather than a
# fixture free to drift from it.

PY ?= uv run python

.PHONY: help test conformance lint fmt project skills refs models commands docs check harness-test harness-lint models-sync models-cost

help:
	@echo "test     the harness's own suite (self-hosted against harness.yaml)"
	@echo "conformance  the TaskStore contract, against every backend's real binary"
	@echo "lint     ruff check"
	@echo "fmt      ruff format"
	@echo "project  config valid, stacks present, hard-coded sites agree"
	@echo "skills   skill declarations resolve, preload bills within budget"
	@echo "refs     every plugin path a skill, command or agent names exists"
	@echo "models   every agent's tier resolves, no frontmatter drift"
	@echo "commands each stack's declared commands still work (--repair to self-heal)"
	@echo "check    all of the above — what CI runs"

test:
	cd harness && $(PY) -m pytest

# Drives the real tracker binary, so it is slower than `test` and lives outside it.
# Run this after touching an adapter — the fast suite cannot see a CLI that changed.
conformance:
	cd harness && $(PY) -m pytest -m conformance

lint:
	cd harness && $(PY) -m ruff check .

fmt:
	cd harness && $(PY) -m ruff format .

project:
	./harness/checks/check-project-config.sh

skills:
	./harness/checks/check-skills.sh

refs:
	./harness/checks/check-script-refs.sh

models:
	./harness/checks/check-model-config.sh

commands:
	./harness/checks/check-stack-commands.sh

# Aliases the docs and error messages use. models-sync repairs the drift that
# check-model-config.sh reports, so the command it tells you to run must exist.
harness-test: test
harness-lint: lint

models-sync:
	./harness/checks/check-model-config.sh --write

models-cost:
	cd harness && $(PY) -m models.report

prose:
	./harness/checks/check-prose.sh

# The docs' enumerable parts are generated from the code they describe. Hand-maintained
# mirrors drift invisibly — the page still reads correctly, it is simply wrong.
docs:
	./harness/checks/check-docs.sh

conventions:
	./harness/checks/check-conventions-mirror.sh

# A REPORT, deliberately not in `check` — a hit needs reading, not failing.
domain:
	./harness/checks/domain-report.sh

check: lint test project skills refs models prose commands docs
	@echo "OK — harness green."
