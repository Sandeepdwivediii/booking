"""Generate docs/EVIDENCE.md: every plan test ID (SANDEEP.md + TANISH.md) -> PASS / FAIL / BLOCKED / NO TEST,
derived from an ACTUAL pytest run (JUnit XML) and the test functions that reference each ID.

Usage (repo root, PostgreSQL running): python tests/platform/smoke/evidence_report.py
Status rules: FAIL if any mapped test failed; PASS if >= 1 mapped test passed and none failed;
BLOCKED if mapped tests only skipped; NO TEST if no test references the ID.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ID_ROW = re.compile(r"^\| `([A-Z]{2}-\d{2})` \| \*\*([AB])\*\* \| \*\*(P\d)\*\* \| (.+?) \|", re.M)
ID_REF = re.compile(r"(?<![A-Z])([A-Z]{2})[-_]?(\d{2})(?:\s*(?:\.\.|–)\s*(?:\1[-_]?)?(\d{2}))?(?!\d)")


def plan_ids() -> dict[str, tuple[str, str, str]]:
    out = {}
    for f in ("SANDEEP.md", "TANISH.md"):
        for m in ID_ROW.finditer((ROOT / f).read_text(encoding="utf-8")):
            out[m.group(1)] = (m.group(2), m.group(3), m.group(4).replace("`", "")[:70])
    return out


def ids_in(text: str, known: set[str]) -> set[str]:
    found = set()
    for m in ID_REF.finditer(text):
        a = int(m.group(2))
        b = int(m.group(3)) if m.group(3) and 0 <= int(m.group(3)) - a <= 20 else a
        found |= {f"{m.group(1)}-{n:02d}" for n in range(a, b + 1)} & known
    return found


def test_id_map(known: set[str]) -> dict[tuple[str, str], set[str]]:
    """(relative file, test function) -> IDs referenced in its decorators, signature, docstring or body."""
    mapping = {}
    for path in (ROOT / "tests").rglob("test_*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        tree = ast.parse("\n".join(lines))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                # names may use lowercase prefixes (test_sy01_...); bodies are matched case-sensitively
                ids = ids_in(node.name.upper() + "\n" + "\n".join(lines[start - 1:node.end_lineno]), known)
                if ids:
                    mapping[(path.relative_to(ROOT).as_posix(), node.name)] = ids
    return mapping


def run_pytest() -> tuple[dict[tuple[str, str], list[str]], str]:
    xml = Path(tempfile.mkdtemp()) / "junit.xml"
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:warnings", f"--junitxml={xml}"],
                          cwd=ROOT, capture_output=True, text=True)
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "no output"
    outcomes = defaultdict(list)
    for tc in ET.parse(xml).iter("testcase"):
        f = tc.get("file") or tc.get("classname", "").replace(".", "/") + ".py"
        name = tc.get("name", "").split("[")[0]
        status = ("FAIL" if tc.find("failure") is not None or tc.find("error") is not None
                  else "SKIP" if tc.find("skipped") is not None else "PASS")
        outcomes[(Path(f).as_posix(), name)].append(status)
    return outcomes, summary


def main() -> None:
    plan = plan_ids()
    mapping = test_id_map(set(plan))
    outcomes, summary = run_pytest()
    by_id = defaultdict(list)
    for key, ids in mapping.items():
        for i in ids:
            by_id[i].append((key, outcomes.get(key, [])))
    rows, totals = [], defaultdict(int)
    for i, (owner, prio, scenario) in sorted(plan.items(), key=lambda kv: (kv[1][0], kv[0])):
        results = [s for _, st in by_id.get(i, []) for s in st]
        status = ("NO TEST" if not results else "FAIL" if "FAIL" in results else "PASS" if "PASS" in results else "BLOCKED")
        totals[status] += 1
        tests = sorted({f"{Path(f).name}::{n}" for (f, n), _ in by_id.get(i, [])})
        shown = ", ".join(f"`{t}`" for t in tests[:3]) + (f" (+{len(tests) - 3})" if len(tests) > 3 else "")
        rows.append(f"| `{i}` | {'Tanish' if owner == 'A' else 'Sandeep'} | {prio} | {status} | "
                    f"{sum(r == 'PASS' for r in results)}/{len(results)} | {shown} |")
    out = [
        "# Acceptance evidence — Build My Holiday",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} by `tests/platform/smoke/evidence_report.py` from an actual run of "
        "`python -m pytest -q` (real domain on the original catalog + PostgreSQL test DB; TEST-ONLY fakes only where named `[fake]`).",
        "",
        f"**Suite:** {summary}",
        "",
        f"**Plan IDs:** {len(plan)} — " + ", ".join(f"{k}: {v}" for k, v in sorted(totals.items())),
        "",
        "Passed = passing test runs / mapped test runs (parametrized and `[fake]`/`[real]` variants each count).",
        "",
        "| ID | Owner | Priority | Status | Passed | Tests |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
    ]
    (ROOT / "docs" / "EVIDENCE.md").write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(summary)
    print(dict(totals))


if __name__ == "__main__":
    main()
