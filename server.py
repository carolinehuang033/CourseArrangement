from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import gradio as gr

from app import APP_CSS, OUTPUT_DIR, demo


app = FastAPI(title="Caroline Huang Portfolio")


@app.get("/", include_in_schema=False)
def portfolio_root() -> RedirectResponse:
    return RedirectResponse(url="/course-arrangement/")


app = gr.mount_gradio_app(
    app,
    demo,
    path="/course-arrangement",
    css=APP_CSS,
    allowed_paths=[str(OUTPUT_DIR)],
)
