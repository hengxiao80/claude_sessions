# claude-sessions

Keeps track of a project's Claude Code sessions:

- **index**: a Markdown file (`notes/sessions.md` by default) of the project's sessions, sorted into groups you
  define, with dates, names, prompt counts, session IDs, transcript dumps and a table of hand-off notes; rebuilt by
  the hooks at every session start and end;
- **dump**: a session's transcript as Markdown, written by the hooks before every compaction and on `/clear`;
- **delete**: the sessions of a group, or given by ID, with everything Claude Code keeps for them.

Standard library only (Python 3.11 or later, for `tomllib`). It reads Claude Code's own files under `~/.claude`, so
a Claude Code update that changes them can break it.

## Install

```
uv tool install --editable /gpfs/wolf2/arm/cli120/proj-shared/hengxiao80/claude_sessions
```

This puts `claude-sessions` in `~/.local/bin`, in an environment of its own with the Python `uv` picks (here
`UV_PYTHON`, the mambaforge `my311` environment; if that one goes, reinstall with `--reinstall`). Editable: changes
to the code here apply at once.

Then the hooks, once, in `~/.claude/settings.json` (merged into any `"hooks"` already there):

```json
"hooks": {
  "SessionStart": [{"hooks": [{"type": "command", "timeout": 60,
                               "command": "$HOME/.local/bin/claude-sessions hook"}]}],
  "SessionEnd":   [{"hooks": [{"type": "command", "timeout": 120,
                               "command": "$HOME/.local/bin/claude-sessions hook"}]}],
  "PreCompact":   [{"hooks": [{"type": "command", "timeout": 120,
                               "command": "$HOME/.local/bin/claude-sessions hook"}]}]
}
```

They run in every project, and do nothing in one without a `.claude-sessions.toml`. In a project, `hook` dumps the
transcript on `PreCompact` and on `SessionEnd` after `/clear`, and rebuilds the index on `SessionStart` and
`SessionEnd`. It prints nothing (a `SessionStart` hook's output would go into the session's context); an error goes
to stderr with exit status 1, which Claude Code shows and then carries on.

## A project

```
cd <project root>
claude-sessions init
```

writes a skeleton `.claude-sessions.toml`, creates `notes/`, `notes/handoffs/` and `notes/transcripts/` where
missing, writes the index `notes/sessions.md`, and adds `notes/sessions.md` and `notes/transcripts/` to `.gitignore`
(both generated; the dumps hold whole transcripts, tool output included). It does nothing at all outside a git
work tree (run `git init` first) or where a `.claude-sessions.toml` exists already. The file is the opt-in: delete
it, and the hooks leave the project alone. Commit it.

The project root is the directory of the nearest `.claude-sessions.toml` at or above the current one. The project's
sessions are those started at the root or below it (Claude Code keeps them in `~/.claude/projects/<the start
directory with every non-alphanumeric character as '-'>/`), unless a nearer `.claude-sessions.toml` claims them.
Sessions started before `init` are included. `CLAUDE_CONFIG_DIR`, if set, takes the place of `~/.claude`.

### `[paths]`

Relative to the root; the defaults:

```toml
[paths]
index = "notes/sessions.md"
transcripts = "notes/transcripts"   # "" turns dumps off, and the index's dump column
handoffs = "notes/handoffs"         # "" leaves out the hand-off table
```

### Groups

```toml
[[group]]
key = "expansion"                       # unique; not "empty" or "ungrouped"
title = "Observation expansion and 1-hour runs"
names = ["Adding more obs"]             # optional
ids = []                                # optional
```

Groups exist only in this file. A session goes to the first of these that claims it:

1. a group whose `ids` holds the first 8 characters of its session ID (of any of them, for a continued session);
2. a group whose key starts the name you gave the session: `/rename expansion: part 11` files it under
   `expansion` (case ignored);
3. the first group, in file order, with a `names` entry found in the name you gave the session or in Claude Code's
   own title (case, spaces and punctuation ignored). The entries are search patterns, not a list of sessions:
   "Adding more obs" catches every "Adding more obs part N".

Sessions nobody claims are listed under "Ungrouped"; sessions without a single prompt are only counted. Groups
appear in the index in file order.

A name like `<word>: ...` whose word is no group key creates no group. The session is filed by the other rules, and
the index's **Checks** section names it, as it names every `ids` or `names` entry that claims no session (stale, or
covered by an earlier entry). `claude-sessions index` run by hand prints the checks too.

### Continued sessions

When Claude Code hands a session to its background daemon (seen after a `/resume <ID>`, and after an `/exit` with a
background task still running), it may go on under a new session ID: the new transcript starts with a copy of the old
one, and the old one gets a `continued-in` record naming the new ID. `claude -r` lists both. The index shows such a
chain as one row under the newest ID, with the older ones after it (`continues bc7793cf`): the newest session's name,
the chain's dates and dumps, and its prompts counted once. Resume it by the newest ID; the older one stops where the
hand-off was.

### Hand-off notes

Notes one session leaves for the next, in the `handoffs` directory as `YYYY-MM-DD_<anything>.md`. The index matches
them to sessions by two lines:

- the title, naming the session that picks the note up: `# Hand-off: "<session name>: <topic>"` (the part before the
  first `:` inside the quotes is matched against the names you gave sessions);
- a line naming the session that wrote it: `Written <YYYY-MM-DD> at the end of session "<name>"` (or `during
  session`).

Without such a title, the session that picks the note up is one whose first prompt names the note's path
(`notes/handoffs/<file>`).

## Commands

All of them work on the project at or above the current directory.

| Command | Does |
|---|---|
| `claude-sessions init [DIR]` | start a project: the skeleton `.claude-sessions.toml`, `notes/` with the index, the `.gitignore` lines |
| `claude-sessions index [--stdout]` | rebuild the index (or print it), with the counts and the checks |
| `claude-sessions delete [-n] [-y] [-g KEY]... [ID]...` | delete the sessions of groups (`empty`: those without prompts) or given by ID prefix; lists them and asks first, `-n` only lists |
| `claude-sessions dump SESSION [LABEL]` | dump a transcript (a `.jsonl`, or a session ID prefix) to `<transcripts>/<UTC time>_<ID[:8]>_<LABEL>.md` |
| `claude-sessions hook` | what the hooks run |

`delete` removes, per session, `~/.claude/projects/<dir>/<ID>.jsonl` (what `claude -r` lists),
`~/.claude/projects/<dir>/<ID>/` (subagent transcripts, tool results), `~/.claude/file-history/<ID>/` (`/rewind`
checkpoints) and `~/.claude/session-env/<ID>/`; a background session goes through `claude rm` first. It leaves the
session's prompts in `~/.claude/history.jsonl` and its transcript dumps, skips sessions still running, deletes
ungrouped sessions only by ID, and rebuilds the index afterwards. A group takes whole rows of the index, a continued
session with its continuation; an ID takes only the session it names, and the listing names the rest of its row.
