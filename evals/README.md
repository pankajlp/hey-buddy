# Hey Buddy scorecard

`tasks.jsonl` is the starter task set. The runner exercises Buddy through the real
router and tools, then checks that the expected tools ran and that their returned
evidence matches each task's success rule.

From the project root:

```bash
.venv/bin/python evals/run_scorecard.py --list
.venv/bin/python evals/run_scorecard.py
.venv/bin/python evals/run_scorecard.py --task system_status --task running_apps
.venv/bin/python evals/run_scorecard.py --task open_calculator --allow-actions
.venv/bin/python evals/run_scorecard.py --suite general --allow-actions
.venv/bin/python evals/run_scorecard.py --personal --allow-actions
```

Without task IDs, it runs the read-only cases. Tasks that open apps, change playback,
or navigate a browser require `--allow-actions`. That flag approves only task-level
actions explicitly listed in that task's `auto_approve_tools`; higher-risk actions
still require a separate approval.

Each run writes a timestamped result under `evals/results/`, including fingerprints
of the task set and Python source so scores from different versions are distinguishable.
It stores task IDs, pass/fail, tool names, and the score only; it does not save prompts,
page text, email contents, or Buddy's spoken reply.

To grow the scorecard from real failures, open **That went wrong** on a Buddy reply and select
**Add this request to my general evaluation scorecard**. Review/edit the task and add a phrase
that should appear in the reply or action evidence. This explicitly saves the task prompt locally
to `~/Library/Application Support/HeyBuddy/learning.json`. Run personal tasks with
`.venv/bin/python evals/run_scorecard.py --personal --allow-actions`; the runner checks expected tool use
and the success phrase, and saves a prompt-free result in the app's local support folder. Personal
tasks are treated as action-capable, and each action still requires approval.

This is an initial smoke scorecard, not yet a validated product success metric. The
core suite's ten tasks cover several current skills, but some checks depend on account
setup or live page content. Expand and calibrate the tasks around one chosen product
workflow before using the score as a headline reliability number. Live runs need the
Mac app's permissions, Ollama, and any account connections used by the selected tasks.

The general suite covers Music, system status, open apps, Gmail, browser search, and
LinkedIn. It is an initial cross-feature sample, not yet a validated product success
metric; expand it with real tasks from normal use and stronger outcome checks before
using it as a headline reliability number.
