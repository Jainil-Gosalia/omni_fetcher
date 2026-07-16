# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.0] - 2026-07-16

Elasticsearch: a bounded search fetcher on the `fetch()` seam, backed by
the scroll API for large result sets.

### Added
- **`elasticsearch` fetcher** — `es://host[:port]/index?q=&size=&scroll=&user=&password=&api_key=`
  queries an index and returns one `Result`: a `search_results` container
  node with one `json_document` child per matching document (up to
  `?size=`, default 100). Each document's `_source` is preserved losslessly
  as a `Text` atom (`format=CODE`) — the PRD's literal "JSONData atom" does
  not exist in v1's closed atom vocabulary (`Text`/`Image`/`Audio`/`Video`/
  `Table`), so JSON bodies are serialised the same way `http_json` already
  does. Query-level facts (index, query, doc_count, total_hits, took_ms)
  live on the container's `source_extra["elasticsearch"]`; per-document
  facts (doc_id, index, score) live on each document node's own
  `source_extra` — mirrors `confluence`'s space/page split rather than the
  Kafka/Redis/WebSocket/SSE per-message streaming pattern (this is a
  **bounded** fetcher: `stream()` yields exactly one item, matching
  `confluence`).
- Internally drives Elasticsearch's scroll API to page through large
  result sets without loading everything into memory; the scroll cursor
  is always cleared in a `finally`. A missing index is `Error(NOT_FOUND)`;
  a malformed query is `Error(INVALID_INPUT)`; a connection/scroll failure
  is `Error(TRANSIENT)` (or a `Partial` if some documents were already
  collected); zero matches is an honest `Error(NOT_FOUND)`.
- Behind a new `elasticsearch` extra (`pip install "omni-fetcher[elasticsearch]"`,
  `elasticsearch-py` 8.x targeting Elasticsearch 7.x+); skipped by
  `builtin_registry()` when absent.

## [1.4.0] - 2026-07-16

Live streams: WebSocket and Server-Sent Events connectors on the `stream()`
seam, following the v1.2 (Kafka/tail) and v1.3 (Redis Streams) pattern.

### Added
- **`websocket` connector** — `ws://host[:port]/path?token=&auth=&sequence=<n>`
  emits one `Result` per message (kind `message`, raw payload as a plain
  `Text` atom, url/handshake_timestamp/sequence/close_code in
  `source_extra["websocket"]`). Auth and resume position travel as URI
  query params, forwarded verbatim to the server. Connection loss yields
  one typed `TRANSIENT`; a clean close (RFC 6455 code 1000/1001 — the
  server ending the stream on purpose) ends the stream with no `Error`,
  so `stream_with_restart` doesn't waste attempts reconnecting to a
  finished stream. `fetch()` is a typed `UNSUPPORTED`.
- **`sse` connector** — `sse://host[:port]/path?token=&auth=&sequence=<n>`
  (and `sses://` for TLS) emits one `Result` per dispatched event (kind
  `message`, raw `data:` payload as a plain `Text` atom, same
  `source_extra["sse"]` shape as websocket). A server-assigned `id:` field
  becomes the resume sequence; falls back to receipt order otherwise.
- Both connectors are behind a new `websockets` extra
  (`pip install "omni-fetcher[websockets]"`, pulling in `websockets` and
  `aiohttp`); skipped by `builtin_registry()` when absent.
- `stream_with_restart`'s resume-URI derivation (`retry.py`) gained
  `websocket`/`sse` branches: `source_extra[...]["sequence"]` maps to
  `?sequence=<n+1>` on reconnect, alongside the existing tail/kafka/redis
  branches.

### Design notes
- Ephemeral vs. durable tradeoff: unlike Kafka/Redis Streams, a message
  lost while disconnected cannot be recovered — resume only prevents
  duplicate delivery, not data loss.
- No JSON parsing of message content (core decomposes, doesn't transform);
  SSE's `data:`/`id:`/blank-line wire format is parsed as protocol framing,
  the same way Kafka's record envelope or tail's line-splitting are.

## [1.2.0] - 2026-07-12

Streaming: the first unbounded connectors on the `stream()` seam, a
host-side restart helper, and a streaming CLI.

### Added
- **`tail` connector** — `tail://<path>?from=end|start|<byte>&poll=<s>`
  follows a local file, emitting one `Result` per line (kind `log_line`,
  resume positions in `source_extra["tail"]`). Follows in-place
  truncation and rotation onto a replaced file; a vanished file ends the
  stream with a typed `TRANSIENT`.
- **`kafka` connector** — `kafka://host[:port]/topic?offset=…&offsets=…&group=…`
  emits one `Result` per message (kind `message`,
  topic/partition/offset/key/timestamp in `source_extra["kafka"]`).
  Stateless assign+seek by default; `?group=` opts into a committing
  consumer group owned by the host. Behind a new `kafka` extra
  (`pip install "omni-fetcher[kafka]"`); skipped by `builtin_registry()`
  when absent.
- **`stream_with_restart`** — a host-side restart wrapper (reusing
  `RetryPolicy`) that resumes a dropped stream from the last item's
  position (tail `byte_offset` → `?from=`, kafka accumulated offsets →
  `?offsets=p:o+1`, or a custom `resume` deriver). Exported from
  `omni_fetcher.v1`.
- **`omni-fetcher v1 stream <uri>`** — NDJSON streaming CLI with
  `--max-items`, env-var-name credential flags, and clean Ctrl-C (exit
  130).

### Changed
- `fetch()` on an unbounded source (`tail`, `kafka`) returns a typed
  `Error(UNSUPPORTED)` immediately instead of hanging.
- The orchestrator's `stream()` now closes the connector's generator
  deterministically when the consumer stops iterating, releasing file
  handles and broker consumers without waiting for garbage collection.

## [1.1.0] - 2026-07-05

Contract completion & developer experience: one-call wiring, working zoom,
stateless retry, a v1 CLI, and the legacy layer's deprecation clock.

### Added
- `builtin_registry()` — an immutable registry of all 21 built-in
  connectors, wired in one line: `OmniFetcher(builtin_registry())`.
  Connector modules load lazily; sources whose optional extra is missing
  are skipped. The wiring API (`OmniFetcher`, `RegistryBuilder`,
  `FrozenRegistry`, `builtin_registry`) is exported from `omni_fetcher.v1`.
- **Zoom works.** Coarser-than-natural specs are applied centrally in
  `fetch()`/the orchestrator for every connector; finer-than-natural text
  levels (`SECTION`/`PARAGRAPH`/`SENTENCE`) decompose `Text` atoms in the
  text-bearing connectors via the new pure, lossless
  `omni_fetcher.v1.decompose` module (pptx maps `SECTION` onto its slides).
  Explicitly requesting a finer level for an undecomposable atom kind
  records an honest gap.
- `RetryPolicy` + `fetch_with_retry` — frozen, host-side, value-driven
  retry for `TRANSIENT`/`RATE_LIMITED` results; never retries delivered
  data, never raises for expected failures.
- `omni-fetcher v1 fetch <uri>` — CLI over the canonical contract with
  rich-tree or `--json` output, `--zoom text=paragraph`, and env-var-name
  credential flags (no secret ever appears in argv or output).
- Behavior test suites for the four connectors that shipped untested
  (notion, linear, confluence, youtube) — all 21 connectors are now
  covered — plus a cross-connector error-classification audit pinning the
  canonical status→ErrorKind table now documented in
  `omni_fetcher.v1.errors`.
- Registry URI patterns support an explicit `re:` prefix for regexes
  containing fnmatch trigger characters.

### Fixed
- Notion databases no longer all render as "Untitled" (the title-typed
  schema stub shadowed the real top-level title array).

### Deprecated
- The legacy pre-v1 API (`OmniFetcher`, `omni_fetcher.fetchers.*`, the
  pre-v1 schemas) now emits a `DeprecationWarning` on first use and will be
  removed in 2.0. Migrate to `omni_fetcher.v1` (see docs/migration-v1.md).

### Changed
- Top-level legacy exports resolve lazily: `import omni_fetcher` (and
  therefore `import omni_fetcher.v1`) no longer imports the legacy fetcher
  tree. `from omni_fetcher import <name>` behaves as before, plus the
  deprecation warning.
- `docs/index.md`, `docs/fetchers.md`, and `docs/auth.md` rewritten for the
  v1 API.

## [1.0.0] - 2026-07-04

The v1.0 clean break: every connector now emits a single **canonical contract**
(a `CompositionNode` tree of typed atoms + uniform `FetchMetadata` + namespaced
`source_extra`, wrapped in a `Result`).

### Removed
- Source-specific public schema classes are no longer part of the public API:
  `GitHub*`, `GoogleDrive*`/`GoogleSheets*`/`GoogleDocs*`/`GoogleSlides*`,
  `Notion*`, `Confluence*`, `Slack*`, and `Jira*` are no longer exported from
  `omni_fetcher`. Their modules remain on disk for the legacy fetchers' internal
  use only. See the migration guide below.

### Added
- `docs/migration-v1.md` — maps every removed schema family onto the canonical
  atoms + metadata core + `source_extra`.
- Multi-tenant isolation proof: concurrency tests (`tests/v1/test_isolation.py`)
  that drive one shared resolver/registry/orchestrator from many threads and
  interleaved coroutines with distinct tenant credentials, asserting no
  cross-tenant leakage of auth or data.

### Changed
- `FetchMetadata.content_hash` is documented as an opt-in Merkle content
  fingerprint (populate on demand via `CompositionNode.populate_hashes`);
  `prev_hash` remains reserved and is never auto-populated. No verification
  logic ships.

### Migration
- See [docs/migration-v1.md](docs/migration-v1.md).

## [0.11.2] - 2026-02-24

### Fixed
- pdf.py: Removed non-existent `title` parameter in PDFDocument creation
- google_drive.py: Added missing folder pattern `drive.google.com/drive/folders/` in parse_file_id()
- csv.py: Fixed bug where headers were set to None even when CSV had no headers
- fetchers: Added missing required fields to FetchMetadata in pdf.py

### Added
- Comprehensive tests for DOCX fetcher (test_docx.py)
- Comprehensive tests for PPTX fetcher (test_pptx.py)
- Comprehensive tests for PDF fetcher (test_pdf.py)
- Tests for CSV fetcher (test_csv.py)
- Tests for Google Drive fetcher (test_google_drive.py)

### Coverage
- Overall coverage improved from 66% to 67%

## [0.11.1] - 2026-02-23

### Changed
- `JSONData.schema` renamed to `JSONData.json_schema` to avoid shadowing Pydantic's `BaseModel.schema` method. Update any code that accesses this field.

### Fixed
- Migrated `class Config` to `model_config = ConfigDict(...)` in `schemas/base.py` and `schemas/containers.py` to fix Pydantic deprecation warnings.

### Added
- Tests for Google Drive fetcher (google_drive.py)
- Tests for core OmniFetcher orchestration (fetcher.py)
- Tests for HTTP/webpage fetcher (http_url.py)
- Tests for cache infrastructure (cache/__init__.py, cache/redis.py)
- Expanded tests for notion, slack, jira, confluence, github, csv, docx, pptx, pdf fetchers

### Coverage
- Overall coverage improved from 58% to 75%+

## [0.10.0] - 2026-02-23

### Added
- ConfluenceFetcher - Fetch pages and spaces from Confluence API
- Confluence schemas: ConfluencePage, ConfluenceSpace, ConfluenceAttachment, ConfluenceUser, ConfluenceComment

### ConfluenceFetcher Features
- Fetches Confluence pages with HTML content
- Fetches Confluence spaces as containers with pages and attachments
- HTML→markdown conversion (headings, lists, code blocks, tables)
- Bearer token auth via CONFLUENCE_TOKEN env var
- Supports self-hosted Confluence via base_url kwarg
- Supports Confluence Cloud (atlassian.net)

### URI Patterns
- `company.atlassian.net/wiki/spaces/SPACE/pages/PAGE_ID` → ConfluencePage
- `confluence.company.com/pages/viewpage.action?pageId=PAGE_ID` → ConfluencePage
- `company.atlassian.net/wiki/spaces/SPACE` → ConfluenceSpace
- `confluence://page-id` → ConfluencePage

### Dependencies
- atlassian-python-api>=3.0.0

### New Tests
- test_confluence.py - 25 tests for ConfluenceFetcher

## [0.9.0] - 2026-02-23

### Added
- NotionFetcher - Fetch pages and databases from Notion API
- Notion schemas: NotionPage, NotionDatabase, NotionBlock, NotionRichText, NotionUser, NotionProperty
- Notion media types in base.py

### NotionFetcher Features
- Fetches Notion pages with block content
- Fetches Notion databases as SpreadsheetDocument (properties as headers, pages as rows)
- Block→markdown conversion for 18 block types (paragraph, headings, lists, code, quote, callout, image, video, embed, bookmark, etc.)
- Rich text with markdown annotations (bold, italic, code, links)
- Bearer token auth via NOTION_TOKEN env var
- Recursive block fetching support
- Uses notion-client SDK with pagination helpers

### URI Patterns
- `notion.so/page-id` → NotionPage
- `notion.so/workspace/page-name-32charid` → NotionPage
- `notion://page-id` → NotionPage

### Dependencies
- notion-client>=2.0.0

### New Tests
- test_notion.py - 23 tests for NotionFetcher

## [0.8.0] - 2026-02-22

### Added
- GoogleDriveFetcher - Fetch files and folders from Google Drive
- GoogleSheetsFetcher - Fetch Google Sheets as CSV/JSON
- GoogleDocsFetcher - Fetch Google Docs as markdown
- GoogleSlidesFetcher - Fetch Google Slides as text per slide
- Google schemas: GoogleDriveFile, GoogleDriveFolder, GoogleDriveContainer, GoogleSheetsSpreadsheet, GoogleDocsDocument, GoogleSlidesPresentation

### Google Features
- Fetches Drive files with metadata (size, mime type, timestamps)
- Recursive folder fetching
- Service account authentication via GOOGLE_SERVICE_ACCOUNT_JSON
- Exports Sheets as CSV/JSON
- Converts Docs to markdown
- Extracts text from Slides

### URI Patterns
- `drive.google.com/file/d/FILE_ID` → GoogleDriveFile
- `drive.google.com/drive/folders/FOLDER_ID` → GoogleDriveFolder
- `docs.google.com/spreadsheets/d/FILE_ID` → GoogleSheetsSpreadsheet
- `docs.google.com/document/d/FILE_ID` → GoogleDocsDocument
- `docs.google.com/presentation/d/FILE_ID` → GoogleSlidesPresentation

### Dependencies
- google-api-python-client>=2.0.0
- google-auth>=2.0.0

## [0.7.0] - 2026-02-22

### Added
- GitHubFetcher - Fetch repos, files, issues, PRs, releases from GitHub API
- GitHub schemas: GitHubFile, GitHubIssue, GitHubPR, GitHubRelease, GitHubRepo
- GitHub containers: GitHubIssueContainer, GitHubReleaseContainer, GitHubPRContainer

### GitHubFetcher Features
- Fetches repository metadata (stars, forks, language, topics)
- Fetches README files
- Fetches individual files with language detection
- Fetches issues with comments
- Fetches pull requests with diff support
- Fetches releases with release notes
- Bearer token auth via GITHUB_TOKEN env var

### URI Patterns
- `github.com/owner/repo` → GitHubRepo
- `github.com/owner/repo/blob/branch/path` → GitHubFile
- `github.com/owner/repo/issues/N` → GitHubIssue
- `github.com/owner/repo/pull/N` → GitHubPR
- `github.com/owner/repo/releases` → GitHubReleaseContainer

### New Tests
- test_github.py - 27 tests for GitHubFetcher

## [0.6.1] - 2026-02-22

### Added
- AudioFetcher - Separate fetcher for audio files (local and remote)
- utils/tags.py - Tag utilities for reuse

### New Tests
- test_audio.py - 20 tests for AudioFetcher
- test_local_file.py - 20 tests for LocalFileFetcher

### Changed
- local_file.py - Removed audio handling (now in AudioFetcher)

### AudioFetcher Features
- Handles local and remote audio files
- URI patterns: .mp3, .wav, .flac, .ogg, .m4a, .aac, .wma
- Tags: audio, local/remote, large_file (>50MB)
- Priority: 15

### Testing
- All 237 tests pass (was 198)


## [0.6.0] - 2026-02-22

### Added
- Tag System - All schemas now include a `tags: list[str]` field
- Automatic tag population by fetchers based on source type
- Tag merging in composite/container schemas via model_validator
- User-supplied tags via `tags` kwarg in `OmniFetcher.fetch()`
- Naming conflict resolution:
  - YouTubeVideo.tags → youtube_tags (YouTube's original tags)
  - RSSItem.tags → categories (RSS feed categories)
- BaseContainer schema with merge_tags validator
- large_file tag (>50MB) for local_file and s3 fetchers

### Fetcher Tags
| Fetcher | Tags |
|---------|------|
| local_file | local, file, format-specific, large_file |
| pdf | pdf, document, scanned |
| docx | docx, document, office, has_images, has_tables |
| pptx | pptx, presentation, office |
| youtube | video, youtube, has_transcript |
| rss | rss, feed |
| s3 | s3, cloud_storage, large_file |
| http_url | web, content-specific |
| http_json | json, api |
| graphql | graphql, api |
| csv | csv, spreadsheet |

### Added Dependencies
- schemas/containers.py - New module for container schemas

### Backward Compatibility
- All changes are additive - existing code works without modification

## [0.5.0] - 2026-02-22

### Added
- AudioFetcher - Fetch and parse audio files (local and remote)
- AudioDocument schema - Audio metadata with transcript support
- Container schemas module - BaseContainer, RSSFeed, S3Bucket, YouTubePlaylist

### AudioFetcher Features
- Handles local and remote audio files
- URI patterns: .mp3, .wav, .flac, .ogg, .m4a, .aac, .wma
- Extracts metadata: duration, sample_rate, channels, artist, album, genre
- Optional transcript extraction
- Tags: audio, local/remote, large_file (>50MB)

### Container Features
- BaseContainer with pagination support (next_page_token)
- RSSFeed for RSS/Atom feeds
- S3Bucket for S3 object listings
- YouTubePlaylist for YouTube playlists


## [0.4.0] - 2026-02-22

### Added
- DOCXDocument schema - Word documents with text, images, tables
- PPTXDocument schema - PowerPoint presentations with slides
- SlideDocument schema - Individual slides
- WebPageDocument schema - Clean webpage extraction (replaces HTMLDocument)
- DOCXFetcher - Fetch and parse .docx files
- PPTXFetcher - Fetch and parse .pptx files

### Changed
- HTTP fetcher now returns WebPageDocument for HTML content (clean extraction via trafilatura)
- HTMLDocument removed - use WebPageDocument instead

### Added Dependencies
- Optional: [office] - python-docx, python-pptx
- Optional: [web] - trafilatura, readability-lxml

## [0.3.1] - 2026-02-21

### Added
- Enriched atomic schemas with optional metadata fields:
  - AudioDocument: file_name, file_size_bytes, artist, album, track_number, year, genre
  - ImageDocument: file_name, file_size_bytes, camera_make, camera_model, gps_latitude, gps_longitude, orientation, photographer, license, page_url
  - VideoDocument: file_name, file_size_bytes

### Changed
- Clean atomic/composite hierarchy:
  - 5 atomics (TextDocument, AudioDocument, ImageDocument, VideoDocument, SpreadsheetDocument)
  - 4 composites (YouTubeVideo, LocalVideo, PDFDocument, HTMLDocument)
  - Removed unnecessary base classes (BaseMedia, Video, Audio, Image, BaseDocument)
- All composites now compose directly from atomics

### Deprecated
- The following schemas are deprecated and will be removed in a future version:
  - BaseMedia, Video, Audio, Image, BaseDocument
  - TextDocument (old version - use atomics.TextDocument)
  - MarkdownDocument, CSVData
  - StreamAudio, LocalAudio, LocalImage, WebImage

### Removed
- Internal base classes that added no value

## [0.3.0] - 2026-02-21

### Added
- Atomic schema layer:
  - 5 atomic schemas: TextDocument, AudioDocument, ImageDocument, VideoDocument, SpreadsheetDocument
  - 4 composite schemas: YouTubeVideo, LocalVideo, PDFDocument, HTMLDocument

### Changed
- All schemas now compose from atomic layer
- Unified metadata across document types

## [0.2.0] - 2026-02-21

### Added
- GraphQL fetcher with query/mutation support
- CLI with fetch, sources, cache, and version commands
- Redis cache backend for distributed caching

### Changed
- Enhanced GraphQLResponse schema with extensions and has_errors

## [0.1.0] - 2026-02-21

### Added
- Initial release
- Multiple data sources (local files, HTTP, YouTube, RSS, S3, PDF, CSV)
- Plugin architecture with @source decorator
- Authentication (bearer, API key, basic, AWS, OAuth2)
- Caching (file, memory), retry, rate limiting
- Pydantic v2 data validation

[Unreleased]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Jainil-Gosalia/omni_fetcher/releases/tag/v0.1.0
