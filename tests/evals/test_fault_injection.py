import pytest

from miniclaw.core.messages import ModelRequest, ResponseCompleted, TextDelta
from miniclaw.evals.faults import DisconnectingModel
from miniclaw.model.fake import FakeModel


async def test_disconnect_fault_happens_after_configured_event() -> None:
    provider = DisconnectingModel(
        FakeModel((TextDelta("partial"), ResponseCompleted("stop"))),
        after_event=1,
    )

    stream = provider.stream(ModelRequest("fake", ()))
    assert await anext(stream) == TextDelta("partial")
    with pytest.raises(ConnectionError, match="injected"):
        await anext(stream)
