"""Fetch pinned TS-Bench AgentDojo-Traj files (or copy them from a local ToolSafe-Lab checkout)
and verify sha256 against data/manifest.json."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import requests


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(manifest_path: Path) -> dict:
    return json.loads(Path(manifest_path).read_text(encoding="utf-8"))


def fetch(manifest_path: Path, raw_root: Path, *, from_local: Path | None = None, force: bool = False) -> list[dict]:
    """Populate raw_root/<asset.path> for every manifest asset.

    from_local: directory holding <domain>.json files (e.g. ToolSafe-Lab/data/raw/eval/agentdojo).
    Returns a list of {path, sha256, ok} records.
    """
    manifest = load_manifest(manifest_path)
    raw_root = Path(raw_root)
    report = []
    for asset in manifest["assets"]:
        target = raw_root / asset["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not force and sha256_of(target) == asset["sha256"]:
            report.append({"path": asset["path"], "sha256": asset["sha256"], "ok": True, "source": "cached"})
            continue
        if from_local is not None:
            src = Path(from_local) / f"{asset['domain']}.json"
            if not src.exists():
                raise FileNotFoundError(src)
            shutil.copyfile(src, target)
            source = str(src)
        else:
            resp = requests.get(asset["source"], timeout=120)
            resp.raise_for_status()
            target.write_bytes(resp.content)
            source = asset["source"]
        digest = sha256_of(target)
        ok = digest == asset["sha256"]
        if not ok:
            raise ValueError(
                f"sha256 mismatch for {asset['path']}: expected {asset['sha256']}, got {digest}"
            )
        report.append({"path": asset["path"], "sha256": digest, "ok": ok, "source": source})
    (raw_root / "FETCH_REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
