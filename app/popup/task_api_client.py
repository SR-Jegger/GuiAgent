"""Lightweight client for existing FastAPI task endpoints."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional
from urllib import error, request


DEFAULT_PORT = int(os.getenv("GUI_AGENT_SERVER_PORT", "8000"))
DEFAULT_HOSTS = [
    os.getenv("GUI_AGENT_SERVER_HOST"),
    "127.0.0.1",
    "localhost",
    "192.168.137.1",
]


@dataclass
class TaskCancelResult:
    success: bool
    message: str = ""
    status_code: Optional[int] = None


class TaskApiClient:
    """Calls the existing FastAPI task endpoints without duplicating backend logic."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 5.0):
        self.base_url = base_url or os.getenv("GUI_AGENT_API_BASE_URL")
        self.timeout = timeout

    def cancel_task(self, task_id: str) -> TaskCancelResult:
        """Call POST /api/v1/tasks/{task_id}/cancel."""
        errors: list[str] = []

        for api_base in self._candidate_api_bases():
            try:
                req = request.Request(
                    url=f"{api_base}/tasks/{task_id}/cancel",
                    data=b"",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with request.urlopen(req, timeout=self.timeout) as response:
                    payload = response.read().decode("utf-8")
                    data = json.loads(payload) if payload else {}
                    return TaskCancelResult(
                        success=bool(data.get("success", True)),
                        message="任务取消请求已提交",
                        status_code=response.status,
                    )
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore")
                message = body or exc.reason
                return TaskCancelResult(
                    success=False,
                    message=message,
                    status_code=exc.code,
                )
            except Exception as exc:
                errors.append(f"{api_base}: {exc}")

        try:
            from fastapi.testclient import TestClient
            from app.server import app

            with TestClient(app) as client:
                response = client.post(f"/api/v1/tasks/{task_id}/cancel")
                if response.ok:
                    return TaskCancelResult(
                        success=True,
                        message="任务取消请求已提交",
                        status_code=response.status_code,
                    )
                return TaskCancelResult(
                    success=False,
                    message=response.text,
                    status_code=response.status_code,
                )
        except Exception as exc:
            errors.append(f"in-process fallback: {exc}")

        return TaskCancelResult(
            success=False,
            message="; ".join(errors) or "取消请求失败",
        )

    def _candidate_api_bases(self) -> list[str]:
        if self.base_url:
            return [self.base_url.rstrip("/")]

        candidates = []
        for host in DEFAULT_HOSTS:
            if not host:
                continue
            candidates.append(f"http://{host}:{DEFAULT_PORT}/api/v1")
        return candidates
