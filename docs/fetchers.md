# Built-in Fetchers

OmniFetcher comes with 20+ built-in fetchers that handle various data sources.

## Fetcher Overview

| Fetcher | Priority | Description |
|---------|----------|-------------|
| `local_file` | 10 | Local file reading |
| `docx` | 20 | Microsoft Word documents |
| `pptx` | 20 | Microsoft PowerPoint |
| `pdf` | 20 | PDF documents |
| `csv` | 20 | CSV files |
| `audio` | 30 | Audio file metadata |
| `http_json` | 40 | JSON API endpoints |
| `http_auth` | 45 | HTTP with authentication |
| `github` | 50 | GitHub API |
| `http_url` | 50 | Generic HTTP/HTTPS |
| `youtube` | 60 | YouTube videos |
| `rss` | 70 | RSS/Atom feeds |
| `graphql` | 80 | GraphQL endpoints |
| `s3` | 90 | AWS S3 storage |
| `google_drive` | 100 | Google Drive files |
| `notion` | 100 | Notion workspace |
| `jira` | 100 | Atlassian Jira |
| `confluence` | 100 | Atlassian Confluence |
| `slack` | 100 | Slack messaging |

## HTTP & Web Fetchers

### http_url

Generic HTTP/HTTPS fetcher for any web content.

```python
result = await fetcher.fetch("https://example.com/page.html")
```

**Returns:** `HTMLDocument`

### http_json

Specialized JSON API fetcher.

```python
result = await fetcher.fetch("https://api.example.com/users")
```

**Returns:** `JSONData`

### http_auth

HTTP fetcher with authentication support.

```python
fetcher = OmniFetcher(auth={"http_auth": {"type": "bearer", "token": "xxx"}})
result = await fetcher.fetch("https://api.example.com/private")
```

**Returns:** `BaseFetchedData`

### graphql

Execute GraphQL queries.

```python
result = await fetcher.fetch(
    "https://api.example.com/graphql",
    query="{ users { name email } }"
)
```

**Returns:** `GraphQLResponse`

## Document Fetchers

### pdf

Parse PDF documents and extract text, metadata, and images.

```python
result = await fetcher.fetch("file:///path/to/document.pdf")
# or
result = await fetcher.fetch("https://example.com/document.pdf")
```

**Returns:** `PDFDocument`

**Attributes:**
- `text`: Extracted text content
- `page_count`: Number of pages
- `author`, `title`, `subject`, `creator`, `producer`: Metadata
- `images`: Extracted images
- `tags`: Auto-generated tags (`pdf`, `document`, `scanned`)

### docx

Parse Microsoft Word documents.

```python
result = await fetcher.fetch("file:///path/to/document.docx")
```

**Returns:** `DOCXDocument`

**Attributes:**
- `text`: Extracted text content
- `tables`: Table data
- `images`: Embedded images
- `author`, `title`: Document metadata

### pptx

Parse Microsoft PowerPoint presentations.

```python
result = await fetcher.fetch("file:///path/to/presentation.pptx")
```

**Returns:** `PPTXDocument`

**Attributes:**
- `slides`: List of slides with text and images
- `slide_count`: Number of slides
- `title`, `author`: Presentation metadata

### csv

Parse CSV files.

```python
result = await fetcher.fetch("file:///path/to/data.csv")
# or
result = await fetcher.fetch("https://example.com/data.csv")
```

**Returns:** `SpreadsheetDocument`

**Attributes:**
- `sheets`: List of sheets with headers and rows
- `row_count`, `column_count`: Dimensions

## Cloud & Storage Fetchers

### s3

Fetch objects from AWS S3.

```python
result = await fetcher.fetch("s3://bucket-name/path/to/file.json")
```

**Authentication:**
```python
fetcher = OmniFetcher(auth={
    "s3": {
        "type": "aws",
        "aws_access_key_id": "KEY",
        "aws_secret_access_key": "SECRET",
        "aws_region": "us-east-1"
    }
})
```

**Returns:** `BaseFetchedData`

### google_drive

Fetch files from Google Drive.

```python
# By file ID
result = await fetcher.fetch("gdrive://1abc123...")

# By share link
result = await fetcher.fetch("https://drive.google.com/file/d/1abc123/view")
```

**Authentication:** Uses `GOOGLE_APPLICATION_CREDENTIALS` or service account JSON.

**Returns:** Appropriate document type based on file

### notion

Fetch data from Notion workspace.

```python
# Fetch a page
result = await fetcher.fetch("notion://page-id")

# Fetch a database
result = await fetcher.fetch("notion://database-id?type=database")
```

**Authentication:** Set `NOTION_TOKEN` environment variable.

### confluence

Fetch pages and spaces from Confluence.

```python
# Fetch a page
result = await fetcher.fetch("confluence://page-id")

# Fetch a space
result = await fetcher.fetch("confluence://space-key?type=space")
```

**Authentication:** Bearer token via `CONFLUENCE_TOKEN` env var.

### jira

Fetch issues and projects from Jira.

```python
# Fetch an issue
result = await fetcher.fetch("jira://project-key/ISSUE-123")

# Fetch a project
result = await fetcher.fetch("jira://project-key?type=project")
```

**Authentication:** API token via `JIRA_EMAIL` and `JIRA_API_TOKEN` env vars.

### slack

Fetch messages and channel data from Slack.

```python
# Fetch messages from a channel
result = await fetcher.fetch("slack://C12345678?type=channel")

# Fetch a thread
result = await fetcher.fetch("slack://C12345678/thread/TS12345678")
```

**Authentication:** Bot token via `SLACK_BOT_TOKEN` env var.

## Media Fetchers

### youtube

Fetch YouTube video metadata and transcripts.

```python
result = await fetcher.fetch("https://youtube.com/watch?v=dQw4w9WgXcQ")
# or
result = await fetcher.fetch("https://youtu.be/dQw4w9WgXcQ")
```

**Returns:** `YouTubeVideo`

**Attributes:**
- `title`, `description`, `author`: Video metadata
- `duration`: Video length
- `thumbnail`: Thumbnail URL
- `transcript`: Available transcript
- `tags`: Auto-generated tags

### audio

Extract metadata from audio files.

```python
result = await fetcher.fetch("file:///path/to/song.mp3")
result = await fetcher.fetch("https://example.com/podcast.mp3")
```

**Returns:** `AudioDocument`

**Attributes:**
- `duration`: Audio length in seconds
- `format`: Audio format (mp3, wav, etc.)
- `bitrate`: Audio bitrate
- `sample_rate`: Sample rate

### rss

Parse RSS and Atom feeds.

```python
result = await fetcher.fetch("https://blog.example.com/feed.xml")
```

**Returns:** `FeedDocument`

**Attributes:**
- `title`: Feed title
- `entries`: List of feed entries
- `updated`: Last updated timestamp

## Local File Fetcher

### local_file

Read local files of various formats.

```python
# By path
result = await fetcher.fetch("/path/to/data.json")

# With file:// prefix
result = await fetcher.fetch("file:///path/to/document.pdf")
```

**Supported formats:** JSON, CSV, PDF, text, images, audio, video

**Returns:** Appropriate type based on file extension

## GitHub Fetcher

### github

Fetch data from GitHub API.

```python
# User data
result = await fetcher.fetch("github://octocat")

# Repository data
result = await fetcher.fetch("github://octocat/hello-world")

# Issues
result = await fetcher.fetch("github://octocat/hello-world/issues/1")
```

**Authentication:** Bearer token via `GITHUB_TOKEN` env var.

**Returns:** Appropriate GitHub schema based on endpoint
