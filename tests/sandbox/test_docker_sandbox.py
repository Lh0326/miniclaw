from pathlib import Path

import pytest

from miniclaw.sandbox.docker import DockerSandbox
from miniclaw.sandbox.router import SandboxCapabilityUnavailable, SandboxRouter
from miniclaw.sandbox.types import Command, SandboxPolicy
from miniclaw.tools.types import ToolCapabilities


async def test_docker_capability_has_public_reason() -> None:
    capabilities = await DockerSandbox().capabilities()
    assert capabilities.reason


async def test_router_never_silently_downgrades_strong() -> None:
    docker = DockerSandbox(
        image="image-that-is-not-present:miniclaw",
        allowed_images=("image-that-is-not-present:miniclaw",),
    )
    router = SandboxRouter(strong=docker)

    with pytest.raises(SandboxCapabilityUnavailable):
        await router.select(ToolCapabilities(subprocess=True), require_strong=True)


async def test_docker_executor_when_available(tmp_path) -> None:
    docker = DockerSandbox()
    capabilities = await docker.capabilities()
    if not capabilities.available:
        pytest.skip(f"strong sandbox unavailable: {capabilities.reason}")

    result = await docker.execute(
        Command(("python", "-c", "print('ok')"), tmp_path, {}),
        SandboxPolicy(
            tmp_path,
            "workspace-write",
            "deny",
            (),
            2.0,
            4096,
        ),
    )

    assert result.stdout.strip() == "ok"


@pytest.mark.parametrize(
    ("filesystem", "expected_output", "expected_content"),
    (
        ("read", "read-only", "original\n"),
        ("workspace-write", "updated", "updated\n"),
    ),
)
async def test_docker_workspace_mount_enforces_write_policy(
    tmp_path,
    filesystem: str,
    expected_output: str,
    expected_content: str,
) -> None:
    docker = DockerSandbox()
    capabilities = await docker.capabilities()
    if not capabilities.available:
        pytest.skip(f"strong sandbox unavailable: {capabilities.reason}")

    target = tmp_path / "state.txt"
    target.write_text("original\n")
    script = """
import errno
from pathlib import Path

try:
    Path("state.txt").write_text("updated\\n")
except OSError as error:
    if error.errno != errno.EROFS:
        raise
    print("read-only")
else:
    print("updated")
"""
    result = await docker.execute(
        Command(("python", "-c", script), tmp_path, {}),
        SandboxPolicy(tmp_path, filesystem, "deny", (), 10.0, 4096),
    )

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == expected_output
    assert target.read_text() == expected_content
    expected_changes = () if filesystem == "read" else (Path("state.txt"),)
    assert result.changed_paths == expected_changes
