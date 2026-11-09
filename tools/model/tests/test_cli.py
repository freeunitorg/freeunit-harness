"""Unit tests for the tools.model CLI commands."""

import argparse
import pytest

from tools.model.cli import (
    cmd_benchmark_optimizations,
    cmd_conformance,
    cmd_lockfree_shm,
    cmd_prove_k1,
    cmd_replay,
    cmd_simulate,
    cmd_verify,
)


def test_cli_simulate():
    args = argparse.Namespace(requests=5, workers=2, engines=2)
    rc = cmd_simulate(args)
    assert rc == 0


def test_cli_verify():
    args = argparse.Namespace(chaos=False, seed=42)
    rc = cmd_verify(args)
    assert rc == 0


def test_cli_benchmark_optimizations():
    args = argparse.Namespace()
    rc = cmd_benchmark_optimizations(args)
    assert rc == 0


def test_cli_prove_k1():
    args = argparse.Namespace(workers=4, permutations=2)
    rc = cmd_prove_k1(args)
    assert rc == 0


def test_cli_lockfree_shm():
    args = argparse.Namespace(engines=2, threads=4, ops=20)
    rc = cmd_lockfree_shm(args)
    assert rc == 0


def test_cli_replay():
    args = argparse.Namespace(trace=None, emit_litmus=True)
    rc = cmd_replay(args)
    assert rc == 0


def test_cli_conformance():
    args = argparse.Namespace(url=None)
    rc = cmd_conformance(args)
    assert rc == 0


def test_cli_verify_chaos_seed_1(capsys):
    """Pin today's chaos run: two untraced violations, exit 1."""
    args = argparse.Namespace(chaos=True, seed=1)
    rc = cmd_verify(args)
    out = capsys.readouterr().out
    assert rc == 1
    assert "INVARIANT VIOLATIONS DETECTED: 2" in out
    assert "[INV-4-PROGRESS]" in out
    assert "[INV-3-NO-LEAK]" in out


def test_cli_benchmark_prints_modelled_counts(capsys):
    args = argparse.Namespace()
    rc = cmd_benchmark_optimizations(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Doorbell counts in the model" in out
    # Every printed statistic must come from the simulated run, not a slogan.
    for banned in (
        "fewer IPC context",
        "99.9",
        "50% fewer",
        "90.0%",
        "4% CPU",
        "10 signals sent",
        "100% false alerts",
        "Formal Proof",
        "Savings",
        "100%",
    ):
        assert banned not in out, banned
    assert "Always-notify doorbells" in out
    assert "One-wake-flag doorbells" in out
    assert "Doorbells sent: 1" in out

    rc = cmd_prove_k1(argparse.Namespace(workers=4, permutations=2))
    out = capsys.readouterr().out
    assert rc == 0
    for banned in ("Formal Proof", "Savings", "100%"):
        assert banned not in out, banned

    rc = cmd_lockfree_shm(argparse.Namespace(engines=2, threads=4, ops=20))
    out = capsys.readouterr().out
    assert rc == 0
    for banned in ("Formal Proof", "Savings", "100%"):
        assert banned not in out, banned


def test_cli_conformance_without_url_is_self_check(capsys):
    args = argparse.Namespace(url=None)
    rc = cmd_conformance(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "self-check, no server compared" in out
    assert "CONFORMANCE VERIFIED" not in out
