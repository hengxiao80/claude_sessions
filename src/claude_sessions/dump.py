"""Dump a Claude Code session transcript (JSONL) to Markdown in the project's transcripts directory.

Run by the hooks before a compaction (PreCompact) and when the context is cleared (SessionEnd, reason "clear"). By
hand: claude-sessions dump <transcript.jsonl or session ID> [label].

Keeps user and assistant messages: text, tool calls (name + input) and tool results, in full. Thinking blocks and
bookkeeping records are left out. Output: <transcripts>/<YYYY-MM-DD_HHMMSS>_<session id[:8]>_<label>.md (UTC time of
the dump).
"""
import datetime
import json
import os


def block_text(b):
    t = b.get('type')
    if t == 'text':
        return b.get('text', '')
    if t == 'tool_use':
        inp = json.dumps(b.get('input', {}), indent=1, ensure_ascii=False)
        return f"**Tool call: {b.get('name')}**\n\n```json\n{inp}\n```"
    if t == 'tool_result':
        c = b.get('content')
        if isinstance(c, list):
            c = '\n'.join(x.get('text', f"[{x.get('type')}]") for x in c if isinstance(x, dict))
        c = str(c or '')
        err = ' (error)' if b.get('is_error') else ''
        return f"**Tool result{err}:**\n\n```\n{c.replace('```', '` ` `')}\n```"
    return None   # thinking, images, ...


def convert(path, session, label, out_dir):
    now = datetime.datetime.now(datetime.timezone.utc)
    parts = [f'# Session transcript {session}', '',
             f'- Source: `{path}`', f'- Dumped: {now:%Y-%m-%d %H:%M:%S} UTC ({label})', '']
    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            role = d.get('type')
            m = d.get('message')
            if role not in ('user', 'assistant') or not isinstance(m, dict):
                continue
            c = m.get('content')
            texts = [c] if isinstance(c, str) else [block_text(b) for b in c or [] if isinstance(b, dict)]
            texts = [t for t in texts if t and t.strip()]
            if not texts:
                continue
            head = 'Compaction summary' if d.get('isCompactSummary') else role.capitalize()
            parts += [f"## {head} — {d.get('timestamp', '')}", ''] + ['\n\n'.join(texts), '']
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f'{now:%Y-%m-%d_%H%M%S}_{session[:8]}_{label}.md')
    with open(out, 'w') as f:
        f.write('\n'.join(parts))
    return out
