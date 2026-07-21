"""Smoke test for the realistic 236-student dataset."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from course_scheduler_agents import parse_course_spreadsheet
from course_arrange_tool import arrange_courses


def main() -> int:
    students_csv = ROOT / "test_data" / "real_students.csv"
    sections_csv = ROOT / "test_data" / "real_sections.csv"

    print("=== parse students csv ===")
    s = parse_course_spreadsheet(str(students_csv))
    print(f"  students parsed: {len(s.student_courses)}")
    print(f"  warnings: {s.warnings}")
    print(f"  sample S001: {s.student_courses.get('S001')}")
    print(f"  sample S121: {s.student_courses.get('S121')}")

    print("\n=== parse sections csv ===")
    sec = parse_course_spreadsheet(str(sections_csv))
    print(f"  sections parsed: {len(sec.section_counts)}")
    print(f"  warnings: {sec.warnings}")
    print(f"  sample: AP Calculus BC -> {sec.section_counts.get('AP Calculus BC')}")
    print(f"  sample: G11 Nationals -> {sec.section_counts.get('G11 Nationals')}")

    # Course coverage check
    all_courses_in_students = set()
    for cs in s.student_courses.values():
        all_courses_in_students.update(cs)
    missing = all_courses_in_students - set(sec.section_counts.keys())
    print(f"\n  courses in students but missing in sections ({len(missing)}): {sorted(missing)[:10]}")

    # Run core algorithm
    print("\n=== run arrange_courses ===")
    t0 = time.time()
    result = arrange_courses(
        student_courses={sid: list(cs) for sid, cs in s.student_courses.items()},
        section_counts=dict(sec.section_counts),
        num_time_slots=7,
        block_ban_map={
            "AP Calculus BC": [1, 4],
            "AP Statistics": [1, 4],
            "Maths 12: Spatial analytic geometry": [1, 4],
            "Maths 12: Math modeling": [1, 4],
            "DP Math HL G12": [1, 4],
            "Accelerated DP Math HL 11": [1, 4],
            "AP Art and Design": [0, 3, 6],
            "AP Computer Science A": [0, 2, 4],
            "AP Computer Science Principles": [0, 2, 4],
        },
        forbidden_course_groups=[
            ["Accelerated Economics", "Interdisciplinary Studies(IDS)"],
            ["Interdisciplinary Studies(IDS)", "AP Seminar", "AP Seminar AC"],
            ["DP English A: Literature G12", "Further English: Literature Philosophy & the Meaning of Life"],
            ["DP English A: Literature G11", "Further English: Literature Philosophy & the Meaning of Life"],
        ],
        course_name_map={
            "DP English A: Literature": "DP English A – Literature",
            "AP English Languagae and Composition": "AP English Language and Composition",
            "Accelerated DP Math HL": "Accelerated DP Math HL 11",
        },
        seed=42,
        max_iterations=20000,
        include_diagnostics=True,
    )
    dt = time.time() - t0
    m = result["metrics"]
    print(f"  [{dt:.1f}s] satisfaction_rate={m['satisfaction_rate']}% conflict={m['conflict_count']} "
          f"section_variance={m['section_variance']:.2f} timeslot_variance={m['timeslot_variance']:.2f} "
          f"min_violations={m['min_students_violations']}")

    # Save full result for review
    out = ROOT / "test_data" / "real_smoke_result.json"
    with out.open("w") as f:
        json.dump(result, f, indent=2, default=str, ensure_ascii=False)
    print(f"\n[INFO] full result -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
