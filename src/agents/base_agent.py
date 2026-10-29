import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from src.config import config
from src.core.ollama_client import OllamaClient
from src.core.types import AgentStepResult, Message
from src.system.mac_bridge import MacBridge
from src.system.tools import SENSITIVE_TOOLS, TOOLS_SPEC, ToolDispatcher, high_risk_reason
from src.system.voice import VoiceEngine
from src.user_settings import USER_NAME

logger = logging.getLogger("heybuddy.agent")

# Asks the user whether one sensitive tool call may run: (tool_name, args, reason) -> approved
ConfirmCallback = Callable[[str, Dict[str, Any], Optional[str]], bool]
# Asks the user to approve a whole task by its plan: (plan, tool_name, args) -> approved
ConfirmTaskCallback = Callable[[str, str, Dict[str, Any]], bool]

PLAN_REQUEST = (
    "Before acting, tell the user in ONE very short spoken sentence (under 12 words) what you "
    "are about to do, ending with 'Go ahead?'. Plain text, no tools. Example: "
    "'I'll open Safari and search for weather. Go ahead?'"
)

def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


# Keep this many recent user turns of conversation (older ones are dropped to stay fast)
HISTORY_TURNS = 8

# A specialist calls this when a request isn't in its area; the orchestrator re-routes it
HANDOFF_TOOL = {
    "type": "function",
    "function": {
        "name": "not_my_task",
        "description": "Call this if the request is not something your tools can do. Another assistant will handle it.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "Short reason."}},
            "required": ["reason"],
        },
    },
}


class BaseAgent:
    # Include the frontmost app, window, and running apps with each request
    wants_screen_context = True

    def __init__(
        self,
        name: str,
        system_prompt: str,
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        on_thought: Optional[Callable[[str], None]] = None,
        on_action: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_observation: Optional[Callable[[str], None]] = None,
        confirm: Optional[ConfirmCallback] = None,
        confirm_task: Optional[ConfirmTaskCallback] = None,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.model = model
        self.tools = tools if tools is not None else TOOLS_SPEC
        self.client = OllamaClient()
        self.messages: List[Dict[str, Any]] = []
        
        # Event callbacks for UI
        self.on_thought = on_thought
        self.on_action = on_action
        self.on_observation = on_observation
        self.confirm = confirm
        self.confirm_task = confirm_task
        self.task_approved = False  # set once the user approves the current task's plan
        self.speak = True
        self.handed_off: Optional[str] = None  # reason, if the agent passed the request on
        self.declined = False  # the user said no; the task stops here
        
        self.reset()

    def reset(self):
        """Reset conversation history with system prompt."""
        about_user = f"\nThe user's name is {USER_NAME}." if USER_NAME != "the user" else ""
        self.messages = [
            {"role": "system", "content": self.system_prompt + about_user}
        ]

    def add_screen_context_to_prompt(self, user_prompt: str) -> str:
        """Enrich user prompt with current live Mac screen & app context."""
        try:
            ctx = MacBridge.get_screen_context()
            context_block = (
                f"\n\n[Live Mac Screen Context]:\n"
                f"- Active Application: {ctx.active_app} (PID: {ctx.pid})\n"
                f"- Active Window: {ctx.active_window or 'N/A'}\n"
                f"- Screen Size: {ctx.screen_width}x{ctx.screen_height}\n"
                f"- Cursor Position: ({ctx.mouse_x}, {ctx.mouse_y})\n"
                f"- Running Apps: {', '.join(ctx.running_apps[:8])}\n"
            )
            return user_prompt + context_block
        except Exception as e:
            logger.debug(f"Could not append screen context: {e}")
            return user_prompt

    def run_step(self) -> AgentStepResult:
        """Run a single ReAct step: LLM call -> parse response -> execute tool."""
        res_data = self.client.chat(
            model=self.model,
            messages=self.messages,
            tools=self.tools,
            temperature=0.4,
        )

        msg = self.client.parse_response(res_data)
        
        # Stream or notify thinking
        if msg.thinking and self.on_thought:
            self.on_thought(msg.thinking)

        # Append assistant response to message history
        assistant_dict: Dict[str, Any] = {
            "role": "assistant",
            "content": msg.content or "",
        }
        if msg.thinking:
            assistant_dict["thinking"] = msg.thinking
            
        if msg.tool_calls:
            assistant_dict["tool_calls"] = [
                {
                    "id": tc.id or f"call_{i}",
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for i, tc in enumerate(msg.tool_calls)
            ]
            self.messages.append(assistant_dict)

            # Execute every requested tool call, in order
            is_done = False
            final_summary = None
            for tc in msg.tool_calls:
                if self.on_action:
                    self.on_action(tc.name, tc.arguments)

                output = self._execute_tool(tc.name, tc.arguments)
                logger.info(f"  {tc.name} {_short(tc.arguments)} -> {_short(output)}")

                if self.on_observation:
                    self.on_observation(output)

                if tc.name == "task_completed":
                    is_done = True
                    final_summary = tc.arguments.get("summary")
                elif tc.name == HANDOFF_TOOL["function"]["name"]:
                    is_done = True
                    self.handed_off = tc.arguments.get("reason") or "not my task"
                    final_summary = ""

                self.messages.append({
                    "role": "tool",
                    "tool_name": tc.name,
                    "content": output,
                })

            last = msg.tool_calls[-1]
            return AgentStepResult(
                thought=msg.thinking,
                action_name=last.name,
                action_args=last.arguments,
                action_output=output,
                final_response=final_summary,
                is_done=is_done,
            )
        else:
            # Plain assistant response (no tool call)
            self.messages.append(assistant_dict)
            return AgentStepResult(
                thought=msg.thinking,
                final_response=msg.content,
                is_done=True,
            )

    def _tool_refusal(self, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        """Reason to refuse this tool call outright (subclasses add rules), or None."""
        return None

    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Run a tool, requiring user approval for sensitive tools in safe mode."""
        if tool_name == HANDOFF_TOOL["function"]["name"]:
            return "Handing the request to another assistant."
        refusal = self._tool_refusal(tool_name, args)
        if tool_name == "task_completed" and self.actions_taken == 0:
            refusal = ("nothing has been done yet. Use a tool to actually do it (or not_my_task if it isn't yours); "
                       "if it can't be done, reply in plain text saying so.")
        if refusal:
            logger.info(f"  refused {tool_name}: {refusal}")
            self.refused.append(tool_name)
            return (f"REFUSED, NOT DONE: {refusal} This action did not happen; "
                    "don't tell the user it did.")
        if config.safe_mode and tool_name in SENSITIVE_TOOLS:
            approved = self._approve(tool_name, args)
            if approved is None:
                return (
                    f"BLOCKED: '{tool_name}' requires user confirmation in safe mode, "
                    "and no confirmation channel is available here. Tell the user what you "
                    "would have done instead."
                )
            if not approved:
                self.declined = True
                return f"DENIED: the user did not approve running '{tool_name}'."
        if tool_name != "task_completed":
            self.actions_taken += 1
        result = ToolDispatcher.execute(tool_name, args, speak=self.speak)
        if tool_name not in ("task_completed", "page_elements", "get_screen_context"):
            shown = ", ".join(f"{v}" for v in args.values() if isinstance(v, (str, int, bool)))[:60]
            self.action_log.append(f"{tool_name}({shown}) -> {_short(result, 70)}")
        return result

    def _approve(self, tool_name: str, args: Dict[str, Any]) -> Optional[bool]:
        """True/False from the user, or None when there is no way to ask."""
        try:
            reason = high_risk_reason(tool_name, args)
            if reason:
                # Always its own explicit approval, even inside an approved task
                return self.confirm(tool_name, args, reason) if self.confirm else None
            if self.task_approved:
                return True
            if self.confirm_task:
                plan = self._plan_summary(tool_name, args)
                logger.info(f"  plan: {plan}")
                approved = self.confirm_task(plan, tool_name, args)
                logger.info(f"  task {'approved' if approved else 'declined'}")
                self.task_approved = approved
                self.declined = not approved
                return approved
            return self.confirm(tool_name, args, None) if self.confirm else None
        except Exception as e:
            logger.warning(f"Confirmation failed for {tool_name}: {e}")
            return False

    def _plan_summary(self, tool_name: str, args: Dict[str, Any]) -> str:
        """One spoken sentence describing what the agent is about to do."""
        try:
            res = self.client.chat(
                model=self.model,
                messages=self.messages + [{"role": "user", "content": PLAN_REQUEST}],
                temperature=0.2,
            )
            plan = self.client.parse_response(res).content
            # Drop any tool-call markup the model appended
            plan = re.split(r"<tool_call>|<function=|</?tool", plan)[0].strip()
            if plan:
                return plan
        except Exception as e:
            logger.warning(f"Could not summarize plan: {e}")
        return f"I'll use {tool_name.replace('_', ' ')}. Go ahead?"

    # What each refusable tool would be described as in a reply
    _CLAIM_WORDS = {"youtube_play": r"\bplay(ed|ing)\b", "click_on_page": r"\b(click(ed)?|open(ed)?|play(ed|ing)?)\b",
                    "gmail_draft": r"\bdraft(ed)?\b", "quit_app": r"\b(clos(ed|ing)|quit)\b"}

    def _without_refused_claims(self, answer: str) -> str:
        """If the reply claims something that was refused, replace it with an honest one."""
        for tool in self.refused:
            pattern = self._CLAIM_WORDS.get(tool)
            if pattern and re.search(pattern, answer, re.IGNORECASE) and self.actions_taken <= len(self.refused):
                return "I didn't do that part, since you didn't ask for it."
            claim = re.search(pattern, answer, re.IGNORECASE) if pattern else None
            if claim:
                # Keep what was really done: cut the reply at the last "and" before the false claim
                cut = answer.lower().rfind(" and ", 0, claim.start())
                return (answer[:cut] if cut > 0 else "I didn't do that part, since you didn't ask for it").rstrip(".") + "."
        return answer

    def _trim_history(self) -> None:
        """Drop old turns, cutting only at user messages so tool calls stay paired with results."""
        user_turns = [i for i, m in enumerate(self.messages) if m["role"] == "user"]
        if len(user_turns) > HISTORY_TURNS:
            self.messages = self.messages[:1] + self.messages[user_turns[-HISTORY_TURNS]:]

    def run(
        self,
        user_prompt: str,
        auto_context: Optional[bool] = None,
        max_steps: Optional[int] = None,
        speak: bool = True,
        context: Optional[str] = None,
    ) -> str:
        """
        Execute an agent turn with optional multi-step tool execution loop.
        `context` is the recent conversation across all agents (for follow-ups like "close that app");
        checks that depend on what the user asked look only at `user_prompt`.
        """
        max_steps = max_steps or config.max_agent_steps
        self.speak = speak
        self.task_approved = False  # each new request needs its own approval
        self.current_request = user_prompt
        self.tool_counts: Dict[str, int] = {}
        self.actions_taken = 0  # tools run for this request (task_completed needs at least one)
        self.action_log: List[str] = []  # short notes of what was done, for the shared conversation memory
        self.refused: List[str] = []  # tool calls refused this request
        self.handed_off = None
        self.declined = False
        if auto_context is None:
            auto_context = self.wants_screen_context
        self._trim_history()
        logger.info(f"{self.name} request: {_short(user_prompt, 300)}")
        enriched_prompt = self.add_screen_context_to_prompt(user_prompt) if auto_context else user_prompt
        if context:
            enriched_prompt = f"[Recent conversation, for context]\n{context}\n\n[Current request]\n{enriched_prompt}"
        self.messages.append({"role": "user", "content": enriched_prompt})

        VoiceEngine.play_sound("Pop")

        for _ in range(max_steps):
            step_result = self.run_step()
            if self.declined:
                # Respect a "no": don't try other ways to do the same thing
                answer = "Okay, I won't do that."
                self.messages.append({"role": "assistant", "content": answer})
                logger.info(f"  reply: {answer}")
                return answer
            if step_result.is_done:
                answer = step_result.final_response or "Done."
                answer = self._without_refused_claims(answer)
                logger.info(f"  reply: {_short(answer, 300)}")
                return answer

        logger.info(f"  stopped: step limit ({max_steps}) reached without finishing")
        return (
            f"I stopped after {max_steps} steps without finishing the task. "
            "Some actions may have run; check the activity log above before retrying."
        )
