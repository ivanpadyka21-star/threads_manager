"""Framework-independent service layer behind the web UI.

Builds settings/agent/orchestrator from a plain request (parsed from JSON) and
runs one workflow, capturing framework logs so the browser can display them.
No Flask imports here, so it stays unit testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

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

# Reverse set of accepted strategy values for validation.
_STRATEGY_VALUES = set(STRATEGIES.values())


@dataclass
class WorkflowRequest:
    """Everything the site collects to drive one workflow run."""

    server_url: str = "http://127.0.0.1:4723"
    platform: str = "Android"
    device: str = "emulator-5554"
    proxy_string: str = ""

    read_strategy: str = "id"
    read_value: str = ""
    input_strategy: str = "id"
    input_value: str = ""
    submit_strategy: str = "id"
    submit_value: str = ""

    system_prompt: str = ""
    tone: str = ""
    fallback: str = ""
    model: str = ""  # empty -> provider default (Gemini)
    base_url: str = ""
    api_key: str = ""

    char_delay: float = 0.05

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowRequest":
        """Build a request from arbitrary JSON, ignoring unknown keys."""
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        known = {k: v for k, v in (data or {}).items() if k in fields}
        return cls(**known)


def parse_proxy_preview(proxy_string: str) -> str:
    """Return a masked, human-readable preview of a proxy string.

    Raises:
        ProxyParseError: If the string is non-empty but invalid.
    """
    if not proxy_string.strip():
        return "No proxy (direct connection)"
    return str(ProxyConfig.from_string(proxy_string))


def _make_locator(strategy: str, value: str) -> Locator:
    return (strategy, value)


class _ListLogHandler(logging.Handler):
    """Collect formatted log records into a list (optionally notifying)."""

    def __init__(self, sink: List[str], on_record: Optional[Callable[[str], None]] = None):
        super().__init__()
        self._sink = sink
        self._on_record = on_record
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        self._sink.append(line)
        if self._on_record is not None:
            self._on_record(line)


class WebService:
    """Validates a request, wires the framework, and runs one workflow."""

    def validate(self, req: WorkflowRequest) -> None:
        """Raise ``ValueError`` if the request is not runnable."""
        if not req.system_prompt.strip():
            raise ValueError("System prompt is required.")
        if not req.read_value.strip():
            raise ValueError("Read locator value is required.")
        if not req.input_value.strip():
            raise ValueError("Input locator value is required.")
        for name, strategy in (
            ("read", req.read_strategy),
            ("input", req.input_strategy),
            ("submit", req.submit_strategy),
        ):
            if strategy not in _STRATEGY_VALUES:
                raise ValueError(f"Unknown {name} locator strategy: {strategy!r}")
        if req.platform not in ("Android", "iOS"):
            raise ValueError(f"Unknown platform: {req.platform!r}")
        if req.proxy_string.strip():
            ProxyConfig.from_string(req.proxy_string)  # raises ProxyParseError

    def build_agent(self, req: WorkflowRequest) -> AIAgent:
        # Leave model/base_url as None when unset so the provider defaults
        # (Gemini by default) apply instead of forcing an OpenAI model.
        settings = AISettings(
            api_key=req.api_key.strip() or None,
            base_url=req.base_url.strip() or None,
            model=req.model.strip() or None,
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

    def run(
        self,
        req: WorkflowRequest,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """Validate and run one workflow, returning a JSON-serialisable result.

        Args:
            req: The parsed request.
            on_log: Optional callback invoked with each captured log line (used
                by the job runner to stream logs to the browser).

        Returns:
            ``{ok, response, screen_text, error, logs}``.

        Raises:
            ValueError: If validation fails (surfaced to the caller as 400).
        """
        self.validate(req)
        orchestrator = self.build_orchestrator(req)

        read_locator = _make_locator(req.read_strategy, req.read_value.strip())
        input_locator = _make_locator(req.input_strategy, req.input_value.strip())
        submit_locator: Optional[Locator] = (
            _make_locator(req.submit_strategy, req.submit_value.strip())
            if req.submit_value.strip()
            else None
        )

        logs: List[str] = []
        handler = _ListLogHandler(logs, on_log)
        root = logging.getLogger()
        root.addHandler(handler)
        # Ensure INFO records actually reach the handler for the duration of the
        # run (the root logger may otherwise sit at WARNING).
        previous_level = root.level
        if not root.isEnabledFor(logging.INFO):
            root.setLevel(logging.INFO)
        try:
            result: WorkflowResult = orchestrator.run_workflow(
                req.proxy_string.strip() or None,
                read_locator,
                input_locator,
                submit_locator=submit_locator,
                name="web-run",
            )
        finally:
            root.removeHandler(handler)
            root.setLevel(previous_level)

        return {
            "ok": result.ok,
            "response": result.response,
            "screen_text": result.screen_text,
            "error": None if result.error is None else str(result.error),
            "logs": logs,
        }
