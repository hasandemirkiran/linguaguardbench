"""Materialize a condition: replace ONLY the `instruction` field of every step and write the
result in the exact TS-Bench schema so any existing guard runner (ToolSafe-Lab, ToolSafe's
guardian_experiment.py, guardian-bench) can consume it unchanged.

Each materialized row also carries two extra keys, `_original_instruction` and
`_condition`, so downstream code can recover the pairing. Runners that validate the schema
strictly can drop keys starting with "_".
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from guard_invariance.data import DOMAINS, Step, dump_domain, load_all, read_jsonl

CONTROL_CONDITIONS = {"original", "placebo_whitespace"}


def placebo_whitespace(text: str) -> str:
    return re.sub(r" {2,}", " ", text).rstrip() + " "


def load_perturbations(path: Path) -> dict[str, str]:
    """perturbations/<condition>.jsonl rows: {instruction_id, text, accepted: bool, ...}"""
    out = {}
    for row in read_jsonl(path):
        if row.get("accepted", True):
            out[row["instruction_id"]] = row["text"]
    return out


def materialize(
    condition: str,
    raw_root: Path,
    out_root: Path,
    perturbation_file: Path | None = None,
    *,
    strict: bool = True,
) -> dict:
    """Write out_root/<condition>/<domain>.json. Returns a small report."""
    steps = load_all(Path(raw_root) / "agentdojo")
    if condition == "original":
        mapping = {s.instruction_id: s.raw_instruction for s in steps}
    elif condition == "placebo_whitespace" and (perturbation_file is None or not Path(perturbation_file).exists()):
        mapping = {s.instruction_id: placebo_whitespace(s.raw_instruction) for s in steps}
    else:
        if perturbation_file is None:
            raise ValueError("perturbation_file is required for non-control conditions")
        mapping = load_perturbations(perturbation_file)

    out_dir = Path(out_root) / condition
    missing, n_rows = set(), 0
    per_domain: dict[str, list[Step]] = {d: [] for d in DOMAINS}
    for s in steps:
        new_text = mapping.get(s.instruction_id)
        if new_text is None:
            missing.add(s.instruction_id)
            if strict:
                continue
            new_text = s.raw_instruction
        raw = copy.deepcopy(s.raw)
        raw["_original_instruction"] = s.raw_instruction
        raw["_condition"] = condition
        raw["instruction"] = new_text
        per_domain[s.domain].append(
            Step(s.domain, s.interaction_id, s.segment_id, new_text, s.history, s.current_action, s.env_info, s.label, raw)
        )
        n_rows += 1
    for domain, rows in per_domain.items():
        dump_domain(rows, out_dir / f"{domain}.json")
    report = {
        "condition": condition,
        "n_rows": n_rows,
        "n_instructions_missing": len(missing),
        "missing_instruction_ids": sorted(missing),
    }
    (out_dir / "MATERIALIZE_REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def verify_invariance(raw_root: Path, cond_root: Path, condition: str) -> dict:
    """Assert that every non-instruction field and every label is unchanged."""
    original = {s.sample_id: s for s in load_all(Path(raw_root) / "agentdojo")}
    cond = load_all(Path(cond_root) / condition)
    problems = []
    for s in cond:
        o = original[s.sample_id]
        for key in ("history", "current_action", "env_info"):
            if o.raw[key] != s.raw[key]:
                problems.append((s.sample_id, key))
        if o.label != s.label:
            problems.append((s.sample_id, "score"))
        if s.raw["_original_instruction"] != o.raw_instruction:
            problems.append((s.sample_id, "_original_instruction"))
    changed = sum(s.instruction != original[s.sample_id].raw_instruction for s in cond)
    return {"condition": condition, "n_rows": len(cond), "n_instruction_changed": changed, "problems": problems[:20], "ok": not problems}
