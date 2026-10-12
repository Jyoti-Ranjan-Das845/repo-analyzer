"""A stand-in for `omni run <bundle> --no-session ... -p <kickoff>` used by the tests (and for `claude --version`).

Its behaviour comes from the scenario file named by FAKE_OMNI_SCENARIO:
    {"preflight": "ok" | "fail", "sessions": [spec, ...]}
Each agent session uses the next spec (the last one repeats). A spec is {"action": ...}:
    write      write spec["map"] to .repo-analyzer/map.json; the answer ends with MAP COMPLETE if spec["done"]
    notes      write spec["text"] to .repo-analyzer/notes.md and answer without the marker
    transient  exit 1 with a network error
    fail       exit 1 with another error
    hang       sleep spec["seconds"] (using no CPU), then answer
Like Omnigent, every session records a few conversation items in $TMPDIR/ap-chat-data-*/chat.db.
"""

import json
import os
import sqlite3
import sys
import time

args = sys.argv[1:]
if args and args[0] == "--version":
    print("fake 1.0.0")
    sys.exit(0)

bundle = args[1]
kickoff = args[args.index("-p") + 1]
scenario_path = os.environ["FAKE_OMNI_SCENARIO"]
with open(scenario_path) as fh:
    scenario = json.load(fh)
with open(scenario_path + ".log", "a") as fh:
    fh.write(json.dumps({"bundle": os.path.basename(bundle), "kickoff": kickoff, "cwd": os.getcwd()}) + "\n")


def record(items):
    folder = os.path.join(os.environ.get("TMPDIR", "/tmp"), "ap-chat-data-fake")
    os.makedirs(folder, exist_ok=True)
    con = sqlite3.connect(os.path.join(folder, "chat.db"))
    con.execute("create table if not exists conversation_items (position integer, data text)")
    start = con.execute("select count(*) from conversation_items").fetchone()[0]
    for i, item in enumerate(items):
        con.execute("insert into conversation_items values (?, ?)", (start + i, json.dumps(item)))
    con.commit()
    con.close()


if os.path.basename(bundle) == "preflight":
    if scenario.get("preflight", "ok") == "ok":
        print("READY")
        sys.exit(0)
    print("Error: not signed in to Claude", file=sys.stderr)
    sys.exit(1)

counter = scenario_path + ".count"
n = int(open(counter).read()) if os.path.exists(counter) else 0
with open(counter, "w") as fh:
    fh.write(str(n + 1))
specs = scenario["sessions"]
spec = specs[min(n, len(specs) - 1)]

record([
    {"agent": "repo_analyzer_mapper", "name": "sys_os_read", "arguments": json.dumps({"path": "README.md"})},
    {"agent": "resp_1", "name": "sys_os_read", "arguments": json.dumps({"path": "README.md"})},
    {"role": "assistant", "content": [{"type": "output_text", "text": f"Session {n + 1} working."}]},
])

action = spec["action"]
if action == "write":
    with open(os.path.join(".repo-analyzer", "map.json"), "w") as fh:
        json.dump(spec["map"], fh)
    print("The map is written." + ("\nMAP COMPLETE" if spec.get("done") else ""))
elif action == "notes":
    with open(os.path.join(".repo-analyzer", "notes.md"), "w") as fh:
        fh.write(spec.get("text", "Read the cart code; payments next."))
    print("Saved my notes; more to do.")
elif action == "transient":
    print("API Error: Can't reach the API server (ENOTFOUND)", file=sys.stderr)
    sys.exit(1)
elif action == "fail":
    print("Error: the harness crashed", file=sys.stderr)
    sys.exit(1)
elif action == "hang":
    time.sleep(spec.get("seconds", 3600))
    print("Woke up.")
