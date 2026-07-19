# OmniFetcher Agent Skill

Teaches Claude (and any agent that reads skills) how to use
[omni-fetcher](https://pypi.org/project/omni-fetcher/) correctly: the `Result`
contract, per-call auth, `source_extra`, bounded vs. streaming sources, zoom,
retry, the MCP server, and writing custom connectors.

It is **discovery-based**: the skill teaches the *stable contract* directly, but
for the *volatile surface* — which connectors exist, which are installed, which
stream, what the MCP server exposes — it teaches the agent the commands to ask
the installed package at runtime, rather than baking in a list that goes stale
and is blind to the actual environment.

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
├── SKILL.md                   # discovery + contract, rules, tree-walking, zoom/retry/CLI/MCP
└── references/
    └── connectors.md          # discovery, custom connectors, v0.x legacy
```

## Maintaining it

The discovery design keeps the maintenance surface small, on purpose. Because
the skill does not enumerate connectors — it teaches the agent to discover them —
**adding a connector or an extra needs no skill change**; the discovery command
surfaces it automatically, correctly, per environment.

Update the skill only when the *stable contract* moves: a new `Result` state or
`ErrorKind`, a change to the atom vocabulary or zoom semantics, a new public
export from `omni_fetcher.v1`, or a change to the MCP tool surface. Every code
sample and discovery command in both files has been executed against the
package — keep it that way rather than editing by eye.
