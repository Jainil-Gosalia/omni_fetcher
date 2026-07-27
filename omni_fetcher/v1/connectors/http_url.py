"""HTTP/HTTPS URL connector for the OmniFetcher v1 canonical contract.

Fetches a web resource over HTTP/HTTPS and emits it as a canonical
``CompositionNode`` tree wrapped in a ``Result``. The node carries an
advisory ``kind`` of ``"webpage"``; the page's extracted text (and title,
when present) is carried as ``Text`` atoms, while every descriptive HTTP
field (final URL, status code, content type, headers) lives in the metadata
core and the namespaced ``source_extra["http_url"]`` mapping -- never inline
on an atom.

An HTML body is *rendered* to text, and the rendering is selectable because
the two useful renderings serve different consumers:

- ``MARKDOWN`` (the default) runs ``trafilatura``, which discards navigation,
  sidebars, and comment chrome and keeps heading/paragraph structure. This is
  the only ``TextFormat`` that ``decompose`` can split at all three of
  ``SECTION`` / ``PARAGRAPH`` / ``SENTENCE``, so it is the right default for
  anything that addresses sub-document spans.
- ``PLAIN`` flattens the page to blank-line-separated prose, for consumers
  (embedding models, keyword indexes, TTS) to which markdown syntax is noise.

Both renderings emit real paragraph breaks. This matters: text joined with
single newlines contains no ``\\n\\n``, so ``decompose``'s ``PARAGRAPH`` rule
finds nothing to split and silently returns the whole page as one "paragraph"
-- a claim that is false, and one no ``Gap`` would report because the rule
technically ran. Emitting blank lines between blocks is what makes
paragraph-addressed grounding work at all.

Expected failures are returned as typed ``Result`` values, never raised:

- An HTTP non-2xx response maps onto the error taxonomy (``404`` ->
  ``NOT_FOUND``, ``401`` -> ``AUTH_FAILED``, ``403`` ->
  ``PERMISSION_DENIED``, ``429`` -> ``RATE_LIMITED``, ``5xx`` ->
  ``TRANSIENT``, other 4xx -> ``INVALID_INPUT``).
- A network/transport failure (timeout, connection error) maps onto
  ``TRANSIENT``.
- Content that cannot be decoded maps onto ``PARSE_ERROR``.
- An unreadable value for the reserved format query key maps onto
  ``INVALID_INPUT``.
- ``MARKDOWN`` *explicitly* requested while ``trafilatura`` is not installed
  yields the ``PLAIN`` rendering plus an honest ``UNSUPPORTED`` ``Gap``.
  Merely *defaulting* to ``MARKDOWN`` records no gap -- otherwise every page
  fetched on a base install would come back ``Partial`` -- but the fallback
  stays visible in ``source_extra["http_url"]["text_format"]``, which always
  names the format actually produced. This is the distinction
  ``decompose.decompose_node`` already draws between a level a spec asked for
  and one it inherited.

Whichever path runs, an atom's declared ``TextFormat`` describes what its
bytes actually are. Markdown that could not be produced is never labelled
``MARKDOWN``.

The connector is read-only and deterministic given a fixed response; the
only non-determinism is the wall-clock timestamp stamped into the streamed
node's temporal position. No model-based extraction is performed.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential, NormalizedAuthResolver
from omni_fetcher.v1.connectors._optional import module_available
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import (
    SequenceCounter,
    build_node,
    now_utc,
    stamp_temporal,
)
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Gap,
    Result,
    error,
    from_exception,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# Advisory semantic ``kind`` for the node a fetched web resource maps onto.
WEBPAGE_KIND = "webpage"

# Source namespace under which descriptive HTTP fields are recorded.
SOURCE_NAMESPACE = "http_url"

# Reserved URI query key selecting how an HTML body is rendered to text.
#
# The query string of an http(s) URL belongs to the *origin server*, not to
# us -- unlike ``tail://`` or ``bigquery://``, where the whole URI is ours to
# define. So this key is namespaced to make collision with a real parameter
# implausible, and is stripped before the request is issued: it is never sent
# upstream, and never appears in the recorded requested/final URLs. It is the
# only per-call channel that reaches consumers (the MCP server, the CLI) that
# construct connectors through the registry, which passes no configuration.
TEXT_FORMAT_PARAM = "__omni_text_format"

# The renderings selectable for an HTML body. Only these two are meaningful:
# HTML is the input, not an output choice, and CODE/OPAQUE would be lies.
_SELECTABLE_FORMATS: dict[str, TextFormat] = {
    "markdown": TextFormat.MARKDOWN,
    "plain": TextFormat.PLAIN,
}

# The markdown renderer ships in the ``web`` extra, so it is probed rather
# than imported: the module must import on a base install.
TRAFILATURA_AVAILABLE = module_available("trafilatura")

# Elements after whose content a paragraph break is inserted when flattening
# to PLAIN. Nesting is harmless -- repeated breaks collapse to one.
#
# The chrome containers (nav/header/footer/aside/form) are here deliberately.
# PLAIN keeps page chrome -- dropping it is what MARKDOWN is for -- but chrome
# must land in blocks of its *own*. Without them a nav bar fuses onto the
# opening sentence of the article, producing an atom that is part menu and
# part prose: exactly the contamination that makes a decomposed piece
# unusable downstream, and one no consumer can filter after the fact.
_TEXT_BLOCK_TAGS: tuple[str, ...] = (
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "dt",
    "dd",
    "blockquote",
    "pre",
    "article",
    "section",
    "div",
    "tr",
    "figcaption",
    "figure",
    "table",
    "ul",
    "ol",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
)

# Maps an HTTP status code onto the closed v1 error taxonomy.
_STATUS_ERROR_KINDS: dict[int, ErrorKind] = {
    401: ErrorKind.AUTH_FAILED,
    403: ErrorKind.PERMISSION_DENIED,
    404: ErrorKind.NOT_FOUND,
    429: ErrorKind.RATE_LIMITED,
}


def _classify_status(status_code: int) -> ErrorKind:
    """Map a non-2xx HTTP status code onto the error taxonomy."""
    if status_code in _STATUS_ERROR_KINDS:
        return _STATUS_ERROR_KINDS[status_code]
    if 500 <= status_code <= 599:
        return ErrorKind.TRANSIENT
    if 400 <= status_code <= 499:
        return ErrorKind.INVALID_INPUT
    # Any other non-2xx (e.g. an unexpected 3xx after redirects) is treated
    # as a transient/retryable anomaly rather than a terminal failure.
    return ErrorKind.TRANSIENT


def _mime_type(content_type: Optional[str]) -> str:
    """Extract the bare MIME type from a Content-Type header value."""
    if not content_type:
        return "application/octet-stream"
    return content_type.split(";")[0].strip().lower()


def _extract_title(soup: BeautifulSoup) -> Optional[str]:
    """Extract a page title from og:title, <title>, or the first <h1>."""
    og_title = soup.find("meta", property="og:title")
    if og_title:
        # Attribute values may be multi-valued lists in bs4; only a plain
        # string is a usable title.
        content = og_title.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text().strip():
        return title_tag.get_text().strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text().strip():
        return h1.get_text().strip()
    return None


def _split_text_format(uri: str) -> tuple[str, Optional[TextFormat]]:
    """Strip the reserved format key from a URI.

    Returns ``(clean_uri, selected)``. ``selected`` is ``None`` when the key
    is absent, in which case ``clean_uri`` is the input unchanged -- the URL
    is deliberately not round-tripped through parse/encode in that case, so a
    real origin-server query is never re-normalised behind the caller's back.

    Raises ``ValueError`` when the key is present with an unsupported value;
    the caller maps that onto ``INVALID_INPUT`` at the boundary.
    """
    parts = urlsplit(uri)
    if TEXT_FORMAT_PARAM not in parts.query:
        return uri, None

    kept: list[tuple[str, str]] = []
    selected: Optional[TextFormat] = None
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key != TEXT_FORMAT_PARAM:
            kept.append((key, value))
            continue
        try:
            selected = _SELECTABLE_FORMATS[value.strip().lower()]
        except KeyError:
            raise ValueError(
                f"{TEXT_FORMAT_PARAM}= must be one of {sorted(_SELECTABLE_FORMATS)}; got {value!r}"
            ) from None
    clean = urlunsplit(parts._replace(query=urlencode(kept)))
    return clean, selected


def _extract_plain(soup: BeautifulSoup) -> str:
    """Flatten a parsed page to blank-line-separated plain prose."""
    # <head> goes too, not just script/style: get_text() would otherwise emit
    # the <title> as the first run of body text, where -- carrying no block
    # tag of its own -- it fuses onto whatever follows. The title is already
    # carried as its own atom, so this drops a duplicate, not content.
    for element in soup(["script", "style", "head", "noscript", "template"]):
        element.decompose()
    # Mark the end of every block element so paragraph boundaries survive
    # get_text(). Without this the page comes back as lines joined by single
    # newlines -- no "\n\n" anywhere -- and PARAGRAPH decomposition silently
    # yields one giant "paragraph" spanning the whole document.
    for element in soup.find_all(_TEXT_BLOCK_TAGS):
        element.append("\n\n")

    blocks: list[str] = []
    current: list[str] = []
    for line in soup.get_text(separator="\n").split("\n"):
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        elif current:
            blocks.append(" ".join(current))
            current = []
    if current:
        blocks.append(" ".join(current))
    return "\n\n".join(blocks)


def _extract_markdown(html: str) -> Optional[str]:
    """Render HTML to markdown, or ``None`` when no main content is found."""
    try:
        # Imported here, not at module scope: the dependency is optional, and
        # the probe that gated this call only proved a spec exists -- an
        # install whose own dependencies are broken still raises on import.
        import trafilatura

        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            output_format="markdown",
        )
    except Exception:
        # trafilatura raises a variety of parser-internal errors on malformed
        # markup. Any of them means "no markdown available"; the caller falls
        # back to PLAIN and records the reason.
        return None
    if extracted and extracted.strip():
        return extracted.strip()
    return None


class HTTPURLConnector(BaseFetcher):
    """
    HTTP/HTTPS URL connector
    ===============================================
    Fetches a web resource over HTTP/HTTPS and maps it onto the v1 canonical
    contract: a single ``CompositionNode`` of advisory ``kind`` ``"webpage"``
    whose content is carried as ``Text`` atoms, with every descriptive HTTP
    field placed in the metadata core and the namespaced
    ``source_extra["http_url"]`` mapping. Only ``stream()`` is implemented;
    ``fetch()`` is inherited from ``BaseFetcher`` and collects the bounded
    single-item stream into one ``Result``.
    ===============================================
    NOTE:
        1. Expected failures (non-2xx status, transport errors, decode
           failures) are returned as typed ``Result`` values, never raised.
        2. Credentials are passed per call via ``auth`` and resolved
           transiently into request headers; nothing is stored on the
           instance.
        3. The connector is read-only: it issues a single ``GET`` and never
           mutates the source.
        4. ``text_format`` governs only how an *HTML* body is rendered. A
           non-HTML body keeps the format its content type implies (a
           ``text/markdown`` response is ``MARKDOWN`` regardless), because
           that format describes what the bytes already are rather than a
           rendering we chose.
        5. A per-call override travels in the reserved ``?__omni_text_format=``
           query key, which is stripped before the request. That is the only
           channel that reaches callers who construct this connector through
           the registry -- the MCP server and the CLI -- since the registry
           resolves to a bare class and passes no constructor arguments.

    Attributes
    ----------
        timeout:
            Per-request timeout in seconds for the HTTP client.
        text_format:
            Default rendering for an HTML body, overridable per call via
            the reserved query key.

    Methods
    -------
        can_handle:
        stream:
    """

    def __init__(
        self,
        timeout: float = 30.0,
        text_format: Optional[TextFormat] = None,
    ) -> None:
        """
        Create an HTTP/HTTPS URL connector

        Parameters
        ----------
            timeout:
                Per-request timeout in seconds applied to the HTTP client
                (default ``30.0``).
            text_format:
                Rendering for an HTML body -- ``MARKDOWN`` (keeps heading and
                paragraph structure, drops page chrome) or ``PLAIN``
                (blank-line-separated prose). ``None`` (the default) means
                ``MARKDOWN`` *as a default rather than a demand*: see note 1.

        Return
        ------
            None
        """
        if text_format is not None and text_format not in _SELECTABLE_FORMATS.values():
            raise ValueError(
                f"text_format must be one of {sorted(_SELECTABLE_FORMATS)}; got {text_format!r}"
            )
        self.timeout = timeout
        self.text_format = text_format or TextFormat.MARKDOWN
        # Whether MARKDOWN was *asked for* or merely defaulted to. Only an
        # explicit ask earns a Gap when it cannot be honoured -- the same
        # distinction decompose.decompose_node draws between a level a spec
        # explicitly requested and one it inherited.
        self._format_is_explicit = text_format is not None

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether this connector handles a URI

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` if ``uri`` is an HTTP or HTTPS URL.
        """
        return uri.startswith("http://") or uri.startswith("https://")

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical result for a single HTTP/HTTPS URL

        Issues one ``GET`` to ``uri`` (following redirects) and yields exactly
        one ``Result``: a ``Success`` carrying a ``"webpage"`` node when the
        response is 2xx and decodable, or a typed ``Error`` when an expected
        failure occurs. This is a bounded stream of a single item, so
        ``fetch()`` returns that same item unchanged.

        NOTE:
            1. ``zoom`` is accepted for interface conformance; the connector
               emits the page at its natural granularity (a single node) and
               does not decompose further.
            2. The streamed node is stamped with a monotonic ``sequence`` and
               a wall-clock ``timestamp`` in its temporal position.
            3. A reserved ``?__omni_text_format=markdown|plain`` key in ``uri``
               overrides the instance default for this call and is stripped
               before the request; the recorded URLs never contain it.

        Parameters
        ----------
            uri:
                The HTTP/HTTPS URL to fetch, optionally carrying the reserved
                ``__omni_text_format`` query key.
            auth:
                The per-call credential, or ``None`` for unauthenticated
                access. When provided, it is resolved into request headers.
            zoom:
                Optional per-atom-type zoom spec; unused (natural
                granularity).

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        try:
            target, override = _split_text_format(uri)
        except ValueError as exc:
            yield from_exception(exc, kind=ErrorKind.INVALID_INPUT, locator=uri)
            return
        text_format = override or self.text_format
        explicit = override is not None or self._format_is_explicit

        headers = NormalizedAuthResolver().resolve_headers(auth)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(target, headers=headers or None)
        except httpx.HTTPError as exc:
            yield from_exception(exc, kind=ErrorKind.TRANSIENT, locator=target)
            return

        if response.status_code < 200 or response.status_code >= 300:
            yield error(
                kind=_classify_status(response.status_code),
                message=f"HTTP {response.status_code} for {target}",
                locator=target,
            )
            return

        try:
            node, gaps = self._build_webpage_node(target, response, text_format, explicit)
        except (UnicodeError, ValueError) as exc:
            yield from_exception(exc, kind=ErrorKind.PARSE_ERROR, locator=target)
            return

        seq = SequenceCounter()
        stamp_temporal(node, sequence=seq.next(), timestamp=now_utc())
        # Zoom is applied centrally (BaseFetcher.fetch / orchestrator.stream).
        result: Result = partial(node, gaps) if gaps else success(node)
        yield result

    def _render_html(
        self,
        response: httpx.Response,
        text_format: TextFormat,
        locator: str,
        explicit: bool,
    ) -> tuple[list[Text], TextFormat, list[Gap]]:
        """Render an HTML body to Text atoms, reporting the format produced."""
        # Parse from *bytes*, not response.text. httpx honours only the
        # Content-Type header's charset and otherwise assumes utf-8 with
        # errors="replace", so a page declaring its encoding solely in a
        # <meta charset> tag arrives already corrupted into U+FFFD. Handing
        # bs4 the raw bytes lets it read that tag (and sniff as a fallback);
        # passing charset_encoding keeps the header authoritative when the
        # server did declare one, which is the precedence the HTML spec sets.
        soup = BeautifulSoup(
            response.content,
            "html.parser",
            from_encoding=response.charset_encoding,
        )
        title = _extract_title(soup)

        gaps: list[Gap] = []
        rendered: Optional[str] = None
        if text_format is TextFormat.MARKDOWN:
            if TRAFILATURA_AVAILABLE:
                # Give trafilatura the *original* markup, correctly decoded
                # via the encoding bs4 detected -- not soup.decode(), whose
                # re-serialisation of malformed markup would change the very
                # structure trafilatura reads to find the main content.
                html = response.content.decode(
                    soup.original_encoding or "utf-8",
                    errors="replace",
                )
                rendered = _extract_markdown(html)
                if rendered is None and explicit:
                    gaps.append(
                        gap(
                            kind=ErrorKind.UNSUPPORTED,
                            locator=locator,
                            detail=(
                                "no main content could be extracted as markdown; "
                                "fell back to the plain rendering"
                            ),
                        )
                    )
            elif explicit:
                gaps.append(
                    gap(
                        kind=ErrorKind.UNSUPPORTED,
                        locator=locator,
                        detail=(
                            "markdown rendering needs trafilatura (install the "
                            "'web' extra); fell back to the plain rendering"
                        ),
                    )
                )

        # The declared format always describes what the bytes actually are.
        # A failed markdown render yields PLAIN content labelled PLAIN plus a
        # Gap -- never markdown-labelled prose we did not manage to produce.
        if rendered is not None:
            body_format = TextFormat.MARKDOWN
        else:
            rendered = _extract_plain(soup)
            body_format = TextFormat.PLAIN

        atoms: list[Text] = []
        if title:
            # The title is a bare string in either mode: it carries no syntax,
            # so PLAIN is the honest label whatever the body ends up as.
            atoms.append(Text(content=title, format=TextFormat.PLAIN))
        atoms.append(Text(content=rendered, format=body_format))
        return atoms, body_format, gaps

    def _build_webpage_node(
        self,
        requested_url: str,
        response: httpx.Response,
        text_format: TextFormat,
        explicit: bool,
    ) -> tuple[CompositionNode, list[Gap]]:
        """Build the canonical ``"webpage"`` node from a 2xx response."""
        final_url = str(response.url)
        content_type = response.headers.get("content-type")
        mime = _mime_type(content_type)

        gaps: list[Gap] = []
        if mime == "text/html":
            atoms, body_format, gaps = self._render_html(response, text_format, final_url, explicit)
        else:
            # Not a rendering choice: this format states what the body is.
            body_format = TextFormat.MARKDOWN if mime == "text/markdown" else TextFormat.PLAIN
            atoms = [Text(content=response.text, format=body_format)]

        source_fields: dict[str, Any] = {
            "requested_url": requested_url,
            "final_url": final_url,
            "status_code": response.status_code,
            "content_type": content_type,
            "mime_type": mime,
            "headers": dict(response.headers),
            # The format actually produced, not the one requested. A default
            # MARKDOWN that fell back to PLAIN records "plain" and raises no
            # Gap, so the downgrade stays discoverable without being noisy.
            "text_format": body_format.value,
        }
        node = build_node(
            kind=WEBPAGE_KIND,
            atoms=atoms,
            source_url=final_url,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
        return node, gaps
