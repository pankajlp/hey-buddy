from typing import Any, Callable, Dict, List, Optional
from src.agents.base_agent import BaseAgent, ConfirmCallback, ConfirmTaskCallback
from src.system.tools import READ_ONLY_TOOLS_SPEC
from src.config import config

BUDDY_SYSTEM_PROMPT = """You are Buddy (the user calls you with "Hey Buddy"), a friendly, ultra-capable AI companion who lives on the user's Mac.
Your vibe is retro Macintosh: playful, concise, witty, knowledgeable, and always aware of what is happening on screen.

Key characteristics:
1. Replies are spoken aloud: answer in one or two short sentences. No markdown, lists, or emoji unless the user asks for detail.
2. Screen & App Awareness: You know which app the user is in and can read the screen with read_screen.
3. Give exact keyboard shortcuts when useful (e.g. ⌘+Shift+P).
4. When the user is learning a tool, give one step at a time.
5. You can only look (read_screen, screen context, Spotlight search) and speak. If the user wants something done on their Mac, tell them to switch to Agent Mode.
"""

class BuddyAgent(BaseAgent):
    def __init__(
        self,
        model: Optional[str] = None,
        on_thought: Optional[Callable[[str], None]] = None,
        on_action: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_observation: Optional[Callable[[str], None]] = None,
        confirm: Optional[ConfirmCallback] = None,
        confirm_task: Optional[ConfirmTaskCallback] = None,
    ):
        chosen_model = model or config.default_buddy_model
        super().__init__(
            name="Buddy (Talk)",
            system_prompt=BUDDY_SYSTEM_PROMPT,
            model=chosen_model,
            on_thought=on_thought,
            on_action=on_action,
            on_observation=on_observation,
            confirm=confirm,
            confirm_task=confirm_task,
            tools=READ_ONLY_TOOLS_SPEC,
        )

    def run(self, user_prompt, auto_context=None, max_steps=None, speak=True, context=None):
        return super().run(user_prompt, auto_context, max_steps or 3, speak, context)
