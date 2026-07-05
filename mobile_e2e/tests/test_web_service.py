"""Tests for the Tk-free web service layer."""

from unittest.mock import MagicMock, patch

import pytest

from mobile_e2e.core.exceptions import ProxyParseError
from mobile_e2e.orchestrator.orchestrator import WorkflowResult
from mobile_e2e.web.service import (
    STRATEGIES,
    WebService,
    WorkflowRequest,
    parse_proxy_preview,
)


def _valid(**kw) -> WorkflowRequest:
    base = dict(
        system_prompt="You reply.",
        read_value="incoming",
        input_value="reply",
    )
    base.update(kw)
    return WorkflowRequest(**base)


def test_from_dict_ignores_unknown_keys():
    req = WorkflowRequest.from_dict(
        {"system_prompt": "hi", "read_value": "x", "bogus": "drop me"}
    )
    assert req.system_prompt == "hi"
    assert req.read_value == "x"
    assert not hasattr(req, "bogus")


def test_parse_proxy_preview_masks_password():
    preview = parse_proxy_preview("10.0.0.1:8080:user:secret")
    assert "secret" not in preview and "***" in preview


def test_parse_proxy_preview_invalid_raises():
    with pytest.raises(ProxyParseError):
        parse_proxy_preview("bad")


@pytest.mark.parametrize(
    "mutate",
    [
        dict(system_prompt=" "),
        dict(read_value=""),
        dict(input_value=""),
        dict(read_strategy="nope"),
        dict(platform="Symbian"),
        dict(proxy_string="bad:proxy:x"),
    ],
)
def test_validate_rejects(mutate):
    with pytest.raises((ValueError, ProxyParseError)):
        WebService().validate(_valid(**mutate))


def test_validate_accepts_valid():
    WebService().validate(_valid(proxy_string="10.0.0.1:8080"))


@patch("mobile_e2e.web.service.TaskOrchestrator")
@patch("mobile_e2e.web.service.AIAgent")
def test_run_returns_serialisable_result(mock_agent, mock_orch):
    orch = MagicMock()
    orch.run_workflow.return_value = WorkflowResult(
        name="web-run", ok=True, screen_text="ctx", response="reply"
    )
    mock_orch.return_value = orch

    result = WebService().run(
        _valid(
            proxy_string="10.0.0.1:8080:u:p",
            read_strategy="accessibility id",
            read_value="msg",
            submit_value="send",
        )
    )
    assert result["ok"] is True
    assert result["response"] == "reply"
    assert result["screen_text"] == "ctx"
    assert result["error"] is None
    assert isinstance(result["logs"], list)

    args, kwargs = orch.run_workflow.call_args
    assert args[0] == "10.0.0.1:8080:u:p"
    assert args[1] == ("accessibility id", "msg")
    assert kwargs["submit_locator"] == ("id", "send")


@patch("mobile_e2e.web.service.TaskOrchestrator")
@patch("mobile_e2e.web.service.AIAgent")
def test_run_captures_logs(mock_agent, mock_orch):
    import logging

    def fake_run_workflow(*a, **k):
        logging.getLogger("mobile_e2e.test").info("hello from pipeline")
        return WorkflowResult(name="web-run", ok=True, response="r")

    orch = MagicMock()
    orch.run_workflow.side_effect = fake_run_workflow
    mock_orch.return_value = orch

    captured = []
    result = WebService().run(_valid(), on_log=captured.append)
    assert any("hello from pipeline" in line for line in result["logs"])
    assert any("hello from pipeline" in line for line in captured)


def test_strategies_exposed():
    assert STRATEGIES["Accessibility ID"] == "accessibility id"
