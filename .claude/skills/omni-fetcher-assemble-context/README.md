# OmniFetcher Assemble-Context Skill

A **task** skill: it teaches an agent to pull a set of related sources —
issues, PRs, wiki pages, documents, a database query, a log tail — into one
provenance-tagged context bundle through [omni-fetcher](https://pypi.org/project/omni-fetcher/),
and to be honest about anything it could not fetch.

It is the first task skill in the family. Where the **`omni-fetcher`** skill
teaches an agent to *use and discover* OmniFetcher, this one teaches it to
*assemble* with OmniFetcher. It adds no library code — it orchestrates the
`fetch` / `sample` tools (over the MCP server or the CLI) and adds only the
judgment that a model has to bring: resolving the source set, budgeting for the
context window, and reporting gaps honestly.

## Depends on the `omni-fetcher` skill

Install the `omni-fetcher` skill too — this skill defers all contract and
tool detail to it (the `Result` states, atoms, zoom, how to discover whether a
source is bounded or streaming). Installed together, they compose: the knowledge
skill handles "how do I fetch this?", this one handles "gather these into
context."

## Install

**Claude Code:**

```bash
git clone https://github.com/Jainil-Gosalia/omni_fetcher /tmp/omni_fetcher
mkdir -p ~/.claude/skills
cp -r /tmp/omni_fetcher/.claude/skills/omni-fetcher                  ~/.claude/skills/
cp -r /tmp/omni_fetcher/.claude/skills/omni-fetcher-assemble-context ~/.claude/skills/
```

Restart Claude Code, or run `/skills` to confirm both are listed. This skill
activates when a task involves gathering several related sources into context.

**Other agentic systems:** point the agent at `SKILL.md` — plain Markdown, no
tool-specific syntax. It assumes the agent can call the omni-fetcher MCP tools
or run the `omni-fetcher` CLI in its environment.

The skill orchestrates omni-fetcher; it does not install it. The target
environment still needs `pip install omni-fetcher` (plus any extra, or
`omni-fetcher[mcp]` for the server).

## Layout

```
omni-fetcher-assemble-context/
└── SKILL.md    # the assemble procedure, output shape, honesty rules
```

## Maintaining it

This skill composes the stable `fetch` / `sample` surface and defers everything
else to the `omni-fetcher` skill, so it rarely needs edits: update it only if the
assemble *procedure* changes (how the bundle is shaped, how gaps are reported),
not when a connector is added — discovery handles that.
