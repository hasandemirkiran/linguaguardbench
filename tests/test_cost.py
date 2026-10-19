import json
from pathlib import Path

import pytest

from guard_invariance.cost import BudgetBrake, BudgetExceeded, estimate, ledger, row_cost
from guard_invariance.data import append_jsonl, load_all
from guard_invariance.run_guard import run_guard
from guard_invariance.vertex import MockProvider
from tests.test_pipeline import _mk_raw

PRICING = {"m1": [1.0, 5.0], "default": [2.0, 10.0]}


def test_row_cost_and_ledger(tmp_path: Path):
    r = {"model": "m1", "usage": {"input": 1_000_000, "output": 100_000}}
    assert row_cost(PRICING, r) == pytest.approx(1.0 + 0.5)
    p = tmp_path / "results" / "raw" / "g" / "c" / "r0.jsonl"
    append_jsonl(p, {**r, "guard": "g"})
    append_jsonl(p, {**r, "guard": "g", "mock": True})  # mock rows never count
    append_jsonl(p, {"model": "zzz", "usage": {"input": 500_000, "output": 0}, "guard": "g"})  # default price
    led = ledger(tmp_path, PRICING)
    assert led["total_usd"] == pytest.approx(1.5 + 1.0, abs=0.01)
    assert led["by_model"]["m1"]["calls"] == 1


def test_estimate_scales_with_repeats(tmp_path: Path):
    _mk_raw(tmp_path / "raw")
    steps = load_all(tmp_path / "raw" / "agentdojo")
    e1 = estimate(steps, "m1", PRICING, repeats=1)
    e3 = estimate(steps, "m1", PRICING, repeats=3)
    assert e3["calls"] == 3 * e1["calls"] and e3["usd"] == pytest.approx(3 * e1["usd"], rel=1e-6)


def test_brake_refuses_estimate_and_stops_run(tmp_path: Path):
    raw, cond, res = tmp_path / "raw", tmp_path / "conditions", tmp_path / "results"
    _mk_raw(raw)
    from guard_invariance.materialize import materialize

    materialize("original", raw, cond)
    guard = {"id": "g", "model": "m1", "provider": "mock"}

    tight = BudgetBrake(budget_usd=0.000001, spent_usd=0.0, pricing=PRICING)
    with pytest.raises(BudgetExceeded):
        run_guard(guard, "original", cond_root=cond, results_root=res, provider=MockProvider(), brake=tight)
    # --force skips the pre-run estimate check but the live brake still stops the run
    out = run_guard(guard, "original", cond_root=cond, results_root=res, provider=MockProvider(), brake=tight, force=True, concurrency=1)
    assert out.get("stopped")
    assert out["done"] >= 1

    loose = BudgetBrake(budget_usd=100.0, spent_usd=0.0, pricing=PRICING)
    out = run_guard(guard, "original", cond_root=cond, results_root=tmp_path / "results2", provider=MockProvider(), brake=loose)
    assert not out.get("stopped") and out["done"] == 4 and loose.spent > 0
