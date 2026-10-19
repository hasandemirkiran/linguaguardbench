"""Instruction inventory: the perturbation unit is the unique (domain, instruction) pair.

Also computes two stratification covariates used in analysis:
  * restatement leakage – the agent's Thought/history often restates the instruction in
    English; a guard can read intent from there and ignore USER_REQUEST. We record the
    fraction of instruction content words present in history+current_action per step.
  * first-step share – segment 1 steps have the least history and therefore the least
    leakage.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from guard_invariance.data import Step, write_jsonl
from guard_invariance.slots import extract_slots

WORD = re.compile(r"[a-z0-9']{4,}")


def restatement_overlap(step: Step) -> float:
    words = set(WORD.findall(step.raw_instruction.lower()))
    if not words:
        return 0.0
    hay = (step.history + " " + step.current_action).lower()
    return sum(w in hay for w in words) / len(words)


def build_inventory(steps: list[Step], *, leak_threshold: float = 0.6) -> list[dict]:
    groups: dict[str, list[Step]] = defaultdict(list)
    for s in steps:
        groups[s.instruction_id].append(s)
    rows = []
    for iid, group in groups.items():
        first = group[0]
        overlaps = [restatement_overlap(s) for s in group]
        rows.append(
            {
                "instruction_id": iid,
                "domain": first.domain,
                "instruction": first.raw_instruction,
                "n_steps": len(group),
                "n_unsafe": sum(s.unsafe for s in group),
                "n_safe": sum(not s.unsafe for s in group),
                "n_interactions": len({s.interaction_id for s in group}),
                "n_first_steps": sum(s.segment_id == 1 for s in group),
                "leak_rate": sum(o >= leak_threshold for o in overlaps) / len(group),
                "leak_mean_overlap": sum(overlaps) / len(overlaps),
                "slots": extract_slots(first.raw_instruction).as_dict(),
                "chars": len(first.raw_instruction),
            }
        )
    rows.sort(key=lambda r: (r["domain"], r["instruction"]))
    return rows


def step_covariates(steps: list[Step], *, leak_threshold: float = 0.6) -> list[dict]:
    return [
        {
            "sample_id": s.sample_id,
            "instruction_id": s.instruction_id,
            "domain": s.domain,
            "trajectory_id": s.trajectory_id,
            "segment_id": s.segment_id,
            "label": s.label,
            "restatement_overlap": round(restatement_overlap(s), 4),
            "leaky": restatement_overlap(s) >= leak_threshold,
            "context_chars": len(s.history) + len(s.current_action) + len(s.env_info),
        }
        for s in steps
    ]


def write_inventory(steps: list[Step], out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = build_inventory(steps)
    write_jsonl(out_dir / "instructions.jsonl", rows)
    write_jsonl(out_dir / "step_covariates.jsonl", step_covariates(steps))
    summary = {
        "n_steps": len(steps),
        "n_unsafe": sum(s.unsafe for s in steps),
        "n_instructions": len(rows),
        "n_trajectories": len({s.trajectory_id for s in steps}),
        "per_domain": {
            d: {
                "steps": sum(s.domain == d for s in steps),
                "instructions": sum(r["domain"] == d for r in rows),
                "unsafe": sum(s.domain == d and s.unsafe for s in steps),
            }
            for d in sorted({s.domain for s in steps})
        },
        "instructions_with_both_labels": sum(r["n_unsafe"] > 0 and r["n_safe"] > 0 for r in rows),
        "leaky_step_share": round(sum(restatement_overlap(s) >= 0.6 for s in steps) / len(steps), 4),
        "approx_input_tokens_per_pass": int(sum(len(s.history) + len(s.current_action) + len(s.env_info) for s in steps) / 4),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
