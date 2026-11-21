# Manual analysis: pick clips with your own AI chat

With `LLM=manual`, SupoClip runs no model and needs no API key. It downloads and transcribes the video as usual, then pauses the task (status `awaiting_analysis`) so you can use a free AI chat (ChatGPT, Claude, Gemini, ...) to choose the clips.

This is useful when you cannot or do not want to run a local model or pay for an API key, and for long videos a small local model cannot analyse within the time budget.

## Flow

1. Set `LLM=manual` and submit a video. The task reaches **50%** and waits in `awaiting_analysis`.
2. Download the prompt: `GET /tasks/{task_id}/analysis-prompt` returns Markdown with the clip-selection instructions, the timestamped transcript, and the exact JSON shape wanted.
3. Give it to an AI chat: attach the downloaded file and send a short message such as `Read the attached file and follow its instructions. Reply with the JSON object only.` (the task page shows this message with a copy button), or paste the file's text if the chat accepts it. The chat replies with one JSON object; the answer may be wrapped in a code fence or have a sentence around it, both are accepted.
4. Submit the answer: `POST /tasks/{task_id}/analysis` with `{"response": "<the chat's reply>"}`. Already-decoded JSON can be sent as `{"analysis": {...}}` instead.
5. The answer is validated like a model's: timestamps are snapped onto transcript lines, spans outside the clip length are repaired or dropped, near-duplicates are removed, and the best clips are kept. An unusable answer returns `422` with the reason (not JSON, cut off, wrong shape, no usable segment), so you can ask the chat to fix it and submit again. The task stays paused until an answer is accepted.
6. Once accepted, the task resumes and renders the clips.

```bash
curl -H "x-api-key: sk_..." http://localhost:8000/tasks/$TASK/analysis-prompt -o prompt.md
# paste prompt.md into your AI chat, save its reply as reply.txt
curl -X POST -H "x-api-key: sk_..." -H "Content-Type: application/json" \
  --data "$(jq -Rs '{response: .}' reply.txt)" http://localhost:8000/tasks/$TASK/analysis
```

## Limits

- Highlights only. Steps that need a real model fail with a clear message under `LLM=manual`: AI B-roll planning and the series, topic, recap and trailer workflows.
- The prompt and your answer are stored with the task in PostgreSQL, so this works when the API and the worker run on different machines.
- Chat answers are only as grounded as the chat makes them. Segments are matched against the real transcript, so invented or misquoted timestamps are snapped or dropped, but check the clips.
- On the task page, the panel for a waiting task has Download prompt, the message to send with the file, then Paste or Upload for the JSON answer.
