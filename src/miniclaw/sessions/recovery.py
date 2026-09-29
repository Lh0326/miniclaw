from dataclasses import dataclass
from enum import StrEnum

from miniclaw.core.errors import ToolNotFound
from miniclaw.core.messages import Message, ToolCall, ToolResultContent


class RecoveryDecision(StrEnum):
    RESUME = "resume"
    REQUIRES_APPROVAL = "requires_approval"
    UNRECOVERABLE = "unrecoverable"


@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    tool_call_id: str
    status: str
    idempotent: bool


def decide_recovery(
    *,
    checkpoint_valid: bool,
    executions: tuple[ToolExecutionRecord, ...],
) -> RecoveryDecision:
    if not checkpoint_valid:
        return RecoveryDecision.UNRECOVERABLE
    if any(
        execution.status == "started" and not execution.idempotent
        for execution in executions
    ):
        return RecoveryDecision.REQUIRES_APPROVAL
    return RecoveryDecision.RESUME


def pending_tool_executions(
    messages: tuple[Message, ...],
    registry,
) -> tuple[ToolExecutionRecord, ...]:
    """Find tool calls a checkpoint recorded without a matching result.

    A crash between dispatching a tool and recording its result leaves exactly
    this gap, so the message history is the authoritative source: any state the
    process meant to write about the call may itself have been lost.
    """
    started: dict[str, str] = {}
    for message in messages:
        for item in message.content:
            if isinstance(item, ToolCall):
                started[item.id] = item.name
            elif isinstance(item, ToolResultContent):
                started.pop(item.tool_call_id, None)
    records = []
    for tool_call_id, name in started.items():
        try:
            idempotent = registry.get(name).spec.capabilities.idempotent
        except (ToolNotFound, AttributeError):
            # An unknown tool cannot be proven safe to replay.
            idempotent = False
        records.append(ToolExecutionRecord(tool_call_id, "started", idempotent))
    return tuple(records)


def close_interrupted_tool_calls(
    messages: tuple[Message, ...],
    executions: tuple[ToolExecutionRecord, ...],
) -> tuple[Message, ...]:
    """Close unresolved calls without guessing their outcome or executing them.

    Call this only after the recovery approval gate has passed. A model request
    must contain a result for every call in an assistant batch before another
    user or assistant message. Keep recorded results and insert error results
    for interrupted calls after the batch's existing tool messages.
    """
    pending_ids = {
        execution.tool_call_id
        for execution in executions
        if execution.status == "started"
    }
    if not pending_ids:
        return messages

    restored: list[Message] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        restored.append(message)
        index += 1
        interrupted = tuple(
            item
            for item in message.content
            if isinstance(item, ToolCall) and item.id in pending_ids
        )
        if not interrupted:
            continue
        while index < len(messages) and messages[index].role == "tool":
            restored.append(messages[index])
            index += 1
        for call in interrupted:
            restored.append(
                Message(
                    "tool",
                    (
                        ToolResultContent(
                            call.id,
                            "Tool execution was interrupted before its result was "
                            "recorded. The outcome is unknown: it may already have "
                            "produced side effects. Recovery did not re-execute "
                            "this call. Inspect the actual state before retrying.",
                            is_error=True,
                        ),
                    ),
                )
            )
            pending_ids.discard(call.id)
    return tuple(restored)
