PY ?= .venv/bin/python
SYSPY ?= $(shell command -v python3.12 || command -v python3.13 || command -v python3.11 || command -v python3.10 || command -v python3)
GI ?= .venv/bin/guard-invariance
PARA = para_typos para_lexical para_terse para_verbose_polite para_casual para_human
TRANS = trans_de trans_fr trans_tr trans_sw trans_am
CONTROLS = original placebo_whitespace
ALL = $(CONTROLS) $(PARA) $(TRANS)
GUARDS ?= gemini-3.8-flash claude-sonnet-5 llama-4-maverick glm-5.2 gpt-oss-120b gemma-4-26b qwen3.8-27b nemotron-3.5-lightning-30b ts-guard gemini-3.8-flash-scanner
REPEATS ?= 3

.PHONY: setup fetch inventory perturb validate review materialize run analyze cost test verify-models endpoints clean-mock

cost:
	$(GI) cost --plan $(GUARDS) --repeats $(REPEATS) --n-conditions 12

setup:
	$(SYSPY) -m venv --clear .venv && $(PY) -m pip install -U pip && $(PY) -m pip install -e '.[dev,vertex]'
	@echo "next: export GOOGLE_CLOUD_PROJECT=GCP-PROJECT-OLD && gcloud auth application-default login && make verify-models"

fetch:
	$(GI) fetch --from-local ../ToolSafe-Lab/data/raw/eval/agentdojo || $(GI) fetch

inventory:
	$(GI) inventory

verify-models:
	$(PY) scripts/verify_models.py

perturb:
	$(GI) perturb placebo_whitespace $(PARA) $(TRANS)

validate:
	$(GI) validate --judge primary $(PARA) $(TRANS)
	$(GI) validate --judge secondary $(PARA) $(TRANS)
	$(GI) validate --export-review --all $(PARA) $(TRANS)

# after editing data/perturbations/manual_review.csv (decision: accept|repair|reject)
review:
	$(GI) validate --apply-review data/perturbations/manual_review.csv

materialize:
	$(GI) materialize $(ALL)

run:
	$(GI) run --guards $(GUARDS) --conditions $(ALL) --repeats $(REPEATS) --concurrency 6

analyze:
	$(GI) analyze

test:
	$(PY) -m pytest -q

clean-mock:
	rm -rf data/perturbations data/conditions results/raw results/summary.* results/cost_ledger.json

# self-deployed endpoints: make endpoints CMD="deploy qwen3.8-27b --execute" | CMD=list | CMD=sync | CMD="undeploy --all --yes"
endpoints:
	$(PY) scripts/endpoints.py $(CMD)
