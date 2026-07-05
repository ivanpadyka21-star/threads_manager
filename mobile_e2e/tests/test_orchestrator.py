"""Tests for TaskOrchestrator, mocking session/UI/agent collaborators."""

from unittest.mock import MagicMock, patch

import pytest

from mobile_e2e.core.exceptions import AIAgentError, SessionStartupError
from mobile_e2e.orchestrator.orchestrator import (
    Profile,
    TaskOrchestrator,
    WorkflowResult,
)

READ = ("id", "incoming")
INPUT = ("id", "reply")
SUBMIT = ("id", "send")


def _fake_manager(driver):
    manager = MagicMock()
    manager.__enter__.return_value = manager
    manager.__exit__.return_value = False
    manager.create_session.return_value = driver
    return manager


def _fake_ui(screen_text="hello from screen"):
    ui = MagicMock()
    ui.read_screen_text.return_value = screen_text
    return ui


def _agent(reply="generated reply"):
    agent = MagicMock()
    agent.generate_response.return_value = reply
    return agent


def _orchestrator(agent=None):
    return TaskOrchestrator(agent or _agent(), char_delay=0)


def test_happy_path_wires_all_stages():
    manager = _fake_manager(MagicMock())
    ui = _fake_ui("screen text")
    agent = _agent("the reply")
    orch = _orchestrator(agent)

    with patch(
        "mobile_e2e.orchestrator.orchestrator.SessionManager",
        return_value=manager,
    ), patch(
        "mobile_e2e.orchestrator.orchestrator.UIWorker", return_value=ui
    ):
        result = orch.run_workflow(
            "10.0.0.1:8080:u:p", READ, INPUT, submit_locator=SUBMIT, name="p1"
        )

    # session created with the proxy string
    manager.create_session.assert_called_once_with(proxy="10.0.0.1:8080:u:p")
    # read -> generate -> type chain
    ui.read_screen_text.assert_called_once_with(READ)
    agent.generate_response.assert_called_once_with("screen text")
    ui.type_and_submit.assert_called_once_with(
        INPUT, "the reply", submit_locator=SUBMIT
    )
    # session torn down via context manager
    manager.__exit__.assert_called_once()

    assert isinstance(result, WorkflowResult)
    assert result.ok is True
    assert result.screen_text == "screen text"
    assert result.response == "the reply"


def test_session_failure_captured():
    manager = _fake_manager(MagicMock())
    manager.create_session.side_effect = SessionStartupError("no server")
    orch = _orchestrator()

    with patch(
        "mobile_e2e.orchestrator.orchestrator.SessionManager",
        return_value=manager,
    ), patch("mobile_e2e.orchestrator.orchestrator.UIWorker"):
        result = orch.run_workflow("1.2.3.4:9000", READ, INPUT, name="p")

    assert result.ok is False
    assert isinstance(result.error, SessionStartupError)
    manager.__exit__.assert_called_once()  # still cleaned up


def test_agent_failure_captured():
    manager = _fake_manager(MagicMock())
    ui = _fake_ui()
    agent = _agent()
    agent.generate_response.side_effect = AIAgentError("llm down")
    orch = _orchestrator(agent)

    with patch(
        "mobile_e2e.orchestrator.orchestrator.SessionManager",
        return_value=manager,
    ), patch(
        "mobile_e2e.orchestrator.orchestrator.UIWorker", return_value=ui
    ):
        result = orch.run_workflow("p:1", READ, INPUT)

    assert result.ok is False
    assert isinstance(result.error, AIAgentError)
    ui.type_and_submit.assert_not_called()


def test_per_call_agent_overrides_default():
    manager = _fake_manager(MagicMock())
    ui = _fake_ui("ctx")
    default_agent = _agent("default")
    override_agent = _agent("override")
    orch = _orchestrator(default_agent)

    with patch(
        "mobile_e2e.orchestrator.orchestrator.SessionManager",
        return_value=manager,
    ), patch(
        "mobile_e2e.orchestrator.orchestrator.UIWorker", return_value=ui
    ):
        result = orch.run_workflow("p:1", READ, INPUT, agent=override_agent)

    override_agent.generate_response.assert_called_once_with("ctx")
    default_agent.generate_response.assert_not_called()
    assert result.response == "override"


def _profiles(n=3):
    return [
        Profile(
            name=f"p{i}",
            proxy_string=f"10.0.0.{i}:8080",
            read_locator=READ,
            input_locator=INPUT,
        )
        for i in range(n)
    ]


def test_run_profiles_sequential_preserves_order():
    manager = _fake_manager(MagicMock())
    ui = _fake_ui()
    orch = _orchestrator()

    with patch(
        "mobile_e2e.orchestrator.orchestrator.SessionManager",
        return_value=manager,
    ), patch(
        "mobile_e2e.orchestrator.orchestrator.UIWorker", return_value=ui
    ):
        results = orch.run_profiles(_profiles(3), parallel=False)

    assert [r.name for r in results] == ["p0", "p1", "p2"]
    assert all(r.ok for r in results)


def test_run_profiles_parallel_runs_all():
    manager = _fake_manager(MagicMock())
    ui = _fake_ui()
    orch = _orchestrator()

    with patch(
        "mobile_e2e.orchestrator.orchestrator.SessionManager",
        return_value=manager,
    ), patch(
        "mobile_e2e.orchestrator.orchestrator.UIWorker", return_value=ui
    ):
        results = orch.run_profiles(_profiles(4), parallel=True, max_workers=4)

    assert {r.name for r in results} == {"p0", "p1", "p2", "p3"}
    assert all(r.ok for r in results)


def test_run_profiles_isolates_one_failure():
    orch = _orchestrator()
    good = _fake_manager(MagicMock())
    bad = _fake_manager(MagicMock())
    bad.create_session.side_effect = SessionStartupError("boom")
    ui = _fake_ui()

    # First profile ok, second fails, third ok.
    managers = [good, bad, good]

    with patch(
        "mobile_e2e.orchestrator.orchestrator.SessionManager",
        side_effect=managers,
    ), patch(
        "mobile_e2e.orchestrator.orchestrator.UIWorker", return_value=ui
    ):
        results = orch.run_profiles(_profiles(3), parallel=False)

    by_name = {r.name: r for r in results}
    assert by_name["p0"].ok is True
    assert by_name["p1"].ok is False
    assert by_name["p2"].ok is True
