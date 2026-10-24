"""claude-sessions: index, dump and delete a project's Claude Code sessions.

A project is the nearest directory at or above the current one with a .claude-sessions.toml (`init` writes one).
"""
import argparse
import glob
import json
import os
import subprocess
import sys

from . import delete, dump, index
from .config import CONFIG, SKELETON, ConfigError, Project, add_gitignore, find_root, first_cwd


def project():
    p = Project.find()
    if not p:
        sys.exit(f'No {CONFIG} here or above; `claude-sessions init` writes one.')
    return p


def in_git(d):
    try:
        return subprocess.run(['git', '-C', d, 'rev-parse', '--is-inside-work-tree'], capture_output=True,
                              text=True).stdout.strip() == 'true'
    except OSError:
        return False


def cmd_init(args):
    """Write the skeleton .claude-sessions.toml into DIR, create the directories of its paths (notes/,
    notes/handoffs/, notes/transcripts/) where missing, write the index (notes/sessions.md), and add the index and
    the transcripts directory to DIR/.gitignore (generated files; the dumps hold whole transcripts). DIR must be in
    a git work tree; if it is not, init does nothing."""
    root = os.path.realpath(args.dir)
    path = os.path.join(root, CONFIG)
    if not os.path.isdir(root):
        sys.exit(f'{root} is no directory.')
    if not in_git(root):
        sys.exit(f'{root} is in no git work tree; nothing done. Run `git init` there first.')
    if os.path.exists(path):
        print(f'{path} exists; nothing done.')
        return 1
    outer = find_root(os.path.dirname(root))
    with open(path, 'x') as f:
        f.write(SKELETON)
    print(f'wrote {path}')
    if outer:
        print(f'Sessions started here now belong to this project, not to the one at {outer}.')
    p = Project(root)
    for d in (os.path.dirname(p.index), p.handoffs, p.transcripts):
        if d and not os.path.isdir(d):
            os.makedirs(d)
            print(f'created {d}/')
    print(index.write(p)[0])
    print(add_gitignore(root, [os.path.relpath(p.index, root), os.path.relpath(p.transcripts, root) + '/']))
    return 0


def cmd_index(args):
    p = project()
    if args.stdout:
        print(index.build(p)[0])
        return 0
    summary, found = index.write(p)
    print(summary)
    if found:
        print('Checks:\n' + '\n'.join(f'  {c}' for c in found))
    return 0


def cmd_delete(args):
    if not args.ids and not args.group:
        sys.exit('Give session IDs or --group.')
    return delete.run(project(), args.ids, args.group, args.dry_run, args.yes)


def cmd_dump(args):
    p = project()
    if not p.transcripts:
        sys.exit(f'{p.config}: transcripts are off ([paths] transcripts = "")')
    path = args.session
    if not os.path.isfile(path):
        m = [f for d in p.dirs for f in glob.glob(os.path.join(d, glob.escape(path.lower()) + '*.jsonl'))]
        if len(m) != 1:
            sys.exit(f'{path!r} is no file and matches ' + (', '.join(sorted(
                os.path.basename(f)[:-6] for f in m)) if m else 'no session of this project'))
        path = m[0]
    print(dump.convert(path, os.path.splitext(os.path.basename(path))[0], args.label, p.transcripts))
    return 0


def cmd_hook(args):
    """For Claude Code's SessionStart, SessionEnd and PreCompact hooks, with the hook input JSON on stdin: dumps the
    transcript before a compaction and on /clear, and rebuilds the index at every session start and end. Does
    nothing outside a project. Prints nothing on stdout, since a SessionStart hook's output goes into the session's
    context; errors go to stderr with exit status 1, which Claude Code shows to the user and then goes on."""
    h = json.load(sys.stdin)
    event = h.get('hook_event_name')
    # The directory the session was started in; the hook's own cwd follows the shell's cd.
    start = os.environ.get('CLAUDE_PROJECT_DIR') or first_cwd(h.get('transcript_path') or '') or h.get('cwd')
    p = Project.find(start) if start else None
    if not p:
        return 0
    errors = []
    if p.transcripts and (event == 'PreCompact' or event == 'SessionEnd' and h.get('reason') == 'clear'):
        label = 'clear' if event == 'SessionEnd' else f"compact-{h.get('trigger', '')}"
        try:
            dump.convert(h['transcript_path'], h.get('session_id', 'unknown'), label, p.transcripts)
        except Exception as e:
            errors.append(f'dump: {e!r}')
    if event in ('SessionStart', 'SessionEnd'):
        try:
            index.write(p)
        except Exception as e:
            errors.append(f'index: {e!r}')
    for e in errors:
        print(f'claude-sessions hook ({event}, {p.root}): {e}', file=sys.stderr)
    return 1 if errors else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='claude-sessions', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True, metavar='command')
    a = sub.add_parser('init', help=f'start a project: {CONFIG}, notes/, the index, .gitignore lines (here, or in DIR)',
                       description=cmd_init.__doc__)
    a.add_argument('dir', nargs='?', default='.', metavar='DIR')
    a.set_defaults(run=cmd_init)
    a = sub.add_parser('index', help='rebuild the session index')
    a.add_argument('--stdout', action='store_true', help='print the index instead of writing it')
    a.set_defaults(run=cmd_index)
    a = sub.add_parser('delete', help='delete sessions by group or ID', description=delete.__doc__,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument('ids', nargs='*', help='session IDs, or unique prefixes of them')
    a.add_argument('-g', '--group', action='append', default=[],
                   help=f'a group key of {CONFIG}, or empty (repeatable)')
    a.add_argument('-n', '--dry-run', action='store_true', help='only list what would be deleted')
    a.add_argument('-y', '--yes', action='store_true', help="delete without asking")
    a.set_defaults(run=cmd_delete)
    a = sub.add_parser('dump', help='dump a session transcript to Markdown', description=dump.__doc__,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument('session', help='a transcript .jsonl, or a session ID or unique prefix of one')
    a.add_argument('label', nargs='?', default='manual', help='end of the dump\'s file name (default: manual)')
    a.set_defaults(run=cmd_dump)
    a = sub.add_parser('hook', help="run from Claude Code's hooks (hook input on stdin)", description=cmd_hook.__doc__)
    a.set_defaults(run=cmd_hook)
    args = ap.parse_args(argv)
    try:
        return args.run(args)
    except ConfigError as e:
        print(f'claude-sessions: {e}', file=sys.stderr)
        return 1
