"""Every requirement in docs/requirements.md must trace to a test that exists."""
import csv
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def test_every_requirement_traces_to_an_existing_test():
    reqs = set(re.findall(r"^\| (REQ-\d+) \|", (DOCS / "requirements.md").read_text(), re.M))
    rows = list(csv.DictReader(open(DOCS / "traceability.csv")))
    traced = {r["requirement"] for r in rows}
    assert reqs and reqs == traced, f"untraced: {reqs - traced}, unknown: {traced - reqs}"
    tests = (Path(__file__).parent / "test_pipeline.py").read_text()
    missing = [r["test"] for r in rows if f"def {r['test']}(" not in tests]
    assert not missing, f"traceability points at tests that don't exist: {missing}"
