"""Delete Claude Code sessions of a project: all sessions of a group, or sessions given by ID.

Groups are those of the session index (rules in .claude-sessions.toml), plus `empty` (the sessions without prompts).
Ungrouped sessions are work not yet filed, so they go only by ID. An ID can be any unique prefix of one, such as the 8
characters the index shows for sessions without prompts.

Per session it deletes what Claude Code keeps under ~/.claude: the transcript projects/<project dir>/<ID>.jsonl
(what `claude -r` lists), projects/<project dir>/<ID>/ (subagent transcripts, tool results), file-history/<ID>/
(checkpoints for /rewind) and session-env/<ID>/; a background session goes through `claude rm` first, which removes
its jobs/<ID[:8]>/. Left alone: its prompts in ~/.claude/history.jsonl (the up-arrow history of all projects) and
its transcript dumps. Sessions still running are skipped. Lists everything and asks before deleting (-n: only list;
-y: don't ask); then rebuilds the index.

  claude-sessions delete --group junk --group empty -n
  claude-sessions delete 05106cb2 6b39cf27
"""
import glob
import json
import os
import shutil
import subprocess
import sys

from . import index
from .config import CLAUDE, tilde

DONE = ('done', 'stopped')      # background sessions in other states still run


def live_sessions():
    """IDs of the sessions running now, from ~/.claude/sessions/<pid>.json. A session whose process can't be
    checked from here (another host or PID namespace) counts as running."""
    try:
        with open('/etc/machine-id') as f:
            domain = f"linux:{f.read().strip()}:{os.readlink('/proc/self/ns/pid')}"
    except OSError:
        domain = None
    live = set()
    for p in glob.glob(os.path.join(CLAUDE, 'sessions', '*.json')):
        try:
            with open(p) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if domain and d.get('pidDomain') == domain:
            try:
                os.kill(d['pid'], 0)
            except ProcessLookupError:
                continue                # a stale file: that process has ended
            except (PermissionError, KeyError, TypeError):
                pass
        live.add(d.get('sessionId'))
    return live


def job_state(sid):
    """State of session `sid` as a background session (jobs/<ID[:8]>/state.json), or None if it never was one."""
    try:
        with open(os.path.join(CLAUDE, 'jobs', sid[:8], 'state.json')) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d.get('state') if d.get('sessionId') == sid else None


def leftovers(s):
    sid = s['id']
    paths = [os.path.join(s['dir'], sid + '.jsonl'), os.path.join(s['dir'], sid),
             os.path.join(CLAUDE, 'file-history', sid), os.path.join(CLAUDE, 'session-env', sid)]
    return [p for p in paths if os.path.lexists(p)]


def size(p):
    if os.path.isdir(p) and not os.path.islink(p):
        n = sum(os.lstat(os.path.join(d, f)).st_size for d, _, fs in os.walk(p) for f in fs)
    else:
        n = os.lstat(p).st_size
    return f'{n / 1e3:.1f} kB'


def select(groups, ids, sessions, project_groups):
    by_group, empty, _ = index.classify(sessions, project_groups)
    keys = [g['key'] for g in project_groups]
    chosen = {}
    for key in groups:
        if key in keys:
            ss = by_group.get(key, [])
        elif key == 'empty':
            ss = empty
        elif key == 'ungrouped':
            sys.exit('Ungrouped sessions are deleted only by ID.')
        else:
            sys.exit(f"No group {key!r}; groups: {', '.join(keys + ['empty'])}")
        if not ss:
            print(f'Group {key!r} has no sessions.')
        chosen.update((s['id'], s) for s in ss)
    for prefix in ids:
        m = [s for s in sessions if s['id'].startswith(prefix.lower())]
        if len(m) != 1:
            sys.exit(f"{prefix!r} matches " + (', '.join(s['id'] for s in m) if m else 'no session of this project'))
        chosen[m[0]['id']] = m[0]
    return sorted(chosen.values(), key=lambda s: s['t0'])


def run(p, ids, groups, dry_run=False, yes=False):
    sessions = index.load_sessions(p)
    chosen = select(groups, ids, sessions, p.groups)
    live = live_sessions()
    named = {}                          # session name -> hand-off notes that name it
    for h in index.read_handoffs(p):
        for n in {h['by'], h['opens']} - {None}:
            named.setdefault(index.norm(n), []).append(os.path.basename(h['path']))

    todo = []
    for s in chosen:
        sid, state = s['id'], job_state(s['id'])
        print(f"{sid}  {s['t0']:%Y-%m-%d}  {s['prompts']} prompt{'' if s['prompts'] == 1 else 's'}  "
              f'{index.label(s)}')
        if sid in live or state not in (None,) + DONE:
            print('    skipped: still running' + (f' (background session, {state})' if state else ''))
            continue
        paths = leftovers(s)
        if state:
            print(f'    claude rm {sid[:8]}  (background session, {state})')
        for f in paths:
            print(f'    {size(f):>10}  {tilde(f)}')
        for d in index.dumps(p, s):
            print(f'    kept: {os.path.relpath(d, p.root)}')
        if s['custom'] and index.norm(s['custom']) in named:
            print(f"    named in hand-off note(s) {', '.join(named[index.norm(s['custom'])])}")
        todo.append((s, paths, state))

    if not todo or dry_run:
        print(f"{len(todo)} session(s) to delete" + ('; dry run, nothing deleted.' if dry_run else '.'))
        return 0
    if not yes:
        if not sys.stdin.isatty():
            sys.exit('Not a terminal, so no one to ask: pass -y to delete, or -n to only list.')
        if input(f'Delete these {len(todo)} session(s)? [y/N] ').strip().lower() not in ('y', 'yes'):
            sys.exit('Nothing deleted.')

    for s, paths, state in todo:
        if state:
            r = subprocess.run(['claude', 'rm', s['id'][:8]], stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=120)
            if r.returncode:
                print(f"{s['id'][:8]}: `claude rm` failed, session left as it is:\n{r.stdout}{r.stderr}")
                continue
        for f in paths:
            if os.path.isdir(f) and not os.path.islink(f):
                shutil.rmtree(f)
            elif os.path.lexists(f):    # `claude rm` may have taken it already
                os.remove(f)
        print(f"deleted {s['id']}")
    summary, _ = index.write(p)
    print(summary)
    return 0
