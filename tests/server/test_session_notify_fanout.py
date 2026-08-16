from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiorpcx import TaskTimeout

from electrumx.server.session import SessionManager


def _fake_session(session_id, notify_side_effect=None):
    session = SimpleNamespace(session_id=session_id)
    session.notify = AsyncMock(side_effect=notify_side_effect)
    return session


def _fake_manager(sessions):
    fake = SimpleNamespace(
        sessions=sessions,
        notified_height=100,
        logger=Mock(),
    )
    fake._notify_one = SessionManager._notify_one.__get__(fake, SessionManager)
    return fake


@pytest.mark.asyncio
async def test_one_session_timeout_does_not_cancel_notifications_for_others():
    # RVN-06: TaskTimeout is a CancelledError subclass. Before the fix,
    # session.notify was spawned directly into the TaskGroup, so one slow
    # session timing out cancelled the whole fan-out -- every other
    # session in the same notification round silently missed it.
    good1 = _fake_session(1)
    slow = _fake_session(2, notify_side_effect=TaskTimeout(30))
    good2 = _fake_session(3)
    fake = _fake_manager([good1, slow, good2])

    await SessionManager._notify_sessions(
        fake, 100, set(), set(), set(), set(), set(), set(), set(), set())

    good1.notify.assert_awaited_once()
    good2.notify.assert_awaited_once()
    slow.notify.assert_awaited_once()
    fake.logger.info.assert_called_once_with('timeout notifying session 2')


@pytest.mark.asyncio
async def test_one_session_exception_does_not_cancel_notifications_for_others():
    good1 = _fake_session(1)
    broken = _fake_session(2, notify_side_effect=RuntimeError('boom'))
    good2 = _fake_session(3)
    fake = _fake_manager([good1, broken, good2])

    await SessionManager._notify_sessions(
        fake, 100, set(), set(), set(), set(), set(), set(), set(), set())

    good1.notify.assert_awaited_once()
    good2.notify.assert_awaited_once()
    broken.notify.assert_awaited_once()
    fake.logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_notify_one_does_not_swallow_a_real_cancellation():
    # A genuine shutdown/cancellation (bare CancelledError, not
    # TaskTimeout) must not be swallowed by _notify_one -- only the
    # per-session timeout/exception cases are contained. Tested directly
    # against _notify_one rather than through _notify_sessions: aiorpcx's
    # TaskGroup.join() never re-raises a spawned task's exception to the
    # `async with` caller (it just stops waiting and cancels remaining
    # siblings), so the containment contract this fix is actually
    # responsible for lives entirely in _notify_one itself.
    import asyncio

    cancelled = _fake_session(1, notify_side_effect=asyncio.CancelledError())
    fake = _fake_manager([cancelled])

    with pytest.raises(asyncio.CancelledError):
        await fake._notify_one(
            cancelled, set(), True, set(), set(), set(), set(), set(), set(), set())
