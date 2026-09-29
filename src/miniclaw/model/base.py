from collections.abc import AsyncIterator
from typing import Protocol

from miniclaw.core.messages import ModelEvent, ModelRequest


class ModelProvider(Protocol):
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
