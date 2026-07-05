"""A minimal, dependency-free scheduler for running session jobs concurrently.

Each :class:`Job` describes one session (its proxy and the worker callable to
run against the driver). :class:`SessionScheduler` runs jobs across a bounded
thread pool, giving every job an isolated :class:`SessionManager` so a failure
in one session never leaks a driver into another.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, List, Optional

from mobile_e2e.config.settings import AppiumSettings, get_settings
from mobile_e2e.core.exceptions import E2EFrameworkError
from mobile_e2e.core.proxy import ProxyConfig
from mobile_e2e.core.session_manager import ProxyLike, SessionManager
from mobile_e2e.utils.logger import get_logger

if TYPE_CHECKING:
    from appium.webdriver.webdriver import WebDriver

LOG = get_logger(__name__)

# A test body: receives the live driver, returns whatever it likes.
JobCallable = Callable[["WebDriver"], object]


@dataclass
class Job:
    """A single unit of scheduled work.

    Attributes:
        name: Human-readable job name (used in logs / results).
        run: Callable invoked with the live WebDriver.
        proxy: Optional per-job proxy (string or :class:`ProxyConfig`).
    """

    name: str
    run: JobCallable
    proxy: ProxyLike = None


@dataclass
class JobResult:
    """Outcome of a single job."""

    name: str
    ok: bool
    value: object = None
    error: Optional[BaseException] = None


@dataclass
class SessionScheduler:
    """Run :class:`Job` objects concurrently, each in its own session."""

    settings: AppiumSettings = field(default_factory=get_settings)
    max_workers: int = 4

    def run(self, jobs: List[Job]) -> List[JobResult]:
        """Execute all jobs and return their results (order not guaranteed)."""
        results: List[JobResult] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._run_job, job): job for job in jobs}
            for future in as_completed(futures):
                results.append(future.result())
        return results

    def _run_job(self, job: Job) -> JobResult:
        LOG.info("Job %r starting", job.name)
        try:
            with SessionManager(self.settings) as manager:
                driver = manager.create_session(proxy=job.proxy)
                value = job.run(driver)
            LOG.info("Job %r finished", job.name)
            return JobResult(name=job.name, ok=True, value=value)
        except E2EFrameworkError as exc:
            LOG.error("Job %r failed: %s", job.name, exc)
            return JobResult(name=job.name, ok=False, error=exc)
        except Exception as exc:  # noqa: BLE001 - isolate unexpected job errors
            LOG.exception("Job %r crashed", job.name)
            return JobResult(name=job.name, ok=False, error=exc)
