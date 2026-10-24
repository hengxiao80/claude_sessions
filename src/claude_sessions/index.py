"""The session index: a project's Claude Code sessions by group, written to its index file (notes/sessions.md).

Reads the session transcripts Claude Code keeps for the project (config.session_dirs) and sorts them into the groups
of .claude-sessions.toml (rules at its top and in the README). Per session: dates (local time), name, number of
prompts, session ID and transcript dumps (<transcripts>/*_<ID[:8]>_*.md, written by the hooks or
`claude-sessions dump`). Then one table of the hand-off notes in <handoffs>/:
- out: the date and session in the note's 'Written <date> at the end of / during session "<name>"';
- in: the session the note's title names ('# Hand-off: "<name>..."'), or else a session whose first prompt names
  the note's file; the date is that session's first.
Last, the checks: group entries that claim no session, and sessions named '<word>: ...' with no group <word>.

Run by the hooks at every session start and end (`claude-sessions hook`), and by hand: claude-sessions index
[--stdout]; by hand it also prints the counts and the checks.
"""
import datetime
import glob
import json
import os
import re

from .config import CONFIG, tilde

KEYED = re.compile(r'\s*([A-Za-z0-9_-]+)\s*:')      # a name that looks like '<group key>: ...'


def norm(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def prompt_text(m):
    """The user's own words in a user record, or None (tool results, commands, meta records)."""
    c = m.get('content') if isinstance(m, dict) else None
    if isinstance(c, list):
        c = '\n'.join(b.get('text', '') for b in c if isinstance(b, dict) and b.get('type') == 'text')
    if not isinstance(c, str) or not c.strip() or c.lstrip().startswith('<'):
        return None
    return c


def read_session(path, handoff_ref=None):
    s = {'id': os.path.splitext(os.path.basename(path))[0], 'dir': os.path.dirname(path), 'custom': None,
         'ai': None, 'prompts': 0, 'first_prompt': None, 'refs': set(), 't0': None, 't1': None}
    with open(path, errors='replace') as f:
        for line in f:
            i = line.find('"timestamp":"')
            if i >= 0:
                ts = line[i + 13:line.find('"', i + 13)]
                s['t0'] = s['t0'] or ts
                s['t1'] = ts
            # Only the small bookkeeping records and user records need parsing.
            if not any(k in line for k in ('"type":"custom-title"', '"type":"ai-title"', '"type":"user"')):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get('type')
            if t == 'custom-title':
                s['custom'] = d.get('customTitle') or s['custom']
            elif t == 'ai-title':
                s['ai'] = d.get('aiTitle') or s['ai']
            elif t == 'user' and not d.get('isMeta'):
                text = prompt_text(d.get('message'))
                if text:
                    s['prompts'] += 1
                    if not s['first_prompt']:
                        s['first_prompt'] = ' '.join(text.split())[:60]
                        if handoff_ref:
                            s['refs'].update(handoff_ref.findall(text))
    s['name'] = s['custom'] or s['ai'] or s['first_prompt'] or '(no name)'
    for k in ('t0', 't1'):
        s[k] = (datetime.datetime.fromisoformat(s[k].replace('Z', '+00:00')).astimezone() if s[k]
                else datetime.datetime.fromtimestamp(os.path.getmtime(path)).astimezone())
    return s


def read_handoffs(p):
    if not p.handoffs:
        return []
    notes = []
    for f in sorted(glob.glob(os.path.join(p.handoffs, '*.md'))):
        with open(f) as fh:
            text = fh.read(3000)
        title = re.search(r'"([^"]+)"', text.split('\n', 1)[0])
        m = re.search(r'Written (\d{4}-\d\d-\d\d) (?:at the end of|during) session "([^"]+)"', text)
        notes.append({'path': f, 'opens': title.group(1).split(':')[0] if title else None,
                      'written': m.group(1) if m else os.path.basename(f)[:10],
                      'by': m.group(2) if m else None})
    return notes


def assign(s, groups):
    """(group key, the `ids` or `names` entry that claimed the session, if any)."""
    id8 = s['id'][:8]
    for g in groups:
        if id8 in g.get('ids', []):
            return g['key'], (g['key'], id8)
    for g in groups:
        if s['custom'] and re.match(re.escape(g['key']) + r'\s*:', s['custom'], re.I):
            return g['key'], None
    names = [norm(n) for n in (s['custom'], s['ai']) if n]
    for g in groups:
        for n in g.get('names', []):
            if any(norm(n) in x for x in names):
                return g['key'], (g['key'], n)
    return None, None


def link(p, path, text):
    return f'[{text}]({os.path.relpath(path, os.path.dirname(p.index))})'


def label(s):
    return f"**{s['custom']}**" if s['custom'] else f"*{s['name']}*"


def dumps(p, s):
    return sorted(glob.glob(os.path.join(p.transcripts, f"*_{s['id'][:8]}_*.md"))) if p.transcripts else []


def row(p, s):
    d0, d1 = f"{s['t0']:%m-%d}", f"{s['t1']:%m-%d}"
    cells = [d0 if d0 == d1 else f'{d0} – {d1}', label(s), str(s['prompts']), f"`{s['id']}`"]
    if p.transcripts:
        cells.append(', '.join(link(p, f, os.path.basename(f)) for f in dumps(p, s)))
    return '| ' + ' | '.join(cells) + ' |'


def handoff_rows(p, notes, sessions):
    named = {norm(s['custom']): s for s in sessions if s['custom']}   # later sessions win
    rows = []
    for h in notes:
        by = named.get(norm(h['by'])) if h['by'] else None
        out = f"{label(by)} (`{by['id'][:8]}`)" if by else (f"{h['by']} (not found)" if h['by'] else '')
        into = [named[norm(h['opens'])]] if h['opens'] and norm(h['opens']) in named else [
            s for s in sessions if os.path.basename(h['path']) in s['refs']]
        rows.append(f"| {link(p, h['path'], os.path.basename(h['path']))} | {h['written']} | {out} | " +
                    '; '.join(f"{s['t0']:%Y-%m-%d}" for s in into) + ' | ' +
                    '; '.join(f"{label(s)} (`{s['id'][:8]}`)" for s in into) + ' |')
    return rows


def load_sessions(p):
    ref = (re.compile(re.escape(os.path.relpath(p.handoffs, p.root)) + r'/([\w.-]+\.md)') if p.handoffs
           else None)
    sessions = [read_session(f, ref) for d in p.dirs for f in glob.glob(os.path.join(d, '*.jsonl'))]
    sessions.sort(key=lambda s: s['t0'])
    return sessions


def classify(sessions, groups):
    """Sessions with prompts by group key (None: ungrouped), sessions without, and the claiming entries. Also sets
    each session's 'group'."""
    empty = [s for s in sessions if s['prompts'] == 0]
    by_group, used = {}, set()
    for s in sessions:
        if s['prompts']:
            s['group'], entry = assign(s, groups)
            by_group.setdefault(s['group'], []).append(s)
            used.add(entry)
    return by_group, empty, used


def checks(sessions, groups, used):
    """Group entries that claim no session, and sessions named '<word>: ...' with no group <word>."""
    found = [f"`{g['key']}`: {'ids' if e in g.get('ids', []) else 'names'} entry {e!r} claims no session "
             '(stale, or covered by an earlier entry)'
             for g in groups for e in g.get('ids', []) + g.get('names', []) if (g['key'], e) not in used]
    keys = {g['key'].lower() for g in groups}
    for s in sessions:
        m = KEYED.match(s['custom'] or '')
        if s['prompts'] and m and m.group(1).lower() not in keys:
            found.append(f"{label(s)} (`{s['id'][:8]}`): no group `{m.group(1)}` (a typo, or a group still to "
                         'add); filed under ' + (f"`{s['group']}`" if s['group'] else 'Ungrouped'))
    return found


def build(p):
    """The index text, a one-line summary, and the checks."""
    sessions = load_sessions(p)
    notes = read_handoffs(p)
    by_group, empty, used = classify(sessions, p.groups)
    found = checks(sessions, p.groups, used)

    now = datetime.datetime.now().astimezone()
    src = ', '.join(f'`{tilde(d)}/`' for d in p.dirs)
    lines = [f'# Claude Code sessions for {os.path.basename(p.root)}', '',
             f"Written by `claude-sessions index` on {now:%Y-%m-%d %H:%M %Z} from {src or 'nowhere yet'} "
             f'({len(sessions)} sessions). Groups and their rules: `{CONFIG}`.', '',
             'Resume one with `claude -r <session ID>` (or `claude -r "<name>"` if you named it). '
             'Bold: a name you gave the session; italics: Claude Code\'s own title. '
             'Prompts: messages you typed, not counting slash commands. Dates in local time.', '']
    head = '| Dates | Name | Prompts | Session ID |' + (' Transcript dumps |' if p.transcripts else '')
    rule = '|---|---|--:|---|' + ('---|' if p.transcripts else '')
    for g in p.groups + [{'key': None, 'title': 'Ungrouped'}]:
        ss = by_group.get(g['key'])
        if not ss:
            continue
        key = f" (`{g['key']}`)" if g['key'] else ''
        d0, d1 = f"{ss[0]['t0']:%Y-%m-%d}", f"{max(s['t1'] for s in ss):%Y-%m-%d}"
        span = d0 if d0 == d1 else f'{d0} – {d1}'
        n = f"{len(ss)} session{'s' if len(ss) > 1 else ''}"
        lines += [f"## {g['title']}{key}: {n}, {span}", '', head, rule] + [row(p, s) for s in ss] + ['']
    if notes:
        lines += [f'## Hand-off notes: {len(notes)}', '',
                  '| Note | Out: written | Out: by session | In: picked up | In: by session |',
                  '|---|---|---|---|---|'] + handoff_rows(p, notes, [s for s in sessions if s['prompts']]) + ['']
    if empty:
        lines += [f'## Without prompts: {len(empty)}', '',
                  'Opened and left, or used only for slash commands (such as a cancelled `/resume`): ' +
                  ', '.join(f"`{s['id'][:8]}` ({s['t0']:%m-%d})" for s in empty) + '.', '']
    lines += ['## Checks', ''] + ([f'- {c}' for c in found] or ['Nothing to report.']) + ['']

    counts = ', '.join(f"{g['key']} {len(by_group[g['key']])}" for g in p.groups if g['key'] in by_group)
    summary = (f"{p.index}: {counts or 'no groups'}; ungrouped {len(by_group.get(None, []))}; "
               f'without prompts {len(empty)}')
    return '\n'.join(lines), summary, found


def write(p):
    """Write the index file; returns the summary and the checks."""
    text, summary, found = build(p)
    os.makedirs(os.path.dirname(p.index), exist_ok=True)
    # Atomic, since the SessionStart/SessionEnd hooks of two sessions may run at once.
    tmp = f'{p.index}.{os.getpid()}.tmp'
    with open(tmp, 'w') as f:
        f.write(text)
    os.replace(tmp, p.index)
    return summary, found
