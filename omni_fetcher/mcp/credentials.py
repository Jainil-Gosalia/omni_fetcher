"""Startup credential resolution for the OmniFetcher MCP server.

Credentials are read *once at boot* from the server's own environment into a
``{source_name: AuthCredential}`` map, and injected per call into the stateless
orchestrator. The model never sees or supplies a token (see the v1.7 MCP PRD,
D3/D4/D5).

The env-var convention is ``OMNI_FETCHER_<SOURCE>_<FIELD>``:

- ``OMNI_FETCHER_GITHUB_TOKEN``              -> github: BearerAuth(token=...)
- ``OMNI_FETCHER_JIRA_USERNAME`` + ``_PASSWORD`` -> jira: BasicAuth(...)
- ``OMNI_FETCHER_S3_ACCESS_KEY_ID`` + ``_SECRET_ACCESS_KEY`` -> s3: AwsAuth(...)

Both source names (``google_drive``) and field names (``access_key_id``) contain
underscores, so the boundary between them is not recoverable by splitting. It is
resolved by matching the *longest known source name* (from the registry) as a
prefix; the remainder is the field. The auth *type* is then inferred from which
fields are present -- the same credential shapes the CLI builds, without the
CLI's explicit ``--auth-type`` flag.

Nothing here reads a ``.env`` file, and the orchestrator's no-ambient-env
contract is untouched: the *server* reads the environment, the orchestrator
never does.
"""

from __future__ import annotations

import logging
import os
from typing import Mapping, Optional

from omni_fetcher.v1.auth import (
    ApiKeyAuth,
    AuthCredential,
    AwsAuth,
    BasicAuth,
    BearerAuth,
    OAuth2Auth,
)

logger = logging.getLogger("omni_fetcher.mcp")

_PREFIX = "OMNI_FETCHER_"

# Credential shapes, most specific first. Each entry is
# (required fields, optional fields, builder). The first shape whose required
# fields are all present wins, so multi-field shapes (aws, basic) are matched
# before single-field ones (bearer) that a subset of their fields could satisfy.
_SHAPES: tuple[tuple[frozenset[str], frozenset[str], str], ...] = (
    (
        frozenset({"access_key_id", "secret_access_key"}),
        frozenset({"session_token", "region"}),
        "aws",
    ),
    (frozenset({"username", "password"}), frozenset(), "basic"),
    (frozenset({"api_key"}), frozenset({"header"}), "api_key"),
    (frozenset({"access_token"}), frozenset(), "oauth2"),
    (frozenset({"token"}), frozenset(), "bearer"),
)


def _build_credential(auth_type: str, fields: Mapping[str, str]) -> AuthCredential:
    """Construct the ``AuthCredential`` for a matched shape."""
    if auth_type == "aws":
        return AwsAuth(
            access_key_id=fields["access_key_id"],
            secret_access_key=fields["secret_access_key"],
            session_token=fields.get("session_token"),
            region=fields.get("region"),
        )
    if auth_type == "basic":
        return BasicAuth(username=fields["username"], password=fields["password"])
    if auth_type == "api_key":
        header = fields.get("header", "X-API-Key")
        return ApiKeyAuth(api_key=fields["api_key"], header=header)
    if auth_type == "oauth2":
        return OAuth2Auth(access_token=fields["access_token"])
    return BearerAuth(token=fields["token"])


def _match_source(var_suffix: str, known: Mapping[str, None]) -> Optional[tuple[str, str]]:
    """Split ``<SOURCE>_<FIELD>`` into ``(source, field)`` using known names.

    ``var_suffix`` is the env-var name with the ``OMNI_FETCHER_`` prefix
    removed, lowercased. The source is the longest known source name that is a
    prefix (on an underscore boundary); the field is the remainder. Returns
    ``None`` when no known source matches -- an unrelated env var is ignored,
    not an error.
    """
    best: Optional[tuple[str, str]] = None
    for source in known:
        marker = source + "_"
        if var_suffix.startswith(marker):
            field = var_suffix[len(marker) :]
            if field and (best is None or len(source) > len(best[0])):
                best = (source, field)
    return best


class CredentialStore:
    """
    Boot-time credential map for the MCP server
    ===============================================
    Holds ``{source_name: AuthCredential}`` resolved from the environment at
    startup and injected per call. A source absent from the map has no
    configured credential -- ``get`` returns ``None`` and the fetch tool
    forwards ``auth=None`` (fine for public sources; an auth-requiring source
    then fails with a typed ``AUTH_FAILED`` the tool enriches with an env-var
    hint).
    ===============================================

    Methods
    -------
        get:
        env_hint:
    """

    __slots__ = ("_by_source",)

    def __init__(self, by_source: Mapping[str, AuthCredential]) -> None:
        self._by_source = dict(by_source)

    def get(self, source: str) -> Optional[AuthCredential]:
        """Return the configured credential for ``source``, or ``None``."""
        return self._by_source.get(source)

    def has(self, source: str) -> bool:
        """Report whether ``source`` has a configured credential."""
        return source in self._by_source

    @staticmethod
    def env_hint(source: str) -> str:
        """A human-readable hint naming the env vars that configure ``source``."""
        upper = source.upper()
        return (
            f"no credential configured for source {source!r}; set "
            f"{_PREFIX}{upper}_TOKEN (bearer), {_PREFIX}{upper}_API_KEY, "
            f"{_PREFIX}{upper}_USERNAME + _PASSWORD (basic), or the "
            f"{_PREFIX}{upper}_ACCESS_KEY_ID + _SECRET_ACCESS_KEY (aws) pair"
        )


def load_credentials(
    known_sources: frozenset[str],
    environ: Optional[Mapping[str, str]] = None,
) -> CredentialStore:
    """
    Resolve ``OMNI_FETCHER_<SOURCE>_<FIELD>`` env vars into a credential store

    Groups matching env vars by source (longest-known-name prefix), infers the
    auth type from the fields present, and builds one ``AuthCredential`` per
    source. A source whose fields match no known shape is skipped with a
    warning (never a boot failure), so a partial or mis-typed configuration
    still yields a working server for every other source.

    Parameters
    ----------
        known_sources:
            The set of source names the registry can route. Used to split the
            env-var name into source and field.
        environ:
            The environment mapping to read (defaults to ``os.environ``).

    Return
    ------
        store:
            The resolved credential store.
    """
    env = os.environ if environ is None else environ
    known = {name: None for name in known_sources}

    # Gather { source: { field: value } } from matching env vars.
    grouped: dict[str, dict[str, str]] = {}
    for name, value in env.items():
        if not name.startswith(_PREFIX):
            continue
        suffix = name[len(_PREFIX) :].lower()
        matched = _match_source(suffix, known)
        if matched is None:
            continue
        source, field = matched
        grouped.setdefault(source, {})[field] = value

    resolved: dict[str, AuthCredential] = {}
    for source, fields in grouped.items():
        present = frozenset(fields)
        for required, optional, auth_type in _SHAPES:
            if required <= present and (present - required) <= optional:
                resolved[source] = _build_credential(auth_type, fields)
                break
        else:
            logger.warning(
                "ignoring credential for source %r: fields %s match no known "
                "auth shape (bearer/api_key/basic/oauth2/aws)",
                source,
                sorted(present),
            )

    return CredentialStore(resolved)
