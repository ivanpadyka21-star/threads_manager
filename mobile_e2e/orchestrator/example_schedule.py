"""Runnable examples of scheduling the orchestrator pipeline.

Three patterns are shown, from simplest to most autonomous:

1. ``run_once``       -- one profile, one shot (smoke test).
2. ``run_sequential`` / ``run_parallel`` -- a batch of profiles, either one
   after another or concurrently (one isolated proxied session each).
3. ``schedule_recurring`` -- an ``asyncio`` loop that re-runs the whole batch on
   a fixed interval, off-loading the blocking Appium work to worker threads so
   the event loop stays responsive.

``asyncio`` was chosen because it is stdlib (no extra dependency) and composes
cleanly with the thread-based parallelism the orchestrator already uses. For a
distributed, multi-machine deployment you would swap ``schedule_recurring`` for
a Celery beat schedule whose task body is a single ``orchestrator.run_profile``
call -- the orchestrator API stays identical.

This module talks to a real Appium server, so it is an illustrative entry point
(guarded by ``__main__``), not part of the unit-test suite.
"""

from __future__ import annotations

import asyncio
from typing import List

from mobile_e2e.ai.agent import AIAgent
from mobile_e2e.orchestrator.orchestrator import (
    Profile,
    TaskOrchestrator,
    WorkflowResult,
)
from mobile_e2e.utils.logger import get_logger
from mobile_e2e.workers.base_worker import Locator
from selenium.webdriver.common.by import By

LOG = get_logger(__name__)

# Example locators; adjust to the app under test.
READ_LOCATOR: Locator = (By.ID, "com.example.app:id/incoming_message")
INPUT_LOCATOR: Locator = (By.ID, "com.example.app:id/reply_box")
SUBMIT_LOCATOR: Locator = (By.ID, "com.example.app:id/send_button")


def build_profiles() -> List[Profile]:
    """Two profiles with distinct proxies and tones of voice."""
    friendly = AIAgent(
        "You reply to chat messages for a support assistant.",
        tone="warm, friendly, concise",
        fallback_response="Thanks for reaching out! We'll get back to you.",
    )
    formal = AIAgent(
        "You reply to chat messages for a corporate account.",
        tone="formal and professional",
        fallback_response="Thank you for your message.",
    )
    return [
        Profile(
            name="profile-a",
            proxy_string="10.0.0.1:8080:userA:passA",
            read_locator=READ_LOCATOR,
            input_locator=INPUT_LOCATOR,
            submit_locator=SUBMIT_LOCATOR,
            agent=friendly,
        ),
        Profile(
            name="profile-b",
            proxy_string="10.0.0.2:8080:userB:passB",
            read_locator=READ_LOCATOR,
            input_locator=INPUT_LOCATOR,
            submit_locator=SUBMIT_LOCATOR,
            agent=formal,
        ),
    ]


def _log_results(results: List[WorkflowResult]) -> None:
    for r in results:
        status = "OK" if r.ok else f"FAILED ({type(r.error).__name__})"
        LOG.info("  %-12s %s", r.name, status)


# 1) Single run -------------------------------------------------------------
def run_once(orchestrator: TaskOrchestrator, profile: Profile) -> WorkflowResult:
    result = orchestrator.run_profile(profile)
    _log_results([result])
    return result


# 2) Batch: sequential or parallel -----------------------------------------
def run_sequential(
    orchestrator: TaskOrchestrator, profiles: List[Profile]
) -> List[WorkflowResult]:
    LOG.info("Running %d profiles sequentially", len(profiles))
    results = orchestrator.run_profiles(profiles, parallel=False)
    _log_results(results)
    return results


def run_parallel(
    orchestrator: TaskOrchestrator, profiles: List[Profile]
) -> List[WorkflowResult]:
    LOG.info("Running %d profiles in parallel", len(profiles))
    results = orchestrator.run_profiles(
        profiles, parallel=True, max_workers=len(profiles) or 1
    )
    _log_results(results)
    return results


# 3) Recurring schedule via asyncio ----------------------------------------
async def schedule_recurring(
    orchestrator: TaskOrchestrator,
    profiles: List[Profile],
    interval_seconds: float,
    iterations: int = 0,
    parallel: bool = True,
) -> None:
    """Re-run the batch every ``interval_seconds``.

    Args:
        orchestrator: The configured orchestrator.
        profiles: Profiles to run each tick.
        interval_seconds: Delay between the start of successive runs.
        iterations: Number of runs; ``0`` means run forever.
        parallel: Whether each tick runs its profiles concurrently.
    """
    tick = 0
    while iterations == 0 or tick < iterations:
        tick += 1
        LOG.info("=== scheduled run #%d ===", tick)
        # Off-load blocking Appium work so the loop can keep scheduling.
        results = await asyncio.to_thread(
            orchestrator.run_profiles, profiles, parallel=parallel
        )
        _log_results(results)
        if iterations and tick >= iterations:
            break
        await asyncio.sleep(interval_seconds)


def main() -> None:
    profiles = build_profiles()
    # A default agent is required; profiles override it with their own.
    default_agent = AIAgent(
        "You reply to chat messages.", fallback_response="Thanks!"
    )
    orchestrator = TaskOrchestrator(default_agent, char_delay=0.05)

    # Run the batch twice, 10 minutes apart, profiles concurrently.
    asyncio.run(
        schedule_recurring(
            orchestrator, profiles, interval_seconds=600, iterations=2
        )
    )


if __name__ == "__main__":
    main()
