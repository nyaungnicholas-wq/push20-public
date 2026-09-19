"""Pin the --live-without---scheduled guard bypass shut.

`main.py rotation-live --live` used to hand a hand-typed command straight to
run_rotation_cycle, the bare rebalance primitive. S1 measured what that skips
(reports/s1_guards.py, drives both entry points against a stub broker):

    guard                                run_scheduled   run_rotation_cycle
    pre-trade risk gate (fails closed)       YES               no
    market-state check                       YES               no
    queue-gap refusal                        YES               no
    session idempotency key                  YES               no
    min_hold_days                            YES               no
    data-freshness assert                    YES               no
    state write                              YES               no
    ledger row on every path                 YES               no
    success / failure alert                  YES               no
    complete_fills=False                     YES               no  <- doubles

The last one is the expensive one: complete_fills defaults True on the primitive,
and that sweep re-submits any shortfall "while the market is still open". Run
after hours it sees a 100% shortfall and re-sends the entire book.

Harmless today only because alpaca_broker.py has no live endpoint. It becomes a
money bug the moment one is added, and it is reachable by one mistyped command.

WHY THE DOCSTRING TEST BELOW EXISTS. The first version of this fix inserted the
guard immediately after the *opening* triple-quote of run_rotation_cycle's
docstring instead of the closing one. It compiled cleanly, read correctly in a
diff, and did absolutely nothing -- and the connected-check further down then
produced a plausible refusal for an unrelated reason, which almost passed for
the fix working. Inert code that looks live is worse than no code.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_the_guard_is_executable_code_and_not_a_docstring():
    """The failure mode that nearly shipped: a guard that is only prose."""
    import inspect
    from trader.rotation_live import run_rotation_cycle as f

    doc = f.__doc__ or ""
    body = inspect.getsource(f).split('"""', 2)[2]
    assert "if not dry_run and not _gated" in body, (
        "the guard is not in the function body -- it is inert")
    assert "UNGATED LIVE" not in doc, (
        "the guard text is inside the docstring, where it does nothing")


def test_primitive_refuses_to_submit_without_a_gate_stack():
    from trader.rotation_live import run_rotation_cycle
    from trader.config import Config

    with pytest.raises(RuntimeError, match="cannot submit live orders directly"):
        run_rotation_cycle(Config(), dry_run=False, verbose=False)


def test_the_refusal_fires_before_anything_else_can_mask_it():
    """It must be the FIRST thing in the body. An earlier draft was shadowed by
    the connected-check, which refused for a different reason and read as a pass."""
    import inspect
    from trader.rotation_live import run_rotation_cycle as f

    body = inspect.getsource(f).split('"""', 2)[2]
    guard_at = body.index("if not dry_run and not _gated")
    for other in ("Alpaca not connected", "get_data_source", "get_positions"):
        if other in body:
            assert guard_at < body.index(other), (
                f"{other!r} is reachable before the gate check")


def test_dry_run_is_still_allowed():
    """The useful manual case -- print the plan, send nothing -- must survive."""
    import inspect
    from trader.rotation_live import run_rotation_cycle as f

    body = inspect.getsource(f).split('"""', 2)[2]
    assert "not dry_run and not _gated" in body, (
        "the guard must key on dry_run, not block the primitive outright")


def test_every_in_module_caller_asserts_it_gated():
    """run_scheduled and run_release apply the gates, so they pass _gated=True.
    If a new caller appears without it, live submission fails loudly rather than
    quietly skipping ten guards."""
    import inspect
    import trader.rotation_live as rl

    src = inspect.getsource(rl)
    body = src[src.index("def run_scheduled"):]
    calls = body.count("run_rotation_cycle(")
    gated = body.count("_gated=True")
    assert calls > 0
    assert gated == calls, (
        f"{calls} call(s) to the primitive after run_scheduled, but {gated} pass "
        "_gated=True -- an ungated caller will raise at submit time")


def test_cli_refuses_live_without_scheduled_and_exits_nonzero():
    """Exit status matters: a wrapper script reading 0 would treat a blocked
    trade as a completed one."""
    import subprocess

    # sys.executable, not a hardcoded .venv path: the latter silently SKIPPED
    # in a scratch copy, and a skipped guard test is indistinguishable from a
    # passing one -- two mutations went undetected before this was fixed.
    r = subprocess.run([sys.executable, "main.py", "rotation-live", "--live"],
                       cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}"
    assert "refusing" in (r.stdout + r.stderr).lower()
    out = r.stdout + r.stderr
    assert "--scheduled --live" in out, "the refusal must name the gated path"


def test_scheduled_flag_still_reaches_the_gated_path():
    """The registered task runs `--scheduled --session next-open --live`. The
    refusal must not have broken it."""
    import inspect
    import main

    src = inspect.getsource(main.cmd_rotation_live)
    assert "run_scheduled" in src
    # The refusal belongs in the else branch only.
    before, _, after = src.partition("if args.scheduled:")
    assert "refusing" not in before, "the refusal must not gate --scheduled"
