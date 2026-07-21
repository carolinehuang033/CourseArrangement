"""Minimal OpenAI SDK example for the course-arrangement tool.

Run with:
    OPENAI_MODEL=<your-model> python course_arrange_openai_example.py
"""

import os

from openai import OpenAI

try:
    from .course_arrange_tool import TOOLS, execute_tool_call
except ImportError:
    from course_arrange_tool import TOOLS, execute_tool_call


STUDENT_COURSES = {
    "S001": ["ELA 11", "AP Calculus BC", "AP Chemistry"],
    "S002": ["ELA 11", "AP Calculus BC", "AP Biology"],
    "S003": ["ELA 11 Honors", "AP Calculus BC", "AP Chemistry"],
    "S004": ["ELA 11 Honors", "AP Statistics", "AP Biology"],
}

SECTION_COUNTS = {
    "ELA 11": 1,
    "ELA 11 Honors": 1,
    "AP Calculus BC": 2,
    "AP Chemistry": 1,
    "AP Biology": 1,
    "AP Statistics": 1,
}


def main() -> None:
    model = os.environ.get("OPENAI_MODEL")
    if not model:
        raise RuntimeError("Set OPENAI_MODEL before running this example.")

    client = OpenAI()
    messages = [
        {
            "role": "user",
            "content": (
                "Please call arrange_courses with these exact inputs, then summarize the "
                "satisfaction rate and conflicts:\n"
                f"student_courses={STUDENT_COURSES}\n"
                f"section_counts={SECTION_COUNTS}\n"
                "num_time_slots=4\n"
                "block_ban_map={'AP Calculus BC': [0]}\n"
                "forbidden_course_groups=[['AP Chemistry', 'AP Biology']]\n"
                "seed=42\n"
                "max_iterations=1000"
            ),
        }
    ]

    first_response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=TOOLS,
        tool_choice={"type": "function", "function": {"name": "arrange_courses"}},
    )

    assistant_message = first_response.choices[0].message
    messages.append(assistant_message)

    for tool_call in assistant_message.tool_calls or []:
        tool_output = execute_tool_call(tool_call)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_output,
            }
        )

    final_response = client.chat.completions.create(
        model=model,
        messages=messages,
    )
    print(final_response.choices[0].message.content)


if __name__ == "__main__":
    main()
