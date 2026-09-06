#!/bin/bash
PROMPT='Write a Python function that queries a SQLite database for sector win rates, applies a penalty adjustment, and returns a sorted dict of {sector: adjusted_confidence}. Handle None values and database connection errors gracefully. Use type hints and docstrings.'

for PORT in 8082 8090; do
  echo "=============================="
  echo "=== Port $PORT ==="
  echo "=============================="
  curl -s http://localhost:$PORT/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{
      \"model\": \"test\",
      \"messages\": [
        {\"role\": \"system\", \"content\": \"You are a Python coding assistant. /no_think\"},
        {\"role\": \"user\", \"content\": \"$PROMPT\"}
      ],
      \"max_tokens\": 1024,
      \"stream\": false,
      \"chat_template_kwargs\": {\"enable_thinking\": false}
    }" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d['choices'][0]['message']['content'])
"
  echo ""
done
