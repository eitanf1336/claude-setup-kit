#!/usr/bin/env python3
"""Smoke test for the two guards. Run: python3 test-guards.py   (from this folder)
Each case is (hook, command, expected exit code). 2 = blocked, 0 = allowed."""
import json, subprocess, sys

D = "--" + "delete"          # built from pieces so a guard on THIS shell does not trip over the test text
CASES = [
    ("delete-guard.py", f"rsync -a {D} a/ b/", 2),
    ("delete-guard.py", f"rsync -an {D} a/ b/", 0),
    ("delete-guard.py", f"rsync -a {D} a/ b/ # I-CHECKED-DRY-RUN", 0),
    ("delete-guard.py", "rm -rf ~/code", 2),
    ("delete-guard.py", "rm -rf ./build", 0),
    ("delete-guard.py", "git clean -fdx", 2),
    ("delete-guard.py", "ls -la", 0),
    ("disk-guard.py", "dd if=/dev/zero of=/dev/" + "sda", 2),
    ("disk-guard.py", "fdisk -l", 0),
    ("disk-guard.py", "ls", 0),
]
bad = 0
for hook, cmd, want in CASES:
    r = subprocess.run([sys.executable, hook], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                       capture_output=True, text=True)
    ok = r.returncode == want
    bad += not ok
    print("ok  " if ok else "FAIL", hook, "|", cmd, "->", r.returncode)
sys.exit(1 if bad else 0)
