"""Interactive REPL - the user-facing terminal interface."""

import sys
import os
import argparse
import threading
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from .agent import Agent
from .llm import LLM, LiteLLM
from .config import Config, load_settings, get_skills_dir, get_knowledge_dir, load_mcp_config
from .hooks import HookManager
from .skills import SkillManager
from .mcp import MCPClientManager
from .cron import CronScheduler
from .knowledge import KnowledgeManager
from .session import save_session, load_session, list_sessions
from . import __version__

console = Console()


def _parse_args():
    p = argparse.ArgumentParser(
        prog="openloop",
        description="Minimal AI coding agent. Works with any OpenAI-compatible LLM.",
    )
    p.add_argument("-m", "--model", help="Model name (default: $OPENLOOP_MODEL or gpt-5.5)")
    p.add_argument("--base-url", help="API base URL (default: $OPENAI_BASE_URL)")
    p.add_argument("--api-key", help="API key (default: $OPENAI_API_KEY)")
    p.add_argument("-p", "--prompt", help="One-shot prompt (non-interactive mode)")
    p.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session")
    p.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args()


def main():
    args = _parse_args()
    config = Config.from_env()

    # CLI args override env vars
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key

    if not config.api_key:
        console.print("[red bold]No API key found.[/]")
        console.print(
            "Set one of: OPENAI_API_KEY, DEEPSEEK_API_KEY, or OPENLOOP_API_KEY\n"
            "\nExamples:\n"
            "  # OpenAI\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "\n"
            "  # DeepSeek\n"
            "  export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com\n"
            "\n"
            "  # Ollama (local)\n"
            "  export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 OPENLOOP_MODEL=qwen2.5-coder\n"
        )
        sys.exit(1)

    llm_cls = LiteLLM if config.provider == "litellm" else LLM
    llm = llm_cls(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )

    # Load hooks from settings.json
    settings = load_settings()
    hook_manager = HookManager()
    hook_manager.load_from_settings(settings)

    # Initialize skill manager
    skills_dir = get_skills_dir()
    skill_manager = SkillManager(skills_dir)
    skill_manager.scan_skills()

    # Initialize MCP manager
    mcp_manager = MCPClientManager()
    mcp_config = load_mcp_config()
    mcp_servers = mcp_config.get("mcpServers", {})

    # Connect to enabled MCP servers
    for server_name, server_config in mcp_servers.items():
        if server_config.get("enabled", True):
            result = mcp_manager._run_async(mcp_manager.connect(server_name, server_config))
            console.print(f"[dim]{result}[/dim]")

    # Initialize cron scheduler
    cron_storage = Path(".openloop") / "cron_jobs.json"
    cron_scheduler = CronScheduler(cron_storage)

    def _on_cron_fire(job):
        console.print(f"\n[bold magenta][Cron fired][/] {job.prompt}")

    cron_scheduler._on_fire = _on_cron_fire
    if cron_scheduler.list_jobs():
        console.print(f"[dim]Loaded {len(cron_scheduler.list_jobs())} cron job(s)[/dim]")

    # Initialize knowledge base
    knowledge_dir = get_knowledge_dir()
    chroma_dir = Path(".openloop") / "chroma_db"
    knowledge_manager = KnowledgeManager(knowledge_dir, chroma_dir)
    try:
        result = knowledge_manager.scan()
        if "Indexed" in result:
            console.print(f"[dim]{result}[/dim]")
    except Exception as e:
        console.print(f"[dim]Knowledge base: {e} (install with: pip install openloop[knowledge])[/dim]")

    agent = Agent(
        llm=llm,
        max_context_tokens=config.max_context_tokens,
        hooks=hook_manager,
        skills=skill_manager,
        mcp_manager=mcp_manager,
        cron_scheduler=cron_scheduler,
        knowledge_manager=knowledge_manager,
    )

    # Lock to prevent concurrent agent execution (REPL vs queue processor)
    agent_lock = threading.Lock()

    def queue_processor_loop():
        """Auto-deliver fired cron jobs when the agent is idle."""
        while True:
            time.sleep(0.2)
            if not agent.cron_scheduler or not agent.cron_scheduler.has_pending():
                continue
            if not agent_lock.acquire(blocking=False):
                continue
            try:
                if not agent.cron_scheduler.has_pending():
                    continue
                fired = agent.cron_scheduler.consume_queue()
                console.print(
                    f"\n[bold magenta][Cron] {len(fired)} job(s) — "
                    f"auto-delivering...[/]"
                )
                for job in fired:
                    console.print(f"  [dim]→ {job.prompt}[/dim]")
                    agent.messages.append({
                        "role": "user",
                        "content": f"[Scheduled: {job.id}] {job.prompt}",
                    })
                try:
                    response = agent.chat(
                        "Please handle the scheduled task(s) that just fired.",
                        on_token=lambda tok: print(tok, end="", flush=True),
                        on_tool=lambda name, kwargs: console.print(
                            f"\n[dim]> {name}({_brief(kwargs)})[/dim]"
                        ),
                    )
                    print()
                except Exception:
                    pass
            finally:
                agent_lock.release()

    import time
    threading.Thread(target=queue_processor_loop, daemon=True).start()

    # resume saved session
    if args.resume:
        loaded = load_session(args.resume)
        if loaded:
            agent.messages, loaded_model = loaded
            # restore the model from the saved session unless overridden by CLI
            if not args.model:
                agent.llm.model = loaded_model
                config.model = loaded_model
            console.print(f"[green]Resumed session: {args.resume} (model: {agent.llm.model})[/green]")
        else:
            console.print(f"[red]Session '{args.resume}' not found.[/red]")
            sys.exit(1)

    # one-shot mode
    if args.prompt:
        _run_once(agent, args.prompt, agent_lock)
        return

    # interactive REPL
    _repl(agent, config, agent_lock)


def _run_once(agent: Agent, prompt: str, agent_lock: threading.Lock):
    """Non-interactive: run one prompt and exit."""
    def on_token(tok):
        print(tok, end="", flush=True)

    def on_tool(name, kwargs):
        console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

    try:
        with agent_lock:
            agent.chat(prompt, on_token=on_token, on_tool=on_tool)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        sys.exit(1)
    print()


def _repl(agent: Agent, config: Config, agent_lock: threading.Lock):
    """Interactive read-eval-print loop."""
    console.print(Panel(
        f"[bold]OpenLoop[/bold] v{__version__}\n"
        f"Model: [cyan]{config.model}[/cyan]"
        + (f"  Base: [dim]{config.base_url}[/dim]" if config.base_url else "")
        + "\nType [bold]/help[/bold] for commands, [bold]Ctrl+C[/bold] to cancel, [bold]quit[/bold] to exit."
        + "\n[bold]Shift+Tab[/bold] to toggle Plan Mode.",
        border_style="blue",
    ))

    hist_path = os.path.expanduser("~/.openloop_history")
    history = FileHistory(hist_path)

    plan_mode = False  # mutable, captured by key binding closure

    def _toggle_plan():
        nonlocal plan_mode
        plan_mode = not plan_mode
        if plan_mode:
            agent.enter_plan_mode("User switched to plan mode")
        else:
            agent.exit_plan_mode()

    def _bottom_toolbar():
        if plan_mode:
            return " ⏵⏵ plan mode on (shift+tab to cycle) "
        else:
            return " ⏵⏵ auto mode on (shift+tab to cycle) "

    # Enter submits, Escape+Enter inserts a newline (for pasting code blocks etc.)
    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    @kb.add("s-tab")
    def _toggle_plan_kb(event):
        _toggle_plan()

    while True:
        try:
            prompt_text = "[PLAN] You > " if plan_mode else "You > "
            user_input = pt_prompt(
                prompt_text,
                history=history,
                multiline=True,
                key_bindings=kb,
                prompt_continuation="...  ",
                bottom_toolbar=_bottom_toolbar,
                style=Style.from_dict({"bottom-toolbar": "noreverse"}),
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break

        # Deliver any pending cron jobs before processing user input
        # (fallback in case queue processor couldn't acquire the lock)
        if agent.cron_scheduler and agent.cron_scheduler.has_pending():
            if agent_lock.acquire(blocking=False):
                try:
                    if not agent.cron_scheduler.has_pending():
                        continue
                    fired = agent.cron_scheduler.consume_queue()
                    console.print(
                        f"\n[bold magenta][Cron] {len(fired)} pending job(s) — "
                        f"injecting...[/]"
                    )
                    for job in fired:
                        console.print(f"  [dim]→ {job.prompt}[/dim]")
                        agent.messages.append({
                            "role": "user",
                            "content": f"[Scheduled: {job.id}] {job.prompt}",
                        })
                    try:
                        response = agent.chat(
                            "Please handle the scheduled task(s) that just fired.",
                            on_token=lambda tok: print(tok, end="", flush=True),
                            on_tool=lambda name, kwargs: console.print(
                                f"\n[dim]> {name}({_brief(kwargs)})[/dim]"
                            ),
                        )
                        print()
                    except KeyboardInterrupt:
                        console.print("\n[yellow]Interrupted.[/yellow]")
                    except Exception as e:
                        console.print(f"\n[red]Error: {e}[/red]")
                finally:
                    agent_lock.release()

        if not user_input:
            continue

        # built-in commands
        if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
            break
        if user_input == "/help":
            _show_help()
            continue
        if user_input == "/reset":
            agent.reset()
            console.print("[yellow]Conversation reset.[/yellow]")
            continue
        if user_input == "/tokens":
            p = agent.llm.total_prompt_tokens
            c = agent.llm.total_completion_tokens
            line = f"Tokens: [cyan]{p}[/cyan] prompt + [cyan]{c}[/cyan] completion = [bold]{p+c}[/bold] total"
            cost = agent.llm.estimated_cost
            if cost is not None:
                line += f"  (~${cost:.4f})"
            console.print(line)
            continue
        if user_input == "/model" or user_input.startswith("/model "):
            new_model = user_input[7:].strip() if user_input.startswith("/model ") else ""
            if new_model:
                agent.llm.model = new_model
                config.model = new_model
                console.print(f"Switched to [cyan]{new_model}[/cyan]")
            else:
                console.print(f"Current model: [cyan]{config.model}[/cyan]")
            continue
        if user_input == "/compact":
            from .context import estimate_tokens
            before = estimate_tokens(agent.messages)
            compressed = agent.context.maybe_compress(agent.messages, agent.llm)
            after = estimate_tokens(agent.messages)
            if compressed:
                console.print(f"[green]Compressed: {before} → {after} tokens ({len(agent.messages)} messages)[/green]")
            else:
                console.print(f"[dim]Nothing to compress ({before} tokens, {len(agent.messages)} messages)[/dim]")
            continue
        if user_input == "/save":
            sid = save_session(agent.messages, config.model)
            console.print(f"[green]Session saved: {sid}[/green]")
            console.print(f"Resume with: openloop -r {sid}")
            continue
        if user_input == "/diff":
            from .tools.edit import _changed_files
            if not _changed_files:
                console.print("[dim]No files modified this session.[/dim]")
            else:
                console.print(f"[bold]Files modified this session ({len(_changed_files)}):[/bold]")
                for f in sorted(_changed_files):
                    console.print(f"  [cyan]{f}[/cyan]")
            continue
        if user_input == "/sessions":
            sessions = list_sessions()
            if not sessions:
                console.print("[dim]No saved sessions.[/dim]")
            else:
                for s in sessions:
                    console.print(f"  [cyan]{s['id']}[/cyan] ({s['model']}, {s['saved_at']}) {s['preview']}")
            continue
        if user_input == "/hooks":
            _show_hooks(agent.hooks)
            continue
        if user_input == "/skills":
            _show_skills(agent.skills)
            continue
        if user_input.startswith("/mcp"):
            _handle_mcp_command(agent.mcp_manager, user_input)
            continue
        if user_input.startswith("/cron"):
            _handle_cron_command(agent.cron_scheduler, user_input)
            continue
        if user_input.startswith("/knowledge"):
            _handle_knowledge_command(agent.knowledge_manager, user_input)
            continue
        if user_input.startswith("/sandbox"):
            _handle_sandbox_command(user_input)
            continue
        if user_input == "/plan":
            _toggle_plan()
            continue

        # an unknown /command shouldn't be sent to the model as a prompt
        if user_input.startswith("/"):
            console.print(f"[yellow]Unknown command: {user_input.split()[0]} (try /help)[/yellow]")
            continue

        # call the agent
        streamed: list[str] = []

        def on_token(tok):
            streamed.append(tok)
            print(tok, end="", flush=True)

        def on_tool(name, kwargs):
            console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

        try:
            with agent_lock:
                response = agent.chat(user_input, on_token=on_token, on_tool=on_tool)
            if streamed:
                print()  # newline after streamed tokens
            else:
                # response wasn't streamed (came after tool calls)
                console.print(Markdown(response))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")


def _show_help():
    console.print(Panel(
        "[bold]Commands:[/bold]\n"
        "  /help          Show this help\n"
        "  /reset         Clear conversation history\n"
        "  /model         Show current model\n"
        "  /model <name>  Switch model mid-conversation\n"
        "  /tokens        Show token usage\n"
        "  /compact       Compress conversation context\n"
        "  /diff          Show files modified this session\n"
        "  /save          Save session to disk\n"
        "  /sessions      List saved sessions\n"
        "  /hooks         Show registered hooks\n"
        "  /skills        Show available skills\n"
        "  /mcp           Manage MCP servers (list/connect/disconnect/tools)\n"
        "  /cron          Manage cron jobs (list/cancel)\n"
        "  /knowledge     Manage knowledge base (list/search)\n"
        "  /sandbox       Manage OpenSandbox instances (list/kill)\n"
        "  /plan          Toggle Plan Mode (read-only tools)\n"
        "  quit           Exit OpenLoop\n"
        "\n"
        "[bold]Input:[/bold]\n"
        "  Enter          Submit message\n"
        "  Shift+Tab      Toggle Plan Mode\n"
        "  Esc+Enter      Insert newline (for pasting code)",
        title="OpenLoop Help",
        border_style="dim",
    ))


def _show_hooks(hook_manager):
    """Display registered hooks."""
    any_hooks = False
    for event in sorted(hook_manager._hooks.keys()):
        entries = hook_manager._hooks[event]
        if entries:
            any_hooks = True
            console.print(f"\n[bold]{event}[/bold] ({len(entries)} hooks)")
            for e in entries:
                if e.callback is not None:
                    name = getattr(e.callback, "__name__", repr(e.callback))
                    console.print(f"  Python: {name}")
                elif e.shell is not None:
                    console.print(f"  Shell [{e.shell.matcher}]: {e.shell.command[:60]}")
    if not any_hooks:
        console.print("[dim]No hooks registered.[/dim]")
        console.print("[dim]Add hooks via settings.json or register_hook().[/dim]")


def _show_skills(skill_manager):
    """Display available skills."""
    if not skill_manager.registry:
        console.print("[dim]No skills available.[/dim]")
        console.print(f"[dim]Skills directory: {skill_manager.skills_dir}[/dim]")
        console.print("[dim]Add skills by creating SKILL.md files in the skills directory.[/dim]")
        return

    console.print(f"\n[bold]Available Skills[/bold] ({len(skill_manager.registry)} skills)")
    console.print(f"[dim]Skills directory: {skill_manager.skills_dir}[/dim]\n")

    for name, skill in sorted(skill_manager.registry.items()):
        desc = skill["description"] or "(no description)"
        console.print(f"  [cyan]{name}[/cyan]: {desc}")

    console.print("\n[dim]Use load_skill(name) to load full skill content.[/dim]")


def _handle_mcp_command(mcp_manager: MCPClientManager, command: str):
    """Handle /mcp commands."""
    parts = command.split()

    if len(parts) == 1 or parts[1] == "list":
        # /mcp or /mcp list - show connected servers
        servers = mcp_manager.list_servers()
        if not servers:
            console.print("[dim]No MCP servers connected.[/dim]")
            console.print("[dim]Configure servers in .openloop/mcp_servers.json[/dim]")
            return

        console.print(f"\n[bold]Connected MCP Servers[/bold] ({len(servers)} servers)\n")
        for name, count in servers.items():
            console.print(f"  [cyan]{name}[/cyan]: {count} tools")
        return

    if parts[1] == "tools":
        # /mcp tools [server] - show tools
        if len(parts) == 2:
            # Show all tools from all servers
            all_tools = mcp_manager.get_all_tools()
            if not all_tools:
                console.print("[dim]No MCP tools available.[/dim]")
                return

            console.print(f"\n[bold]MCP Tools[/bold] ({len(all_tools)} tools)\n")
            for tool in all_tools:
                console.print(f"  [cyan]{tool['name']}[/cyan]")
                console.print(f"    {tool['description'][:100]}")
        else:
            # Show tools from specific server
            server_name = parts[2]
            tools = mcp_manager.get_server_tools(server_name)
            if tools is None:
                console.print(f"[red]Server '{server_name}' not connected.[/red]")
                return

            if not tools:
                console.print(f"[dim]Server '{server_name}' has no tools.[/dim]")
                return

            console.print(f"\n[bold]Tools from '{server_name}'[/bold] ({len(tools)} tools)\n")
            for tool in tools:
                console.print(f"  [cyan]{tool['name']}[/cyan]")
                console.print(f"    {tool['description'][:100]}")
        return

    if parts[1] == "disconnect" and len(parts) >= 3:
        # /mcp disconnect <server>
        server_name = parts[2]
        result = mcp_manager._run_async(mcp_manager.disconnect(server_name))
        if "Error" in result:
            console.print(f"[red]{result}[/red]")
        else:
            console.print(f"[green]{result}[/green]")
        return

    # Unknown command
    console.print("[yellow]Usage: /mcp [list|tools|disconnect <server>][/]")
    console.print("[dim]  list                  - Show connected servers[/dim]")
    console.print("[dim]  tools                 - Show all MCP tools[/dim]")
    console.print("[dim]  tools <server>        - Show tools from specific server[/dim]")
    console.print("[dim]  disconnect <server>   - Disconnect from a server[/dim]")


def _handle_cron_command(scheduler, command: str):
    """Handle /cron commands."""
    if scheduler is None:
        console.print("[dim]Cron scheduler not initialized.[/dim]")
        return

    parts = command.split()

    if len(parts) == 1 or (len(parts) == 2 and parts[1] == "list"):
        jobs = scheduler.list_jobs()
        if not jobs:
            console.print("[dim]No cron jobs registered.[/dim]")
            console.print("[dim]Use schedule_cron tool to add jobs.[/dim]")
            return

        console.print(f"\n[bold]Cron Jobs[/bold] ({len(jobs)} jobs)\n")
        for j in jobs:
            tag = "recurring" if j.recurring else "one-shot"
            dur = "durable" if j.durable else "session"
            console.print(f"  [cyan]{j.id}[/cyan]: '{j.cron}'")
            console.print(f"    {j.prompt} [[dim]{tag}, {dur}[/dim]]")
        return

    if len(parts) >= 3 and parts[1] == "cancel":
        job_id = parts[2]
        result = scheduler.cancel(job_id)
        if "not found" in result:
            console.print(f"[red]{result}[/red]")
        else:
            console.print(f"[green]{result}[/green]")
        return

    # Unknown
    console.print("[yellow]Usage: /cron [list|cancel <id>][/]")


def _handle_knowledge_command(km, command: str):
    """Handle /knowledge commands."""
    if km is None:
        console.print("[dim]Knowledge base not available.[/dim]")
        console.print("[dim]Install with: pip install openloop[knowledge][/dim]")
        return

    parts = command.split()

    if len(parts) == 1 or (len(parts) == 2 and parts[1] == "list"):
        result = km.list_entries()
        console.print(f"\n[bold]Knowledge Base[/bold]\n{result}")
        return

    if len(parts) >= 3 and parts[1] == "search":
        query = " ".join(parts[2:])
        result = km.search(query)
        console.print(f"\n[bold]Search: {query}[/bold]\n{result}")
        return

    if len(parts) >= 3 and parts[1] == "remove":
        result = km.remove_entry(parts[2])
        if "Error" in result:
            console.print(f"[red]{result}[/red]")
        else:
            console.print(f"[green]{result}[/green]")
        return

    if len(parts) >= 3 and parts[1] == "ingest":
        pdf_path = parts[2]
        result = km.ingest(pdf_path)
        if "Error" in result:
            console.print(f"[red]{result}[/red]")
        else:
            console.print(f"[green]{result}[/green]")
        return

    console.print("[yellow]Usage: /knowledge [list|search <query>|remove <name>|ingest <file>][/]")


def _brief(kwargs: dict, maxlen: int = 80) -> str:
    s = ", ".join(f"{k}={repr(v)[:40]}" for k, v in kwargs.items())
    return s[:maxlen] + ("..." if len(s) > maxlen else "")


def _handle_sandbox_command(command: str):
    """Handle /sandbox commands (status/list/kill)."""
    from .tools.sandbox import get_active_sandboxes, kill_sandbox, kill_all_sandboxes

    parts = command.split()
    if len(parts) == 1 or (len(parts) == 2 and parts[1] in ("list", "status", "ps")):
        sandboxes = get_active_sandboxes()
        if not sandboxes:
            console.print("[dim]No active OpenSandbox instances running.[/dim]")
            console.print("[dim]Use sandbox_create tool or run Python code to launch containers.[/dim]")
            return

        from rich.table import Table
        table = Table(title=f"Active Alibaba OpenSandbox Instances ({len(sandboxes)})", border_style="cyan")
        table.add_column("Sandbox ID", style="cyan", no_wrap=True)
        table.add_column("Image", style="blue")
        table.add_column("Status", style="green")
        table.add_column("Uptime", style="magenta")
        table.add_column("Remaining TTL", style="yellow")

        for sb in sandboxes:
            table.add_row(
                sb["id"],
                sb["image"],
                sb["status"],
                f"{sb['uptime_seconds']}s",
                f"{sb['remaining_seconds']}s",
            )
        console.print()
        console.print(table)
        console.print()
        return

    if len(parts) >= 3 and parts[1] == "kill":
        target = parts[2].strip()
        if target.lower() == "all":
            count = kill_all_sandboxes()
            console.print(f"[green]Successfully terminated {count} active sandbox container(s).[/green]")
        else:
            success = kill_sandbox(target)
            if success:
                console.print(f"[green]Sandbox '{target}' terminated successfully.[/green]")
            else:
                console.print(f"[red]Sandbox '{target}' not found or already destroyed.[/red]")
        return

    console.print("[yellow]Usage: /sandbox [list|status|kill <id|all>][/yellow]")
    console.print("[dim]  list/status       - Show running OpenSandbox container instances[/dim]")
    console.print("[dim]  kill <id|all>     - Terminate specific or all running container sandboxes[/dim]")

