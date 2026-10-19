# Language Is a Security Surface for Agent Guards

**Hasan Demirkiran and Jens Ernstberger** ([Kontext](https://kontext.security))

[arXiv:2610.22244](https://arxiv.org/abs/2610.22244) · Accepted at the NeurIPS 2026 Workshop on Agents in the Wild: Safety, Security, and Beyond (AIWILD).

Code, frozen rewrite files (translations and paraphrases with hashes, generation traces, judge outputs and review decisions), materialiser, guard prompts and adapters, aggregate results and all per-row verdicts for the paper. Sibling repositories: [toolrosetta](https://github.com/hasandemirkiran/toolrosetta) and [shortcut-sensitivity-agent-safety-evaluations](https://github.com/hasandemirkiran/shortcut-sensitivity-agent-safety-evaluations).

## Per-row verdicts

`results/raw/<guard>/<condition>/r<k>.jsonl.gz` holds one JSON row per guard call (401 files, 493,640 rows): `sample_id`, `trajectory_id`, `segment_id`, `instruction_id`, `domain`, `label`, `condition`, `guard`, `model`, `prompt_version`, `prompt_sha`, `repeat`, `temperature`, `instruction`, `assessment`, `composite`, `block`, `usage`, `latency_s`, `raw_text`, `error`. Rows with a non-null `error` are failed calls that the analysis skips. The analysis code reads uncompressed files: run `gunzip -rk results/raw` first, then follow the `analyze` step below. TS-Bench trajectories are not redistributed; they are fetched from the TS-Bench release at the pinned commit in `data/manifest.json`.

## How to cite

```bibtex
@article{demirkiran2026language,
  title   = {Language Is a Security Surface for Agent Guards},
  author  = {Demirkiran, Hasan and Ernstberger, Jens},
  journal = {arXiv preprint arXiv:2610.22244},
  year    = {2026},
  note    = {NeurIPS 2026 Workshop on Agents in the Wild (AIWILD)}
}
```

## License

Code, scripts, documentation and derived aggregate results in this repository are released under the MIT License (see `LICENSE`). Upstream benchmarks, model weights and provider responses remain under their own terms.

---

Does a step-level agent guard's verdict depend on how the user *phrased* the request?

We take TS-Bench AgentDojo-Traj (1,220 labelled tool-invocation steps, 58 unique user
instructions), rewrite **only the user instruction** — English paraphrases in five styles and
German / French / Turkish translations — keep every tool call, observation and label fixed,
and re-run guards. Because the trajectory and labels never change, any change in a guard's
decision is attributable to the wording of the trusted channel alone.

Guards, all on one Google Cloud project: managed (Gemini 3.8 Flash, Claude Sonnet 5, Llama 4
Maverick, GLM 5.2, gpt-oss-120b, Gemma 4 26B), self-deployed on dedicated vLLM endpoints
(Qwen 3.8 27B, Nemotron 3.5 Lightning 30B, and the specialized TS-Guard), and a *scanner*
control that never sees the user request and therefore must be invariant.

Sibling of the shortcut audit ([shortcut-sensitivity-agent-safety-evaluations](https://github.com/hasandemirkiran/shortcut-sensitivity-agent-safety-evaluations)) (which perturbs the *untrusted* channel: injection
markers and tool names). This project perturbs the *trusted* channel with meaning-preserving
edits.

## Research questions

1. **Invariance.** What fraction of per-step verdicts flip under a semantics-preserving
   rewording or translation of the user request, over and above the formatting placebo?
2. **Error shift.** Do flips add false positives (benign steps blocked) or misses (attacks
   allowed)? Report ΔFPR / ΔTPR with trajectory-clustered bootstrap CIs.
3. **Ranking stability.** Does the guard leaderboard (balanced accuracy) reorder across
   conditions? (Kendall τ vs. the original wording.)
4. **Mechanism.** Alignment guards (read the request) vs. scanner control (don't) vs. the
   restatement-leakage stratum (the agent's own Thought restates the request in English).
5. **Mitigation.** Does translate-then-guard recover the original error rates?

## Pipeline

```
fetch ──► inventory ──► perturb ──► validate ──► materialize ──► run ──► analyze
 pinned    58 unique     Gemini 3.8   Sonnet 5       TS-Bench       Agent     flips, ΔFPR/ΔTPR,
 TS-Bench  instructions  Flash, slot  judge + back-  schema, only   Platform  bootstrap CIs,
 files     + covariates  checks       translation    instruction    guards    Kendall τ, strata
                                      + manual CSV   changed
                                                                      └─ budget brake + cost ledger
```

```bash
make setup                       # venv + deps (needs the [vertex] extra for real runs)
make fetch inventory             # copies ToolSafe-Lab's pinned files if present, else downloads
export GOOGLE_CLOUD_PROJECT=...  # + `gcloud auth application-default login`
make verify-models               # pings every model id in configs/models.yaml
#   write data/perturbations/human/para_human.csv (instruction_id,text — 58 rows) BEFORE generating
make perturb                     # typos (programmatic), 4 LLM styles, human import, 3 translations
make validate                    # two judges (Sonnet 5, Gemini 3.1 Pro) + review sheet of EVERY row
#   ... review data/perturbations/manual_review.csv (decision: accept | repair | reject) ...
make review materialize          # applies decisions, writes data/conditions/<cond>/<domain>.json
make run                         # managed + self-deployed guards (endpoint ids in models.yaml)
make analyze                     # results/summary.md + summary.json
make cost                        # spend ledger (tokens + endpoint hours) + full-design estimate
```

How paraphrases are produced and validated — the part of the study reviewers will attack —
is specified in [docs/PARAPHRASE_DESIGN.md](docs/PARAPHRASE_DESIGN.md).

### Self-deployed guards (Model Garden → vLLM endpoint)

For Qwen 3.8 27B, Nemotron 3.5 Lightning and TS-Guard:

```bash
python scripts/endpoints.py deploy qwen3.8-27b            # prints the gcloud command (max-model-len 32768, FP8)
python scripts/endpoints.py deploy qwen3.8-27b --execute  # runs it (≈10–20 min)
python scripts/endpoints.py sync                          # writes endpoint id + dedicated DNS into models.yaml
python scripts/endpoints.py list                          # uptime and $ so far per endpoint
python scripts/endpoints.py undeploy --all --yes          # stop billing; then `guard-invariance cost --add-hours ...`
```

The console route works too (Model Garden → card → **Edit settings → max model length
32768** → Deploy → name the endpoint `gi-<guard id>` so `sync` finds it). The deploy panel's
"Equivalent code" button is the authoritative reference if a gcloud flag name differs.
TS-Guard is called with the authors' XML prompt (`prompt_version: ts_guard_native`, sha
asserted against ToolSafe-Lab's pinned template) and greedy decoding.

### Spend control

Every hosted call records token usage on its JSONL row. `guard-invariance cost` prices
those with `configs/models.yaml → pricing` (Agent Platform price sheet, read 2026-09-03; Gemini
Flash is on introductory pricing until 2026-12-31) and writes `results/cost_ledger.json`. `run` refuses to start when ledger + pre-run
estimate exceed `budget.budget_usd` (default $5,000; `--force` overrides the estimate check
only) and stops mid-run once recorded spend crosses the cap. Mock rows never count.

The ledger only sees calls made through this repo. Add a Google Cloud **billing budget
alert** (Billing → Budgets & alerts) as the backstop; note that Cloud budgets alert but do
not stop spend. At current prices the full design (7 managed guards × 12 conditions × 3 repeats plus
~20 GPU-hours of self-deployed endpoints, all 1,220 steps) estimates at ≈ $800, dominated by
the Sonnet 5 ceiling guard. The real risk is idle endpoints: three RTX PRO 6000 endpoints left
up for a week ≈ $2.5k — `python scripts/endpoints.py list` shows running cost, `undeploy` stops it.

Offline smoke test of the whole pipeline (no cloud):

```bash
.venv/bin/guard-invariance perturb placebo_whitespace para_typos para_casual trans_de --mock
.venv/bin/guard-invariance materialize original placebo_whitespace para_typos para_casual trans_de
.venv/bin/guard-invariance run --guards gemini-3.8-flash ts-guard gemini-3.8-flash-scanner \
    --conditions original placebo_whitespace para_typos para_casual trans_de --repeats 2 --limit 200 --mock
.venv/bin/guard-invariance analyze --n-boot 200
make clean-mock
```

### TS-Guard on the Mac (fallback)

Materialized condition files use the exact TS-Bench schema, so the ToolSafe-Lab MLX build
runs unchanged if you'd rather not deploy:

```bash
../ToolSafe-Lab/tmp/mlx-venv/bin/python scripts/run_ts_guard_mlx.py \
    --condition trans_de --model-path ../ToolSafe-Lab/artifacts/models/<ts_guard_mlx_q8_dir>
```

## Layout

```
docs/PARAPHRASE_DESIGN.md   what "task-equivalent" means, candidate checks, judges, freeze
configs/conditions.yaml     controls, programmatic typos, 4 LLM styles, human, 3 languages + generation bounds
configs/models.yaml         generator / judges / guards (managed + self-deployed), pricing, budget
data/manifest.json          pinned TS-Bench files (commit + sha256)
data/inventory/             58 instructions + per-step covariates (leakage, first-step)   [generated]
data/perturbations/         <cond>.jsonl, validation_<cond>.jsonl, manual_review.csv     [generated]
data/conditions/<cond>/     TS-Bench-schema files with only `instruction` replaced       [generated]
results/raw/<guard>/<cond>/r<k>.jsonl   one row per step per repeat, full assessment     [generated]
results/summary.{json,md}   analysis report                                              [generated]
results/cost_ledger.json    recorded spend by model / guard                                        [generated]
src/guard_invariance/       fetch · inventory · slots · perturb · validate · materialize · guard_prompt · vertex · run_guard · cost · analyze · cli
scripts/                    verify_models.py, run_ts_guard_mlx.py
PROTOCOL.md                 pre-registration draft; freeze before the first non-mock guard run
```

## Data facts (TS-Bench AgentDojo-Traj, commit 46358fa)

1,220 steps (868 safe / 352 unsafe), 58 unique instructions (banking 6, slack 6, travel 6,
workspace 40), 589 trajectories, 57/58 instructions have both labels. ~2.5M input tokens per
full pass per condition. 59.6% of steps restate ≥60% of the instruction's content words in
the history/thought (`leaky` stratum).

## Related work (positioning)

* Rabanser, Kapoor, Narayanan et al., *Towards a Science of AI Agent Reliability* (ICML 2026):
  paraphrase robustness of **agents** on τ-bench/GAIA. We measure **guards**, with labels held
  fixed by construction.
* Mizrahi et al. (TACL 2024), Lunardi et al. (ECAI 2025): multi-prompt evaluation of LLMs;
  paraphrases shift absolute scores, sometimes rankings. Same question, agent-guard setting.
* ToolSafe / TS-Bench / TS-Guard (2026), TraceSafe (COLM 2026), LlamaFirewall AlignmentCheck
  (2025), Task Shield (2024): the guards and benchmarks under test.
* The shortcut audit ([shortcut-sensitivity-agent-safety-evaluations](https://github.com/hasandemirkiran/shortcut-sensitivity-agent-safety-evaluations)): untrusted-channel cue interventions on the same cohort.

## License

MIT. TS-Bench is MIT (MurrayTom/ToolSafe); AgentDojo is MIT.
