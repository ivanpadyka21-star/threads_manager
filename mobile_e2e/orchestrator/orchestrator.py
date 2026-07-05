"""The end-to-end pipeline: session -> read -> generate -> type.

:class:`TaskOrchestrator` ties the previous stages together. A single call to
:meth:`TaskOrchestrator.run_workflow` opens a proxied Appium session, reads text
from the screen, asks an :class:`AIAgent` for a styled reply, and types that
reply back into the UI -- cleaning the session up afterwards no matter what.

Failures are captured into a :class:`WorkflowResult` rather than raised, so a
batch of profiles keeps running even if one of them fails.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional

from mobile_e2e.ai.agent import AIAgent
from mobile_e2e.config.settings import AppiumSettings, get_settings
from mobile_e2e.core.exceptions import E2EFrameworkError
from mobile_e2e.core.session_manager import SessionManager
from mobile_e2e.utils.logger import get_logger
from mobile_e2e.workers.base_worker import Locator
from mobile_e2e.workers.ui_worker import UIWorker

LOG = get_logger(__name__)


@dataclass
class Profile:
    """A single, independently schedulable unit of work.

    Attributes:
        name: Human-readable identifier used in logs and results.
        proxy_string: Proxy as ``IP:Port:Login:Password`` (or ``None``).
        read_locator: Locator of the element to read context text from.
        input_locator: Locator of the field to type the reply into.
        submit_locator: Optional submit button; ``ENTER`` is used if omitted.
        agent: Optional per-profile agent (e.g. its own tone of voice). Falls
            back to the orchestrator's default agent when ``None``.
    """

    name: str
    proxy_string: Optional[str]
    read_locator: Locator
    input_locator: Locator
    submit_locator: Optional[Locator] = None
    agent: Optional[AIAgent] = None


@dataclass
class WorkflowResult:
    """Outcome of one workflow run."""

    name: str
    ok: bool
    screen_text: Optional[str] = None
    response: Optional[str] = None
    error: Optional[BaseException] = None


class TaskOrchestrator:
    """Run the read -> generate -> type pipeline, for one or many profiles.

    Args:
        agent: Default :class:`AIAgent` used when a profile has none of its own.
        settings: Appium settings; env-derived defaults when omitted.
        char_delay: Per-character typing delay handed to the UIWorker.
        retry_attempts: UIWorker retry attempts for flaky lookups.
        timeout: UIWorker explicit-wait timeout in seconds.
    """

    def __init__(
        self,
        agent: AIAgent,
        *,
        settings: Optional[AppiumSettings] = None,
        char_delay: float = 0.05,
        retry_attempts: int = 3,
        timeout: int = 15,
    ) -> None:
        self._agent = agent
        self._settings = settings or get_settings()
        self._char_delay = char_delay
        self._retry_attempts = retry_attempts
        self._timeout = timeout

    # -- single workflow ----------------------------------------------------
    def run_workflow(
        self,
        proxy_string: Optional[str],
        read_locator: Locator,
        input_locator: Locator,
        *,
        submit_locator: Optional[Locator] = None,
        agent: Optional[AIAgent] = None,
        name: str = "workflow",
    ) -> WorkflowResult:
        """Execute the full pipeline once.

        1. Start a proxied Appium session via :class:`SessionManager`.
        2. Read context text from ``read_locator`` with a :class:`UIWorker`.
        3. Generate a styled reply with the (per-call or default) agent.
        4. Type the reply into ``input_locator`` and submit.

        The session is always torn down. Framework and unexpected errors are
        captured into the returned :class:`WorkflowResult` (never raised).
        """
        active_agent = agent or self._agent
        try:
            with SessionManager(self._settings) as manager:
                driver = manager.create_session(proxy=proxy_string)
                ui = UIWorker(
                    driver,
                    timeout=self._timeout,
                    char_delay=self._char_delay,
                    retry_attempts=self._retry_attempts,
                )

                screen_text = ui.read_screen_text(read_locator)
                LOG.info("[%s] read %d chars from screen", name, len(screen_text))

                response = active_agent.generate_response(screen_text)
                LOG.info("[%s] generated reply (%d chars)", name, len(response))

                ui.type_and_submit(
                    input_locator, response, submit_locator=submit_locator
                )
                LOG.info("[%s] reply submitted", name)

            return WorkflowResult(
                name=name, ok=True, screen_text=screen_text, response=response
            )
        except E2EFrameworkError as exc:
            LOG.error("[%s] workflow failed: %s", name, exc)
            return WorkflowResult(name=name, ok=False, error=exc)
        except Exception as exc:  # noqa: BLE001 - isolate unexpected failures
            LOG.exception("[%s] workflow crashed", name)
            return WorkflowResult(name=name, ok=False, error=exc)

    def run_profile(self, profile: Profile) -> WorkflowResult:
        """Run :meth:`run_workflow` from a :class:`Profile`."""
        return self.run_workflow(
            profile.proxy_string,
            profile.read_locator,
            profile.input_locator,
            submit_locator=profile.submit_locator,
            agent=profile.agent,
            name=profile.name,
        )

    # -- batches ------------------------------------------------------------
    def run_profiles(
        self,
        profiles: List[Profile],
        *,
        parallel: bool = False,
        max_workers: int = 4,
    ) -> List[WorkflowResult]:
        """Run many profiles sequentially or in parallel.

        Each profile gets its own session (its own proxy), so parallel runs are
        fully isolated. Results are returned for every profile regardless of
        individual failures.
        """
        if not parallel:
            return [self.run_profile(p) for p in profiles]

        results: List[WorkflowResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self.run_profile, p): p for p in profiles}
            for future in as_completed(futures):
                results.append(future.result())
        return results
