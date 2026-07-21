"""Full agent-team run, captures complete structured output for review."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from course_scheduler_agents import CourseSchedulerAgentTeam  # noqa: E402


async def main() -> None:
    team = CourseSchedulerAgentTeam(model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    user_prompt = (ROOT / "test_data" / "test_user_prompt.md").read_text()
    t0 = time.time()
    result = await team.run(
        user_message=user_prompt,
        uploaded_file_paths=[
            str(ROOT / "test_data" / "course_students.csv"),
            str(ROOT / "test_data" / "course_sections.csv"),
        ],
    )
    dt = time.time() - t0
    out = ROOT / "test_data" / "agent_full_result.json"
    payload = {
        "latency_s": dt,
        "structured_request": result.structured_request.model_dump() if hasattr(result, "structured_request") else None,
        "schedule": result.schedule if hasattr(result, "schedule") else None,
        "summary": getattr(result, "summary", None),
        "satisfaction_rate": getattr(result, "satisfaction_rate", None),
        "conflict_count": getattr(result, "conflict_count", None),
        "candidate_count": getattr(result, "candidate_count", None),
        "retry_rounds_used": getattr(result, "retry_rounds_used", None),
        "accepted": getattr(result, "accepted", None),
        "all_metrics": getattr(result, "all_metrics", None),
        "diagnostics": getattr(result, "diagnostics", None),
        "raw": str(result),
    }
    with out.open("w") as f:
        json.dump(payload, f, indent=2, default=str, ensure_ascii=False)
    print(f"[OK] {dt:.1f}s -> {out}")
    print(f"  satisfaction_rate={payload['satisfaction_rate']}")
    print(f"  conflict_count={payload['conflict_count']}")
    print(f"  accepted={payload['accepted']}")
    print(f"  candidate_count={payload['candidate_count']}")
    print(f"  retry_rounds_used={payload['retry_rounds_used']}")


if __name__ == "__main__":
    asyncio.run(main())
