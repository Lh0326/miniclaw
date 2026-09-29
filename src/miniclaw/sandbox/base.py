from typing import Protocol

from miniclaw.sandbox.types import Command, SandboxPolicy, SandboxResult


class SandboxExecutor(Protocol):
    async def execute(
        self,
        command: Command,
        policy: SandboxPolicy,
    ) -> SandboxResult: ...
