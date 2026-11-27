"""Regression tests for the cross-platform, non-destructive sibling-PID liveness probe."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from aiwatchdogs.cli import runner as runner_mod
from aiwatchdogs.cli.runner import _pid_is_alive


def test_rejects_invalid_pids() -> None:
    for bad in (0, -1, True, False, None, "123", 1.5):
        assert _pid_is_alive(bad) is False  # type: ignore[arg-type]


def test_own_pid_is_alive() -> None:
    assert _pid_is_alive(os.getpid()) is True


def test_live_child_is_detected_and_not_killed() -> None:
    """The probe must report a running sibling as alive WITHOUT terminating it.

    On Windows ``os.kill(pid, 0)`` calls TerminateProcess(pid, 0); this test fails there
    if the probe ever regresses to signal-0 semantics.
    """
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _pid_is_alive(child.pid) is True
        assert _pid_is_alive(child.pid) is True
        assert child.poll() is None, "liveness probe terminated the process it was checking"
    finally:
        child.kill()
        child.wait(timeout=10)


def test_exited_child_is_not_alive() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    assert _pid_is_alive(child.pid) is False


def test_windows_never_uses_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("os.kill must not be used for liveness checks on Windows")

    monkeypatch.setattr(runner_mod.sys, "platform", "win32")
    monkeypatch.setattr(runner_mod.os, "kill", _forbidden)
    monkeypatch.setattr(runner_mod, "_win_pid_is_alive", lambda pid: pid == 4242)
    assert _pid_is_alive(4242) is True
    assert _pid_is_alive(4343) is False


def test_windows_probe_errors_do_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_pid: int) -> bool:
        raise SystemError("<built-in function kill> returned a result with an exception set")

    monkeypatch.setattr(runner_mod.sys, "platform", "win32")
    monkeypatch.setattr(runner_mod, "_win_pid_is_alive", _boom)
    assert _pid_is_alive(4242) is False


def test_posix_systemerror_is_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object) -> None:
        raise SystemError("unexpected")

    monkeypatch.setattr(runner_mod.sys, "platform", "linux")
    monkeypatch.setattr(runner_mod.os, "kill", _boom)
    assert _pid_is_alive(4242) is False
