# 参与 MiniClaw

欢迎修复问题、改进文档、补充真实故障场景，或扩展工具与执行策略。

## 开发环境

使用 Python 3.12+。完整交互与 Process 后端测试请在 Linux、macOS 或 WSL2 中运行。

```bash
git clone https://github.com/Lh0326/miniclaw.git
cd miniclaw
uv sync --locked --extra dev --python 3.12
uv run ruff check .
uv run pytest -q
bash scripts/smoke_offline.sh
```

离线测试不需要 API Key。不要在测试中访问真实付费模型服务；优先使用 FakeModel、ScriptedModel、MockTransport 和可注入的时钟/等待函数。

## 提交改动

1. Fork 仓库，从 `main` 创建分支。
2. 每次改动聚焦一个问题，保持现有公共接口与默认权限行为。
3. 说明触发条件、修改后的行为和验证结果。影响持久化或权限时，说明恢复与兼容边界。
4. 运行上述检查后提交 Pull Request；文档改动确认示例命令、相对链接和源码一致。

新增工具必须声明 `ToolCapabilities`，尤其是 `side_effects` 与 `idempotent`。无法确认幂等时应声明为非幂等，避免恢复时未经审批继续。外部工具需要说明其执行环境与权限边界。

修改流式协议、Checkpoint、审批策略或事件存储时，请补充能区分正确行为与回归的测试。不要让模型输出或 Skill 内容绕过宿主策略。

## 问题反馈

请提供版本/提交号、Python 版本、操作系统、最小复现步骤，以及脱敏后的错误信息。报告模型协议问题时可提供去除凭据的最小流式片段；说明是否发生在首个模型事件之前。

不要提交 `.env`、API Key、个人配置、会话数据库、Trace、Checkpoint 或真实工作区内容。安全问题参见 [SECURITY.md](SECURITY.md)。

本仓库以 [MIT](LICENSE) 许可发布，提交贡献时保留已有版权与许可声明。
