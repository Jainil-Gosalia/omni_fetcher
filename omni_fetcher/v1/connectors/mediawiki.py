"""The ``mediawiki`` connector for the OmniFetcher v1 contract (v1.14).

Reads a wiki page through the MediaWiki API (``action=parse``) and maps it onto
the canonical contract via the knowledge-base spec (``_wiki_notes``): a
``kind="note"`` node carrying the page's rendered HTML as a ``Text`` atom
(``TextFormat.HTML`` -- the API returns HTML, which the connector labels
honestly rather than lossily converting), with the page title, its outbound
links as ``wikilinks``, and its categories as ``tags`` in
``source_extra["mediawiki"]``.

URI: ``mediawiki://<host>/wiki/<Title>`` (e.g.
``mediawiki://en.wikipedia.org/wiki/Python_(programming_language)``); the API is
called at ``https://<host>/w/api.php``. Auth is optional -- a per-call
``BearerAuth`` for a private wiki -- since most wikis are public. The HTTP client
is ``httpx`` (a core dependency), so there is no optional extra.

Expected failures are returned as typed ``Error`` values, never raised: an HTTP
status maps onto the taxonomy the same way ``http_json`` maps it, an API
``error`` object (a missing page, a denied read) maps onto the taxonomy by its
code, and a non-JSON body is a ``PARSE_ERROR``.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional

import httpx

from omni_fetcher.v1.atoms import TextFormat
from omni_fetcher.v1.auth import AuthCredential, NormalizedAuthResolver
from omni_fetcher.v1.connectors._wiki_notes import build_note_node
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import SequenceCounter, now_utc, stamp_temporal
from omni_fetcher.v1.result import Result, error, success
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace under which all descriptive ``mediawiki`` fields are stored.
SOURCE_NAMESPACE = "mediawiki"

_SCHEME = "mediawiki://"
_DEFAULT_TIMEOUT = 30.0

# MediaWiki API error codes -> v1 error taxonomy.
_API_ERROR_KINDS: dict[str, ErrorKind] = {
    "missingtitle": ErrorKind.NOT_FOUND,
    "nosuchpageid": ErrorKind.NOT_FOUND,
    "invalidtitle": ErrorKind.INVALID_INPUT,
    "permissiondenied": ErrorKind.PERMISSION_DENIED,
    "readapidenied": ErrorKind.PERMISSION_DENIED,
}


def _status_to_error_kind(status_code: int) -> ErrorKind:
    """Map an HTTP status code onto a taxonomy ``ErrorKind`` (as ``http_json``)."""
    if status_code == 401:
        return ErrorKind.AUTH_FAILED
    if status_code == 403:
        return ErrorKind.PERMISSION_DENIED
    if status_code == 404:
        return ErrorKind.NOT_FOUND
    if status_code == 429:
        return ErrorKind.RATE_LIMITED
    if 500 <= status_code <= 599:
        return ErrorKind.TRANSIENT
    return ErrorKind.INVALID_INPUT


def _parse_uri(uri: str) -> tuple[str, str]:
    """Parse ``mediawiki://host/wiki/Title`` into ``(host, title)``.

    Accepts ``host/wiki/Title`` and the terser ``host/Title``. Raises
    ``ValueError`` for a URI that names no host or title.
    """
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a mediawiki:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    host, _, rest = remainder.partition("/")
    if rest.startswith("wiki/"):
        rest = rest[len("wiki/") :]
    if not host or not rest:
        raise ValueError(f"mediawiki:// URI must be mediawiki://host/wiki/Title: {uri}")
    return host, rest


def _page_html(parse: dict[str, Any]) -> str:
    """Pull the rendered HTML from a ``parse`` block (formatversion 1 or 2)."""
    text = parse.get("text")
    if isinstance(text, dict):  # formatversion=1: {"*": "<html>"}
        return str(text.get("*", ""))
    return str(text or "")


def _link_titles(parse: dict[str, Any]) -> list[str]:
    """Pull outbound link titles from a ``parse`` block (fv1 or fv2 shapes)."""
    titles: list[str] = []
    for link in parse.get("links", []) or []:
        if isinstance(link, dict):
            title = link.get("title") or link.get("*")
            if title:
                titles.append(str(title))
    return titles


def _category_names(parse: dict[str, Any]) -> list[str]:
    """Pull category names from a ``parse`` block (fv1 or fv2 shapes)."""
    names: list[str] = []
    for category in parse.get("categories", []) or []:
        if isinstance(category, dict):
            name = category.get("category") or category.get("*")
            if name:
                names.append(str(name))
    return names


class MediaWikiConnector(BaseFetcher):
    """
    Canonical v1 connector for MediaWiki pages
    ===============================================
    Fetches one wiki page via ``action=parse`` and emits a ``kind="note"`` node
    carrying the page's rendered HTML as a ``Text`` atom
    (``TextFormat.HTML``), with the title, outbound links (``wikilinks``), and
    categories (``tags``) in ``source_extra["mediawiki"]``. Read-only.
    ===============================================
    NOTE:
        1. Implements only ``stream()``; ``fetch()`` is inherited.
        2. Auth is optional (a per-call ``BearerAuth`` for a private wiki);
           public wikis need none.
        3. Expected failures -- HTTP status, an API ``error`` object, a non-JSON
           body -- are typed ``Error`` values, never raised.

    Attributes
    ----------
        timeout:
            Per-request transport timeout in seconds.

    Methods
    -------
        stream:
        can_handle:
    """

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self._auth_resolver = NormalizedAuthResolver()

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Report whether ``uri`` is a ``mediawiki://`` reference."""
        return uri.startswith(_SCHEME)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical node for one MediaWiki page

        Yields exactly one ``Result``: a ``Success`` ``note`` node, or a typed
        ``Error`` (bad URI, HTTP status, API error, non-JSON body, network
        blip).

        Parameters
        ----------
            uri:
                The ``mediawiki://host/wiki/Title`` source URI.
            auth:
                Optional per-call ``BearerAuth`` for a private wiki.
            zoom:
                Accepted for protocol conformance; central pruning still applies.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        del zoom

        try:
            host, title = _parse_uri(uri)
        except ValueError as exc:
            yield error(ErrorKind.INVALID_INPUT, message=str(exc), locator=uri)
            return

        api_url = f"https://{host}/w/api.php"
        params = {
            "action": "parse",
            "page": title,
            "prop": "text|links|categories|revid",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
        }
        headers = self._auth_resolver.resolve_headers(auth)

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(api_url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            yield error(ErrorKind.TRANSIENT, message=str(exc), locator=uri)
            return

        if response.status_code != 200:
            yield error(
                _status_to_error_kind(response.status_code),
                message=f"MediaWiki API returned HTTP {response.status_code}",
                locator=uri,
            )
            return

        try:
            payload = response.json()
        except ValueError as exc:
            # httpx's .json() raises json.JSONDecodeError, a ValueError subclass.
            yield error(ErrorKind.PARSE_ERROR, message=str(exc), locator=uri)
            return

        api_error = payload.get("error") if isinstance(payload, dict) else None
        if api_error:
            code = str(api_error.get("code", ""))
            yield error(
                _API_ERROR_KINDS.get(code, ErrorKind.INVALID_INPUT),
                message=str(api_error.get("info", f"MediaWiki API error: {code}")),
                locator=uri,
            )
            return

        parse = payload.get("parse") if isinstance(payload, dict) else None
        if not isinstance(parse, dict):
            yield error(
                ErrorKind.PARSE_ERROR,
                message="MediaWiki API response has no 'parse' block",
                locator=uri,
            )
            return

        node = build_note_node(
            uri=uri,
            namespace=SOURCE_NAMESPACE,
            title=str(parse.get("title") or title),
            body=_page_html(parse),
            text_format=TextFormat.HTML,
            wikilinks=_link_titles(parse),
            tags=_category_names(parse),
            extra_fields={
                "host": host,
                "pageid": parse.get("pageid"),
                "revid": parse.get("revid"),
            },
        )
        result = success(node)
        stamp_temporal(result.tree, sequence=SequenceCounter().next(), timestamp=now_utc())
        yield result
