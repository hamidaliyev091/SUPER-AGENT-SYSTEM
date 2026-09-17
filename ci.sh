#!/bin/sh
# CI gate (PROJECT_CONTRACT s24): every phase must pass before advancing.
# Runs the complete test suite plus the Core purity check. Exit 0 = pass.
set -e
cd "$(dirname "$0")"

echo "== CI gate: full test suite =="
PYTHONPATH=src python3 -m unittest discover -s tests -t .

echo "== CI gate: Core purity (no runtime/model/platform imports) =="
PYTHONPATH=src python3 - <<PYEOF
from pathlib import Path
forbidden = ("termux", "android", "deepseek", "anthropic", "claude",
             "gemini", "pi_ultracode")
offenders = []
for path in Path("src/core").glob("*.py"):
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip().startswith(("import ", "from ")):
            for name in forbidden:
                if name in line.lower():
                    offenders.append(f"{path}:{line_number}: {line.strip()}")
if offenders:
    print("CORE PURITY VIOLATIONS:")
    for offender in offenders:
        print(" ", offender)
    raise SystemExit(1)
print("core purity OK")
PYEOF

echo "== CI gate: PASS =="
