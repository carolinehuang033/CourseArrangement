"""LLM-path smoke test for the course-arrangement project.

Exercises both the OpenAI-SDK direct path (course_arrange_openai_example.py)
and the openai-agents path (course_scheduler_agents.CourseSchedulerAgentTeam)
against the bundled test_data. We expect these to fail today because the
project's .env has an invalid OpenAI key, but the goal is to surface the
exact failure mode for the user.

Outputs: writes a JSON report to test_data/llm_smoke_report.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Load .env so OPENAI_API_KEY etc. are visible to openai/openai-agents.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception as exc:  # noqa: BLE001
    print(f"[WARN] could not load .env: {exc}")

REPORT: dict = {"started_at": time.time(), "tests": {}}


def _record(name: str, ok: bool, **fields) -> None:
    REPORT["tests"][name] = {"ok": ok, **fields}
    status = "OK " if ok else "FAIL"
    print(f"[{status}] {name}: {json.dumps(fields, ensure_ascii=False, default=str)[:500]}")


# ---------------------------------------------------------------------------
# 1) Plain OpenAI SDK path
# ---------------------------------------------------------------------------
def test_openai_example() -> None:
    name = "openai_example_script"
    print(f"\n=== {name} ===")
    try:
        from openai import OpenAI
        # mimic what course_arrange_openai_example.py does but stay non-fatal
        client = OpenAI()
        t0 = time.time()
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=4,
        )
        _record(name, True, latency_s=time.time() - t0, reply=resp.choices[0].message.content)
    except Exception as exc:  # noqa: BLE001
        _record(name, False, error_type=type(exc).__name__, error=str(exc)[:600])


# ---------------------------------------------------------------------------
# 2) openai-agents path (what the Gradio UI runs)
# ---------------------------------------------------------------------------
def test_agent_team() -> None:
    name = "agent_team_path"
    print(f"\n=== {name} ===")
    try:
        from course_scheduler_agents import CourseSchedulerAgentTeam
        team = CourseSchedulerAgentTeam(model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
        user_prompt = (ROOT / "test_data" / "test_user_prompt.md").read_text()
        t0 = time.time()
        result = asyncio.run(
            team.run(
                user_message=user_prompt,
                uploaded_file_paths=[
                    str(ROOT / "test_data" / "course_students.csv"),
                    str(ROOT / "test_data" / "course_sections.csv"),
                ],
            )
        )
        _record(name, True, latency_s=time.time() - t0, summary=str(result)[:500])
    except Exception as exc:  # noqa: BLE001
        _record(name, False, error_type=type(exc).__name__, error=str(exc)[:1200])
        tb = traceback.format_exc()
        # store compact traceback excerpt (skip the noisy gradio frames if any)
        REPORT["tests"][name]["traceback_tail"] = "\n".join(tb.splitlines()[-30:])


def main() -> int:
    test_openai_example()
    test_agent_team()
    REPORT["finished_at"] = time.time()
    out = ROOT / "test_data" / "llm_smoke_report.json"
    with out.open("w") as f:
        json.dump(REPORT, f, indent=2, default=str, ensure_ascii=False)
    print(f"\n[INFO] full report -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
