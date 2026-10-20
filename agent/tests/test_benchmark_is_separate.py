"""The classifier must never be able to see the benchmark's answers: nothing in the agent refers to the benchmark folder."""
import os

AGENT = os.path.join(os.path.dirname(__file__), "..")


def test_agent_code_does_not_read_or_import_the_benchmark():
    offenders = []
    for name in sorted(os.listdir(AGENT)):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(AGENT, name), encoding="utf-8").read().lower()
        for needle in ("benchmark", "answers.json", "ground_truth", "ground truth"):
            if needle in text:
                offenders.append((name, needle))
    assert offenders == [], f"agent code refers to the benchmark or its answers: {offenders}"
