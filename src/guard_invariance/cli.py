"""Command-line entry point: guard-invariance <subcommand> [...]"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("GUARD_INVARIANCE_ROOT", Path(__file__).resolve().parents[2]))
DATA, CONFIGS, RESULTS = ROOT / "data", ROOT / "configs", ROOT / "results"


def _models_cfg() -> dict:
    return yaml.safe_load((CONFIGS / "models.yaml").read_text(encoding="utf-8"))


def _provider(name: str, cfg: dict, guard: dict | None = None):
    from guard_invariance.vertex import make_provider

    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or (None if str(cfg.get("project", "")).startswith("${") else cfg.get("project"))
    if name == "vertex_endpoint":
        g = guard or {}
        return make_provider(name, project, g.get("location") or cfg.get("location"), endpoint_id=str(g.get("endpoint_id") or ""), dedicated_dns=g.get("dedicated_dns") or None, hf_repo=g.get("hf_repo"))
    # a guard may pin its own region (e.g. Llama 4 MaaS is served from us-east5 only)
    return make_provider(name, project, (guard or {}).get("location") or cfg.get("location"))


def cmd_fetch(a):
    from guard_invariance.fetch import fetch

    rep = fetch(DATA / "manifest.json", DATA / "raw", from_local=Path(a.from_local) if a.from_local else None, force=a.force)
    print(json.dumps(rep, indent=2))


def cmd_inventory(a):
    from guard_invariance.data import load_all
    from guard_invariance.inventory import write_inventory

    print(json.dumps(write_inventory(load_all(DATA / "raw" / "agentdojo"), DATA / "inventory"), indent=2))


def cmd_perturb(a):
    from guard_invariance.data import read_jsonl
    from guard_invariance.perturb import generate_condition, load_conditions

    cfg = _models_cfg()
    cyaml = load_conditions(CONFIGS / "conditions.yaml")
    conds = {c["id"]: c for c in cyaml["conditions"]}
    gen_cfg = cyaml.get("generation", {})
    inv = read_jsonl(DATA / "inventory" / "instructions.jsonl")
    if not inv:
        sys.exit("run `inventory` first")
    gen = cfg["generator"]
    providers = {}

    def prov(name):
        key = "mock" if a.mock else name
        if key not in providers:
            providers[key] = _provider(key, cfg)
        return providers[key]

    for cid in a.conditions:
        cond = conds[cid]
        if cond["id"] == "original":
            continue
        kw = {}
        if cond["kind"] in ("paraphrase", "translate"):
            g = gen["paraphrase"] if cond["kind"] == "paraphrase" else gen["translate"]
            kw = {"provider": prov(g["provider"]), "model": g["model"], "temperature": g.get("temperature", 0.7)}
        elif cond["kind"] == "human":
            kw = {"human_csv": DATA / "perturbations" / "human" / "para_human.csv"}
        rep = generate_condition(cond, gen_cfg, inv, DATA / "perturbations" / f"{cid}.jsonl", dry_run=a.dry_run, limit=a.limit, **kw)
        print(json.dumps(rep))
    _print_spend(cfg)


def _print_spend(cfg: dict) -> None:
    from guard_invariance.cost import write_ledger

    led = write_ledger(ROOT, cfg.get("pricing", {}), cfg.get("budget", {}), cfg)
    live = led["live_endpoints"]
    extra = f" + ${live['usd']:,.2f} on {len(live['endpoints'])} DEPLOYED endpoint(s) still billing" if live["endpoints"] else ""
    print(f"[budget] recorded spend ${led['total_usd']:,.2f}{extra} / ${cfg.get('budget', {}).get('budget_usd', 0):,.0f} → results/cost_ledger.json")


def cmd_cost(a):
    from guard_invariance.cost import add_endpoint_hours, estimate, write_ledger
    from guard_invariance.data import load_all

    cfg = _models_cfg()
    if a.add_hours:
        gid, hours, rate = a.add_hours
        print(json.dumps(add_endpoint_hours(ROOT, guard=gid, hours=float(hours), usd_per_hour=float(rate), note=a.note or "")))
    led = write_ledger(ROOT, cfg.get("pricing", {}), cfg.get("budget", {}), cfg)
    print(json.dumps(led, indent=2))
    if a.plan:
        guards = {g["id"]: g for g in cfg["guards"]}
        raw = DATA / "raw" / "agentdojo"
        if not raw.exists():
            return
        steps = load_all(raw)
        total = 0.0
        print("\nPlanned full design (all 1,220 steps):")
        for gid in a.plan:
            g = guards[gid]
            if g["provider"] == "local_toolsafe_lab":
                continue
            if g["provider"] == "vertex_endpoint":
                hrs = float(g.get("est_hours_per_condition", 1.0)) * a.n_conditions
                usd = hrs * float(g.get("usd_per_hour", 0.0))
                print(f"  {gid:26s} {g.get('model',''):48s} {hrs:6.1f} GPU-h × ${float(g.get('usd_per_hour', 0)):.2f}/h            = ${usd:9,.2f}")
                total += usd
                continue
            est = estimate(steps, g.get("model", ""), cfg.get("pricing", {}), repeats=a.repeats)
            per_cond = est["usd"]
            print(f"  {gid:26s} {g.get('model',''):48s} ${per_cond:8,.2f} per condition × {a.n_conditions} = ${per_cond * a.n_conditions:9,.2f}")
            total += per_cond * a.n_conditions
        print(f"  {'TOTAL':26s} {'':48s} {'':>10s}   ${total:9,.2f}  (repeats={a.repeats})")


def cmd_validate(a):
    from guard_invariance.validate import apply_manual_decisions, export_manual_review, validate_condition, validation_path

    cfg = _models_cfg()
    pdir = DATA / "perturbations"
    if a.apply_review:
        print(json.dumps(apply_manual_decisions(Path(a.apply_review), pdir)))
        return
    if a.export_review:
        rep = export_manual_review(pdir, a.conditions, pdir / "manual_review.csv", everything=a.all)
        print(json.dumps(rep))
        return
    key = "validator" if a.judge == "primary" else "validator_secondary"
    v = cfg["generator"][key]
    provider = _provider("mock" if a.mock else v["provider"], cfg)
    for cid in a.conditions:
        rep = validate_condition(pdir / f"{cid}.jsonl", validation_path(pdir, cid, a.judge), provider=provider, model=v["model"], judge=a.judge, limit=a.limit)
        print(json.dumps(rep))
    _print_spend(cfg)


def cmd_materialize(a):
    from guard_invariance.materialize import materialize, verify_invariance

    for cid in a.conditions:
        pf = None if cid == "original" else DATA / "perturbations" / f"{cid}.jsonl"
        rep = materialize(cid, DATA / "raw", DATA / "conditions", pf, strict=not a.fill_missing)
        chk = verify_invariance(DATA / "raw", DATA / "conditions", cid)
        print(json.dumps({**rep, "invariance_ok": chk["ok"], "instruction_changed": chk["n_instruction_changed"]}))
        if not chk["ok"]:
            sys.exit(f"invariance violated for {cid}: {chk['problems'][:3]}")


def cmd_run(a):
    from guard_invariance.run_guard import run_guard

    from guard_invariance.cost import BudgetBrake, BudgetExceeded, ledger

    cfg = _models_cfg()
    guards = {g["id"]: g for g in cfg["guards"]}
    subset = None
    if a.subset:
        subset = {line.strip() for line in Path(a.subset).read_text().splitlines() if line.strip()}
    budget = cfg.get("budget", {})
    brake = None
    if not a.mock and budget:
        from guard_invariance.cost import live_endpoint_usd

        live = live_endpoint_usd(cfg)
        spent = ledger(ROOT, cfg.get("pricing", {}))["total_usd"] + live["usd"]
        brake = BudgetBrake(budget_usd=budget["budget_usd"], spent_usd=spent, pricing=cfg.get("pricing", {}), warn_at_usd=budget.get("warn_at_usd"), live_fn=lambda: live_endpoint_usd(cfg), live_usd=live["usd"])
        print(f"[budget] spent so far ${spent:,.2f} / ${budget['budget_usd']:,.0f} (incl. ${live['usd']:,.2f} of live endpoint uptime)")
    for gid in a.guards:
        g = guards[gid]
        if g["provider"] == "local_toolsafe_lab":
            print(f"{gid}: run via scripts/run_ts_guard_mlx.py on data/conditions/<condition>/ (see README)")
            continue
        if g["provider"] == "vertex_endpoint" and not a.mock and not g.get("endpoint_id"):
            sys.exit(f"{gid}: set endpoint_id (and location) in configs/models.yaml after deploying it from Model Garden")
        provider = _provider("mock" if a.mock else g["provider"], cfg, g)
        for cid in a.conditions:
            try:
                rep = run_guard(g, cid, cond_root=DATA / "conditions", results_root=RESULTS, provider=provider, repeats=a.repeats, limit=a.limit, subset=subset, concurrency=a.concurrency, temperature=a.temperature, brake=brake, force=a.force)
            except BudgetExceeded as exc:
                sys.exit(f"[budget] {exc}")
            print(json.dumps(rep))
            if rep.get("stopped"):
                sys.exit(1)
    if brake is not None:
        _print_spend(cfg)


def cmd_import(a):
    from guard_invariance.run_guard import import_external

    print(json.dumps(import_external(a.guard, a.condition, Path(a.source), RESULTS, repeat=a.repeat, cond_root=DATA / "conditions")))


def cmd_freeze(a):
    """Pin the reviewed perturbation files: sha256 per file → data/perturbations/FREEZE.json,
    and force-add them to git so they ship as a paper artifact."""
    import hashlib
    import subprocess
    import time

    from guard_invariance.data import read_jsonl

    pdir = DATA / "perturbations"
    files = sorted(pdir.glob("*.jsonl"))
    if not files:
        sys.exit("nothing to freeze")
    entries, problems = [], []
    for f in files:
        rows = read_jsonl(f)
        if not f.name.startswith("validation_"):
            n_acc = sum(r.get("accepted", False) for r in rows)
            reviewed = sum("manual" in r for r in rows)
            is_control = bool(rows) and all(r.get("kind") == "control" for r in rows)  # placebo_whitespace: programmatic, outside the review scope
            entries.append({"file": f.name, "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "rows": len(rows), "accepted": n_acc, "manually_reviewed": reviewed, "control": is_control})
            if a.require_review and reviewed < len(rows) and not is_control:
                problems.append(f"{f.name}: {len(rows) - reviewed} rows without a manual decision")
        else:
            entries.append({"file": f.name, "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "rows": len(rows)})
    if problems:
        sys.exit("refusing to freeze:\n  " + "\n  ".join(problems) + "\n(run the review, or pass --no-require-review for a pilot freeze)")
    freeze = {"frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip(), "files": entries}
    (pdir / "FREEZE.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "-f", *[str(f) for f in files], str(pdir / "FREEZE.json")], cwd=ROOT, check=False)
    print(json.dumps(freeze, indent=2))
    print("\nstaged; commit with: git commit -m 'Freeze perturbations'")


def cmd_analyze(a):
    from guard_invariance.analyze import analyze, write_report

    rep = analyze(RESULTS, covariates_file=DATA / "inventory" / "step_covariates.jsonl", perturbations_dir=DATA / "perturbations", n_boot=a.n_boot)
    write_report(RESULTS, rep)
    print((RESULTS / "summary.md").read_text(encoding="utf-8"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="guard-invariance")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("fetch", help="download pinned TS-Bench AgentDojo-Traj files (or copy from ToolSafe-Lab)")
    s.add_argument("--from-local", help="dir with <domain>.json, e.g. ../ToolSafe-Lab/data/raw/eval/agentdojo")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_fetch)

    s = sp.add_parser("inventory", help="unique instructions + step covariates")
    s.set_defaults(fn=cmd_inventory)

    s = sp.add_parser("perturb", help="generate paraphrases / typos / translations; import human paraphrases")
    s.add_argument("conditions", nargs="+")
    s.add_argument("--limit", type=int)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--mock", action="store_true")
    s.set_defaults(fn=cmd_perturb)

    s = sp.add_parser("validate", help="cross-family equivalence judges + back-translation; manual review export/apply")
    s.add_argument("conditions", nargs="*")
    s.add_argument("--judge", choices=["primary", "secondary"], default="primary")
    s.add_argument("--limit", type=int)
    s.add_argument("--mock", action="store_true")
    s.add_argument("--export-review", action="store_true")
    s.add_argument("--all", action="store_true", help="with --export-review: list every row for a full read-through")
    s.add_argument("--apply-review", help="path to reviewed manual_review.csv")
    s.set_defaults(fn=cmd_validate)

    s = sp.add_parser("materialize", help="write TS-Bench-schema condition files")
    s.add_argument("conditions", nargs="+")
    s.add_argument("--fill-missing", action="store_true", help="fall back to original text for unaccepted instructions")
    s.set_defaults(fn=cmd_materialize)

    s = sp.add_parser("run", help="run guards over conditions")
    s.add_argument("--guards", nargs="+", required=True)
    s.add_argument("--conditions", nargs="+", required=True)
    s.add_argument("--repeats", type=int, default=1)
    s.add_argument("--limit", type=int)
    s.add_argument("--subset", help="file with one sample_id per line")
    s.add_argument("--concurrency", type=int, default=4)
    s.add_argument("--temperature", type=float, default=0.0)
    s.add_argument("--mock", action="store_true")
    s.add_argument("--force", action="store_true", help="start even if the pre-run estimate exceeds the budget")
    s.set_defaults(fn=cmd_run)

    s = sp.add_parser("cost", help="spend ledger from recorded token usage; optional full-design plan")
    s.add_argument("--plan", nargs="*", help="guard ids to estimate for the full design")
    s.add_argument("--repeats", type=int, default=3)
    s.add_argument("--n-conditions", type=int, default=12)
    s.add_argument("--add-hours", nargs=3, metavar=("GUARD", "HOURS", "USD_PER_HOUR"), help="record a self-deployed endpoint's billed hours")
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_cost)

    s = sp.add_parser("import-external", help="import predictions made outside this runner (TS-Guard)")
    s.add_argument("--guard", required=True)
    s.add_argument("--condition", required=True)
    s.add_argument("--source", required=True)
    s.add_argument("--repeat", type=int, default=0)
    s.set_defaults(fn=cmd_import)

    s = sp.add_parser("freeze", help="pin reviewed perturbations (sha256 → FREEZE.json, git add -f)")
    s.add_argument("--no-require-review", dest="require_review", action="store_false", help="allow freezing rows without a manual decision (pilot only)")
    s.set_defaults(fn=cmd_freeze, require_review=True)

    s = sp.add_parser("analyze", help="invariance / error-shift / ranking report")
    s.add_argument("--n-boot", type=int, default=1000)
    s.set_defaults(fn=cmd_analyze)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
