# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression tests for the REAUDIT-001 certification evidence-channel binding."""

import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import run_candidate_certification as rcc  # noqa: E402


def _invoke(monkeypatch, tmp_path, *, overall, returncode, frames=1, raw_stdout=None):
    record = tmp_path / "candidate.json"
    record.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(rcc, "_repo_root", lambda: tmp_path)

    if raw_stdout is None:
        frame = rcc.REPORT_STDOUT_PREFIX + json.dumps({"overall": overall})
        raw_stdout = "\n".join(frame for _ in range(frames)) + "\n"

    completed = SimpleNamespace(
        returncode=returncode,
        stdout=raw_stdout,
        stderr="",
    )
    monkeypatch.setattr(rcc.subprocess, "run", lambda *args, **kwargs: completed)
    return rcc._run_certification_container(
        "candidate-image:test", "a" * 40, record)


def test_pass_requires_exit_zero(monkeypatch, tmp_path):
    evidence = _invoke(
        monkeypatch, tmp_path,
        overall="CERTIFICATION_PASSED", returncode=0)
    assert evidence["capture"]["exitCode"] == 0
    assert evidence["report"]["overall"] == "CERTIFICATION_PASSED"


def test_non_pass_requires_exit_one(monkeypatch, tmp_path):
    evidence = _invoke(
        monkeypatch, tmp_path,
        overall="CERTIFICATION_FAILED", returncode=1)
    assert evidence["capture"]["exitCode"] == 1
    assert evidence["report"]["overall"] == "CERTIFICATION_FAILED"


@pytest.mark.parametrize(
    "overall,returncode",
    [
        ("CERTIFICATION_PASSED", 1),
        ("CERTIFICATION_FAILED", 0),
        ("REVIEW_REQUIRED", 0),
        ("BUILD_FAILED", 0),
        ("KNOWN_UNSAFE", 0),
    ],
)
def test_exit_verdict_mismatch_fails_closed(
        monkeypatch, tmp_path, overall, returncode):
    with pytest.raises(RuntimeError, match="exit/verdict mismatch"):
        _invoke(
            monkeypatch, tmp_path,
            overall=overall, returncode=returncode)


def test_multiple_report_frames_fail_closed(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="exactly one is required"):
        _invoke(
            monkeypatch, tmp_path,
            overall="CERTIFICATION_PASSED", returncode=0, frames=2)


def test_missing_report_frame_fails_closed(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="exactly one is required"):
        _invoke(
            monkeypatch, tmp_path,
            overall="CERTIFICATION_PASSED", returncode=0,
            raw_stdout="ordinary log line only\n")


def test_malformed_report_json_fails_closed(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="malformed report JSON"):
        _invoke(
            monkeypatch, tmp_path,
            overall="CERTIFICATION_PASSED", returncode=0,
            raw_stdout=rcc.REPORT_STDOUT_PREFIX + "{not-json}\n")


def test_non_object_report_fails_closed(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="JSON object"):
        _invoke(
            monkeypatch, tmp_path,
            overall="CERTIFICATION_PASSED", returncode=0,
            raw_stdout=rcc.REPORT_STDOUT_PREFIX + json.dumps(["not", "an", "object"]) + "\n")


def test_unexpected_infrastructure_exit_fails_before_evidence(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="infrastructure failure"):
        _invoke(
            monkeypatch, tmp_path,
            overall="CERTIFICATION_FAILED", returncode=2)
