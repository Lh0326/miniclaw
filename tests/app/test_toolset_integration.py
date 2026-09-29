import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from miniclaw.app import create_app
from miniclaw.config import ConfigDocument, ConfigSecretProvider, MiniClawConfig
from miniclaw.core.messages import ResponseCompleted, TextDelta, ToolCallDelta
from miniclaw.mcp.config import McpConfigError, load_mcp_servers
from miniclaw.model.fake import FakeModel
from miniclaw.model.scripted import ScriptedModel
from miniclaw.permissions.approval import UnattendedApprovalProvider
from miniclaw.scheduler.store import SchedulerStore
from miniclaw.scheduler.types import ScheduledTask, ScheduledTaskStatus
from miniclaw.web.config import SearchConfigError, load_search_endpoint
from miniclaw.web.search import HttpSearchProvider, SearchEndpoint

SERVER = Path(__file__).parent.parent / "fixtures" / "mcp" / "echo_server.py"


async def _app(tmp_path: Path):
    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    return await create_app(
        MiniClawConfig(tmp_path / "data", workspace),
        provider=FakeModel([]),
    )


def _write_mcp_config(workspace: Path, payload: dict) -> None:
    directory = workspace / ".miniclaw"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "mcp.json").write_text(json.dumps(payload))


# --- toolset wiring ----------------------------------------------------------


async def test_local_and_web_tools_are_registered(tmp_path: Path) -> None:
    app = await _app(tmp_path)
    names = {tool.spec.name for tool in app.tools.values()}
    await app.aclose()

    assert {"git_status", "git_log", "git_diff", "git_show"} <= names
    assert "fetch_url" in names
    # No endpoint configured, so the model is not offered a search it cannot do.
    assert "web_search" not in names


async def test_search_tool_appears_once_configured(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    (workspace / ".miniclaw").mkdir(parents=True)
    (workspace / ".miniclaw" / "search.json").write_text(
        json.dumps({"url": "https://search.invalid/v1"})
    )

    app = await create_app(
        MiniClawConfig(tmp_path / "data", workspace), provider=FakeModel([])
    )
    names = {tool.spec.name for tool in app.tools.values()}
    await app.aclose()

    assert "web_search" in names


@pytest.mark.parametrize("search_key", ["search-environment-secret", None, ""])
async def test_cli_secret_provider_reaches_search_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    search_key: str | None,
) -> None:
    # Isolate user configuration, and inject only the transport: app assembly,
    # endpoint loading, credential selection and the HTTP request remain real.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    workspace = tmp_path / "project"
    directory = workspace / ".miniclaw"
    directory.mkdir(parents=True)
    (directory / "search.json").write_text(
        json.dumps(
            {
                "url": "https://search.invalid/v1",
                "api_key_header": "X-Api-Key",
            }
        )
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Harness reference",
                        "url": "https://example.invalid/harness",
                        "description": "Offline search result",
                    }
                ]
            },
        )

    def search_provider(
        endpoint: SearchEndpoint,
        *,
        api_key: str | None,
    ) -> HttpSearchProvider:
        return HttpSearchProvider(
            endpoint,
            api_key=api_key,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(
        "miniclaw.tools.builtin.toolsets.HttpSearchProvider",
        search_provider,
    )
    environment = {"OPENAI_API_KEY": "model-environment-secret"}
    if search_key is not None:
        environment["MINICLAW_SEARCH_API_KEY"] = search_key
    secrets = ConfigSecretProvider(
        ConfigDocument({"api_key": "model-file-secret"}),
        environment,
    )
    model = ScriptedModel(
        (
            (
                ToolCallDelta(0, "search-1", "web_search", '{"query":"harness"}'),
                ResponseCompleted("tool_calls"),
            ),
            (TextDelta("search completed"), ResponseCompleted("stop")),
        )
    )
    app = await create_app(
        MiniClawConfig(tmp_path / "data", workspace),
        provider=model,
        secret_provider=secrets,
        approval_provider=UnattendedApprovalProvider(("web_search",)),
    )
    try:
        result = await app.run_prompt("find a harness reference")
    finally:
        await app.aclose()

    assert result.output == "search completed"
    assert len(requests) == 1
    request = requests[0]
    assert request.url.params["q"] == "harness"
    assert request.headers.get("X-Api-Key") == (search_key or None)
    assert "authorization" not in request.headers
    assert "model-environment-secret" not in str(request.headers)
    assert "model-file-secret" not in str(request.headers)


def test_search_config_refuses_an_inline_credential(tmp_path: Path) -> None:
    directory = tmp_path / ".miniclaw"
    directory.mkdir(parents=True)
    (directory / "search.json").write_text(
        json.dumps({"url": "https://s.invalid", "api_key": "leaked"})
    )

    with pytest.raises(SearchConfigError, match="MINICLAW_SEARCH_API_KEY"):
        load_search_endpoint(tmp_path)


# --- MCP configuration and startup -------------------------------------------


def test_mcp_config_is_parsed(tmp_path: Path) -> None:
    _write_mcp_config(
        tmp_path,
        {"servers": {"echo": {"command": "python", "args": ["s.py"]}}},
    )

    servers = load_mcp_servers(tmp_path, user_root=tmp_path / "nowhere")

    assert servers[0].name == "echo"
    assert servers[0].args == ("s.py",)
    assert servers[0].enabled is True


def test_mcp_config_accepts_the_common_alias(tmp_path: Path) -> None:
    _write_mcp_config(tmp_path, {"mcpServers": {"a": {"command": "x"}}})

    assert load_mcp_servers(tmp_path, user_root=tmp_path / "nowhere")[0].name == "a"


def test_mcp_config_rejects_a_server_without_a_command(tmp_path: Path) -> None:
    _write_mcp_config(tmp_path, {"servers": {"bad": {"args": []}}})

    with pytest.raises(McpConfigError, match="needs a command"):
        load_mcp_servers(tmp_path, user_root=tmp_path / "nowhere")


async def test_configured_server_tools_reach_the_registry(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    _write_mcp_config(
        workspace,
        {"servers": {"echo": {"command": sys.executable, "args": [str(SERVER), "ok"]}}},
    )

    app = await create_app(
        MiniClawConfig(tmp_path / "data", workspace), provider=FakeModel([])
    )
    try:
        names = {tool.spec.name for tool in app.tools.values()}
        status = json.loads((await app.handle_command("/mcp")).output)
    finally:
        await app.aclose()

    assert "mcp_echo_echo" in names
    assert status[0]["status"] == "ready"
    assert status[0]["version"] == "1.2.3"


async def test_a_broken_server_does_not_break_startup(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    _write_mcp_config(
        workspace,
        {"servers": {"missing": {"command": "/nonexistent/mcp-binary"}}},
    )

    app = await create_app(
        MiniClawConfig(tmp_path / "data", workspace), provider=FakeModel([])
    )
    try:
        status = json.loads((await app.handle_command("/mcp")).output)
        # The rest of the agent is fully usable.
        assert app.tools.get("read_file") is not None
    finally:
        await app.aclose()

    assert status[0]["status"] == "unavailable"
    assert status[0]["tools"] == []


async def test_disabled_server_is_not_started(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    _write_mcp_config(
        workspace,
        {
            "servers": {
                "echo": {
                    "command": sys.executable,
                    "args": [str(SERVER), "ok"],
                    "enabled": False,
                }
            }
        },
    )

    app = await create_app(
        MiniClawConfig(tmp_path / "data", workspace), provider=FakeModel([])
    )
    try:
        status = json.loads((await app.handle_command("/mcp")).output)
        names = {tool.spec.name for tool in app.tools.values()}
    finally:
        await app.aclose()

    assert status[0]["status"] == "disabled"
    assert not any(name.startswith("mcp_") for name in names)


async def test_closing_the_app_stops_mcp_servers(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    _write_mcp_config(
        workspace,
        {"servers": {"echo": {"command": sys.executable, "args": [str(SERVER), "ok"]}}},
    )
    app = await create_app(
        MiniClawConfig(tmp_path / "data", workspace), provider=FakeModel([])
    )
    process = app.mcp_clients[0]._process

    await app.aclose()

    assert process.returncode is not None


# --- cross-process scheduler claim -------------------------------------------


async def test_two_workers_cannot_claim_the_same_task(tmp_path: Path) -> None:
    database = tmp_path / "scheduler.db"
    first = SchedulerStore(database)
    await first.initialize()
    second = SchedulerStore(database)
    now = datetime.now(UTC)
    for index in range(4):
        await first.add(ScheduledTask.once(f"job-{index}", "work", now))

    claimed_a, claimed_b = await asyncio.gather(
        first.claim_due(now, 4, owner="worker-a"),
        second.claim_due(now, 4, owner="worker-b"),
    )

    ids_a = {task.task_id for task in claimed_a}
    ids_b = {task.task_id for task in claimed_b}
    assert not (ids_a & ids_b), "a task was claimed twice"
    assert len(ids_a | ids_b) == 4


async def test_claim_records_the_owning_worker(tmp_path: Path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.db")
    await store.initialize()
    now = datetime.now(UTC)
    await store.add(ScheduledTask.once("job-1", "work", now))

    await store.claim_due(now, 1, owner="worker-a")

    assert (await store.get("job-1")).status is ScheduledTaskStatus.RUNNING
    with store._connect() as connection:
        owner = connection.execute(
            "SELECT owner FROM scheduled_tasks WHERE task_id = ?", ("job-1",)
        ).fetchone()["owner"]
    assert owner == "worker-a"


async def test_existing_database_gains_the_owner_column(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "scheduler.db"
    # A database created before the column existed.
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE scheduled_tasks (
                task_id TEXT PRIMARY KEY, task_text TEXT NOT NULL,
                run_at TEXT NOT NULL, status TEXT NOT NULL,
                attempts INTEGER NOT NULL, max_attempts INTEGER NOT NULL,
                idempotent INTEGER NOT NULL, parent_task_id TEXT,
                child_run_id TEXT, last_error TEXT
            )
            """
        )

    store = SchedulerStore(database)
    await store.initialize()
    now = datetime.now(UTC)
    await store.add(ScheduledTask.once("job-1", "work", now))

    assert len(await store.claim_due(now, 1, owner="w")) == 1


# --- unattended pre-authorization --------------------------------------------


async def test_background_run_denies_a_tool_that_was_not_pre_authorized(
    tmp_path: Path,
) -> None:
    from miniclaw.permissions.approval import UnattendedApprovalProvider
    from miniclaw.permissions.types import PermissionRequest
    from miniclaw.tools.types import ToolCapabilities

    request = PermissionRequest(
        "req-1", "run-1", "call-1", "fetch_url", "<redacted>",
        ToolCapabilities(network="unrestricted"), "unrestricted network",
    )

    resolution = await UnattendedApprovalProvider().resolve(request)

    assert resolution.approved is False


async def test_background_run_allows_only_the_named_tool(tmp_path: Path) -> None:
    from miniclaw.permissions.approval import UnattendedApprovalProvider
    from miniclaw.permissions.types import PermissionRequest
    from miniclaw.tools.types import ToolCapabilities

    provider = UnattendedApprovalProvider(("fetch_url",))

    def request(tool: str) -> PermissionRequest:
        return PermissionRequest(
            f"req-{tool}", "run-1", "call-1", tool, "<redacted>",
            ToolCapabilities(network="unrestricted"), "unrestricted network",
        )

    allowed = await provider.resolve(request("fetch_url"))
    refused = await provider.resolve(request("run_command"))

    assert allowed.approved is True
    assert "pre-authorized" in allowed.reason
    assert refused.approved is False


async def test_pre_authorization_reaches_the_scheduler_agent(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    app = await create_app(
        MiniClawConfig(tmp_path / "data", workspace),
        provider=FakeModel([]),
        unattended_approved_tools=("fetch_url",),
    )
    try:
        provider = app.scheduler_runtime.runner.agent.approval_provider
    finally:
        await app.aclose()

    assert provider.approved_tools == frozenset({"fetch_url"})


async def test_subagents_can_inspect_git_history(tmp_path: Path) -> None:
    from miniclaw.app import DEFAULT_SUBAGENT_TOOLS

    app = await _app(tmp_path)
    try:
        # The parent allowlist is what a child may ever request.
        child_tools = app.subagents.parent_allowed_tools
        registry_names = {tool.spec.name for tool in app.tools.values()}
    finally:
        await app.aclose()

    assert "git_log" in child_tools
    assert set(DEFAULT_SUBAGENT_TOOLS) <= registry_names
