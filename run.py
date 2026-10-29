#!/usr/bin/env python3
"""
Hey Buddy - An AI Companion & Agent for macOS powered by Local Ollama.
"""

import argparse
import logging
import sys
import uvicorn
from rich.console import Console

from src.config import config
from src.core.ollama_client import OllamaClient
from src.interfaces.cli import BuddyCLI
from src.system.mac_bridge import MacBridge

console = Console()

def check_environment():
    ollama = OllamaClient()
    if not ollama.is_alive():
        console.print("[bold red]Warning:[/bold red] Could not reach Ollama at " + config.ollama_host)
        console.print("Please start Ollama with: [bold cyan]ollama serve[/bold cyan]\n")
        return False
    return True

def run_cli():
    check_environment()
    cli = BuddyCLI()
    cli.run_interactive()

def run_web():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    check_environment()
    console.print(f"[bold cyan]Launching Hey Buddy Floating Companion HUD on http://{config.web_host}:{config.web_port}[/bold cyan]")
    uvicorn.run("src.web.app:app", host=config.web_host, port=config.web_port, reload=False)

def run_status():
    ctx = MacBridge.get_screen_context()
    ollama = OllamaClient()
    models = ollama.list_models()
    
    console.print("\n[bold cyan]=== Hey Buddy System Diagnostic ===[/bold cyan]")
    console.print(f"Ollama Status: {'[green]Online[/green]' if ollama.is_alive() else '[red]Offline[/red]'}")
    console.print(f"Available Models: {', '.join(models) if models else 'None'}")
    console.print(f"Active Frontmost App: [bold]{ctx.active_app}[/bold] (PID: {ctx.pid})")
    console.print(f"Screen Resolution: {ctx.screen_width}x{ctx.screen_height}")
    console.print(f"Mouse Coordinates: ({ctx.mouse_x}, {ctx.mouse_y})")
    console.print(f"Running GUI Applications: {', '.join(ctx.running_apps[:8])}")
    from src.connectors import google_auth
    gmail = google_auth.account_email() if google_auth.is_connected() else None
    console.print(f"Gmail: {'[green]connected (' + gmail + ')[/green]' if gmail else '[yellow]not connected (run: python run.py google-login)[/yellow]'}\n")

def run_google_login():
    from src.connectors import google_auth
    console.print("[bold cyan]Connecting Hey Buddy to Gmail[/bold cyan]")
    console.print("Your browser will open. Sign in to Google and approve access (read mail + create drafts).\n")
    try:
        email = google_auth.login()
    except google_auth.NotConnected as e:
        console.print(f"[bold red]Not connected:[/bold red] {e}")
        return
    console.print(f"[bold green]Connected:[/bold green] {email}. Buddy can now read your mail and save drafts (it never sends).")

def main():
    parser = argparse.ArgumentParser(description="Hey Buddy - AI Companion for macOS via Ollama")
    parser.add_argument("mode", nargs="?", default="cli", choices=["cli", "web", "status", "google-login"], help="Execution mode (default: cli)")
    args = parser.parse_args()

    if args.mode == "cli":
        run_cli()
    elif args.mode == "web":
        run_web()
    elif args.mode == "status":
        run_status()
    elif args.mode == "google-login":
        run_google_login()

if __name__ == "__main__":
    main()
