"""Run selected Hey Buddy tasks and save a privacy-preserving scorecard.

Examples:
  python evals/run_scorecard.py --list
  python evals/run_scorecard.py --task system_status
  python evals/run_scorecard.py --task browser_search --allow-actions

Action tasks change the live Mac or browser and therefore require --allow-actions.
Sensitive tool approvals remain interactive. Result files contain no prompts, page
text, email contents, or Buddy replies.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TASKS_PATH = Path(__file__).with_name("tasks.jsonl")
RESULTS_DIR = Path(__file__).with_name("results")
PERSONAL_STORE = Path.home() / "Library" / "Application Support" / "HeyBuddy" / "learning.json"


def load_tasks() -> List[Dict[str, Any]]:
    return [json.loads(line) for line in TASKS_PATH.read_text().splitlines() if line.strip()]


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    paths = sorted((ROOT / "src").rglob("*.py")) + [Path(__file__), TASKS_PATH]
    for path in sorted(set(paths)):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List tasks without running them.")
    parser.add_argument("--task", action="append", help="Task ID to run; repeat to run several.")
    parser.add_argument("--suite", choices=["general"], help="Run the general task suite.")
    parser.add_argument("--personal", action="store_true",
                        help="Run locally saved tasks created from mistake reports.")
    parser.add_argument("--allow-actions", action="store_true",
                        help="Allow selected tasks to change the live Mac or browser.")
    args = parser.parse_args()
    tasks = load_tasks()
    if args.personal:
        try:
            personal_data = json.loads(PERSONAL_STORE.read_text())
            tasks = personal_data.get("eval_tasks", [])
        except (OSError, ValueError):
            tasks = []
        if not tasks:
            print("No personal evaluation tasks found. Add one from Buddy's 'That went wrong' report.")
            return 0
    by_id = {task["id"]: task for task in tasks}
    if args.list:
        for task in tasks:
            suffix = " [changes Mac/browser]" if task.get("changes_state") else " [read only]"
            print(f"{task['id']}{suffix}: {task['prompt']}")
        return 0
    if sum(bool(x) for x in (args.task, args.suite, args.personal)) > 1:
        parser.error("choose only one of --task, --suite, or --personal")
    if args.personal:
        selected_ids = [task["id"] for task in tasks]
    elif args.suite:
        selected_ids = [task["id"] for task in tasks if task.get("suite") == args.suite]
    else:
        selected_ids = args.task or [task["id"] for task in tasks if not task.get("changes_state")]
    unknown = [task_id for task_id in selected_ids if task_id not in by_id]
    if unknown:
        parser.error("unknown task ID(s): " + ", ".join(unknown))
    selected = [by_id[task_id] for task_id in selected_ids]
    blocked = [task["id"] for task in selected if task.get("changes_state") and not args.allow_actions]
    if blocked:
        parser.error("these tasks change the live Mac/browser; rerun with --allow-actions: " + ", ".join(blocked))

    events: List[Dict[str, str]] = []
    current = {"tool": ""}

    def on_action(tool: str, _arguments: Dict[str, Any]) -> None:
        current["tool"] = tool

    def on_observation(output: str) -> None:
        events.append({"tool": current["tool"], "output": output})

    active_task: Dict[str, Any] = {}

    def approve_task(plan: str, tool: str, arguments: Dict[str, Any]) -> bool:
        if args.allow_actions and tool in active_task.get("auto_approve_tools", []):
            if tool != "type_on_page" or re.search(r"search", arguments.get("field", ""), re.I):
                return True
        answer = input(f"Approve for this eval? {plan} [y/N] ").strip().lower()
        return answer in {"y", "yes"}

    def approve_high_risk(tool: str, _arguments: Dict[str, Any], reason: str | None) -> bool:
        answer = input(f"High-risk action {tool}: {reason}. Approve? [y/N] ").strip().lower()
        return answer in {"y", "yes"}

    from src.agents import memory
    from src.agents.orchestrator import Orchestrator

    # Keep eval history isolated from Buddy's normal on-disk conversation memory.
    eval_turns: List[Dict[str, Any]] = []
    memory._load = lambda: list(eval_turns)

    def save_eval_turns(turns: List[Dict[str, Any]]) -> None:
        eval_turns[:] = turns

    memory._save = save_eval_turns
    run_records = []
    for task in selected:
        print(f"\nRunning {task['id']}…")
        events.clear()
        eval_turns.clear()
        current["tool"] = ""
        active_task.clear()
        active_task.update(task)
        try:
            orchestrator = Orchestrator(on_action=on_action, on_observation=on_observation,
                                        confirm=approve_high_risk, confirm_task=approve_task)
            reply = orchestrator.handle(task["prompt"], mode="auto", speak=False)
        except KeyboardInterrupt:
            print("Stopped by user.")
            return 130
        except Exception as exc:
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            run_records.append({"task": task["id"], "success": False, "reason": "runner_error", "tools": []})
            continue

        used_tools = [event["tool"] for event in events]
        evidence = "\n".join([*(event["output"] for event in events), reply or ""])
        required_tools_seen = all(name in used_tools for name in task["tools"])
        completed = bool(reply and reply.strip())
        evidence_seen = bool(re.search(task["evidence"], evidence, re.I | re.S))
        failed = bool(re.search(r"\b(FAILED|REFUSED|BLOCKED|DENIED|Error executing|AppleScript Error|Gmail error)\b",
                                evidence, re.I))
        success = required_tools_seen and completed and evidence_seen and not failed
        reason = ("passed" if success else "no_final_response" if not completed else
                  "missing_expected_tool_or_evidence" if not failed else "tool_failure")
        run_records.append({"task": task["id"], "success": success, "reason": reason,
                            "tools": sorted(set(used_tools))})
        print(f"  {'PASS' if success else 'FAIL'} ({reason})")

    if args.personal:
        results_dir = PERSONAL_STORE.parent / "evaluation-results"
        results_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    else:
        results_dir = RESULTS_DIR
        results_dir.mkdir(exist_ok=True)
    path = results_dir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    passed = sum(record["success"] for record in run_records)
    summary = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "suite": args.suite or "selected",
               "task_set_sha256": hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest(),
               "source_sha256": source_fingerprint(),
               "passed": passed,
               "total": len(run_records), "success_rate": passed / len(run_records) if run_records else 0,
               "results": run_records}
    path.write_text(json.dumps(summary, indent=2) + "\n")
    if args.personal:
        path.chmod(0o600)
    print(f"\nScore: {passed}/{len(run_records)} ({summary['success_rate']:.0%}). Results: {path}")
    return 0 if passed == len(run_records) else 1


if __name__ == "__main__":
    sys.exit(main())
