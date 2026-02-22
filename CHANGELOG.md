# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[Unreleased]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Jainil-Gosalia/omni_fetcher/releases/tag/v0.1.0
