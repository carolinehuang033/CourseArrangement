---
title: Course Arrangement
emoji: 📚
colorFrom: blue
colorTo: indigo
sdk: gradio
app_file: app.py
pinned: false
---

# Course Arrangement

An AI-assisted course scheduling application built with Gradio and the OpenAI Agents SDK.

## Features

- Upload separate student-selection and course-section CSV/Excel files
- Collect scheduling constraints through a conversational agent
- Agent handoffs from Conversation to Orchestrator to Scheduler
- Generate and compare multiple timetable candidates
- Download the final schedule as an Excel workbook
- Inspect metrics, structured inputs, and candidate results

## Run locally

```bash
python -m pip install -r requirements.txt
python app.py
```

Create a local `.env` file:

```dotenv
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4o-mini
```

Never commit `.env` or API keys. For Hugging Face Spaces, configure these values as Space secrets.

## Input files

- Student selections: a student identifier column plus course columns
- Section counts: `course` and `section_count` columns

## Deployment

The included `render.yaml` deploys the app with FastAPI and mounts Gradio at:

```text
/course-arrangement/
```

Set `OPENAI_API_KEY` as a Render secret. A custom domain can then serve the application at
`https://your-domain.com/course-arrangement/`.
