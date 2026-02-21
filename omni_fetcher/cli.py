"""CLI module for OmniFetcher."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint
from rich.theme import Theme

from omni_fetcher import OmniFetcher, SourceInfo
from omni_fetcher.cache import FileCacheBackend, MemoryCacheBackend
from omni_fetcher.auth import AuthConfig
from omni_fetcher.core.exceptions import OmniFetcherError, SourceNotFoundError, FetchError

custom_theme = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "error": "bold red",
        "success": "green",
    }
)

app = typer.Typer(
    help="OmniFetcher - Universal data fetcher with support for multiple sources",
    add_completion=False,
)
cache_app = typer.Typer(help="Manage cache")

app.add_typer(cache_app, name="cache")

console = Console(theme=custom_theme)

CACHE_DIR = ".omni_fetcher_cache"
_global_fetcher: Optional[OmniFetcher] = None
_global_cache: Optional[FileCacheBackend] = None


def get_fetcher() -> OmniFetcher:
    """Get or create the global fetcher instance."""
    global _global_fetcher
    if _global_fetcher is None:
        _global_fetcher = OmniFetcher()
    return _global_fetcher


def get_cache() -> FileCacheBackend:
    """Get or create the global cache instance."""
    global _global_cache
    if _global_cache is None:
        _global_cache = FileCacheBackend(cache_dir=CACHE_DIR)
    return _global_cache


def handle_error(e: Exception) -> None:
    """Handle and display errors nicely."""
    if isinstance(e, SourceNotFoundError):
        console.print(f"[error]Error:[/error] No handler found for '{e.uri}'")
        console.print("[info]Hint:[/info] Check if the URL/URI pattern is supported.")
    elif isinstance(e, FetchError):
        console.print(f"[error]Error:[/error] Failed to fetch: {e.reason}")
    elif isinstance(e, OmniFetcherError):
        console.print(f"[error]Error:[/error] {str(e)}")
    else:
        console.print(f"[error]Error:[/error] {str(e)}")
    raise typer.Exit(code=1)


def format_output(data: any, format: str) -> str:
    """Format data according to the specified format."""
    if format == "json":
        return json.dumps(data, indent=2, default=str)
    elif format == "yaml":
        return yaml.dump(data, default_flow_style=False, sort_keys=False)
    elif format == "text":
        if isinstance(data, dict):
            return json.dumps(data, indent=2, default=str)
        elif isinstance(data, list):
            return "\n".join(str(item) for item in data)
        return str(data)
    elif format == "table":
        if isinstance(data, dict):
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Key", style="cyan")
            table.add_column("Value", style="green")
            for key, value in data.items():
                table.add_row(str(key), str(value))
            return table
        elif isinstance(data, list):
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Index", style="cyan")
            table.add_column("Value", style="green")
            for i, item in enumerate(data):
                table.add_row(str(i), str(item))
            return table
        return str(data)
    return str(data)


@app.command()
def fetch(
    url: str = typer.Argument(..., help="URL or file path to fetch"),
    auth_type: Optional[str] = typer.Option(
        None, "--auth-type", "-a", help="Auth type: bearer, api_key, basic, oauth2"
    ),
    auth_token: Optional[str] = typer.Option(
        None, "--auth-token", "-t", help="Auth token or API key"
    ),
    auth_header: Optional[str] = typer.Option(
        None, "--auth-header", help="API key header name (default: X-API-Key)"
    ),
    auth_username: Optional[str] = typer.Option(
        None, "--auth-user", help="Username for basic auth"
    ),
    auth_password: Optional[str] = typer.Option(
        None, "--auth-pass", help="Password for basic auth"
    ),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
    format: str = typer.Option(
        "json",
        "-f",
        "--format",
        help="Output format: json, yaml, text, table",
        case_sensitive=False,
    ),
    timeout: float = typer.Option(30.0, "--timeout", help="Request timeout in seconds"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Disable caching"),
) -> None:
    """Fetch data from a URL or file.

    Examples:
        omni-fetcher fetch https://api.example.com/data
        omni-fetcher fetch https://api.example.com/data -a bearer -t mytoken
        omni-fetcher fetch /path/to/file.txt -f yaml -o output.yaml
    """
    try:
        auth_config = None
        if auth_type:
            auth_config = AuthConfig(type=auth_type)

            if auth_type == "bearer":
                auth_config.token = auth_token
            elif auth_type == "api_key":
                auth_config.api_key = auth_token
                if auth_header:
                    auth_config.api_key_header = auth_header
            elif auth_type == "basic":
                auth_config.username = auth_username
                auth_config.password = auth_password
            elif auth_type == "oauth2":
                auth_config.oauth2_client_id = auth_username
                auth_config.oauth2_client_secret = auth_password

        fetcher = get_fetcher()

        async def do_fetch():
            cache = None if no_cache else get_cache()

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task(f"Fetching from {url}...", total=None)

                try:
                    if auth_config:
                        result = await fetcher.fetch(
                            url,
                            auth=auth_config.model_dump(),
                            timeout=timeout,
                            cache=cache,
                        )
                    else:
                        result = await fetcher.fetch(
                            url,
                            timeout=timeout,
                            cache=cache,
                        )

                    progress.update(task, completed=True)

                    data = result.data if hasattr(result, "data") else result

                    formatted = format_output(data, format)

                    if output:
                        Path(output).write_text(formatted)
                        console.print(f"[success]Output written to {output}[/success]")
                    else:
                        if format == "table":
                            console.print(formatted)
                        else:
                            console.print(formatted)

                except Exception as e:
                    progress.stop()
                    raise

        asyncio.run(do_fetch())

    except OmniFetcherError as e:
        handle_error(e)
    except Exception as e:
        handle_error(e)


@app.command("sources")
def sources_cmd(
    list_sources: bool = typer.Option(False, "--list", "-l", help="List all registered sources"),
    info_name: Optional[str] = typer.Option(
        None, "--info", "-i", help="Show detailed info for a specific source"
    ),
) -> None:
    """List and manage sources.

    Examples:
        omni-fetcher sources --list
        omni-fetcher sources --info http
    """
    fetcher = get_fetcher()

    if info_name:
        source_info = fetcher.get_source_info(info_name)
        if not source_info:
            console.print(f"[error]Source '{info_name}' not found[/error]")
            raise typer.Exit(code=1)

        _display_source_info(source_info)
    else:
        sources = fetcher.list_sources()
        if not sources:
            console.print("[warning]No sources registered[/warning]")
            return

        table = Table(title="Registered Sources", show_header=True, header_style="bold magenta")
        table.add_column("Name", style="cyan", width=20)
        table.add_column("Priority", style="yellow", width=10)
        table.add_column("Description", style="green")

        for name in sources:
            info = fetcher.get_source_info(name)
            if info:
                table.add_row(name, str(info.priority), info.description or "-")

        console.print(table)
        console.print(f"\n[info]Total: {len(sources)} source(s)[/info]")
        console.print("\nUse [cyan]--info <name>[/cyan] for detailed info")


def _display_source_info(info: SourceInfo) -> None:
    """Display detailed information about a source."""
    table = Table(title=f"Source: {info.name}", show_header=False, box=None)
    table.add_column("Property", style="cyan", width=20)
    table.add_column("Value", style="green")

    table.add_row("Name", info.name)
    table.add_row("Priority", str(info.priority))
    table.add_row("Description", info.description or "-")
    table.add_row("URI Patterns", ", ".join(info.uri_patterns) if info.uri_patterns else "-")
    table.add_row("MIME Types", ", ".join(info.mime_types) if info.mime_types else "-")

    if info.auth_config:
        auth_type = info.auth_config.get("type", "unknown")
        table.add_row("Auth Type", auth_type)

    console.print(table)


@cache_app.command("clear")
def cache_clear(
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Clear the cache.

    Example:
        omni-fetcher cache clear --yes
    """
    if not confirm:
        response = typer.confirm("Are you sure you want to clear the cache?", default=False)
        if not response:
            console.print("[info]Cancelled[/info]")
            return

    async def do_clear():
        cache = get_cache()
        await cache.clear()

    asyncio.run(do_clear())
    console.print("[success]Cache cleared successfully[/success]")


@cache_app.command("stats")
def cache_stats() -> None:
    """Show cache statistics.

    Example:
        omni-fetcher cache stats
    """
    cache_dir = Path(CACHE_DIR)

    if not cache_dir.exists():
        console.print("[warning]No cache found[/warning]")
        return

    cache_files = list(cache_dir.glob("*.json"))
    total_size = sum(f.stat().st_size for f in cache_files)

    table = Table(title="Cache Statistics", show_header=False, box=None)
    table.add_column("Property", style="cyan", width=20)
    table.add_column("Value", style="green")

    table.add_row("Cache Directory", str(cache_dir.absolute()))
    table.add_row("Cached Items", str(len(cache_files)))
    table.add_row("Total Size", _format_size(total_size))

    console.print(table)


def _format_size(size: int) -> str:
    """Format size in bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


@app.command()
def version() -> None:
    """Show version information.

    Example:
        omni-fetcher version
    """
    from omni_fetcher import __version__

    panel = Panel(
        f"[bold cyan]OmniFetcher[/bold cyan] v{__version__}\n"
        "[dim]Universal data fetcher with Pydantic schemas[/dim]",
        title="Version Info",
        border_style="magenta",
    )
    console.print(panel)


def main() -> None:
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
