import pytest

from miniclaw.core.errors import ToolArgumentsInvalid
from miniclaw.core.messages import ToolCall, ToolCallDelta
from miniclaw.tools.assembler import ToolCallAssembler


def test_assembler_combines_fragments_by_index() -> None:
    assembler = ToolCallAssembler()
    assembler.feed(ToolCallDelta(0, "call-1", "read_file", '{"pa'))
    assembler.feed(ToolCallDelta(0, arguments_fragment='th":"README.md"}'))

    assert assembler.complete() == (
        ToolCall("call-1", "read_file", {"path": "README.md"}),
    )


def test_assembler_rejects_conflicting_identity() -> None:
    assembler = ToolCallAssembler()
    assembler.feed(ToolCallDelta(0, "call-1", "read_file", "{}"))

    with pytest.raises(ToolArgumentsInvalid, match="conflicting"):
        assembler.feed(ToolCallDelta(0, "call-2", "read_file", ""))
