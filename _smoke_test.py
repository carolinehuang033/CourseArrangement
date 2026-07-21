"""Smoke test: run arrange_courses against test_data without any LLM.

Validates that the core scheduler (course_arrange_tool.arrange_courses) works
end-to-end on the bundled test data, independent of OpenAI / agents.
"""
from __future__ import annotations

import csv
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from course_arrange_tool import arrange_courses  # noqa: E402


def load_students(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            sid = row["student_id"]
            courses = [v for k, v in row.items() if k.startswith("course") and v]
            out[sid] = courses
    return out


def load_sections(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            out[row["course"]] = int(row["section_count"])
    return out


def main() -> int:
    students = load_students(ROOT / "test_data" / "course_students.csv")
    sections = load_sections(ROOT / "test_data" / "course_sections.csv")

    print(f"[INFO] students={len(students)} unique_courses_in_csv={len({c for cs in students.values() for c in cs})}")
    print(f"[INFO] sections defined for {len(sections)} courses")

    # mirror test_user_prompt.md
    block_ban_map = {
        "AP Calculus BC": [0],
        "AP Chemistry": [1],
        "AP Computer Science A": [0, 2],
    }
    forbidden_course_groups = [
        ["AP Chemistry", "AP Biology"],
        ["AP Seminar", "Interdisciplinary Studies(IDS)"],
    ]

    t0 = time.time()
    try:
        result = arrange_courses(
            student_courses=students,
            section_counts=sections,
            num_time_slots=5,
            block_ban_map=block_ban_map,
            forbidden_course_groups=forbidden_course_groups,
            seed=42,
            max_iterations=2000,
            include_diagnostics=True,
        )
    except Exception as exc:  # noqa: BLE001
        print("[FAIL] arrange_courses raised:")
        traceback.print_exc()
        return 1
    dt = time.time() - t0
    print(f"[OK] arrange_courses returned in {dt:.2f}s")
    print(f"[INFO] top-level keys: {list(result.keys())}")

    # Print compact summary
    print("--- summary ---")
    for k in (
        "summary",
        "satisfaction_rate",
        "conflict_count",
        "metrics",
    ):
        if k in result:
            v = result[k]
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False, indent=2)[:800]
            print(f"{k}: {v}")

    # Save full result for inspection
    out_path = ROOT / "test_data" / "smoke_test_result.json"
    try:
        with out_path.open("w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"[OK] full result written to {out_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] could not write result file: {exc}")

    # Basic sanity assertions
    if "satisfaction_rate" in result:
        sr = result["satisfaction_rate"]
        if isinstance(sr, (int, float)) and 0 <= sr <= 1:
            print(f"[OK] satisfaction_rate in [0,1]: {sr}")
        else:
            print(f"[WARN] suspicious satisfaction_rate: {sr}")
    if "conflict_count" in result:
        cc = result["conflict_count"]
        print(f"[INFO] conflict_count={cc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
