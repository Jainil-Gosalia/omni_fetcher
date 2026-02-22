# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- GraphQL fetcher with query/mutation support
- CLI with fetch, sources, cache, and version commands
- Redis cache backend for distributed caching

### Changed
- Enhanced GraphQLResponse schema with extensions and has_errors

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

[Unreleased]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Jainil-Gosalia/omni_fetcher/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Jainil-Gosalia/omni_fetcher/releases/tag/v0.1.0
