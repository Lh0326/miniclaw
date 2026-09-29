from datetime import UTC, datetime
from pathlib import Path

from miniclaw.permissions.approval import InMemoryApprovalProvider
from miniclaw.permissions.types import PermissionRequest
from miniclaw.scheduler.clock import FakeClock
from miniclaw.scheduler.runtime import SchedulerRuntime
from miniclaw.scheduler.store import SchedulerStore
from miniclaw.scheduler.types import ScheduledTask, ScheduledTaskStatus, TaskRunResult
from miniclaw.subagents.runtime import SubagentRuntime
from miniclaw.subagents.types import SubagentResult, SubagentSpec
from miniclaw.tools.types import RiskLevel, ToolCapabilities


async def test_governed_branches_are_independent(tmp_path: Path) -> None:
    class ChildFactory:
        async def run(self, spec: SubagentSpec) -> SubagentResult:
            return SubagentResult(
                spec.agent_id,
                "completed",
                "inspected",
                ("README.md",),
                "child-run",
            )

    child = await SubagentRuntime(
        ChildFactory(),
        max_depth=1,
        max_children=1,
        parent_allowed_tools=("read_file",),
    ).spawn(
        SubagentSpec("inspect", "inspect docs", 1, 2, 2, ("read_file",))
    )

    approval = InMemoryApprovalProvider({"approval-1": False})
    request = PermissionRequest(
        "approval-1",
        "run-parent",
        "call-risky",
        "run_command",
        "<redacted>",
        ToolCapabilities(
            filesystem="workspace-write",
            subprocess=True,
            risk_level=RiskLevel.HIGH,
            side_effects=True,
            idempotent=False,
        ),
        "high risk",
    )
    first = await approval.resolve(request)
    second = await approval.resolve(request)

    now = datetime(2026, 7, 26, tzinfo=UTC)
    store = SchedulerStore(tmp_path / "scheduler.db")
    await store.initialize()
    await store.add(ScheduledTask.once("parent", "cancel me", now))
    await store.add(
        ScheduledTask.once("descendant", "child", now, parent_task_id="parent")
    )
    await store.add(ScheduledTask.once("independent", "verify", now))
    cancelled = await store.cancel("parent")

    class Runner:
        async def run(self, task: ScheduledTask) -> TaskRunResult:
            return TaskRunResult("verify-run", True, None)

    await SchedulerRuntime(
        store,
        Runner(),
        FakeClock(now),
        max_concurrency=1,
    ).tick()

    assert child.status == "completed"
    assert first.approved is False
    assert second.reason == "approval already consumed"
    assert cancelled == 2
    assert (
        await store.get("independent")
    ).status is ScheduledTaskStatus.DONE
