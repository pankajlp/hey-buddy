"""
Skills: deterministic actions written in code (not improvised by the model).
Each skill is exposed to agents as a tool. Specialist agents get a few skills each.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


@dataclass
class Skill:
    name: str
    description: str
    func: Callable[..., str]
    parameters: Dict[str, Any] = field(default_factory=dict)  # JSON-schema properties
    required: List[str] = field(default_factory=list)
    needs_approval: bool = False  # True: part of a task the user must approve first
    read_only: bool = False       # True: only looks, never changes anything

    def spec(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": self.parameters, "required": self.required},
            },
        }


SKILLS: Dict[str, Skill] = {}


def skill(name: str, description: str, parameters: Dict[str, Any] = None, required: List[str] = None,
          needs_approval: bool = False, read_only: bool = False):
    """Register a function as a skill."""
    def register(func: Callable[..., str]) -> Callable[..., str]:
        SKILLS[name] = Skill(name, description, func, parameters or {}, required or [],
                             needs_approval, read_only)
        return func
    return register
