"""The v1 canonical-contract CLI (mounted as ``omni-fetcher v1 ...``).

One command, ``fetch``: wires ``OmniFetcher(builtin_registry())``, fetches a
URI, and renders the canonical ``Result`` either as a rich tree (default) or
as contract JSON (``--json``).

Security posture: credentials are accepted as environment-variable *names*
only (``--token-env API_TOKEN``), never as raw values -- nothing secret ever
appears in ``argv``, shell history, or output; nothing is persisted.

Exit codes: ``0`` for ``Success`` and ``Partial`` (gaps render to stderr),
``1`` for a typed ``Error`` result, ``2`` for usage errors (bad flags,
missing environment variables, malformed ``--zoom``).
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import typer
from rich.console import Console
from rich.tree import Tree

from omni_fetcher.v1.atoms import Atom, AtomKind
from omni_fetcher.v1.auth import (
    ApiKeyAuth,
    AuthCredential,
    AwsAuth,
    BasicAuth,
    BearerAuth,
    OAuth2Auth,
)
from omni_fetcher.v1.builtin import builtin_registry
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.orchestrator import OmniFetcher
from omni_fetcher.v1.result import Error, Partial, Result
from omni_fetcher.v1.zoom import DepthLevel, ZoomSpec

v1_app = typer.Typer(
    help="v1 canonical-contract commands (one typed Result shape per fetch).",
    no_args_is_help=True,
)

_AUTH_TYPES = ("bearer", "basic", "api-key", "oauth2", "aws")


@v1_app.callback()
def _v1_group() -> None:
    """Keep ``fetch`` a named subcommand (typer collapses one-command apps)."""


def _env_value(name: Optional[str], flag: str) -> str:
    """Read a required environment variable named by a CLI flag."""
    if not name:
        raise typer.BadParameter(f"{flag} is required for this --auth-type")
    value = os.environ.get(name)
    if not value:
        raise typer.BadParameter(f"environment variable {name!r} ({flag}) is not set")
    return value


def _credential_from(
    auth_type: Optional[str],
    *,
    token_env: Optional[str],
    username_env: Optional[str],
    password_env: Optional[str],
    api_key_env: Optional[str],
    header: str,
    access_token_env: Optional[str],
    access_key_id_env: Optional[str],
    secret_access_key_env: Optional[str],
) -> Optional[AuthCredential]:
    """Build the per-call credential from environment-variable names."""
    if auth_type is None:
        return None
    if auth_type == "bearer":
        return BearerAuth(token=_env_value(token_env, "--token-env"))
    if auth_type == "basic":
        return BasicAuth(
            username=_env_value(username_env, "--username-env"),
            password=_env_value(password_env, "--password-env"),
        )
    if auth_type == "api-key":
        return ApiKeyAuth(api_key=_env_value(api_key_env, "--api-key-env"), header=header)
    if auth_type == "oauth2":
        return OAuth2Auth(access_token=_env_value(access_token_env, "--access-token-env"))
    if auth_type == "aws":
        return AwsAuth(
            access_key_id=_env_value(access_key_id_env, "--access-key-id-env"),
            secret_access_key=_env_value(secret_access_key_env, "--secret-access-key-env"),
        )
    raise typer.BadParameter(
        f"unknown --auth-type {auth_type!r}; expected one of {', '.join(_AUTH_TYPES)}"
    )


def _zoom_from(zoom: Optional[str]) -> Optional[ZoomSpec]:
    """Parse ``--zoom text=paragraph,image=whole`` into a ``ZoomSpec``."""
    if not zoom:
        return None
    per_type: dict[AtomKind, DepthLevel] = {}
    for pair in zoom.split(","):
        key, _, value = pair.partition("=")
        try:
            per_type[AtomKind(key.strip())] = DepthLevel(value.strip())
        except ValueError as exc:
            raise typer.BadParameter(
                f"bad --zoom entry {pair!r}: expected <atom-kind>=<depth-level> "
                f"(kinds: {', '.join(k.value for k in AtomKind)}; levels: "
                f"{', '.join(level.value for level in DepthLevel)})"
            ) from exc
    return ZoomSpec(per_type=per_type)


def _atom_label(atom: Atom) -> str:
    """One-line summary of an atom for the tree renderer."""
    content = getattr(atom, "content", None)
    if isinstance(content, str):
        preview = content.strip().replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:57] + "..."
        return f"[{atom.kind.value}] {preview!r}"
    reference = getattr(atom, "uri", None)
    return f"[{atom.kind.value}] {reference or '<binary>'}"


def _node_tree(node: CompositionNode, label: Optional[str] = None) -> Tree:
    """Render a composition node (recursively) as a rich tree."""
    metadata = node.metadata
    title = label or f"[bold]{metadata.kind or 'node'}[/bold]"
    if metadata.id:
        title += f"  id={metadata.id}"
    tree = Tree(title)
    core = []
    if metadata.author:
        core.append(f"author={metadata.author}")
    if metadata.created:
        core.append(f"created={metadata.created.isoformat()}")
    if metadata.source_url:
        core.append(f"url={metadata.source_url}")
    if metadata.tags:
        core.append(f"tags={','.join(metadata.tags)}")
    if core:
        tree.add("[dim]" + "  ".join(core) + "[/dim]")
    for namespace, fields in (metadata.source_extra or {}).items():
        tree.add(f"[dim]source_extra[{namespace}]: {len(fields)} fields[/dim]")
    for child in node.children:
        if isinstance(child, CompositionNode):
            tree.add(_node_tree(child))
        else:
            tree.add(_atom_label(child))
    return tree


@v1_app.command("fetch")
def fetch_command(
    uri: str = typer.Argument(..., help="The source URI to fetch."),
    auth_type: Optional[str] = typer.Option(
        None, "--auth-type", help=f"Credential shape: {', '.join(_AUTH_TYPES)}."
    ),
    token_env: Optional[str] = typer.Option(
        None, "--token-env", help="Env var holding the bearer token."
    ),
    username_env: Optional[str] = typer.Option(
        None, "--username-env", help="Env var holding the basic-auth username."
    ),
    password_env: Optional[str] = typer.Option(
        None, "--password-env", help="Env var holding the basic-auth password."
    ),
    api_key_env: Optional[str] = typer.Option(
        None, "--api-key-env", help="Env var holding the API key."
    ),
    header: str = typer.Option(
        "X-API-Key", "--header", help="Header name for --auth-type api-key."
    ),
    access_token_env: Optional[str] = typer.Option(
        None, "--access-token-env", help="Env var holding the OAuth2 access token."
    ),
    access_key_id_env: Optional[str] = typer.Option(
        None, "--access-key-id-env", help="Env var holding the AWS access key id."
    ),
    secret_access_key_env: Optional[str] = typer.Option(
        None,
        "--secret-access-key-env",
        help="Env var holding the AWS secret access key.",
    ),
    zoom: Optional[str] = typer.Option(
        None, "--zoom", help="Per-atom-type depth, e.g. text=paragraph,image=whole."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the contract JSON instead of a tree."),
    tag: list[str] = typer.Option(
        [], "--tag", help="Advisory tag merged into node metadata (repeatable)."
    ),
) -> None:
    """
    Fetch a URI through the v1 canonical contract

    Routes via the built-in registry, fetches with the (optional) per-call
    credential and zoom spec, and renders the typed ``Result``. Exit code 0
    for Success/Partial (gaps go to stderr), 1 for a typed Error.

    Parameters
    ----------
        uri:
            The source URI to fetch (any built-in connector's shape).
        auth_type:
            Optional credential shape; the matching ``*-env`` flags name the
            environment variables holding the secret values.
        zoom:
            Optional per-atom-type semantic depth specification.
        as_json:
            Emit ``Result`` JSON instead of the rich tree.
        tag:
            Advisory tags merged into each returned node's metadata.

    Return
    ------
        nothing:
            Output is written to stdout/stderr; the process exit code
            carries the outcome.
    """
    credential = _credential_from(
        auth_type,
        token_env=token_env,
        username_env=username_env,
        password_env=password_env,
        api_key_env=api_key_env,
        header=header,
        access_token_env=access_token_env,
        access_key_id_env=access_key_id_env,
        secret_access_key_env=secret_access_key_env,
    )
    spec = _zoom_from(zoom)

    omni = OmniFetcher(builtin_registry())
    result: Result = asyncio.run(omni.fetch(uri, auth=credential, zoom=spec, tags=tag or None))

    stdout = Console()
    stderr = Console(stderr=True)

    if isinstance(result, Error):
        if as_json:
            stdout.print_json(result.model_dump_json())
        else:
            stderr.print(f"[red]error[/red] {result.kind.value}: {result.message}")
        raise typer.Exit(code=1)

    if as_json:
        stdout.print_json(result.model_dump_json())
    else:
        stdout.print(_node_tree(result.tree, label=f"[bold]{uri}[/bold]"))

    if isinstance(result, Partial):
        for hole in result.gaps:
            stderr.print(f"[yellow]gap[/yellow] {hole.kind.value}: {hole.detail}")
    raise typer.Exit(code=0)
