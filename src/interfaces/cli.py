import sys
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from src.agents.orchestrator import Orchestrator
from src.config import config
from src.core.ollama_client import OllamaClient
from src.system.mac_bridge import MacBridge
from src.system.voice import VoiceEngine
from src.web.mascot import MASCOT_FACES, RETRO_LOGO_ASCII

console = Console()

class BuddyCLI:
    def __init__(self):
        self.ollama = OllamaClient()
        self.current_mode = "auto"  # 'auto' (router picks), 'buddy', 'agent', 'coder'
        
        # UI Callbacks for live activity display
        def on_thought(thought_text: str):
            clean_thought = thought_text.strip()
            if clean_thought:
                console.print(Panel(
                    Text(clean_thought, style="italic dim cyan"),
                    title=f"[dim cyan]{MASCOT_FACES['thinking']} Thinking...[/dim cyan]",
                    border_style="dim cyan",
                    padding=(0, 1),
                ))

        def on_action(tool_name: str, args: dict):
            console.print(Panel(
                f"[bold yellow]Tool:[/bold yellow] [green]{tool_name}[/green]\n[bold yellow]Args:[/bold yellow] {escape(str(args))}",
                title=f"[yellow]{MASCOT_FACES['agent_working']} Executing Action[/yellow]",
                border_style="yellow",
                padding=(0, 1),
            ))

        def on_observation(obs_text: str):
            preview = obs_text[:300] + ("..." if len(obs_text) > 300 else "")
            console.print(Panel(
                Text(preview, style="dim white"),
                title="[dim green]✓ Observation[/dim green]",
                border_style="dim green",
                padding=(0, 1),
            ))

        def ask_yes_no(title: str, body: str) -> bool:
            console.print(Panel(body, title=f"[bold red]{title}[/bold red]", border_style="red", padding=(0, 1)))
            VoiceEngine.play_sound("Ping")
            try:
                answer = input("Go ahead? [y/N] ").strip().lower()
            except EOFError:
                return False
            return answer in ("y", "yes")

        def confirm(tool_name: str, args: dict, reason=None) -> bool:
            details = "\n".join(f"[bold]{k}:[/bold] {escape(str(v))}" for k, v in args.items())
            if reason:
                details = f"[yellow]{escape(reason)}[/yellow]\n" + details
            return ask_yes_no(f"Allow {tool_name}?", details or "(no arguments)")

        def confirm_task(plan: str, tool_name: str, args: dict) -> bool:
            VoiceEngine.speak(plan)
            return ask_yes_no("Approve this task?", escape(plan) + "\n[dim]Its actions will run without asking again, except risky ones.[/dim]")

        callbacks = dict(on_thought=on_thought, on_action=on_action, on_observation=on_observation,
                         confirm=confirm, confirm_task=confirm_task)
        def on_route(key: str, label: str, method: str):
            console.print(f"[dim]→ {label} ({method})[/dim]")

        self.orchestrator = Orchestrator(on_route=on_route, **callbacks)

    def print_banner(self):
        console.clear()
        console.print(f"[bold cyan]{RETRO_LOGO_ASCII}[/bold cyan]")
        
        # Check Ollama status
        alive = self.ollama.is_alive()
        status_color = "green" if alive else "red"
        status_text = "Online (http://127.0.0.1:11434)" if alive else "Offline - Please start Ollama"
        
        # Get active app info
        screen_ctx = MacBridge.get_screen_context()
        
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_row("[bold]Ollama:[/bold]", f"[{status_color}]{status_text}[/{status_color}]")
        table.add_row("[bold]Active App:[/bold]", f"[cyan]{screen_ctx.active_app}[/cyan] ({screen_ctx.screen_width}x{screen_ctx.screen_height})")
        table.add_row("[bold]Model:[/bold]", f"[magenta]{self.orchestrator.agents['general'].model}[/magenta]")
        table.add_row("[bold]Voice (say):[/bold]", "[green]Enabled[/green]" if config.voice_enabled else "[dim]Disabled[/dim]")
        
        console.print(Panel(table, title="[bold]System Status[/bold]", border_style="cyan"))
        console.print("[dim]Type your message, or commands: [bold]/agent <task>[/bold], [bold]/code <query>[/bold], [bold]/screen[/bold], [bold]/models[/bold], [bold]/voice[/bold], [bold]/quit[/bold][/dim]\n")

    def show_screen_info(self):
        ctx = MacBridge.get_screen_context()
        table = Table(title=f"Mac Screen Context {MASCOT_FACES['listening']}")
        table.add_column("Property", style="bold cyan")
        table.add_column("Value", style="white")
        table.add_row("Frontmost App", ctx.active_app)
        table.add_row("Bundle Identifier", ctx.bundle_id)
        table.add_row("Process ID", str(ctx.pid))
        table.add_row("Window Title", ctx.active_window or "(None)")
        table.add_row("Display Resolution", f"{ctx.screen_width} x {ctx.screen_height}")
        table.add_row("Mouse Position", f"({ctx.mouse_x}, {ctx.mouse_y})")
        table.add_row("Running GUI Apps", ", ".join(ctx.running_apps[:8]))
        console.print(table)

    def run_interactive(self):
        self.print_banner()

        while True:
            try:
                prompt_prefix = {
                    "auto": f"[bold cyan]heybuddy {MASCOT_FACES['idle']}[/bold cyan] > ",
                    "buddy": f"[bold cyan]heybuddy {MASCOT_FACES['idle']}[/bold cyan] > ",
                    "agent": f"[bold yellow]buddy-agent {MASCOT_FACES['agent_working']}[/bold yellow] > ",
                    "coder": f"[bold green]buddy-coder 💻[/bold green] > ",
                }[self.current_mode]

                console.print(prompt_prefix, end="")
                user_input = input().strip()

                if not user_input:
                    continue

                if user_input.lower() in ("/quit", "/exit", "exit", "quit"):
                    console.print(f"[cyan]Bye! {MASCOT_FACES['happy']}[/cyan]")
                    VoiceEngine.speak("Goodbye!")
                    break

                elif user_input.startswith("/screen"):
                    self.show_screen_info()
                    continue

                elif user_input.startswith("/models"):
                    models = self.ollama.list_models()
                    console.print(f"[bold]Installed Ollama Models:[/bold] {', '.join(models)}")
                    continue

                elif user_input.startswith("/voice"):
                    parts = user_input.split()
                    if len(parts) > 1 and parts[1].lower() in ("off", "disable", "0"):
                        config.voice_enabled = False
                        console.print("[dim]Voice feedback disabled.[/dim]")
                    else:
                        config.voice_enabled = True
                        console.print("[green]Voice feedback enabled.[/green]")
                        VoiceEngine.speak("Voice enabled!")
                    continue

                elif user_input.startswith("/agent"):
                    task = user_input[6:].strip()
                    if not task:
                        self.current_mode = "agent"
                        console.print("[yellow]Switched to Autonomous Agent Mode. What shall I do on your Mac?[/yellow]")
                        continue
                    console.print(f"\n[bold yellow]⚡ Running Autonomous Agent Task:[/bold yellow] {task}")
                    reply = self.orchestrator.handle(task, "agent")
                    console.print(Panel(reply, title=f"[bold green]{MASCOT_FACES['celebrating']} Task Complete[/bold green]", border_style="green"))
                    VoiceEngine.speak(reply)
                    continue

                elif user_input.startswith("/code"):
                    code_task = user_input[5:].strip()
                    if not code_task:
                        self.current_mode = "coder"
                        console.print("[green]Switched to Coder Mode (Qwen Coder).[/green]")
                        continue
                    reply = self.orchestrator.handle(code_task, "coder")
                    console.print(Panel(reply, title="[bold green]Coder Response[/bold green]", border_style="green"))
                    continue

                elif user_input.startswith("/auto"):
                    self.current_mode = "auto"
                    console.print("[cyan]Auto mode: Buddy picks the right specialist.[/cyan]")
                    continue

                elif user_input.startswith("/buddy"):
                    self.current_mode = "buddy"
                    console.print("[cyan]Switched to Buddy (Talk) Mode.[/cyan]")
                    continue

                elif user_input.startswith("/help"):
                    console.print("""
[bold cyan]Available Commands:[/bold cyan]
  [bold]/agent <task>[/bold]  - Execute autonomous Mac computer tasks ("runs while you are away")
  [bold]/code <query>[/bold]  - Ask coding/terminal debugging questions (using Qwen Coder)
  [bold]/auto[/bold]          - Let Buddy pick the right specialist (default)
  [bold]/buddy[/bold]         - Switch to friendly on-screen companion mode
  [bold]/screen[/bold]        - Inspect live Mac screen, active window, and coordinates
  [bold]/models[/bold]        - List available local Ollama models
  [bold]/voice on|off[/bold]  - Toggle spoken speech feedback
  [bold]/quit[/bold]         - Exit Hey Buddy
                    """)
                    continue

                # Auto mode lets the router pick; the other modes are fixed
                response = self.orchestrator.handle(user_input, self.current_mode)
                console.print(Panel(response, title=f"[bold cyan]Buddy {MASCOT_FACES['happy']}[/bold cyan]", border_style="cyan"))
                VoiceEngine.speak(response)

            except (KeyboardInterrupt, EOFError):
                console.print(f"\n[cyan]See you later! {MASCOT_FACES['happy']}[/cyan]")
                break
            except Exception as e:
                console.print(f"[bold red]Error:[/bold red] {e}")
