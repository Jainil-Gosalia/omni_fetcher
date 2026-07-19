# Verification scenarios

A skill has no code to unit-test; it is verified by exercising it on
representative tasks and checking the behaviour. Run these by hand (or drive them
in a harness) after changing the procedure, so a regression in the instructions
is catchable.

Each scenario is a prompt to give the agent (with the `omni-fetcher` and
`omni-fetcher-assemble-context` skills installed and omni-fetcher available), and
what the assembled output must show.

1. **Explicit set.**
   *Prompt:* "Assemble context from `report.pdf`, `sqlite:///app.db?query=SELECT%20*%20FROM%20users%20LIMIT%205`, and `https://example.com`."
   *Expect:* all three appear, each headed by its `source_url`, in the order
   given; the SQL result renders as a table.

2. **Honest failure.**
   *Prompt:* same set plus one URI that 404s (e.g. a missing file).
   *Expect:* the failing source appears under "Could not fetch" with its
   `kind` (`not_found`), and a "gathered N of M" count is stated. It is never
   silently omitted.

3. **Stream handling.**
   *Prompt:* include a `tail://<a real log file>?from=start`.
   *Expect:* the agent `sample`s it (bounded, small `max_items`) and notes the
   window and stop reason — it does not `fetch` it and hang.

4. **Budgeting.**
   *Prompt:* include an oversized document.
   *Expect:* the agent fetched at a coarser `zoom` (or capped a query) and said
   so — no silent truncation, and it does not dump the whole thing.

5. **Compound expansion.**
   *Prompt:* "Assemble the epic PROJ-42 and its linked issues" against a fixture
   with typed links.
   *Expect:* the anchor plus its *direct* linked issues, one level, then stop —
   no recursion into the links' links.

6. **Channel parity.**
   Run scenario 1 once with the MCP server connected and once over the CLI.
   *Expect:* an equivalent assembled bundle either way, and the agent names which
   channel it used.

The non-negotiable across all of them: no requested source is missing from the
report without an explicit reason. If the bundle ever hides a failure, that is a
defect regardless of how good the rest looks.
