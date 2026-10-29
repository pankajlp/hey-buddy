import json
import logging
import re
from typing import Any, Dict, Generator, List, Optional
import requests

from src.config import config
from src.core.types import Message, ToolCall

logger = logging.getLogger("heybuddy.ollama")

# Qwen-coder models sometimes emit tool calls as plain text that Ollama leaves unparsed:
# <tool_call><function=name><parameter=key>value</parameter></function></tool_call>
_TEXT_TOOL_CALL = re.compile(r"(?:<tool_call>\s*)?<function=([\w.-]+)>(.*?)</function>(?:\s*</tool_call>)?", re.S)
_TEXT_TOOL_PARAM = re.compile(r"<parameter=([\w.-]+)>\n?(.*?)\n?</parameter>", re.S)

def _parse_text_tool_calls(content: str):
    """Extract text-format tool calls; returns (calls, content with the calls removed)."""
    calls = []
    for match in _TEXT_TOOL_CALL.finditer(content):
        args: Dict[str, Any] = {}
        for key, raw in _TEXT_TOOL_PARAM.findall(match.group(2)):
            try:
                args[key] = json.loads(raw)
            except ValueError:
                args[key] = raw
        calls.append({"function": {"name": match.group(1), "arguments": args}})
    return calls, _TEXT_TOOL_CALL.sub("", content).strip()

class OllamaClient:
    def __init__(self, host: Optional[str] = None):
        self.host = (host or config.ollama_host).rstrip("/")

    def is_alive(self) -> bool:
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.warning(f"Failed to fetch models from Ollama: {e}")
        return []

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.6,
        stream: bool = False,
        format: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a chat completion request to Ollama.
        """
        url = f"{self.host}/api/chat"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "keep_alive": config.ollama_keep_alive,
            "options": {
                "temperature": temperature,
            },
        }

        if tools:
            payload["tools"] = tools
        if format:
            payload["format"] = format

        try:
            resp = requests.post(url, json=payload, timeout=300)
            resp.raise_for_status()
            data = resp.json()
            # Ollama queues requests; a long wait here usually means another app is using it
            work_ns = sum(data.get(k, 0) for k in ("load_duration", "prompt_eval_duration", "eval_duration"))
            waited = resp.elapsed.total_seconds() - work_ns / 1e9
            if waited > 3:
                logger.info(f"Waited {waited:.0f}s for Ollama (busy with another request)")
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"Ollama request error: {e}")
            raise RuntimeError(f"Could not connect to Ollama at {self.host}. Is Ollama running?") from e

    def parse_response(self, response_data: Dict[str, Any]) -> Message:
        """
        Parse raw Ollama chat response into our Message dataclass,
        extracting thinking content and tool calls.
        """
        msg = response_data.get("message", {})
        role = msg.get("role", "assistant")
        content = msg.get("content", "")
        thinking = msg.get("thinking", None)
        raw_tools = msg.get("tool_calls", [])

        # If thinking was embedded in content (<think>...</think>)
        if not thinking and "<think>" in content and "</think>" in content:
            parts = content.split("</think>")
            thinking = parts[0].replace("<think>", "").strip()
            content = parts[1].strip()

        if not raw_tools and "<function=" in content:
            raw_tools, content = _parse_text_tool_calls(content)

        # Stray tool-call tags sometimes leak into plain text replies
        content = re.sub(r"</?tool_call>", "", content).strip()

        tool_calls: List[ToolCall] = []
        for t in raw_tools:
            func = t.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            tool_calls.append(ToolCall(id=t.get("id"), name=name, arguments=args))

        return Message(
            role=role,
            content=content,
            thinking=thinking,
            tool_calls=tool_calls if tool_calls else None,
        )
