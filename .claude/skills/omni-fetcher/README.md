# OmniFetcher Agent Skill

Teaches Claude (and any agent that reads skills) how to use
[omni-fetcher](https://pypi.org/project/omni-fetcher/) correctly: the `Result`
contract, per-call auth, `source_extra`, streaming vs. bounded connectors, zoom,
retry, and writing custom connectors.

The skill loads only when it's relevant — an agent that never touches
omni-fetcher never pays for the context.

## Install

**Claude Code — one user, all projects:**

```bash
git clone https://github.com/Jainil-Gosalia/omni_fetcher /tmp/omni_fetcher
mkdir -p ~/.claude/skills
cp -r /tmp/omni_fetcher/.claude/skills/omni-fetcher ~/.claude/skills/
```

**Claude Code — one project, checked into the repo** (the whole team gets it):

```bash
mkdir -p .claude/skills
cp -r /path/to/omni_fetcher/.claude/skills/omni-fetcher .claude/skills/
```

Restart Claude Code, or run `/skills` to confirm `omni-fetcher` is listed. It
then activates on its own when a task involves fetching from a source
omni-fetcher covers.

**Claude.ai / Claude Desktop:** zip this directory and upload it as a skill via
Settings → Capabilities → Skills.

**Other agentic systems:** point the agent at `SKILL.md`. It's plain Markdown
with YAML frontmatter and no tool-specific syntax — a system prompt, a RAG
corpus entry, or a `context7`-style doc source all work. `references/` is
progressive disclosure: load `SKILL.md` first and pull in
`references/connectors.md` only when the task needs the connector table or a
custom connector.

The skill documents the library; it does not install it. The agent still needs
`pip install omni-fetcher` (plus any extra) in the target environment.

## Layout

```
omni-fetcher/
├── SKILL.md                   # contract, rules, walking the tree, zoom/retry/CLI
└── references/
    └── connectors.md          # connector tables, custom connectors, v0.x legacy
```

## Maintaining it

`SKILL.md` is API documentation and drifts like any other. When a connector is
added, a URI shape changes, or the public surface of `omni_fetcher.v1` moves,
update the skill in the same PR. Every code sample in both files has been
executed against the package — keep it that way rather than editing samples by
eye.
