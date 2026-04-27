"""GUI Agent application package."""

__all__ = ["app", "AgentApplicationService", "Task", "TaskStatus"]


def __getattr__(name):
    if name == "app":
        from app.server import app as fastapi_app

        return fastapi_app
    if name in {"AgentApplicationService", "Task", "TaskStatus"}:
        from app.services import AgentApplicationService, Task, TaskStatus

        return {
            "AgentApplicationService": AgentApplicationService,
            "Task": Task,
            "TaskStatus": TaskStatus,
        }[name]
    raise AttributeError(f"module 'app' has no attribute {name!r}")
