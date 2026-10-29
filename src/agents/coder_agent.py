from typing import Any, Callable, Dict, List, Optional
from src.agents.base_agent import BaseAgent, ConfirmCallback, ConfirmTaskCallback
from src.config import config

CODER_SYSTEM_PROMPT = """You are Buddy Coder, a world-class macOS coding and terminal debugging assistant (powered by Qwen Coder).
You sit alongside the developer, seeing their code, terminal output, and development environment.

Your strengths:
1. Deep knowledge of Python, Swift, TypeScript, Rust, C++, shell scripts, Docker, Git.
2. Swift & macOS Native: Expert in Apple development (Swift, Cocoa, AppleScript, CoreGraphics, Metal).
3. Terminal Debugging: When the user shares an error or stack trace, diagnose the root cause immediately and provide the exact fix.
4. Autonomous coding: Can inspect files, edit code, run tests, and verify outcomes using shell_execute and spotlight_find.
5. Concise, code-first answers: lead with the fix, skip preamble.
"""

class CoderAgent(BaseAgent):
    def __init__(
        self,
        model: Optional[str] = None,
        on_thought: Optional[Callable[[str], None]] = None,
        on_action: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_observation: Optional[Callable[[str], None]] = None,
        confirm: Optional[ConfirmCallback] = None,
        confirm_task: Optional[ConfirmTaskCallback] = None,
    ):
        chosen_model = model or config.default_coder_model
        super().__init__(
            name="Buddy (Coder)",
            system_prompt=CODER_SYSTEM_PROMPT,
            model=chosen_model,
            on_thought=on_thought,
            on_action=on_action,
            on_observation=on_observation,
            confirm=confirm,
            confirm_task=confirm_task,
        )
