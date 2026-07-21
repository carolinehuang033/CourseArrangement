# CourseArrangement Project Documentation

This document is the handoff note for future agents working on this project. It records the current project goal, architecture, UI behavior, data contracts, recent fixes, and known next steps.

## 1. Project Goal

CourseArrangement is a local course scheduling assistant.

The intended user workflow is:

1. The user opens a Gradio chat UI.
2. The user uploads a student course-selection spreadsheet.
3. The user describes scheduling requirements in natural language, for example:
   - each course should have how many sections
   - two or more courses should not be in the same time slot
   - a course cannot appear in a specific slot
   - preferred time slot count or random seed
4. The app checks whether the uploaded data and user prompt are complete enough.
5. If information is missing or conflicts with advanced settings, the app asks a follow-up question instead of running.
6. If information is complete, the OpenAI agent converts the text into a structured `CourseSchedulingRequest`.
7. The local scheduler tool generates candidate timetables and returns a best result.
8. The UI shows a short chat summary plus detailed schedule/metrics JSON in a collapsible results area.

The project should feel like a real chat agent, not a form-first tool.

## 2. Current File Map

- `app.py`
  - Main Gradio app.
  - Builds the chat interface.
  - Handles uploads, advanced settings, progress messages, missing-info checks, and calls the agent team.

- `course_scheduler_agents.py`
  - OpenAI Agents SDK integration.
  - Parses uploaded spreadsheets.
  - Defines `CourseSchedulingRequest`.
  - Converts natural language into structured scheduler input.
  - Runs multiple local scheduling candidates.
  - Asks a second lightweight agent to summarize the candidate result.

- `course_arrange_tool.py`
  - Core local scheduling algorithm.
  - Exposes `arrange_courses(...)`.
  - Exposes tool schemas in `TOOLS` and `RESPONSES_TOOLS`.
  - Contains `execute_tool_call(...)` for OpenAI tool-call style usage.

- `course_arrange_openai_example.py`
  - Example OpenAI usage file. Not the main UI entry point.

- `requirements.txt`
  - Python package dependencies.

- `.env`
  - Local environment config. It should contain `OPENAI_API_KEY` and optionally `OPENAI_MODEL`.
  - Do not paste real keys into documentation, commits, or chat output.

- `test_data/course_students.csv`
  - Sample student-course upload file.

- `test_data/course_sections.csv`
  - Sample course-section count file from the older workflow.
  - The current UI no longer expects a separate section-count upload; section counts should come from user chat text or from the uploaded file if present.

- `test_data/test_user_prompt.md`
  - Sample prompt text.

## 3. How To Run

From the project root:

```bash
cd /Users/carolinehuang/projects/CourseArrangement
uv pip install -r requirements.txt
uv run app.py
```

Alternative:

```bash
python app.py
```

The app binds to `127.0.0.1` and searches for an available port from `7860` through `8060`. If `7860` is busy, Gradio may start on `7861`, `7862`, etc. Check the terminal output for the exact URL.

Required environment:

```bash
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o-mini
```

`OPENAI_MODEL` defaults to `gpt-4o-mini` if omitted.

## 4. Main User Experience Requirements

The user asked for the Gradio UI to be completely remade as a chat interface.

Current UI requirements implemented in `app.py`:

- Chat messages:
  - user prompt appears on the right
  - agent reply appears on the left
  - default agent welcome message appears when the app opens
  - every user message should be answered by an agent-style reply from the beginning of the conversation
  - the agent should keep session context across turns, including earlier user requirements, uploaded file metadata, recognized courses, and section counts
  - normal agent replies should show only a small animated three-dot typing bubble while waiting, not a text status such as "正在让 agent..."
  - real scheduling progress should be split into separate assistant messages instead of repeatedly overwriting one bubble
  - user bubble is pale yellow, not black
  - agent bubble is white with a light border

- Upload panel:
  - independent file upload component above the chatbox
  - shows upload status and parsed counts, e.g. recognized students and courses
  - updates hidden memory without adding a user chat bubble

- Composer/input:
  - independent `gr.Group` container with `elem_id="composer"`
  - white background
  - 24px rounded corners
  - light gray thin border
  - subtle shadow
  - wide text input above a bottom toolbar

- Bottom toolbar:
  - right side: black circular send button with upward arrow `"↑"`
  - send is the only visually primary action button
  - toolbar wrapper is styled transparent

- Advanced settings:
  - kept in a collapsed accordion
  - labels are short and user-friendly
  - currently:
    - `Time slots`
    - `Seed`
    - `大课线`
    - `小课线`
    - `班底`
    - `班顶`
  - max iterations, disabled rules, avoid-same-slot JSON, and course-name-map JSON were removed from advanced UI.

- Results:
  - collapsed `结果详情` accordion
  - includes summary textbox, schedule dataframe, metrics JSON, structured request JSON, candidate metrics JSON

## 5. Chat Flow In `app.py`

Main function: `respond(...)`.

Important behavior:

1. It appends the user message to `chat_state`.
2. It updates hidden `memory_state` with all user messages, known requirement text, parsed section counts, uploaded file path, recognized selected courses, student count, and file warnings.
3. It immediately shows a small animated three-dot typing bubble while the conversation agent replies.
4. If the app cannot schedule yet, it calls the conversation agent to reply naturally instead of returning a hardcoded form-style message.
5. If no student file was uploaded, the conversation agent explains that the user can keep describing needs but must upload a student course-selection file before final scheduling.
6. It yields separate progress messages during long-running scheduling work:
   - `正在检查上传文件和课程分班数...`
   - `正在整理你的需求，转换成结构化排课输入...`
   - `正在调用排课 agent...`
7. It checks for conflicts between user text and advanced options:
   - if the prompt mentions a different number of time slots than the slider
   - if the prompt mentions a different seed than the advanced field
8. It checks whether every course in the uploaded student file has a section count in user messages.
9. If section counts are missing, the conversation agent asks the user to provide them.
10. If `OPENAI_API_KEY` is missing, it asks the user to fill `.env`.
11. It calls `run_course_scheduler_agent_team_sync(...)`.
12. If any exception occurs, it shows a short chat error: `运行时出错：...`.
13. On success, it appends the final summary and fills the result detail components.

Important current caveat:

- The decision about whether scheduling is allowed is still deterministic and regex-based, but the user-facing clarification reply is generated by the conversation agent.
- The section-count recognizer reads only user messages and recognizes simple lines like `AP Calculus BC 2个班` or `AP Calculus BC: 2`.
- It may not understand every natural-language way of expressing section counts.
- If this becomes a UX issue, improve `_parse_section_counts(...)` or move this completeness check into the agent.

## 6. Uploaded Student File Format

Parsing is in `course_scheduler_agents.py`.

Supported file types:

- `.csv`
- `.tsv`
- `.xlsx`
- `.xlsm`
- `.xls`

Recognized student columns include:

- `student_id`
- `student`
- `id`
- `学号`
- `学生`
- `姓名`
- `name`

Recognized course columns include:

- `course`
- `课程`
- `subject`

If there is a student column and a course column, each row maps one student to one or more courses.

If there is a student column but no exact course column, all other columns are treated as course-selection cells.

If there is no student column, each row is treated as a synthetic student named `row_1`, `row_2`, etc.

Course cells can contain multiple courses separated by:

- newline
- semicolon
- Chinese semicolon
- vertical bar
- Chinese list separator

## 7. Structured Request Contract

`CourseSchedulingRequest` lives in `course_scheduler_agents.py`.

Current fields:

```python
student_courses: Dict[str, List[str]]
section_counts: Dict[str, int]
num_time_slots: int = 7
block_ban_map: Dict[str, List[int]] = {}
forbidden_course_groups: List[List[str]] = []
course_name_map: Dict[str, str] = {}
min_students_threshold: int = 25
max_students_threshold: int = 65
min_students_per_section: int = 12
max_students_per_section: int = 30
cost_weights: CostWeights
max_iterations: int = 20000
seed: Optional[int] = None
candidate_runs: int = 3
max_conflict_count: Optional[int] = 0
min_satisfaction_rate: Optional[float] = None
include_schedule: bool = True
include_section_loads: bool = True
include_diagnostics: bool = False
```

Important meanings:

- `student_courses`: map of student ID/name to selected course names.
- `section_counts`: map of course name to number of sections.
- `num_time_slots`: number of time slots.
- `block_ban_map`: course-to-banned-slot mapping.
  - Correct form: `{"AP Calculus BC": [0, 4]}`
  - Slot indices are zero-based.
- `forbidden_course_groups`: course groups that should not be in the same slot.
  - Correct form: `[["AP Chemistry", "AP Biology"]]`
- `course_name_map`: optional mapping from uploaded names to canonical names.
- `min_students_threshold`: if total enrollment is above this value, treat as a large course.
- `max_students_threshold`: if total enrollment is below this value, treat as a small/medium course.
- `min_students_per_section`: large-course section minimum.
- `max_students_per_section`: small/medium-course section maximum.
- `candidate_runs`: number of candidate schedules per round.
- `max_conflict_count`: acceptance criterion.
- `min_satisfaction_rate`: optional acceptance criterion.

## 8. Recent Important Fixes

### Removed hardcoded demo data

`course_arrange_tool.py` previously contained a huge embedded return value like:

```python
return """237名学生的完整选课数据"""
return {1: [...], 2: [...], ...}
```

That old embedded data was removed from the active tool path. The tool now expects real input from the caller.

### Import cleanup

Imports were adjusted so modules can run both as package-style imports and direct script imports.

Example pattern:

```python
try:
    from .course_arrange_tool import arrange_courses
except ImportError:
    from course_arrange_tool import arrange_courses
```

### Requirements and environment

`requirements.txt` was added.

`.env` was added locally. Keep real secrets private.

### UI rewrite

The old Gradio UI was replaced with a chat-first UI.

The separate course-section upload was removed from the UI. Section counts should generally come from natural-language chat text, although the spreadsheet parser can still detect section-count tables if a file contains them.

### Continuous conversation agent

The user clarified that every user message should be answered by the agent, starting from the first message.

Fix:

- Added `CONVERSATION_INSTRUCTIONS` and `run_course_conversation_agent_sync(...)` in `course_scheduler_agents.py`.
- `app.py` now uses this conversation agent for missing file, invalid file, missing section counts, and advanced-setting conflict replies.
- Deterministic checks still decide whether the app can safely run the scheduler, but the visible reply is agent-generated.
- Checks for section counts, time slots, and seed now read only user messages so the agent's own clarification text does not interfere with later turns.

### Session memory

The user asked for the agent to have context memory across turns.

Implementation:

- `app.py` defines hidden `memory_state` with:
  - `user_messages`
  - `known_requirements_text`
  - `section_counts`
  - `uploaded_file_path`
  - `student_count`
  - `selected_courses`
  - `file_warnings`
- `respond(...)` updates this memory every turn.
- `handle_file_upload(...)` updates upload status and memory when the user uploads a file.
- Conversation-agent context includes `session_memory`.
- The scheduler-orchestrator prompt includes both filtered chat history and `Session memory`.
- Temporary typing HTML is filtered out before messages are sent to the agent.

### Agent max-turn issue

The user saw:

```text
agents.exceptions.MaxTurnsExceeded: Max turns (10) exceeded
```

The agent flow was simplified:

- Orchestrator now produces structured output directly.
- Scheduler summarizes already-generated candidates.
- Orchestrator max turns increased to `20`.
- Scheduler max turns set to `5`.

### Invalid `block_ban_map` JSON issue

The user saw an error where the model produced this invalid shape:

```json
{"0": ["AP Calculus BC"]}
```

But the model expects:

```json
{"AP Calculus BC": [0]}
```

Fixes:

- Orchestrator instructions now explicitly state the correct shape.
- `CourseSchedulingRequest.normalize_agent_output(...)` converts slot-to-course maps into course-to-slot maps as a fallback.
- Rules like `AP Chemistry 和 AP Biology 不要同 slot` should become `forbidden_course_groups`, not `block_ban_map`.

### Invalid `max_iterations` issue

The user saw a validation error because the model produced `max_iterations=10`, while the schema requires at least `100`.

Fix:

- `CourseSchedulingRequest.normalize_agent_output(...)` now clamps any provided `max_iterations` to at least `100`.
- If the value cannot be parsed as an integer, it is removed so the model default can apply.
- The Gradio app still hides this field from the user and overrides runtime scheduling with `max_iterations=20000`.

### Empty `student_courses` issue

The user saw:

```text
student_courses must contain at least one student with at least one course.
```

This happened when the model returned an empty `student_courses` object and the local tool received it.

Fixes:

- `app.py` now normalizes Gradio file upload values into a real filepath, handling strings, dicts, and lists.
- `app.py` parses the uploaded file before calling the agent and asks for a valid student-course file if no student selections are recognized.
- `CourseSchedulerAgentTeam.run(...)` treats uploaded spreadsheet student selections as the source of truth and forcibly writes parsed `student_courses` into the final request whenever available.
- If uploaded paths exist but no student selections are parsed, it raises a clear file-format error before calling the scheduler tool.

## 9. Local Scheduler Tool

Main function:

```python
arrange_courses(
    student_courses,
    section_counts,
    num_time_slots=7,
    block_ban_map=None,
    forbidden_course_groups=None,
    course_name_map=None,
    min_students_threshold=25,
    max_students_threshold=65,
    min_students_per_section=12,
    max_students_per_section=30,
    cost_weights=None,
    max_iterations=20000,
    seed=None,
    include_schedule=True,
    include_section_loads=True,
    include_diagnostics=False,
)
```

The scheduler:

- creates sections from `section_counts`
- assigns sections to time slots
- tries to reduce student conflicts
- respects banned slot constraints
- tries to keep sections/load balanced
- supports optional forbidden same-slot course groups
- returns metrics, optional schedule, optional section loads, and optional diagnostics

The algorithm is local and does not require OpenAI by itself. The OpenAI layer is only used to parse natural language and summarize results.

## 10. Output Shape

The best candidate is stored in:

```python
run_result.best_schedule
```

Its local scheduler payload is under:

```python
run_result.best_schedule["result"]
```

Common fields:

- `metrics`
- `schedule_by_slot`
- `section_loads`
- `diagnostics`, when requested

UI display helpers in `app.py`:

- `_summary(run_result)` creates chat summary text.
- `_schedule_table(result)` converts `schedule_by_slot` and `section_loads` into rows:
  - slot
  - section
  - course
  - estimated_students
- `_candidate_metrics(candidates)` flattens candidate metrics for JSON display.

## 11. Validation And Testing

Quick syntax check:

```bash
python -m py_compile app.py course_scheduler_agents.py course_arrange_tool.py
```

Build-app smoke test:

```bash
python - <<'PY'
from app import build_app
demo = build_app()
print(type(demo).__name__)
PY
```

Manual UI test:

1. Run `uv run app.py`.
2. Open the printed local URL.
3. Upload `test_data/course_students.csv`.
4. Paste a prompt with section counts for every selected course.
5. Confirm the chat shows progress messages.
6. Confirm result details populate after scheduling.

Known issue while testing inside Codex:

- Binding localhost may require elevated tool permission in the Codex sandbox.
- This is a sandbox limitation, not necessarily an app bug.

## 12. Known Product Decisions

- The user wants natural-language control first.
- The UI should not expose technical JSON options in advanced settings.
- `Time slots` and `Seed` remain advanced settings.
- The four scheduling thresholds remain advanced settings, but with short names:
  - `大课线`
  - `小课线`
  - `班底`
  - `班顶`
- `max_iterations` is hidden from the user and currently fixed at `20000` in `request_overrides`.
- The send button should stay visually dominant and circular.
- The upload icon should look like a file icon, not a paperclip.
- The toolbar background should be transparent.

## 13. Important Caveats For Future Agents

- Do not expose the real `.env` key in responses or documentation.
- Do not reintroduce hardcoded student-course data.
- Do not add back a separate course-section upload unless the user explicitly asks.
- If changing the Gradio CSS, verify actual rendered selectors because Gradio component internals can change between versions.
- Gradio 6 may reject old `Chatbot(type="messages")` assumptions; check the installed API before changing chatbot config.
- `respond(...)` is a generator because it yields progress states. Preserve that if progress updates are still required.
- The app currently uses simple regex completeness checks. If the user wants smarter follow-up behavior, the likely next improvement is an agent-based clarification step before scheduling.

## 14. Suggested Next Improvements

1. Replace the emoji file icon with a CSS/lucide-style SVG or custom component if the exact visual matters.
2. Improve section-count extraction from natural language beyond line-based patterns.
3. Add a small download button for generated schedule CSV/XLSX.
4. Add a deterministic local test for:
   - spreadsheet parsing
   - inverted `block_ban_map` normalization
   - missing section-count follow-up
5. Add a README for end users, separate from this agent handoff document.
