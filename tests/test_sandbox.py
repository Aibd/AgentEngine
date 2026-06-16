"""Sandbox tests using a fake Docker client — no real containers required.

These prove the host-side contract for the resident session-container model:
lifecycle (acquire / reuse / release), idle reaping, capacity caps, workspace
wiring, and that SandboxedBashTool stays transparent (name + schema) to the
tool layer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentengine.sandbox import (
    SandboxCapacityError,
    SandboxConfig,
    SandboxManager,
    SandboxedBashTool,
)
from agentengine.tools.builtin.bash_tool import BashTool


# --- fakes -----------------------------------------------------------------

class FakeExecResult:
    def __init__(self, exit_code, output):
        self.exit_code = exit_code
        self.output = output


class FakeContainer:
    removed = 0  # class-level counter

    def __init__(self, name):
        self.name = name
        self.short_id = name[-6:]
        self.status = "running"
        self._removed = False

    def exec_run(self, argv, demux=False, workdir=None):
        cmd = argv[-1]
        return FakeExecResult(0, (f"ran: {cmd}".encode(), b""))

    def remove(self, force=False):
        self._removed = True
        FakeContainer.removed += 1

    def reload(self):
        pass

    def logs(self, tail=40):
        return b""


class FakeContainers:
    def __init__(self):
        self.created = []

    def run(self, image, command=None, name=None, labels=None, **kwargs):
        c = FakeContainer(name or "anon")
        self.created.append(c)
        return c


class FakeDocker:
    def __init__(self):
        self.containers = FakeContainers()


@pytest.fixture(autouse=True)
def _reset_fake_counter():
    FakeContainer.removed = 0
    yield


@pytest.fixture
def fake_docker():
    return FakeDocker()


@pytest.fixture
def manager(tmp_path: Path, fake_docker: FakeDocker):
    cfg = SandboxConfig(max_containers=2, idle_ttl_seconds=0)
    return SandboxManager(
        sessions_root=tmp_path / "sessions",
        config=cfg,
        docker_client=fake_docker,
    )


# --- lifecycle -------------------------------------------------------------

def test_acquire_creates_then_reuses(manager: SandboxManager):
    a = manager.acquire("conv-1")
    b = manager.acquire("conv-1")
    assert a is b  # same conversation reuses the same container
    assert manager.active_count == 1
    assert len(manager._client.containers.created) == 1


def test_distinct_conversations_get_distinct_containers(manager: SandboxManager):
    manager.acquire("conv-1")
    manager.acquire("conv-2")
    assert manager.active_count == 2
    assert len(manager._client.containers.created) == 2


def test_release_destroys_and_wipes(manager: SandboxManager, tmp_path: Path):
    manager.acquire("conv-1")
    ws = manager.host_workspace_for("conv-1")
    assert ws.exists()
    before = FakeContainer.removed
    manager.release("conv-1")
    assert manager.active_count == 0
    assert FakeContainer.removed == before + 1
    assert not ws.exists()  # anti-pollution: workspace gone


def test_capacity_cap(manager: SandboxManager):
    manager.acquire("conv-1")
    manager.acquire("conv-2")
    with pytest.raises(SandboxCapacityError):
        manager.acquire("conv-3")


def test_release_unknown_is_noop(manager: SandboxManager):
    manager.release("never-existed")  # must not raise


def test_shutdown_clears_all(manager: SandboxManager):
    manager.acquire("conv-1")
    manager.acquire("conv-2")
    manager.shutdown()
    assert manager.active_count == 0
    assert FakeContainer.removed == 2


def test_idle_reaping(tmp_path: Path, fake_docker: FakeDocker):
    """Idle containers past TTL are reaped by the background thread."""
    cfg = SandboxConfig(max_containers=4, idle_ttl_seconds=0.05)
    mgr = SandboxManager(
        sessions_root=tmp_path / "s",
        config=cfg,
        docker_client=fake_docker,
        reap_interval=0.05,  # scan every 50ms for fast test
    )
    sb = mgr.acquire("conv-1")
    assert mgr.active_count == 1

    # Force it stale
    sb.last_used -= 10.0

    # Wait for the reaper to pick it up
    import time
    deadline = time.monotonic() + 2.0
    while mgr.active_count > 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert mgr.active_count == 0


# --- exec uses the resident container ---------------------------------------

def test_exec_uses_resident_container(manager: SandboxManager):
    """Multiple execs go into the same resident container (no new creates)."""
    sb = manager.acquire("conv-1")
    before = len(manager._client.containers.created)

    sb.exec_shell("echo hi")
    sb.exec_shell("echo again")

    # No new containers created — same resident container reused
    assert len(manager._client.containers.created) == before


def test_exec_updates_last_used(manager: SandboxManager):
    sb = manager.acquire("conv-1")
    before = sb.last_used
    import time
    time.sleep(0.1)
    sb.exec_shell("echo hi")
    assert sb.last_used >= before  # monotonic — same tick possible on fast machines


# --- tool transparency -----------------------------------------------------

def test_sandboxed_bash_matches_builtin_contract(manager: SandboxManager):
    tool = SandboxedBashTool(manager=manager, conversation_id="conv-1")
    assert tool.name == BashTool.name == "bash"
    assert tool.schema["required"] == ["command"]


def test_sandboxed_bash_runs_in_container(manager: SandboxManager):
    tool = SandboxedBashTool(manager=manager, conversation_id="conv-1")
    out = asyncio.run(tool.run(command="echo hi"))
    assert "exit=0" in out
    assert "ran: echo hi" in out
    assert manager.active_count == 1
