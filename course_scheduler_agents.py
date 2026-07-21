"""Handoff workflow for turning uploaded course spreadsheets into schedules.

Conversation hands off to an orchestrator, which extracts a compact scheduling intent and
hands off to a scheduler that calls the local arrange_courses tool.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from agents import (
    Agent,
    ModelSettings,
    RunContextWrapper,
    Runner,
    function_tool,
    handoff,
    trace,
)
from pydantic import BaseModel, Field, model_validator

try:
    from .course_arrange_tool import arrange_courses
except ImportError:
    from course_arrange_tool import arrange_courses


COURSE_SPLIT_PATTERN = re.compile(r"[\n;；|、]+")


class CostWeights(BaseModel):
    conflict: Optional[float] = Field(default=None, ge=0)
    timeslot: Optional[float] = Field(default=None, ge=0)
    section: Optional[float] = Field(default=None, ge=0)
    excess_variance: Optional[float] = Field(default=None, ge=0)
    min_students: Optional[float] = Field(default=None, ge=0)

    def compact(self) -> Dict[str, float]:
        return {key: value for key, value in self.model_dump().items() if value is not None}


class CourseSchedulingRequest(BaseModel):
    student_courses: Dict[str, List[str]] = Field(
        description="Map of student ID/name to selected course names."
    )
    section_counts: Dict[str, int] = Field(
        description="Map of course name to number of sections to create."
    )
    num_time_slots: int = Field(default=7, ge=2, le=12)
    block_ban_map: Dict[str, List[int]] = Field(default_factory=dict)
    forbidden_course_groups: List[List[str]] = Field(default_factory=list)
    course_name_map: Dict[str, str] = Field(default_factory=dict)
    min_students_threshold: int = Field(default=25, ge=0)
    max_students_threshold: int = Field(default=65, ge=0)
    min_students_per_section: int = Field(default=12, ge=0)
    max_students_per_section: int = Field(default=30, ge=1)
    cost_weights: CostWeights = Field(default_factory=CostWeights)
    max_iterations: int = Field(default=20000, ge=100, le=100000)
    seed: Optional[int] = None
    candidate_runs: int = Field(
        default=1,
        ge=1,
        le=1,
        description="Exactly one schedule is generated per round.",
    )
    max_conflict_count: Optional[int] = Field(
        default=0,
        ge=0,
        description="Accept a schedule when conflicts are at or below this number.",
    )
    min_satisfaction_rate: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="Optional minimum satisfaction percentage required for acceptance.",
    )
    include_schedule: bool = True
    include_section_loads: bool = True
    include_diagnostics: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_agent_output(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        block_ban_map = data.get("block_ban_map")
        if isinstance(block_ban_map, dict):
            normalized: Dict[str, List[int]] = {}
            for key, value in block_ban_map.items():
                if isinstance(value, list) and str(key).isdigit():
                    slot = int(key)
                    for course in value:
                        if isinstance(course, str) and course.strip():
                            normalized.setdefault(course.strip(), []).append(slot)
                else:
                    slots: List[int] = []
                    values = value if isinstance(value, list) else [value]
                    for slot in values:
                        try:
                            slots.append(int(slot))
                        except (TypeError, ValueError):
                            continue
                    normalized[str(key)] = slots
            data["block_ban_map"] = normalized

        if "max_iterations" in data:
            try:
                data["max_iterations"] = max(100, int(data["max_iterations"]))
            except (TypeError, ValueError):
                data.pop("max_iterations", None)

        return data

    @model_validator(mode="after")
    def validate_course_coverage(self) -> "CourseSchedulingRequest":
        selected_courses = {
            self.course_name_map.get(course, course)
            for courses in self.student_courses.values()
            for course in courses
            if course and course.strip()
        }
        configured_courses = {
            self.course_name_map.get(course, course) for course in self.section_counts
        }
        missing = sorted(selected_courses - configured_courses)
        if missing:
            raise ValueError(
                "Every selected course needs a section count. Missing: " + ", ".join(missing)
            )
        return self


class ParsedSpreadsheet(BaseModel):
    file_path: str
    sheets_seen: List[str] = Field(default_factory=list)
    student_courses: Dict[str, List[str]] = Field(default_factory=dict)
    section_counts: Dict[str, int] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class ScheduleRunResult(BaseModel):
    structured_request: CourseSchedulingRequest
    schedule_summary: str
    best_schedule: Dict[str, Any]
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    retry_rounds_used: int = 0
    accepted: bool = False
    parsed_spreadsheets: List[ParsedSpreadsheet] = Field(default_factory=list)


class CourseBlockBan(BaseModel):
    course: str
    slots: List[int]


class CourseAlias(BaseModel):
    source: str
    target: str


class SchedulingIntent(BaseModel):
    """LLM-extracted settings; authoritative roster data stays in local context."""

    block_bans: List[CourseBlockBan] = Field(default_factory=list)
    forbidden_course_groups: List[List[str]] = Field(default_factory=list)
    course_aliases: List[CourseAlias] = Field(default_factory=list)
    cost_weights: CostWeights = Field(default_factory=CostWeights)
    max_iterations: int = Field(default=20000, ge=100, le=100000)
    max_conflict_count: Optional[int] = Field(default=0, ge=0)
    min_satisfaction_rate: Optional[float] = Field(default=None, ge=0, le=100)
    include_diagnostics: bool = False


@dataclass
class CourseWorkflowContext:
    student_courses: Dict[str, List[str]]
    section_counts: Dict[str, int]
    request_overrides: Dict[str, Any]
    parsed_spreadsheets: List[ParsedSpreadsheet]
    stage: str = "conversation"
    cancel_requested: bool = False
    request: Optional[CourseSchedulingRequest] = None
    result: Optional[Dict[str, Any]] = None


@dataclass
class CourseWorkflowRunResult:
    message: str
    schedule: Optional[ScheduleRunResult] = None


def _clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _split_course_cell(value: Any) -> List[str]:
    text = _clean_text(value)
    if not text:
        return []
    return [part.strip() for part in COURSE_SPLIT_PATTERN.split(text) if part.strip()]


def _find_column(columns: List[str], candidates: List[str]) -> Optional[str]:
    lowered = {column.lower().strip(): column for column in columns}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]

    for column in columns:
        normalized = column.lower().strip()
        if any(candidate in normalized for candidate in candidates):
            return column
    return None


def _read_tables(file_path: str) -> Dict[str, pd.DataFrame]:
    path = Path(file_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Uploaded file not found: {file_path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return {"csv": pd.read_csv(path)}
    if suffix == ".tsv":
        return {"tsv": pd.read_csv(path, sep="\t")}
    if suffix in {".xlsx", ".xlsm"}:
        return pd.read_excel(path, sheet_name=None)
    if suffix == ".xls":
        return pd.read_excel(path, sheet_name=None)

    raise ValueError(f"Unsupported course data file type: {suffix}")


def _extract_section_counts(frame: pd.DataFrame) -> Dict[str, int]:
    columns = [str(column).strip() for column in frame.columns]
    frame = frame.copy()
    frame.columns = columns

    course_col = _find_column(columns, ["course", "课程", "subject", "class"])
    count_col = _find_column(
        columns,
        ["section_count", "sections", "section", "num_sections", "分班数", "班数"],
    )
    if not course_col or not count_col:
        return {}

    counts: Dict[str, int] = {}
    for _, row in frame.iterrows():
        course = _clean_text(row.get(course_col))
        if not course:
            continue
        try:
            count = int(float(row.get(count_col)))
        except (TypeError, ValueError):
            continue
        if count > 0:
            counts[course] = count
    return counts


def _extract_student_courses(frame: pd.DataFrame) -> Dict[str, List[str]]:
    columns = [str(column).strip() for column in frame.columns]
    frame = frame.copy()
    frame.columns = columns

    student_col = _find_column(columns, ["student_id", "student", "id", "学号", "学生", "姓名", "name"])
    course_col = _find_column(columns, ["course", "课程", "subject"])
    exact_course_column = (
        course_col is not None and course_col.lower().strip() in {"course", "课程", "subject"}
    )

    student_courses: Dict[str, List[str]] = {}
    if student_col and exact_course_column and student_col != course_col:
        for _, row in frame.iterrows():
            student = _clean_text(row.get(student_col))
            courses = _split_course_cell(row.get(course_col))
            if student and courses:
                student_courses.setdefault(student, [])
                student_courses[student].extend(courses)
    elif student_col:
        course_columns = [column for column in columns if column != student_col]
        for _, row in frame.iterrows():
            student = _clean_text(row.get(student_col))
            courses: List[str] = []
            for column in course_columns:
                courses.extend(_split_course_cell(row.get(column)))
            if student and courses:
                student_courses[student] = courses
    else:
        for row_index, row in frame.iterrows():
            courses: List[str] = []
            for column in columns:
                courses.extend(_split_course_cell(row.get(column)))
            if courses:
                student_courses[f"row_{row_index + 1}"] = courses

    return {
        student: sorted(set(course for course in courses if course))
        for student, courses in student_courses.items()
    }


def parse_course_spreadsheet(file_path: str) -> ParsedSpreadsheet:
    tables = _read_tables(file_path)
    parsed = ParsedSpreadsheet(file_path=file_path, sheets_seen=list(tables.keys()))

    for sheet_name, frame in tables.items():
        frame = frame.dropna(how="all")
        if frame.empty:
            continue

        section_counts = _extract_section_counts(frame)
        if section_counts:
            parsed.section_counts.update(section_counts)
            continue

        student_courses = _extract_student_courses(frame)
        if student_courses:
            parsed.student_courses.update(student_courses)
        else:
            parsed.warnings.append(f"No recognizable student/course data in sheet {sheet_name!r}.")

    if not parsed.student_courses:
        parsed.warnings.append("No student course selections were detected.")
    if not parsed.section_counts:
        parsed.warnings.append("No section-count table was detected.")

    return parsed


def _schedule_request_to_tool_kwargs(
    request: CourseSchedulingRequest,
    *,
    seed: Optional[int],
) -> Dict[str, Any]:
    return {
        "student_courses": request.student_courses,
        "section_counts": request.section_counts,
        "num_time_slots": request.num_time_slots,
        "block_ban_map": request.block_ban_map,
        "forbidden_course_groups": request.forbidden_course_groups,
        "course_name_map": request.course_name_map,
        "min_students_threshold": request.min_students_threshold,
        "max_students_threshold": request.max_students_threshold,
        "min_students_per_section": request.min_students_per_section,
        "max_students_per_section": request.max_students_per_section,
        "cost_weights": request.cost_weights.compact(),
        "max_iterations": request.max_iterations,
        "seed": seed,
        "include_schedule": request.include_schedule,
        "include_section_loads": request.include_section_loads,
        "include_diagnostics": request.include_diagnostics,
    }


def _candidate_accepted(result: Dict[str, Any], request: CourseSchedulingRequest) -> bool:
    metrics = result.get("metrics", {})
    conflict_count = metrics.get("conflict_count")
    satisfaction_rate = metrics.get("satisfaction_rate")

    if request.max_conflict_count is not None and conflict_count is not None:
        if int(conflict_count) > request.max_conflict_count:
            return False

    if request.min_satisfaction_rate is not None and satisfaction_rate is not None:
        if float(satisfaction_rate) < request.min_satisfaction_rate:
            return False

    return True


def _candidate_sort_key(candidate: Dict[str, Any]) -> tuple:
    metrics = candidate["result"].get("metrics", {})
    return (
        not candidate["accepted"],
        int(metrics.get("conflict_count", 10**9)),
        -float(metrics.get("satisfaction_rate", 0.0)),
        float(metrics.get("total_cost", float("inf"))),
        float(metrics.get("section_variance", float("inf"))),
        float(metrics.get("timeslot_variance", float("inf"))),
    )


def _run_candidate_schedules(
    request: CourseSchedulingRequest,
    *,
    max_retry_rounds: int = 4,
    context: Optional[CourseWorkflowContext] = None,
) -> tuple[Dict[str, Any], List[Dict[str, Any]], int, bool]:
    candidates: List[Dict[str, Any]] = []
    base_seed = request.seed
    rounds_total = max_retry_rounds + 1
    accepted = False
    retry_rounds_used = 0

    for round_index in range(rounds_total):
        if context is not None and context.cancel_requested:
            break
        if round_index > 0:
            retry_rounds_used = round_index

        for candidate_index in range(request.candidate_runs):
            if context is not None and context.cancel_requested:
                break
            if base_seed is None:
                candidate_seed = None
            else:
                candidate_seed = base_seed + round_index * request.candidate_runs + candidate_index

            result = arrange_courses(
                **_schedule_request_to_tool_kwargs(request, seed=candidate_seed),
                cancel_check=(
                    None
                    if context is None
                    else lambda: context.cancel_requested
                ),
            )
            candidate = {
                "round": round_index + 1,
                "candidate": candidate_index + 1,
                "seed": candidate_seed,
                "accepted": _candidate_accepted(result, request),
                "result": result,
            }
            candidates.append(candidate)

        if not candidates or (context is not None and context.cancel_requested):
            break
        candidates.sort(key=_candidate_sort_key)
        accepted = candidates[0]["accepted"]

    if not candidates:
        raise RuntimeError("Scheduling was cancelled before a candidate completed.")
    return candidates[0], candidates, retry_rounds_used, accepted


@function_tool(strict_mode=False)
def inspect_uploaded_course_file(file_path: str) -> Dict[str, Any]:
    """Parse an uploaded course-selection spreadsheet into student courses and section counts."""
    return parse_course_spreadsheet(file_path).model_dump()


@function_tool(strict_mode=False)
def generate_course_schedule(
    context: RunContextWrapper[CourseWorkflowContext],
) -> Dict[str, Any]:
    """Generate a timetable from the validated request in the workflow context."""
    request = context.context.request
    if request is None:
        raise RuntimeError("Scheduler handoff did not provide a validated request.")
    context.context.stage = "tool"
    print(
        f"[tool] generate_course_schedule started: "
        f"{len(request.student_courses)} students, {len(request.section_counts)} courses",
        flush=True,
    )
    best, candidates, retry_rounds_used, accepted = _run_candidate_schedules(
        request,
        context=context.context,
    )
    if context.context.cancel_requested:
        raise RuntimeError("Scheduling was cancelled.")
    context.context.result = {
        "accepted": accepted,
        "retry_rounds_used": retry_rounds_used,
        "best_candidate": best,
        "candidates": candidates,
    }
    context.context.stage = "summary"
    print(
        f"[tool] generate_course_schedule completed: "
        f"{len(candidates)} candidates, accepted={accepted}",
        flush=True,
    )
    return {
        "accepted": accepted,
        "retry_rounds_used": retry_rounds_used,
        "candidate_count": len(candidates),
        "best_candidate_metrics": best["result"].get("metrics", {}),
        "schedule_included": "schedule" in best["result"],
    }


ORCHESTRATOR_INSTRUCTIONS = """
You are the Course Scheduling Orchestrator.

Extract scheduling constraints from the conversation, then hand off exactly once to the
Course Schedule Generator. The handoff payload must be one SchedulingIntent.

Rules:
- Do not include or reproduce student rosters or section counts; they are injected from trusted
  local context during handoff.
- Extract banned blocks, forbidden course groups, aliases, weights, and acceptance criteria from
  the user's messages.
- Do not extract candidate or retry counts. Runtime policy is fixed at one schedule per round with
  four retry rounds.
- Extract acceptance criteria when present. Use max_conflict_count for conflict tolerance and
  min_satisfaction_rate for satisfaction-rate requirements.
- Represent each banned-block rule as one block_bans item with a course and zero-based slots.
- "Course A and Course B should not be together" belongs in forbidden_course_groups,
  for example [["Course A", "Course B"]]. Do not put that rule in block_ban_map.
- Keep costs down: do not ask for diagnostics unless the user explicitly requests conflict details.
- Do not answer the user directly. Always call the scheduler handoff once.
"""

SCHEDULER_INSTRUCTIONS = """
You are the Course Schedule Generator.

The validated CourseSchedulingRequest is already stored in local workflow context.
Call generate_course_schedule exactly once with no arguments. After the tool returns, do not call it again.
Provide a concise operational summary: whether the best run met the acceptance criteria,
how many scheduling runs and retry rounds were used, satisfaction rate, conflict count, major load
metrics, and where the schedule appears in the returned payload.
"""

CONVERSATION_INSTRUCTIONS = """
你是专业的智能排课助手。

语言规则：
- 识别用户最新一条有效消息所使用的语言，并使用相同语言回复。
- 如果无法判断语言，沿用当前对话语言；仍无法判断时默认使用中文。
- 除课程名称、文件字段名等必须保留的原文外，不要在同一句回复中混用不同语言。

你的目标：
- 从第一条用户消息开始，以专业、自然的方式回应排课需求。
- 帮用户把自然语言排课需求逐步补全，而不是让用户面对技术表单。
- 开场和补问时要覆盖完整排课需求：学生选课文件、每门课的开班数量、排课时段总数、
  不能安排在同一时段的课程组、指定课程的禁排时段、每个班的人数限制，以及可选的随机种子。
- 如果信息不足，简短说明你已经理解了什么，并优先问最影响排课的缺失项；同一轮可以列出一个短清单。
- 如果高级设置和用户文字冲突，明确指出冲突并问用户按哪个为准。
- 如果文件缺失或格式不对，温和地告诉用户需要上传学生选课表。
- 如果缺课程分班数，列出缺失课程，让用户补每门课开几个班。
- 你会收到 session_memory；回答时要利用里面的历史需求、已上传文件、已识别课程和已给分班数。
- 如果用户说“刚才那个”“同上”“再加一个限制”等，结合 session_memory 理解上下文。
- 在排课工具运行前，必须让用户看到所有设置汇总并确认；如果 app 已经在做确认，你不要绕过它。
- 当 context.issue=confirmation_required 时，根据 context.settings_summary 和完整对话，
  用自己的话简洁汇总关键设置并请用户确认；不要逐字复制或大段复述用户原消息。
- 当输入明确标记 ready_to_schedule=true 时，不要回复用户，也不要声称已经开始；
  立即且只调用一次 handoff，把任务转交给 Course Scheduling Orchestrator。
- 当 ready_to_schedule=false 时，handoff 不可用。必须针对 context.issue 继续补全信息，
  绝不能说“已确认”“已开始”“正在运行”或暗示排课任务已经启动。
- 不要输出 JSON、内部字段名或工具调用细节，除非用户明确要求。
- 不要假装已经生成课表；只有排课工具实际运行完成后才说已经生成。
- 回复保持简短、清楚、像聊天一样。
"""


def _on_orchestrator_handoff(context: RunContextWrapper[CourseWorkflowContext]) -> None:
    context.context.stage = "orchestrator"
    print("[handoff] Conversation -> Orchestrator", flush=True)


def _on_scheduler_handoff(
    context: RunContextWrapper[CourseWorkflowContext],
    intent: SchedulingIntent,
) -> None:
    request_data = intent.model_dump(exclude={"block_bans", "course_aliases"})
    request_data["block_ban_map"] = {
        item.course: item.slots for item in intent.block_bans
    }
    request_data["course_name_map"] = {
        item.source: item.target for item in intent.course_aliases
    }
    request_data.update(
        {
            "student_courses": context.context.student_courses,
            "section_counts": context.context.section_counts,
        }
    )
    request_data.update(context.context.request_overrides)
    context.context.request = CourseSchedulingRequest.model_validate(request_data)
    context.context.stage = "scheduler"
    print("[handoff] Orchestrator -> Scheduler", flush=True)


def build_course_handoff_agents(
    model: str = "gpt-4o-mini",
    *,
    enable_workflow_handoff: bool = True,
) -> tuple[Agent, Agent, Agent]:
    scheduler: Agent[CourseWorkflowContext] = Agent(
        name="Course Schedule Generator",
        instructions=SCHEDULER_INSTRUCTIONS,
        model=model,
        tools=[generate_course_schedule],
        model_settings=ModelSettings(tool_choice="generate_course_schedule"),
    )

    scheduler_handoff = handoff(
        scheduler,
        tool_name_override="transfer_to_course_generator",
        on_handoff=_on_scheduler_handoff,
        input_type=SchedulingIntent,
    )
    orchestrator: Agent[CourseWorkflowContext] = Agent(
        name="Course Scheduling Orchestrator",
        instructions=ORCHESTRATOR_INSTRUCTIONS,
        model=model,
        handoffs=[scheduler_handoff],
        model_settings=ModelSettings(tool_choice=scheduler_handoff.tool_name),
    )

    orchestrator_handoff = handoff(
        orchestrator,
        on_handoff=_on_orchestrator_handoff,
    )
    conversation: Agent[CourseWorkflowContext] = Agent(
        name="Course Arrangement Conversation Agent",
        instructions=CONVERSATION_INSTRUCTIONS,
        model=model,
        handoffs=[orchestrator_handoff] if enable_workflow_handoff else [],
        model_settings=ModelSettings(),
    )
    return conversation, orchestrator, scheduler


def build_course_scheduler_agents(model: str = "gpt-4o-mini") -> tuple[Agent, Agent]:
    """Backward-compatible accessor for the handoff-connected scheduler pair."""
    _, orchestrator, scheduler = build_course_handoff_agents(model=model)
    return orchestrator, scheduler


def _workflow_input(
    messages: List[Dict[str, str]],
    conversation_context: Dict[str, Any],
    workflow_context: CourseWorkflowContext,
    *,
    ready_to_schedule: bool,
) -> str:
    execution_policy = (
        "All local checks and final confirmation passed. Decide now whether to call the "
        "Orchestrator handoff; do not claim execution started without making that handoff."
        if ready_to_schedule
        else (
            "The Orchestrator handoff is unavailable because local validation has not passed. "
            f"Address context.issue={conversation_context.get('issue')!r} directly and never "
            "claim that scheduling is confirmed, started, or running."
        )
    )
    return json.dumps(
        {
            "messages": messages,
            "context": conversation_context,
            "ready_to_schedule": ready_to_schedule,
            "execution_policy": execution_policy,
            "authoritative_data": {
                "student_count": len(workflow_context.student_courses),
                "selected_courses": sorted(workflow_context.section_counts),
                "section_counts": workflow_context.section_counts,
                "request_overrides": workflow_context.request_overrides,
            },
        },
        ensure_ascii=False,
    )


def _empty_workflow_context() -> CourseWorkflowContext:
    return CourseWorkflowContext(
        student_courses={},
        section_counts={},
        request_overrides={},
        parsed_spreadsheets=[],
    )


async def run_course_handoff_workflow(
    messages: List[Dict[str, str]],
    conversation_context: Dict[str, Any],
    *,
    model: str = "gpt-4o-mini",
    workflow_context: Optional[CourseWorkflowContext] = None,
    ready_to_schedule: bool = False,
) -> CourseWorkflowRunResult:
    context = workflow_context or _empty_workflow_context()
    conversation, _, _ = build_course_handoff_agents(
        model=model,
        enable_workflow_handoff=ready_to_schedule,
    )
    with trace(
        "CourseArrangement Workflow",
        group_id="course-arrangement-gradio",
        metadata={
            "model": model,
            "message_count": str(len(messages)),
            "issue": str(conversation_context.get("issue") or ""),
            "ready_to_schedule": str(ready_to_schedule).lower(),
        },
    ):
        result = await Runner.run(
            conversation,
            _workflow_input(
                messages,
                conversation_context,
                context,
                ready_to_schedule=ready_to_schedule,
            ),
            context=context,
            max_turns=10 if ready_to_schedule else 3,
        )

    message = str(result.final_output)
    if not ready_to_schedule:
        return CourseWorkflowRunResult(message=message)
    if context.request is None or context.result is None:
        raise RuntimeError(
            f"Handoff workflow ended before scheduling completed (stage={context.stage})."
        )

    context.stage = "completed"
    schedule = ScheduleRunResult(
        structured_request=context.request,
        schedule_summary=message,
        best_schedule=context.result["best_candidate"],
        candidates=context.result["candidates"],
        retry_rounds_used=context.result["retry_rounds_used"],
        accepted=context.result["accepted"],
        parsed_spreadsheets=context.parsed_spreadsheets,
    )
    return CourseWorkflowRunResult(message=message, schedule=schedule)


def run_course_handoff_workflow_sync(
    messages: List[Dict[str, str]],
    conversation_context: Dict[str, Any],
    *,
    model: str = "gpt-4o-mini",
    workflow_context: Optional[CourseWorkflowContext] = None,
    ready_to_schedule: bool = False,
) -> CourseWorkflowRunResult:
    return asyncio.run(
        run_course_handoff_workflow(
            messages,
            conversation_context,
            model=model,
            workflow_context=workflow_context,
            ready_to_schedule=ready_to_schedule,
        )
    )


async def run_course_conversation_agent(
    messages: List[Dict[str, str]],
    context: Dict[str, Any],
    model: str = "gpt-4o-mini",
) -> str:
    result = await run_course_handoff_workflow(
        messages,
        context,
        model=model,
        ready_to_schedule=False,
    )
    return result.message


def run_course_conversation_agent_sync(
    messages: List[Dict[str, str]],
    context: Dict[str, Any],
    model: str = "gpt-4o-mini",
) -> str:
    return asyncio.run(
        run_course_conversation_agent(
            messages=messages,
            context=context,
            model=model,
        )
    )


class CourseSchedulerAgentTeam:
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.orchestrator_agent, self.scheduler_agent = build_course_scheduler_agents(model=model)

    async def run(
        self,
        user_message: str,
        uploaded_file_paths: Optional[List[str]] = None,
        request_overrides: Optional[Dict[str, Any]] = None,
    ) -> ScheduleRunResult:
        parsed_spreadsheets = [
            parse_course_spreadsheet(path) for path in (uploaded_file_paths or [])
        ]
        parsed_student_courses: Dict[str, List[str]] = {}
        parsed_section_counts: Dict[str, int] = {}
        for spreadsheet in parsed_spreadsheets:
            parsed_student_courses.update(spreadsheet.student_courses)
            parsed_section_counts.update(spreadsheet.section_counts)

        if uploaded_file_paths and not parsed_student_courses:
            warnings = [
                warning
                for spreadsheet in parsed_spreadsheets
                for warning in spreadsheet.warnings
            ]
            suffix = f" Warnings: {'; '.join(warnings)}" if warnings else ""
            raise ValueError(
                "Uploaded student course file did not contain recognizable student course selections."
                + suffix
            )

        context = CourseWorkflowContext(
            student_courses=parsed_student_courses,
            section_counts=parsed_section_counts,
            request_overrides=dict(request_overrides or {}),
            parsed_spreadsheets=parsed_spreadsheets,
            stage="orchestrator",
        )
        scheduler_result = await Runner.run(
            self.orchestrator_agent,
            json.dumps({"messages": [{"role": "user", "content": user_message}]}, ensure_ascii=False),
            context=context,
            max_turns=8,
        )
        if context.request is None or context.result is None:
            raise RuntimeError("Scheduler handoff workflow completed without a schedule.")

        return ScheduleRunResult(
            structured_request=context.request,
            schedule_summary=str(scheduler_result.final_output),
            best_schedule=context.result["best_candidate"],
            candidates=context.result["candidates"],
            retry_rounds_used=context.result["retry_rounds_used"],
            accepted=context.result["accepted"],
            parsed_spreadsheets=parsed_spreadsheets,
        )


async def run_course_scheduler_agent_team(
    user_message: str,
    uploaded_file_paths: Optional[List[str]] = None,
    model: str = "gpt-4o-mini",
    request_overrides: Optional[Dict[str, Any]] = None,
) -> ScheduleRunResult:
    team = CourseSchedulerAgentTeam(model=model)
    with trace(
        "CourseArrangement Scheduler",
        group_id="course-arrangement-gradio",
        metadata={
            "model": model,
            "uploaded_file_count": str(len(uploaded_file_paths or [])),
            "has_request_overrides": str(bool(request_overrides)).lower(),
            "override_keys": ",".join(sorted((request_overrides or {}).keys())),
        },
    ):
        return await team.run(
            user_message,
            uploaded_file_paths=uploaded_file_paths,
            request_overrides=request_overrides,
        )


def run_course_scheduler_agent_team_sync(
    user_message: str,
    uploaded_file_paths: Optional[List[str]] = None,
    model: str = "gpt-4o-mini",
    request_overrides: Optional[Dict[str, Any]] = None,
) -> ScheduleRunResult:
    return asyncio.run(
        run_course_scheduler_agent_team(
            user_message=user_message,
            uploaded_file_paths=uploaded_file_paths,
            model=model,
            request_overrides=request_overrides,
        )
    )
