"""Tests for SessionScheduler, patching SessionManager to avoid real drivers."""

from unittest.mock import MagicMock, patch

from mobile_e2e.core.exceptions import SessionStartupError
from mobile_e2e.scheduler.scheduler import Job, SessionScheduler


def _fake_manager_cm(driver):
    """Return an object usable as `with SessionManager(...) as m:`."""
    manager = MagicMock()
    manager.__enter__.return_value = manager
    manager.__exit__.return_value = False
    manager.create_session.return_value = driver
    return manager


@patch("mobile_e2e.scheduler.scheduler.SessionManager")
def test_all_jobs_succeed(mock_sm):
    mock_sm.return_value = _fake_manager_cm(MagicMock())
    calls = []

    def body(driver):
        calls.append(driver)
        return "done"

    jobs = [Job(name=f"j{i}", run=body, proxy=None) for i in range(3)]
    results = SessionScheduler(max_workers=2).run(jobs)

    assert len(results) == 3
    assert all(r.ok for r in results)
    assert {r.value for r in results} == {"done"}
    assert len(calls) == 3


@patch("mobile_e2e.scheduler.scheduler.SessionManager")
def test_proxy_forwarded_to_manager(mock_sm):
    manager = _fake_manager_cm(MagicMock())
    mock_sm.return_value = manager

    job = Job(name="p", run=lambda d: None, proxy="10.0.0.1:8080:u:p")
    SessionScheduler(max_workers=1).run([job])

    manager.create_session.assert_called_once_with(proxy="10.0.0.1:8080:u:p")


@patch("mobile_e2e.scheduler.scheduler.SessionManager")
def test_startup_failure_captured_as_result(mock_sm):
    manager = MagicMock()
    manager.__enter__.return_value = manager
    manager.__exit__.return_value = False
    manager.create_session.side_effect = SessionStartupError("boom")
    mock_sm.return_value = manager

    results = SessionScheduler(max_workers=1).run([Job("bad", lambda d: None)])

    assert len(results) == 1
    assert results[0].ok is False
    assert isinstance(results[0].error, SessionStartupError)


@patch("mobile_e2e.scheduler.scheduler.SessionManager")
def test_job_body_exception_isolated(mock_sm):
    mock_sm.return_value = _fake_manager_cm(MagicMock())

    def ok(d):
        return 1

    def crash(d):
        raise ValueError("job crashed")

    results = SessionScheduler(max_workers=2).run(
        [Job("ok", ok), Job("crash", crash)]
    )
    by_name = {r.name: r for r in results}
    assert by_name["ok"].ok is True
    assert by_name["crash"].ok is False
    assert isinstance(by_name["crash"].error, ValueError)
