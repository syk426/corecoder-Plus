# OpenLoop

OpenLoop 是一个面向终端的 AI 编程助手，支持通过 OpenAI 兼容接口调用大语言模型，并提供文件读写、代码搜索、Shell 命令、会话管理、Skills、Hooks、MCP、知识库和定时任务等能力。

## 安装与启动

在已有的 Conda `agent` 环境中安装本项目：

```bash
conda activate agent
cd /path/to/OpenLoop
python -m pip install -e .
openloop
```

可以使用 `openloop --version` 查看版本、`openloop --help` 查看参数，
也可以使用 `python -m openloop` 启动。
可编辑安装会记录源码的绝对路径；以后移动项目后，需要在新目录重新运行
`python -m pip install -e .`。

项目配置从当前工作目录的 `.env` 加载；模型相关变量使用 `OPENLOOP_MODEL`、
`OPENLOOP_API_KEY`、`OPENLOOP_BASE_URL`、`OPENLOOP_MAX_TOKENS`、
`OPENLOOP_MAX_CONTEXT`、`OPENLOOP_TEMPERATURE` 和 `OPENLOOP_PROVIDER`。
也支持原有的 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `DEEPSEEK_API_KEY`。
本地状态目录为 `.openloop/`，全局会话位于 `~/.openloop/sessions/`，
命令历史保存在 `~/.openloop_history`。

可选功能依赖可通过 `pip install -e '.[knowledge]'`、
`pip install -e '.[mcp]'` 或 `pip install -e '.[litellm]'` 安装。

## 项目来源

本项目基于 [he-yufeng/CoreCoder](https://github.com/he-yufeng/CoreCoder) 进行学习和二次开发。在原项目的基础上，我围绕工具扩展、自动化工作流、上下文管理和开发体验进行了改进。

主要改进记录请参阅 [CHANGELOG.md](./CHANGELOG.md)。

## 免责声明

本项目主要用于学习、研究和个人开发实践。使用外部模型、Shell 工具或远程沙箱时，请确认相关服务的权限、费用和安全策略。

## 致谢

感谢 [he-yufeng/CoreCoder](https://github.com/he-yufeng/CoreCoder) 提供的项目基础和启发。
