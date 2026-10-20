"""Flaky-test detection.

A failure is treated as flaky when the same pipeline passed on the very same commit: nothing in the code
changed between the failing and the passing run, so the failure cannot be a regression. If every run on that
commit failed, the failure is consistent and is reported as a real regression instead.
"""
import re


def failed_tests(log: str) -> list[str]:
    """Names of failed tests from pytest output (lines like 'FAILED tests/x.py::name - reason')."""
    return sorted(set(re.findall(r"^(?:\S+Z\s+)?FAILED\s+(\S+)", log or "", re.MULTILINE)))


def assess(build: dict, builds: list[dict]) -> dict:
    """Compare a failed build with other runs of the same pipeline on the same commit."""
    commit = build.get("sourceVersion")
    pipeline_id = (build.get("definition") or {}).get("id")
    others = [b for b in builds
              if b["id"] != build["id"] and (b.get("definition") or {}).get("id") == pipeline_id
              and commit and b.get("sourceVersion") == commit]
    passes = [b for b in others if b.get("result") == "succeeded"]
    fails = [b for b in others if b.get("result") in ("failed", "canceled")] + [build]
    return {
        "flaky": bool(passes),
        "passes": len(passes),
        "fails": len(fails),
        "pass_runs": [b["id"] for b in passes],
        "commit": (commit or "")[:8],
    }
