"""Tests for the Tk-independent GUI controller."""

from unittest.mock import MagicMock, patch

import pytest

from mobile_e2e.core.exceptions import ProxyParseError
from mobile_e2e.gui.controller import (
    STRATEGIES,
    GuiController,
    WorkflowRequest,
    make_locator,
    parse_proxy_preview,
)
from mobile_e2e.orchestrator.orchestrator import WorkflowResult


def _valid_request(**kw) -> WorkflowRequest:
    base = dict(
        system_prompt="You reply to messages.",
        read_strategy="id",
        read_value="incoming",
        input_strategy="id",
        input_value="reply",
    )
    base.update(kw)
    return WorkflowRequest(**base)


# -- helpers ----------------------------------------------------------------
def test_strategies_contain_common_appium_backends():
    assert STRATEGIES["ID"] == "id"
    assert STRATEGIES["Accessibility ID"] == "accessibility id"
    assert STRATEGIES["XPath"] == "xpath"


def test_make_locator():
    assert make_locator("accessibility id", "login") == ("accessibility id", "login")


def test_parse_proxy_preview_empty():
    assert "No proxy" in parse_proxy_preview("")


def test_parse_proxy_preview_masks_password():
    preview = parse_proxy_preview("10.0.0.1:8080:user:secret")
    assert "secret" not in preview
    assert "***" in preview


def test_parse_proxy_preview_invalid_raises():
    with pytest.raises(ProxyParseError):
        parse_proxy_preview("not-a-proxy")


# -- validation -------------------------------------------------------------
def test_validate_requires_system_prompt():
    with pytest.raises(ValueError):
        GuiController().validate(_valid_request(system_prompt="  "))


def test_validate_requires_read_value():
    with pytest.raises(ValueError):
        GuiController().validate(_valid_request(read_value=""))


def test_validate_requires_input_value():
    with pytest.raises(ValueError):
        GuiController().validate(_valid_request(input_value=""))


def test_validate_rejects_bad_proxy():
    with pytest.raises(ProxyParseError):
        GuiController().validate(_valid_request(proxy_string="bad:proxy:x"))


def test_validate_accepts_valid_request():
    GuiController().validate(_valid_request(proxy_string="10.0.0.1:8080"))


# -- run wiring -------------------------------------------------------------
@patch("mobile_e2e.gui.controller.TaskOrchestrator")
@patch("mobile_e2e.gui.controller.AIAgent")
def test_run_builds_locators_and_forwards_proxy(mock_agent, mock_orch):
    orchestrator = MagicMock()
    orchestrator.run_workflow.return_value = WorkflowResult(
        name="gui-run", ok=True, response="hi"
    )
    mock_orch.return_value = orchestrator

    req = _valid_request(
        proxy_string="10.0.0.1:8080:u:p",
        read_strategy="accessibility id",
        read_value="msg",
        input_value="box",
        submit_strategy="id",
        submit_value="send",
    )
    result = GuiController().run(req)

    assert result.ok is True
    args, kwargs = orchestrator.run_workflow.call_args
    assert args[0] == "10.0.0.1:8080:u:p"           # proxy string
    assert args[1] == ("accessibility id", "msg")   # read locator
    assert args[2] == ("id", "box")                 # input locator
    assert kwargs["submit_locator"] == ("id", "send")


@patch("mobile_e2e.gui.controller.TaskOrchestrator")
@patch("mobile_e2e.gui.controller.AIAgent")
def test_run_without_submit_or_proxy(mock_agent, mock_orch):
    orchestrator = MagicMock()
    orchestrator.run_workflow.return_value = WorkflowResult(name="gui-run", ok=True)
    mock_orch.return_value = orchestrator

    GuiController().run(_valid_request())  # no proxy, no submit

    args, kwargs = orchestrator.run_workflow.call_args
    assert args[0] is None                  # no proxy -> None
    assert kwargs["submit_locator"] is None  # no submit -> None


@patch("mobile_e2e.gui.controller.TaskOrchestrator")
@patch("mobile_e2e.gui.controller.AIAgent")
def test_run_passes_agent_config(mock_agent, mock_orch):
    mock_orch.return_value = MagicMock()
    req = _valid_request(tone="friendly", fallback="thanks", base_url="", model="m")
    GuiController().run(req)

    # AIAgent constructed with the system prompt, tone and fallback.
    _, kwargs = mock_agent.call_args
    assert kwargs["tone"] == "friendly"
    assert kwargs["fallback_response"] == "thanks"
