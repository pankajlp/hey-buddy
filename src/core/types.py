from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class ToolCall(BaseModel):
    id: Optional[str] = None
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)

class Message(BaseModel):
    role: str
    content: str
    thinking: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None

class ScreenContext(BaseModel):
    active_app: str = "Unknown"
    bundle_id: str = ""
    pid: int = 0
    screen_width: int = 1920
    screen_height: int = 1080
    mouse_x: int = 0
    mouse_y: int = 0
    active_window: str = ""
    running_apps: List[str] = Field(default_factory=list)
    ocr_snippets: List[str] = Field(default_factory=list)
    raw_ocr: Optional[List[Dict[str, Any]]] = None

class AgentStepResult(BaseModel):
    thought: Optional[str] = None
    action_name: Optional[str] = None
    action_args: Optional[Dict[str, Any]] = None
    action_output: Optional[str] = None
    final_response: Optional[str] = None
    is_done: bool = False
