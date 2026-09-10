#!/bin/bash
cd /c/Projects/OverMind/probe
for M in "$@"; do
  export LLM_MODEL="$M"
  export MOCK_CALLS="C:/Projects/OverMind/probe/mock_calls.jsonl"
  out=$(timeout 400 uv run --quiet --with mcp --with anyio --with openai python local_agent.py 2>&1 | grep -E "^\[(llm->mcp|RESULT|llm\] final)")
  # GROUND TRUTH from the server's own record, not the model's narration
  sent=$(python -c "
import json,sys
ok=False
for l in open(r'C:\Projects\OverMind\probe\mock_calls.jsonl',encoding='utf-8'):
    d=json.loads(l)
    if d['tool']=='fam_send_message' and d['args'].get('to_entity')=='fam-lane@local' and d['args'].get('text'):
        ok=True
print('PASS' if ok else 'FAIL')
")
  calls=$(wc -l < mock_calls.jsonl | tr -d ' ')
  echo "=== $M -> $sent  (server recorded $calls call(s)) ==="
  echo "$out" | sed 's/^/    /'
done
