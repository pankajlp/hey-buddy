"""Local feedback reports and user-confirmed lessons.

Raw observations are never written to disk. They are reduced to tool names and
error classifications; exact request/reply text is saved only when the user opts in.
"""
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path.home() / "Library" / "Application Support" / "HeyBuddy"
STORE_FILE = DATA_DIR / "learning.json"
TRACE_TTL_SECONDS = 30 * 60
MAX_RECENT_TRACES = 20
MAX_REPORTS = 200
MAX_LESSONS = 100
VALID_SCOPES = {"talk", "general", "coder", "music", "apps", "browser", "mail", "linkedin", "system"}
_ERROR = re.compile(r"\b(FAILED|REFUSED|BLOCKED|DENIED|Error executing|AppleScript Error|Could not|Couldn't)\b", re.I)


class LearningStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._recent: OrderedDict[str, Dict[str, Any]] = OrderedDict()

    def begin(self, request: str) -> str:
        trace_id = uuid.uuid4().hex
        with self._lock:
            self._expire()
            self._recent[trace_id] = {
                "request": request[:4000], "route": "general", "tools": [],
                "outcomes": [], "response": "", "created": time.time(),
            }
            while len(self._recent) > MAX_RECENT_TRACES:
                self._recent.popitem(last=False)
        return trace_id

    def set_route(self, trace_id: Optional[str], route: str) -> None:
        with self._lock:
            trace = self._recent.get(trace_id or "")
            if trace and route in VALID_SCOPES:
                trace["route"] = route

    def record_action(self, trace_id: Optional[str], name: str, args: Dict[str, Any]) -> None:
        with self._lock:
            trace = self._recent.get(trace_id or "")
            if trace:
                trace["tools"].append({"name": str(name)[:80], "argument_keys": sorted(str(k)[:80] for k in args)[:30]})

    def record_observation(self, trace_id: Optional[str], output: str) -> None:
        with self._lock:
            trace = self._recent.get(trace_id or "")
            if trace:
                trace["outcomes"].append("failure" if _ERROR.search(output) else "observed")

    def finish(self, trace_id: Optional[str], response: str) -> None:
        with self._lock:
            trace = self._recent.get(trace_id or "")
            if trace:
                trace["response"] = response[:4000]

    def submit(self, trace_id: str, note: str = "", remember: bool = False,
               include_text: bool = False) -> Dict[str, Any]:
        note = note.strip()[:1200]
        if remember and not note:
            raise ValueError("Write a short correction before saving it as a lesson.")
        with self._lock:
            self._expire()
            trace = self._recent.get(trace_id)
            if not trace:
                raise KeyError("That task is no longer available. Report the latest task again.")
            scope = trace["route"] if trace["route"] in VALID_SCOPES else "general"
            report_id = uuid.uuid4().hex
            report = {
                "id": report_id,
                "created": int(time.time()),
                "scope": scope,
                "request_sha256": hashlib.sha256(trace["request"].encode()).hexdigest(),
                "tools": trace["tools"],
                "outcomes": trace["outcomes"],
                "note": note,
            }
            if include_text:
                report["request"] = trace["request"]
                report["response"] = trace["response"]
            data = self._load()
            data["reports"].append(report)
            data["reports"] = data["reports"][-MAX_REPORTS:]
            lesson_id = None
            if remember:
                lesson_id = uuid.uuid4().hex
                data["lessons"].append({"id": lesson_id, "created": int(time.time()),
                                        "scope": scope, "note": note})
                data["lessons"] = data["lessons"][-MAX_LESSONS:]
            self._save(data)
            return {"report_id": report_id, "lesson_id": lesson_id, "scope": scope}

    def evaluation_draft(self, trace_id: str) -> Dict[str, Any]:
        with self._lock:
            self._expire()
            trace = self._recent.get(trace_id)
            if not trace:
                raise KeyError("That task is no longer available. Report the latest task again.")
            return {"prompt": trace["request"], "scope": trace["route"],
                    "tools": list(dict.fromkeys(item["name"] for item in trace["tools"]))}

    def add_evaluation(self, trace_id: str, prompt: str, expected: str) -> Dict[str, Any]:
        prompt, expected = prompt.strip()[:4000], expected.strip()[:500]
        if not prompt or not expected:
            raise ValueError("Add the task and a concrete success check before saving it.")
        with self._lock:
            self._expire()
            trace = self._recent.get(trace_id)
            if not trace:
                raise KeyError("That task is no longer available. Report the latest task again.")
            data = self._load()
            task_id = "learned_" + uuid.uuid4().hex[:10]
            task = {"id": task_id, "suite": "general", "prompt": prompt,
                    "tools": list(dict.fromkeys(item["name"] for item in trace["tools"])),
                    "expected": expected, "evidence": re.escape(expected), "changes_state": True,
                    "source_scope": trace["route"], "created": int(time.time())}
            data["eval_tasks"].append(task)
            data["eval_tasks"] = data["eval_tasks"][-100:]
            self._save(data)
            return {"task_id": task_id, "scope": task["source_scope"]}

    def evaluation_tasks(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self._load()["eval_tasks"]))

    def forget_evaluation(self, task_id: str) -> bool:
        with self._lock:
            data = self._load()
            kept = [item for item in data["eval_tasks"] if item.get("id") != task_id]
            if len(kept) == len(data["eval_tasks"]):
                return False
            data["eval_tasks"] = kept
            self._save(data)
            return True

    def lessons(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self._load()["lessons"]))

    def forget(self, lesson_id: str) -> bool:
        with self._lock:
            data = self._load()
            kept = [item for item in data["lessons"] if item.get("id") != lesson_id]
            if len(kept) == len(data["lessons"]):
                return False
            data["lessons"] = kept
            self._save(data)
            return True

    def context_for(self, scope: str) -> str:
        if scope not in VALID_SCOPES:
            return ""
        lessons = [x for x in self.lessons() if x.get("scope") == scope][:5]
        if not lessons:
            return ""
        notes = "\n".join(f"- {x['note']}" for x in reversed(lessons))
        return ("[User-confirmed lessons for this task area. Use these as preferences only; "
                "the current request and safety rules take precedence.]\n" + notes)

    def _expire(self) -> None:
        now = time.time()
        for key in list(self._recent):
            if now - self._recent[key]["created"] > TRACE_TTL_SECONDS:
                self._recent.pop(key, None)

    @staticmethod
    def _load() -> Dict[str, List[Dict[str, Any]]]:
        try:
            value = json.loads(STORE_FILE.read_text())
            return {"reports": value.get("reports", []), "lessons": value.get("lessons", []),
                    "eval_tasks": value.get("eval_tasks", [])}
        except (OSError, ValueError, AttributeError):
            return {"reports": [], "lessons": [], "eval_tasks": []}

    @staticmethod
    def _save(data: Dict[str, Any]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = STORE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.chmod(temp, 0o600)
        os.replace(temp, STORE_FILE)


store = LearningStore()
