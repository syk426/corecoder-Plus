# OpenLoop

OpenLoop 是一个面向终端的 AI 编程助手，支持通过 OpenAI 兼容接口调用大语言模型，并提供文件读写、代码搜索、Shell 命令、会话管理、Skills、Hooks、MCP、知识库和定时任务等能力。

## 五分钟先把它跑起来

读之前先让它在你的机器上活一次，后面所有代码你才有体感。

```bash
git clone https://github.com/syk426/corecoder-Plus OpenLoop
cd OpenLoop
pip install -e .
```

然后给它一个模型和 key。OpenLoop 默认走 OpenAI 兼容接口，所以你手上任何一家的 key 都能用，换 provider 只是换两个环境变量：

```bash
# OpenAI
export OPENLOOP_API_KEY=sk-...

# 或者 DeepSeek，便宜很多，改一个 base_url 就行
export OPENLOOP_API_KEY=sk-... OPENLOOP_BASE_URL=https://api.deepseek.com OPENLOOP_MODEL=deepseek-chat

# 或者本地 Ollama，一分钱不花
export OPENLOOP_API_KEY=ollama OPENLOOP_BASE_URL=http://localhost:11434/v1 OPENLOOP_MODEL=qwen2.5-coder
```

跑起来：

```bash
openloop
```

也可以使用 `openloop --version` 查看版本、`openloop --help` 查看参数，
或使用 `python -m openloop` 启动。

项目配置从当前工作目录的 `.env` 加载；也支持原有的 `OPENAI_API_KEY`、
`OPENAI_BASE_URL` 和 `DEEPSEEK_API_KEY`。本地状态目录为 `.openloop/`，
全局会话位于 `~/.openloop/sessions/`，命令历史保存在 `~/.openloop_history`。

可选功能依赖可通过 `pip install -e '.[knowledge]'`、
`pip install -e '.[mcp]'` 或 `pip install -e '.[litellm]'` 安装。

## 项目来源

本项目基于 [he-yufeng/CoreCoder](https://github.com/he-yufeng/CoreCoder) 进行学习和二次开发。在原项目的基础上，我围绕工具扩展、自动化工作流、上下文管理和开发体验进行了改进。

主要改进记录请参阅 [CHANGELOG.md](./CHANGELOG.md)。

## 免责声明

本项目主要用于学习、研究和个人开发实践。使用外部模型、Shell 工具或远程沙箱时，请确认相关服务的权限、费用和安全策略。

## 致谢

感谢 [he-yufeng/CoreCoder](https://github.com/he-yufeng/CoreCoder) 提供的项目基础和启发。
