"""Generate the realistic test dataset (236 students) as CSV + prompt.

Outputs (under test_data/):
- real_students.csv    : student_id + 7 course columns (wide table)
- real_sections.csv    : course + section_count
- real_prompt.md       : all scheduling requirements in natural language

Source: _user_reference.py (the user's standalone Scheduler_Final reference).
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from _user_reference import (  # noqa: E402
    get_test_student_data,
    get_custom_sections,
    get_block_ban_map,
    get_forbidden_course_groups,
    get_course_name_remap,
    get_num_time_slots,
    get_max_iterations,
    get_thresholds,
)

OUT = ROOT / "test_data"
OUT.mkdir(exist_ok=True)


def export_students_csv() -> None:
    students = get_test_student_data()
    n_courses = max(len(cs) for cs in students.values())
    headers = ["student_id"] + [f"course{i}" for i in range(1, n_courses + 1)]
    path = OUT / "real_students.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for sid in sorted(students):
            row = [f"S{sid:03d}"]
            courses = students[sid]
            # pad with blanks to n_courses
            row.extend(courses + [""] * (n_courses - len(courses)))
            writer.writerow(row)
    print(f"[OK] {path}  rows={len(students)}  max_courses={n_courses}")


def export_sections_csv() -> None:
    sections = get_custom_sections()
    path = OUT / "real_sections.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["course", "section_count"])
        for course in sorted(sections):
            writer.writerow([course, sections[course]])
    print(f"[OK] {path}  rows={len(sections)}")


def export_prompt() -> None:
    sections = get_custom_sections()
    ban = get_block_ban_map()
    fcg = get_forbidden_course_groups()
    remap = get_course_name_remap()
    nslots = get_num_time_slots()
    maxiter = get_max_iterations()
    thr = get_thresholds()

    ban_lines = "\n".join(
        f"- `{course}`: 禁止时间段 {sorted(bans)}"
        for course, bans in sorted(ban.items())
    )
    fcg_lines = "\n".join(
        f"- `{', '.join(group)}`"
        for group in sorted(fcg, key=lambda g: g[0])
    )
    remap_lines = "\n".join(f"- `{k}` → `{v}`" for k, v in remap.items())

    large_courses = sorted(sections.items(), key=lambda kv: -kv[1])[:10]
    large_lines = "\n".join(f"- `{c}`: {n} 个分班" for c, n in large_courses)

    body = f"""# 课程排课请求

请根据上传的学生选课表（`real_students.csv`）和课程分班表（`real_sections.csv`）生成 G11/G12 年级的最终课表。

## 一、时间段

- 一共 **{nslots} 个 time slots**，编号从 0 到 {nslots - 1}。
- 每天按 slot 顺序排课；同一时间段里只能有非冲突的课程（同一学生选的所有课不能撞同一 slot）。

## 二、算法与候选

- 每轮请生成 **{4} 个候选课表**（不同 seed）。
- 如果最佳候选还有超过 **{0} 个学生冲突**，请最多重跑 **{2} 轮**。
- 最终选择 **冲突最少 → 满意率最高 → 总成本最低** 的课表。
- 验收标准：**冲突学生数必须等于 0**；如果做不到，也请返回所有候选里最好的一个，并解释为什么无法完全满足。
- 设置 `seed=42`，`max_iterations={maxiter}`。

## 三、分班最小/最大学生数（4 个阈值）

> 这些是"开关"：决定要不要对某门课应用分班平衡。具体含义见后文。

- `min_students_threshold = {thr['min_students_threshold']}`：当某门课**总选课人数** > 这个值时，要求该门课每个分班 ≥ `min_students_per_section` 人。
- `max_students_threshold = {thr['max_students_threshold']}`：当某门课**总选课人数** < 这个值时，要求该门课每个分班 ≤ `max_students_per_section` 人。
- `min_students_per_section = {thr['min_students_per_section']}`
- `max_students_per_section = {thr['max_students_per_section']}`
- 落在中间区间（{thr['min_students_threshold']} ≤ 总人数 ≤ {thr['max_students_threshold']}）的课，同时受两个约束。

## 四、禁排规则（block_ban_map）

以下课程**不能**放在指定时间段：

{ban_lines}

> 含义：列表里出现的时间段编号，这门课的所有分班都不能排进去。
> 例：AP Calculus BC 不能在 slot 1 或 slot 4。

## 五、避免同时段的课程组（forbidden_course_groups）

以下组合的课程**尽量不要**安排在同一个 slot（如果硬冲突，penalty 加重）：

{fcg_lines}

> 含义：列表里同一行的课程两两/全部要避开同 slot。
> 例：选了 `Accelerated Economics` 的学生不能跟 `Interdisciplinary Studies(IDS)` 同 slot。

## 六、课程名别名（course_name_map）

学生选课表里偶尔会出现历史名称/拼写错误，**必须**按下面的映射规范化成正式名称后再排课：

{remap_lines}

> 例：学生表里的 `Accelerated DP Math HL` 实际是 `Accelerated DP Math HL 11`。

## 七、返回内容

请返回：

1. 最终的 `schedule_by_slot`（每个 slot 列出所有分班，格式 `课程名_分班号`，例 `AP Calculus BC_1`）。
2. 满意率（被成功排课的学生比例）。
3. 冲突学生数（至少有一门课没排上的学生数）。
4. 每个候选的 metrics（含 satisfaction_rate、conflict_count、timeslot_variance、section_variance、min_students_violations、total_cost）。
5. 是否通过验收（冲突=0 为通过）。
6. 如果未通过验收：说明卡点（是 slot 不够？分班不够？硬约束冲突？）。

## 八、上传文件

- `test_data/real_students.csv` — 236 名学生选课（学生 ID 列 `student_id`，7 列课程 `course1`~`course7`，空白表示该生没选这门课）
- `test_data/real_sections.csv` — 课程分班数（`course`, `section_count` 两列）
"""
    path = OUT / "real_prompt.md"
    path.write_text(body, encoding="utf-8")
    print(f"[OK] {path}  bytes={len(body)}")


if __name__ == "__main__":
    export_students_csv()
    export_sections_csv()
    export_prompt()
    print("\nDone. Generated 3 files under test_data/.")
