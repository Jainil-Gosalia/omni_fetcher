# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[Unreleased]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Jainil-Gosalia/omni_fetcher/releases/tag/v0.1.0
