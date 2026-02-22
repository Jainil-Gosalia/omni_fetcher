"""CLI example for OmniFetcher using Typer."""

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from omni_fetcher import OmniFetcher

app = typer.Typer(help="OmniFetcher CLI")
console = Console()


@app.command()
def fetch(uri: str):
    """Fetch data from a URI."""
    fetcher = OmniFetcher()
    console.print(f"[blue]Fetching:[/blue] {uri}")

    try:
        result = asyncio.run(fetcher.fetch(uri))
        console.print(f"[green]Success![/green] Type: {type(result).__name__}")

        if hasattr(result, "data"):
            import json

            console.print(result.data)
        elif hasattr(result, "content"):
            console.print(result.content[:500])
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")


@app.command()
def list_sources():
    """List all registered sources."""
    fetcher = OmniFetcher()
    sources = fetcher.list_sources()

    table = Table(title="Registered Sources")
    table.add_column("Name", style="cyan")
    table.add_column("Priority", style="magenta")

    for source_name in sources:
        info = fetcher.get_source_info(source_name)
        table.add_row(source_name, str(info.priority))

    console.print(table)


if __name__ == "__main__":
    app()
