"""TS-Bench AgentDojo-Traj loading.

Each raw file is a JSON list of step dicts with keys
  id-interaction, id-segment, instruction, history, current_action, env_info, score
`score` is the released step label: 1.0 = unsafe (harmful tool invocation), 0.0 = safe.

We keep the raw dict on every Step so materialized condition files reproduce the
original structure byte-for-byte except for the `instruction` value.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

DOMAINS = ("banking", "slack", "travel", "workspace")
RAW_KEYS = ("id-interaction", "id-segment", "instruction", "history", "current_action", "env_info", "score")


def instruction_id(domain: str, instruction: str) -> str:
    """Stable short id for a (domain, instruction) pair. Two workspace tasks share text
    across variants, so the domain is part of the key."""
    digest = hashlib.sha1(f"{domain}\x1f{instruction}".encode("utf-8")).hexdigest()
    return f"{domain[:2]}-{digest[:10]}"


@dataclass
class Step:
    domain: str
    interaction_id: int
    segment_id: int
    instruction: str
    history: str
    current_action: str
    env_info: str
    label: float
    raw: dict = field(repr=False)

    @property
    def sample_id(self) -> str:
        return f"{self.domain}:{self.interaction_id}:{self.segment_id}"

    @property
    def trajectory_id(self) -> str:
        return f"{self.domain}:{self.interaction_id}"

    @property
    def instruction_id(self) -> str:
        return instruction_id(self.domain, self.raw_instruction)

    @property
    def raw_instruction(self) -> str:
        """The ORIGINAL benchmark instruction, even after materialization replaced
        `instruction`. Materialized files carry it in `_original_instruction`."""
        return self.raw.get("_original_instruction", self.raw["instruction"])

    @property
    def unsafe(self) -> bool:
        return self.label == 1.0


def load_domain(path: Path, domain: str) -> list[Step]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected a JSON list")
    steps: list[Step] = []
    for row in rows:
        missing = [k for k in RAW_KEYS if k not in row]
        if missing:
            raise ValueError(f"{path}: row missing keys {missing}")
        steps.append(
            Step(
                domain=domain,
                interaction_id=int(row["id-interaction"]),
                segment_id=int(row["id-segment"]),
                instruction=row["instruction"],
                history=row["history"],
                current_action=row["current_action"],
                env_info=row["env_info"],
                label=float(row["score"]),
                raw=row,
            )
        )
    return steps


def load_all(root: Path, domains: Iterable[str] = DOMAINS) -> list[Step]:
    """Load every domain file under `root` (a directory with <domain>.json files)."""
    steps: list[Step] = []
    for domain in domains:
        path = Path(root) / f"{domain}.json"
        if not path.exists():
            raise FileNotFoundError(path)
        steps.extend(load_domain(path, domain))
    return steps


def dump_domain(steps: list[Step], path: Path) -> None:
    """Write steps back in the original TS-Bench structure (list of raw dicts)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [s.raw for s in steps]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path: Path, row: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)
