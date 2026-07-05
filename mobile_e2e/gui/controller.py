"""Tk-independent logic behind the GUI.

Keeping all the wiring here (building settings, agent, orchestrator and running
the workflow) means the GUI layer stays a thin view and this part is unit
testable without a display.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from mobile_e2e.ai.agent import AIAgent
from mobile_e2e.ai.settings import AISettings
from mobile_e2e.config.settings import AppiumSettings
from mobile_e2e.core.proxy import ProxyConfig
from mobile_e2e.orchestrator.orchestrator import TaskOrchestrator, WorkflowResult
from mobile_e2e.workers.base_worker import Locator

# Human label -> Appium/Selenium locator strategy string.
STRATEGIES = {
    "ID": "id",
    "Accessibility ID": "accessibility id",
    "XPath": "xpath",
    "Class name": "class name",
    "Android UIAutomator": "-android uiautomator",
    "Name": "name",
}


@dataclass
class WorkflowRequest:
    """Everything the GUI collects to drive one workflow run."""

    # Appium / session
    server_url: str = "http://127.0.0.1:4723"
    platform: str = "Android"
    device: str = "emulator-5554"
    proxy_string: str = ""

    # Locators (strategy is a value from STRATEGIES, e.g. "id")
    read_strategy: str = "id"
    read_value: str = ""
    input_strategy: str = "id"
    input_value: str = ""
    submit_strategy: str = "id"
    submit_value: str = ""

    # AI agent
    system_prompt: str = ""
    tone: str = ""
    fallback: str = ""
    model: str = "gpt-4o-mini"
    base_url: str = ""
    api_key: str = ""

    # Typing behaviour
    char_delay: float = 0.05


def make_locator(strategy: str, value: str) -> Locator:
    """Build a ``(by, value)`` locator tuple."""
    return (strategy, value)


def parse_proxy_preview(proxy_string: str) -> str:
    """Return a masked, human-readable preview of a proxy string.

    Raises:
        ProxyParseError: If the string is non-empty but invalid.
    """
    if not proxy_string.strip():
        return "No proxy (direct connection)"
    return str(ProxyConfig.from_string(proxy_string))


class GuiController:
    """Builds framework objects from a request and runs the workflow."""

    def validate(self, req: WorkflowRequest) -> None:
        """Raise ``ValueError`` if required fields are missing."""
        if not req.system_prompt.strip():
            raise ValueError("System prompt is required.")
        if not req.read_value.strip():
            raise ValueError("Read locator value is required.")
        if not req.input_value.strip():
            raise ValueError("Input locator value is required.")
        # A non-empty proxy must parse; surfaces a clear error early.
        if req.proxy_string.strip():
            ProxyConfig.from_string(req.proxy_string)

    def build_agent(self, req: WorkflowRequest) -> AIAgent:
        settings = AISettings(
            api_key=req.api_key.strip() or None,
            base_url=req.base_url.strip() or None,
            model=req.model.strip() or "gpt-4o-mini",
        )
        return AIAgent(
            req.system_prompt,
            tone=req.tone.strip() or None,
            settings=settings,
            fallback_response=req.fallback.strip() or None,
        )

    def build_orchestrator(self, req: WorkflowRequest) -> TaskOrchestrator:
        appium_settings = AppiumSettings(
            server_url=req.server_url.strip(),
            platform_name=req.platform,
            device_name=req.device.strip(),
        )
        return TaskOrchestrator(
            self.build_agent(req),
            settings=appium_settings,
            char_delay=req.char_delay,
        )

    def run(self, req: WorkflowRequest) -> WorkflowResult:
        """Validate the request and execute one workflow.

        Never raises for framework/runtime failures — those come back inside the
        :class:`WorkflowResult`. Only input validation raises (``ValueError``).
        """
        self.validate(req)
        orchestrator = self.build_orchestrator(req)

        read_locator = make_locator(req.read_strategy, req.read_value.strip())
        input_locator = make_locator(req.input_strategy, req.input_value.strip())
        submit_locator: Optional[Locator] = (
            make_locator(req.submit_strategy, req.submit_value.strip())
            if req.submit_value.strip()
            else None
        )

        return orchestrator.run_workflow(
            req.proxy_string.strip() or None,
            read_locator,
            input_locator,
            submit_locator=submit_locator,
            name="gui-run",
        )
