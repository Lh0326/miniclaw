from miniclaw.agent.limits import RunLimits
from miniclaw.agent.loop import AgentLoop
from miniclaw.agent.state import RunStatus
from miniclaw.core.messages import ResponseCompleted, TextDelta
from miniclaw.model.fake import FakeModel


async def test_agent_loop_completes_with_text() -> None:
    loop = AgentLoop(
        FakeModel([TextDelta("done"), ResponseCompleted("stop")]),
        model="fake",
    )

    result = await loop.run("finish this")

    assert result.status is RunStatus.COMPLETED
    assert result.output == "done"
    assert [event.payload.__class__.__name__ for event in result.events] == [
        "RunStarted",
        "ContextBuilt",
        "ModelStreamStarted",
        "TextDelta",
        "RunCompleted",
    ]


async def test_agent_loop_exhausts_turn_budget() -> None:
    loop = AgentLoop(
        FakeModel([ResponseCompleted("tool_calls")]),
        model="fake",
        limits=RunLimits(max_turns=0),
    )

    result = await loop.run("loop forever")

    assert result.status is RunStatus.EXHAUSTED
