"""One-call wiring for the built-in v1 connectors.

``builtin_registry()`` returns an immutable :class:`FrozenRegistry` with all
built-in connectors registered under URI patterns mirroring each connector's
own ``can_handle`` semantics. Connector modules are imported lazily: each
definition carries a *proxy* class whose instantiation imports the real
connector on first use, so neither ``import omni_fetcher.v1`` nor
``builtin_registry()`` pays for heavy optional dependencies (yt_dlp, the
atlassian client, ...).

Connectors whose optional extra is not installed (``office`` for docx/pptx,
``jira`` / ``confluence`` for the Atlassian client) are skipped with a
debug-level log entry -- never an ImportError -- so a core install still
builds a working registry for every other source.

Deliberately not registered: ``HTTPAuthConnector``. Its URI shape is
identical to the generic HTTP connector's (the difference is intent, not the
URI), so pattern routing cannot distinguish them; hosts that want
authenticated-HTTP routing add it to their own ``RegistryBuilder`` with the
priority that matches their policy.

Everything here is stateless: each ``builtin_registry()`` call returns a
fresh immutable registry, and the proxy classes hold no configuration or
credentials.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from typing import Any

from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.registry import (
    FrozenRegistry,
    RegistryBuilder,
    SourceDefinition,
)

logger = logging.getLogger(__name__)

_CONNECTORS_PKG = "omni_fetcher.v1.connectors"

# Audio extensions mirrored from connectors/audio.py (kept in sync by the
# routing agreement tests).
_AUDIO_EXT_ALT = "mp3|wav|flac|ogg|m4a|aac|wma|opus"

# One record per built-in source:
# (name, module, class name, uri patterns, priority, required import probes)
#
# Pattern semantics follow ``registry._matches_uri``: fnmatch glob when the
# pattern contains ``*``/``?``/``[``, otherwise regex search, otherwise plain
# substring containment. Regex patterns below deliberately avoid fnmatch
# trigger characters.
#
# Priorities (lower wins): 10 unambiguous schemes/hosts, 15 Atlassian wiki
# catch-all (after jira's specific paths), 20 well-known hosts, 25 shape
# heuristics (graphql, local audio), 30 document extensions, 40 feed
# heuristics, 50 JSON-ish HTTP, 60 explicit file://, 90 generic HTTP,
# 95 schemeless-path fallback.
_BUILTIN_SOURCES: tuple[tuple[str, str, str, tuple[str, ...], int, tuple[str, ...]], ...] = (
    (
        "jira",
        f"{_CONNECTORS_PKG}.jira",
        "JiraConnector",
        (
            "jira://*",
            "atlassian.net/browse",
            "atlassian.net/jira",
            "atlassian.net/projects",
        ),
        10,
        ("atlassian",),
    ),
    ("slack", f"{_CONNECTORS_PKG}.slack", "SlackConnector", ("slack://*",), 10, ()),
    ("tail", f"{_CONNECTORS_PKG}.tail", "TailConnector", ("tail://*",), 10, ()),
    (
        "kafka",
        f"{_CONNECTORS_PKG}.kafka",
        "KafkaConnector",
        ("kafka://*",),
        10,
        ("aiokafka",),
    ),
    (
        "redis",
        f"{_CONNECTORS_PKG}.redis",
        "RedisConnector",
        ("redis://*",),
        10,
        (),
    ),
    (
        "websocket",
        f"{_CONNECTORS_PKG}.websocket",
        "WebSocketConnector",
        ("ws://*", "wss://*"),
        10,
        ("websockets",),
    ),
    (
        "sse",
        f"{_CONNECTORS_PKG}.sse",
        "SSEConnector",
        ("sse://*", "sses://*"),
        10,
        ("aiohttp",),
    ),
    (
        "postgres_cdc",
        f"{_CONNECTORS_PKG}.postgres_cdc",
        "PostgresCDCConnector",
        ("postgres-cdc://*",),
        10,
        ("asyncpg",),
    ),
    # Cloud messaging streams (v1.15). kinesis uses core boto3; pubsub and amqp
    # are behind their extras.
    (
        "kinesis",
        f"{_CONNECTORS_PKG}.kinesis",
        "KinesisConnector",
        ("kinesis://*",),
        10,
        (),
    ),
    (
        "pubsub",
        f"{_CONNECTORS_PKG}.pubsub",
        "PubSubConnector",
        ("pubsub://*",),
        10,
        ("google.cloud.pubsub_v1",),
    ),
    (
        "amqp",
        f"{_CONNECTORS_PKG}.amqp",
        "AMQPConnector",
        ("amqp://*", "amqps://*"),
        10,
        ("aio_pika",),
    ),
    (
        "elasticsearch",
        f"{_CONNECTORS_PKG}.elasticsearch",
        "ElasticsearchFetcher",
        ("es://*",),
        10,
        ("elasticsearch",),
    ),
    (
        "notion",
        f"{_CONNECTORS_PKG}.notion",
        "NotionConnector",
        ("notion://*", "notion.so"),
        10,
        (),
    ),
    (
        "linear",
        f"{_CONNECTORS_PKG}.linear",
        "LinearConnector",
        ("linear://*", "linear.app"),
        10,
        (),
    ),
    (
        "sharepoint",
        f"{_CONNECTORS_PKG}.sharepoint",
        "SharePointConnector",
        # Personal ("-my") sites are excluded, mirroring can_handle.
        ("sharepoint://*", r"re:^(?!.+-my\.sharepoint\.com).+\.sharepoint\.com"),
        10,
        (),
    ),
    (
        "s3",
        f"{_CONNECTORS_PKG}.s3",
        "S3Fetcher",
        ("s3://*", ".s3.amazonaws.com"),
        10,
        (),
    ),
    (
        "confluence",
        f"{_CONNECTORS_PKG}.confluence",
        "ConfluenceConnector",
        ("confluence://*", "atlassian.net"),
        15,
        ("atlassian",),
    ),
    (
        "github",
        f"{_CONNECTORS_PKG}.github",
        "GitHubConnector",
        ("github.com",),
        20,
        (),
    ),
    (
        "google_drive",
        f"{_CONNECTORS_PKG}.google_drive",
        "GoogleDriveFetcher",
        (
            "drive.google.com",
            "docs.google.com",
            "spreadsheets.google.com",
            "slides.google.com",
        ),
        20,
        (),
    ),
    (
        "youtube",
        f"{_CONNECTORS_PKG}.youtube",
        "YouTubeConnector",
        ("youtube.com", "youtu.be"),
        20,
        (),
    ),
    (
        "graphql",
        f"{_CONNECTORS_PKG}.graphql",
        "GraphQLConnector",
        ("graphql", "gql"),
        25,
        (),
    ),
    (
        "audio",
        f"{_CONNECTORS_PKG}.audio",
        "AudioConnector",
        # Local-only, mirroring can_handle: file:// URIs, POSIX absolute
        # paths, or Windows drive paths with a recognised audio extension.
        (
            rf"^file://.+\.({_AUDIO_EXT_ALT})$",
            rf"^(/|\w:).+\.({_AUDIO_EXT_ALT})$",
        ),
        25,
        (),
    ),
    ("docx", f"{_CONNECTORS_PKG}.docx", "DocxConnector", ("*.docx",), 30, ("docx",)),
    ("pptx", f"{_CONNECTORS_PKG}.pptx", "PptxConnector", ("*.pptx",), 30, ("pptx",)),
    ("pdf", f"{_CONNECTORS_PKG}.pdf", "PDFConnector", ("*.pdf",), 30, ()),
    ("csv", f"{_CONNECTORS_PKG}.csv", "CSVConnector", ("*.csv",), 30, ()),
    # SQL query connectors. postgres needs the asyncpg (postgres) extra; sqlite
    # is stdlib and ungated, so it is always available.
    (
        # Registry name is "postgres" (not "postgres_query") so the MCP
        # credential convention is OMNI_FETCHER_POSTGRES_USERNAME/_PASSWORD;
        # longest-prefix matching keeps it distinct from "postgres_cdc".
        "postgres",
        f"{_CONNECTORS_PKG}.postgres_query",
        "PostgresQueryConnector",
        ("postgres://*",),
        10,
        ("asyncpg",),
    ),
    ("sqlite", f"{_CONNECTORS_PKG}.sqlite", "SQLiteQueryConnector", ("sqlite://*",), 10, ()),
    (
        "mysql",
        f"{_CONNECTORS_PKG}.mysql",
        "MySQLQueryConnector",
        ("mysql://*", "mariadb://*"),
        10,
        ("aiomysql",),
    ),
    (
        "rss",
        f"{_CONNECTORS_PKG}.rss",
        "RSSConnector",
        ("feed", "rss", "atom"),
        40,
        (),
    ),
    (
        "http_json",
        f"{_CONNECTORS_PKG}.http_json",
        "HTTPJSONConnector",
        (r"re:^https?://.+(api|json)",),
        50,
        (),
    ),
    (
        "local_file_uri",
        f"{_CONNECTORS_PKG}.local_file",
        "LocalFileFetcher",
        ("file://*",),
        60,
        (),
    ),
    (
        "http_url",
        f"{_CONNECTORS_PKG}.http_url",
        "HTTPURLConnector",
        ("http://*", "https://*"),
        90,
        (),
    ),
    (
        "local_file",
        f"{_CONNECTORS_PKG}.local_file",
        "LocalFileFetcher",
        # Fallback: anything without a URI scheme is treated as a local
        # path. Unknown schemes (nope://x) match nothing and stay NOT_FOUND.
        # The scheme charset follows RFC 3986 (letters, digits, +, -, .) so
        # hyphenated schemes like postgres-cdc:// never fall through here.
        (r"re:^(?![A-Za-z][A-Za-z0-9+.-]*://)",),
        95,
        (),
    ),
)


def _extra_available(module_name: str) -> bool:
    """Report whether an optional dependency is importable, without importing it."""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _lazy_proxy(module_path: str, class_name: str) -> type[BaseFetcher]:
    """Build a stand-in fetcher class that imports the real one on first use.

    The proxy subclasses ``BaseFetcher`` so it satisfies
    ``SourceDefinition.fetcher_class``; instantiating it imports the real
    connector module and returns a *real* connector instance (the proxy
    itself is never constructed, so per-call freshness is preserved).
    """

    def __new__(cls: type, *args: Any, **kwargs: Any) -> BaseFetcher:
        real = getattr(importlib.import_module(module_path), class_name)
        return real(*args, **kwargs)

    async def stream(self: Any, uri: str, *, auth: Any = None, zoom: Any = None) -> Any:
        # Never reached: __new__ always returns a real connector instance.
        raise AssertionError("lazy proxy is never instantiated")  # pragma: no cover
        yield  # pragma: no cover  (marks this as an async generator)

    return type(
        class_name,
        (BaseFetcher,),
        {
            "__new__": __new__,
            "stream": stream,
            "__doc__": f"Lazy stand-in for {module_path}.{class_name}.",
            "__module__": __name__,
        },
    )


def builtin_registry() -> FrozenRegistry:
    """
    Build an immutable registry of every available built-in connector

    Registers all built-in v1 connectors under URI patterns mirroring each
    connector's own ``can_handle`` semantics, resolving connector classes
    lazily on first instantiation. Sources whose optional extra is missing
    are skipped with a debug log entry.

    Return
    ------
        registry:
            A fresh immutable ``FrozenRegistry`` routing all available
            built-in sources.
    """
    builder = RegistryBuilder()
    for name, module_path, class_name, patterns, priority, requires in _BUILTIN_SOURCES:
        missing = [probe for probe in requires if not _extra_available(probe)]
        if missing:
            logger.debug(
                "builtin_registry: skipping %r (missing optional dependency %s)",
                name,
                ", ".join(missing),
            )
            continue
        builder.add(
            SourceDefinition(
                name=name,
                fetcher_class=_lazy_proxy(module_path, class_name),
                uri_patterns=patterns,
                priority=priority,
            )
        )
    return builder.build()
