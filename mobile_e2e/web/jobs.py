"""A tiny in-memory job runner for background workflow executions.

Appium runs can take a while, so the site starts a job and polls it rather than
blocking the HTTP request. Jobs are kept in memory (single-process dev server);
for a multi-worker deployment swap this for Redis/RQ or Celery.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from mobile_e2e.web.service import WebService, WorkflowRequest


@dataclass
class Job:
    """State of a single background workflow run."""

    id: str
    status: str = "running"  # running | done | error
    logs: List[str] = field(default_factory=list)
    result: Optional[dict] = None
    error: Optional[str] = None
    account_id: Optional[int] = None

    def snapshot(self) -> dict:
        """A JSON-serialisable copy safe to hand to the browser."""
        return {
            "id": self.id,
            "status": self.status,
            "logs": list(self.logs),
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    """Creates, runs and tracks background jobs."""

    def __init__(
        self,
        service: Optional[WebService] = None,
        on_done: Optional[Callable[[Job], None]] = None,
    ) -> None:
        self._service = service or WebService()
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        # Called when a job finishes (used to record analytics events).
        self._on_done = on_done

    def submit(self, req: WorkflowRequest, account_id: Optional[int] = None) -> Job:
        """Validate the request, start a background run and return the job.

        Raises:
            ValueError: If validation fails (before any thread is started).
        """
        # Validate synchronously so bad input becomes an immediate 400.
        self._service.validate(req)

        job = Job(id=uuid.uuid4().hex, account_id=account_id)
        with self._lock:
            self._jobs[job.id] = job

        thread = threading.Thread(
            target=self._run, args=(job, req), daemon=True
        )
        thread.start()
        return job

    def _run(self, job: Job, req: WorkflowRequest) -> None:
        def on_log(line: str) -> None:
            with self._lock:
                job.logs.append(line)

        try:
            result = self._service.run(req, on_log=on_log)
            with self._lock:
                job.result = result
                job.status = "done"
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            with self._lock:
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "error"
        if self._on_done is not None:
            try:
                self._on_done(job)
            except Exception:  # noqa: BLE001 - analytics must not break a run
                pass

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)
