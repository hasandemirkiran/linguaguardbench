"""Run TS-Guard (MurrayTom/TS-Guard, MLX quantized) over a materialized condition and write
results in guard-invariance's JSONL format.

Reuses ToolSafe-Lab's pinned prompt template and released parser so the run is
byte-comparable with the shortcut-audit TS-Guard runs. Run inside ToolSafe-Lab's MLX venv:

  ../ToolSafe-Lab/tmp/mlx-venv/bin/python scripts/run_ts_guard_mlx.py \
      --condition original --model-path ../ToolSafe-Lab/artifacts/models/<ts_guard_mlx_q8_dir> \
      [--limit N] [--max-tokens 512]

Greedy decoding (temperature 0) so repeats are deterministic; `--repeat` only labels the file.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guard_invariance.data import append_jsonl, load_all, read_jsonl  # noqa: E402
from guard_invariance.run_guard import BLOCK_THRESHOLD, result_path  # noqa: E402

try:
    from toolsafe_lab.ts_guard_local import TS_GUARD_PROMPT, TS_GUARD_PROMPT_VERSION, parse_ts_guard_output, prompt_sha256
except ImportError as exc:  # pragma: no cover
    sys.exit(f"toolsafe_lab not importable ({exc}); run inside ToolSafe-Lab's venv: `../ToolSafe-Lab/tmp/mlx-venv/bin/python`")


def render(step) -> str:
    agent_action = {"interaction_history": step.history, "current_action": step.current_action}
    return TS_GUARD_PROMPT.format(user_request=step.instruction, agent_action=agent_action, env_info=step.env_info)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True)
    ap.add_argument("--model-path", required=True)
    ap.add_argument("--guard-id", default="ts-guard-q8")
    ap.add_argument("--repeat", type=int, default=0)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--max-tokens", type=int, default=512)
    a = ap.parse_args()

    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = load(a.model_path)
    sampler = make_sampler(temp=0.0)
    steps = sorted(load_all(ROOT / "data" / "conditions" / a.condition), key=lambda s: (s.domain, s.interaction_id, s.segment_id))
    if a.limit:
        steps = steps[: a.limit]
    out = result_path(ROOT / "results", a.guard_id, a.condition, a.repeat)
    done = {r["sample_id"] for r in read_jsonl(out) if not r.get("error")}
    todo = [s for s in steps if s.sample_id not in done]
    print(f"{a.condition}: {len(todo)} to do, {len(done)} cached → {out}")
    t0 = time.time()
    for i, s in enumerate(todo, 1):
        prompt = render(s)
        messages = [{"role": "user", "content": prompt}]
        text_in = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        t1 = time.time()
        text = generate(model, tokenizer, prompt=text_in, max_tokens=a.max_tokens, sampler=sampler, verbose=False)
        parsed = parse_ts_guard_output(text)
        row = {
            "sample_id": s.sample_id, "instruction_id": s.instruction_id, "trajectory_id": s.trajectory_id, "domain": s.domain,
            "segment_id": s.segment_id, "label": s.label, "condition": a.condition, "guard": a.guard_id, "model": Path(a.model_path).name,
            "prompt_version": TS_GUARD_PROMPT_VERSION, "prompt_sha": prompt_sha256()[:16], "repeat": a.repeat, "temperature": 0.0,
            "instruction": s.instruction, "assessment": parsed.components, "composite": parsed.prediction,
            "block": (parsed.prediction is not None and parsed.prediction >= BLOCK_THRESHOLD) if parsed.status == "ok" else None,
            "latency_s": round(time.time() - t1, 2), "raw_text": text[:600], "error": None if parsed.status == "ok" else parsed.error_type,
        }
        append_jsonl(out, row)
        if i % 10 == 0:
            print(f"  {i}/{len(todo)} ({(time.time() - t0) / i:.1f}s/step)", flush=True)


if __name__ == "__main__":
    main()
