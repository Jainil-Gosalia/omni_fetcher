"""Console entry point for the OmniFetcher MCP server (``omni-fetcher-mcp``).

Builds the stdio server from the environment and serves it. The only knob is
``--max-bytes``, the cap the size guard applies to a serialised tool result.
"""

from __future__ import annotations

import argparse

from omni_fetcher.mcp.server import DEFAULT_MAX_BYTES, run


def main() -> None:
    """Parse arguments and run the stdio MCP server."""
    parser = argparse.ArgumentParser(
        prog="omni-fetcher-mcp",
        description="Serve OmniFetcher as an MCP server over stdio.",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=(
            "Cap on a serialised tool result before the size guard degrades it "
            f"to a partial with a gap (default: {DEFAULT_MAX_BYTES})."
        ),
    )
    args = parser.parse_args()
    run(max_bytes=args.max_bytes)


if __name__ == "__main__":
    main()
