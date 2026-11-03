from fastapi import BackgroundTasks, FastAPI, Request

from ..events import forward_pending


def forward_now(app: FastAPI) -> int:
    return forward_pending(app.state.engine, app.state.settings, app.state.feedback_sender)


def schedule_forward(background: BackgroundTasks, request: Request) -> None:
    """Forward new events to the feedback endpoint after the response is sent."""
    background.add_task(forward_now, request.app)
