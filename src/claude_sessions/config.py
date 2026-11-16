"""A project's .claude-sessions.toml, and where Claude Code keeps the project's sessions.

The project root is the nearest directory at or above the current one that holds a .claude-sessions.toml; a
directory with none above it is no project, and the hooks do nothing there. Paths in the file are relative to the
root. Claude Code keeps the sessions started in a directory under ~/.claude/projects/<that directory with every
non-alphanumeric character as '-'>/ (or under $CLAUDE_CONFIG_DIR/projects/); a project's sessions are those started
at its root or below it, unless a nearer .claude-sessions.toml claims them.
"""
import glob
import json
import os
import re
import tomllib

CONFIG = '.claude-sessions.toml'
CLAUDE = os.path.expanduser(os.environ.get('CLAUDE_CONFIG_DIR') or '~/.claude')
PATHS = {'index': 'notes/sessions.md', 'transcripts': 'notes/transcripts', 'handoffs': 'notes/handoffs'}
RESERVED = ('empty', 'ungrouped')       # names `delete --group` gives the sessions outside the groups
SESSION_FILE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl')

SKELETON = '''\
# Claude Code sessions of this project, for claude-sessions (rules and
# conventions in its README). `claude-sessions index`, run by the hooks at
# every session start and end, writes the index file; delete this file to
# turn it all off here.
#
# A session goes to the first of these that claims it:
#   1. a group whose `ids` holds the first 8 characters of its session ID;
#   2. a group whose key starts the name you gave it ("setup: part 2" goes
#      to `setup`); groups exist only here, a name never creates one;
#   3. the first group, in file order, with a `names` entry found in the
#      name you gave it or in Claude Code's own title (case, spaces and
#      punctuation ignored).
# Sessions nobody claims are listed under "Ungrouped"; sessions without a
# single prompt are only counted. Groups appear in the index in this order.

[paths]           # relative to this file; "" turns transcripts or handoffs off
index = "notes/sessions.md"
transcripts = "notes/transcripts"
handoffs = "notes/handoffs"

# [[group]]
# key = "setup"
# title = "Project setup"
# names = ["setup"]

[[group]]
key = "junk"
title = "Redundant or stray"
# Stray sessions go here by ID, then: claude-sessions delete --group junk
ids = []
'''


class ConfigError(Exception):
    pass


def tilde(path):
    """`path` with the home directory as ~."""
    home = os.path.expanduser('~')
    return '~' + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def find_root(start):
    """The nearest directory at or above `start` that holds a .claude-sessions.toml (symlinks resolved), or None."""
    d = os.path.realpath(start)
    while not os.path.isfile(os.path.join(d, CONFIG)):
        if d == os.path.dirname(d):
            return None
        d = os.path.dirname(d)
    return d


def sanitize(path):
    """Claude Code's directory name for the sessions started in `path`."""
    return re.sub(r'[^A-Za-z0-9]', '-', path)


def transcripts(d, prefix=''):
    """The session transcripts in Claude Code's transcript directory `d`, of the sessions whose ID starts with
    `prefix`: the files <session ID>.jsonl. Other .jsonl files there are no sessions of their own, such as the
    <ID>.orphaned-<...>.jsonl stub (a copy of a session's title records) Claude Code can leave next to a transcript."""
    return [f for f in glob.glob(os.path.join(d, glob.escape(prefix) + '*.jsonl'))
            if SESSION_FILE.fullmatch(os.path.basename(f))]


def first_cwd(path):
    """The directory the session of transcript `path` was started in: the `cwd` of its first record that has one
    (later records follow the shell's cd)."""
    try:
        with open(path, errors='replace') as f:
            for line in f:
                if '"cwd":' in line:
                    return json.loads(line).get('cwd')
    except (OSError, ValueError):
        pass
    return None


def session_dirs(root):
    """Claude Code's transcript directories for the project at `root`: the one of sessions started at the root,
    then those of sessions started below it that no nearer .claude-sessions.toml claims."""
    name = sanitize(root)
    dirs = []
    for d in sorted(glob.glob(os.path.join(CLAUDE, 'projects', name + '*'))):
        n = os.path.basename(d)
        if n == name:
            dirs.insert(0, d)
        elif n.startswith(name + '-'):   # also /x/root_other; the start directory tells
            cwd = next(filter(None, map(first_cwd, transcripts(d))), None)
            if cwd and find_root(cwd) == root:
                dirs.append(d)
    return dirs


class Project:
    """The project whose .claude-sessions.toml is nearest at or above a directory (Project.find)."""

    def __init__(self, root):
        self.root = root
        self.config = os.path.join(root, CONFIG)
        try:
            with open(self.config, 'rb') as f:
                cfg = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f'{self.config}: {e}') from None
        paths = cfg.get('paths', {})
        if set(paths) - set(PATHS):
            raise ConfigError(f"{self.config}: unknown [paths] key(s) {', '.join(sorted(set(paths) - set(PATHS)))}")
        paths = {**PATHS, **paths}
        if not paths['index']:
            raise ConfigError(f'{self.config}: [paths] index is empty')
        self.index, self.transcripts, self.handoffs = (os.path.join(root, paths[k]) if paths[k] else None
                                                       for k in ('index', 'transcripts', 'handoffs'))
        self.groups = cfg.get('group', [])
        keys = [g.get('key') for g in self.groups]
        for g in self.groups:
            if not g.get('key') or not g.get('title'):
                raise ConfigError(f'{self.config}: a [[group]] without key or title: {g}')
            if keys.count(g['key']) > 1 or g['key'].lower() in RESERVED:
                raise ConfigError(f"{self.config}: group key {g['key']!r} is " +
                                  ('reserved' if g['key'].lower() in RESERVED else 'not unique'))
        self.dirs = session_dirs(root)

    @classmethod
    def find(cls, start='.'):
        root = find_root(start)
        return cls(root) if root else None


def add_gitignore(root, entries):
    """Append to root/.gitignore those of `entries` it lacks as lines, under a comment; returns what it did."""
    ignore = os.path.join(root, '.gitignore')
    text = ''
    if os.path.exists(ignore):
        with open(ignore) as f:
            text = f.read()
    add = [x for x in entries if x not in text.splitlines()]
    if not add:
        return f"{ignore} has {', '.join(entries)} already"
    sep = ('' if text.endswith('\n') or not text else '\n') + ('\n' if text.strip() else '')
    with open(ignore, 'a') as f:
        f.write(sep + '# Claude Code session index and transcript dumps (claude-sessions)\n' + '\n'.join(add) + '\n')
    return f"added {', '.join(add)} to {ignore}"
