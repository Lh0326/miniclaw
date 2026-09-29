import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from miniclaw.app import create_app
from miniclaw.config import MiniClawConfig
from miniclaw.core.messages import Message, ToolCall, ToolResultContent
from miniclaw.model.openai_compat import OpenAICompatibleClient
from miniclaw.permissions.types import ApprovalResolution
from miniclaw.sessions.recovery import RecoveryDecision, pending_tool_executions

STREAM = (
    'data: {"choices":[{"delta":{"content":"continued"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


class StrictToolProtocolTransport:
    """Reject the unresolved tool-call history accepted by permissive fakes."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append(payload)
        pending: set[str] = set()
        for message in payload["messages"]:
            if message["role"] == "tool":
                call_id = message["tool_call_id"]
                if call_id not in pending:
                    return httpx.Response(400, json={"error": "unmatched tool result"})
                pending.remove(call_id)
                continue
            if pending:
                return httpx.Response(400, json={"error": "tool result required"})
            pending = {call["id"] for call in message.get("tool_calls", ())}
        if pending:
            return httpx.Response(400, json={"error": "tool result required"})
        return httpx.Response(200, text=STREAM)


class RecordingApprovalProvider:
    def __init__(self, approved: bool) -> None:
        self.approved = approved
        self.requests = []

    async def resolve(self, request) -> ApprovalResolution:
        self.requests.append(request)
        return ApprovalResolution(request.request_id, self.approved, "test decision")


class RejectReplayExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, call, context):
        self.calls.append(call)
        raise AssertionError("recovery must not execute interrupted calls")


async def _app(tmp_path: Path, *, approved: bool):
    workspace = tmp_path / "project"
    workspace.mkdir()
    transport = StrictToolProtocolTransport()
    client = OpenAICompatibleClient(
        "https://model.invalid/v1",
        "offline-key",
        transport=httpx.MockTransport(transport),
    )
    approvals = RecordingApprovalProvider(approved)
    app = await create_app(
        MiniClawConfig(tmp_path / "data", workspace),
        provider=client,
        approval_provider=approvals,
    )
    return app, transport, approvals


async def _save_interrupted_history(app, suffix: tuple[Message, ...]):
    run = await app.run_prompt("start")
    checkpoint = await app.checkpoints.latest_for_run(run.run_id)
    assert checkpoint is not None
    interrupted = replace(
        checkpoint,
        checkpoint_id=str(uuid4()),
        sequence=checkpoint.sequence + 1,
        messages=(*checkpoint.messages, *suffix),
    )
    await app.checkpoints.save(interrupted)
    return run, interrupted


@pytest.mark.parametrize(
    ("tool_name", "expected_decision", "approval_count"),
    [
        ("read_file", RecoveryDecision.RESUME, 0),
        ("run_command", RecoveryDecision.REQUIRES_APPROVAL, 1),
        ("vanished_tool", RecoveryDecision.REQUIRES_APPROVAL, 1),
    ],
)
async def test_interrupted_history_can_continue_through_real_http_encoder(
    tmp_path: Path, tool_name: str, expected_decision: RecoveryDecision, approval_count: int
) -> None:
    app, transport, approvals = await _app(tmp_path, approved=True)
    try:
        run, checkpoint = await _save_interrupted_history(
            app,
            (Message("assistant", (ToolCall("interrupted", tool_name, {}),)),),
        )
        executor = RejectReplayExecutor()
        app.agent.tool_executor = executor

        command = await app.handle_command(f"/resume {run.session_id}")
        payload = json.loads(command.output)
        assert payload["decision"] == expected_decision.value
        assert payload["resumed"] is True
        assert payload["restored_messages"] == len(checkpoint.messages) + 1
        assert len(approvals.requests) == approval_count
        if approval_count:
            assert approvals.requests[0].tool_name == "resume_session"
        result = app.conversation[-1].content[0]
        assert isinstance(result, ToolResultContent)
        assert result.tool_call_id == "interrupted"
        assert result.is_error is True
        assert "outcome is unknown" in result.output
        assert "did not re-execute" in result.output
        assert pending_tool_executions(app.conversation, app.tools) == ()

        continued = await app.run_prompt("continue")

        assert continued.output == "continued"
        assert executor.calls == []
        assert len(transport.requests) == 2
        wire_messages = transport.requests[-1]["messages"]
        assert wire_messages[-2]["role"] == "tool"
        assert wire_messages[-2]["tool_call_id"] == "interrupted"
        assert "outcome is unknown" in wire_messages[-2]["content"]
        assert wire_messages[-1] == {"role": "user", "content": "continue"}
        # Recovery repairs the active history, never the original crash record.
        assert await app.checkpoints.load(checkpoint.checkpoint_id) == checkpoint
    finally:
        await app.aclose()


async def test_partial_batch_preserves_results_before_closing_missing_calls(
    tmp_path: Path,
) -> None:
    app, transport, approvals = await _app(tmp_path, approved=True)
    try:
        recorded = Message("tool", (ToolResultContent("completed", "original result"),))
        run, checkpoint = await _save_interrupted_history(
            app,
            (
                Message(
                    "assistant",
                    (
                        ToolCall("completed", "read_file", {"path": "known.txt"}),
                        ToolCall("risky", "run_command", {"argv": ["side-effect"]}),
                        ToolCall("read", "read_file", {"path": "pending.txt"}),
                    ),
                ),
                recorded,
                Message("user", ()),
            ),
        )
        executor = RejectReplayExecutor()
        app.agent.tool_executor = executor

        command = await app.handle_command(f"/resume {run.session_id}")

        assert json.loads(command.output)["resumed"] is True
        assert len(approvals.requests) == 1
        assert app.conversation[: len(checkpoint.messages) - 1] == checkpoint.messages[:-1]
        assert app.conversation[-4] == recorded
        repaired = [message.content[0] for message in app.conversation[-3:-1]]
        assert [item.tool_call_id for item in repaired] == ["risky", "read"]
        assert all(item.is_error for item in repaired)

        await app.run_prompt("continue after partial batch")

        assert executor.calls == []
        wire_messages = transport.requests[-1]["messages"]
        tool_messages = [message for message in wire_messages if message["role"] == "tool"]
        assert [item["tool_call_id"] for item in tool_messages] == [
            "completed",
            "risky",
            "read",
        ]
        assert tool_messages[0]["content"] == "original result"
    finally:
        await app.aclose()


async def test_refused_resume_preserves_history_and_sends_no_request(tmp_path: Path) -> None:
    app, transport, approvals = await _app(tmp_path, approved=False)
    try:
        run, checkpoint = await _save_interrupted_history(
            app,
            (Message("assistant", (ToolCall("risky", "run_command", {}),)),),
        )
        prior_conversation = app.conversation
        executor = RejectReplayExecutor()
        app.agent.tool_executor = executor

        command = await app.handle_command(f"/resume {run.session_id}")

        assert json.loads(command.output)["resumed"] is False
        assert app.conversation == prior_conversation
        assert await app.checkpoints.load(checkpoint.checkpoint_id) == checkpoint
        assert len(approvals.requests) == 1
        assert len(transport.requests) == 1
        assert executor.calls == []
    finally:
        await app.aclose()


async def test_complete_tool_history_is_restored_unchanged(tmp_path: Path) -> None:
    app, transport, approvals = await _app(tmp_path, approved=True)
    try:
        run, checkpoint = await _save_interrupted_history(
            app,
            (
                Message("assistant", (ToolCall("finished", "run_command", {}),)),
                Message("tool", (ToolResultContent("finished", "recorded outcome"),)),
            ),
        )

        command = await app.handle_command(f"/resume {run.session_id}")

        assert json.loads(command.output)["decision"] == RecoveryDecision.RESUME.value
        assert app.conversation == checkpoint.messages
        assert approvals.requests == []
        await app.run_prompt("continue complete history")
        assert len(transport.requests) == 2
        assert transport.requests[-1]["messages"][-2]["content"] == "recorded outcome"
    finally:
        await app.aclose()


async def test_corrupted_checkpoint_is_rejected_before_history_repair(tmp_path: Path) -> None:
    app, transport, approvals = await _app(tmp_path, approved=True)
    try:
        run, checkpoint = await _save_interrupted_history(
            app,
            (Message("assistant", (ToolCall("risky", "run_command", {}),)),),
        )
        prior_conversation = app.conversation
        path = app.checkpoints.checkpoint_directory / f"{checkpoint.checkpoint_id}.json"
        path.write_bytes(b"corrupted checkpoint")

        command = await app.handle_command(f"/resume {run.session_id}")

        payload = json.loads(command.output)
        assert payload["decision"] == RecoveryDecision.UNRECOVERABLE.value
        assert payload["resumed"] is False
        assert app.conversation == prior_conversation
        assert approvals.requests == []
        assert len(transport.requests) == 1
    finally:
        await app.aclose()
