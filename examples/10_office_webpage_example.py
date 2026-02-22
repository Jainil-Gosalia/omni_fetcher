"""Office documents and WebPage examples for OmniFetcher v0.4.0."""

from omni_fetcher.schemas.documents import (
    DOCXDocument,
    PPTXDocument,
    SlideDocument,
    WebPageDocument,
)
from omni_fetcher.schemas.atomics import (
    TextDocument,
    ImageDocument,
    SpreadsheetDocument,
    SheetData,
    TextFormat,
)


def main():
    print("=" * 60)
    print("Office Documents & WebPage Examples (v0.4.0)")
    print("=" * 60)

    # DOCXDocument example
    doc = DOCXDocument(
        text=TextDocument(
            source_uri="file:///docs/report.docx",
            content="Annual Report 2024\n\nIntroduction...",
            format=TextFormat.PLAIN,
        ),
        images=[
            ImageDocument(
                source_uri="file:///docs/chart.png",
                format="PNG",
                width=800,
                height=600,
            )
        ],
        tables=[
            SpreadsheetDocument(
                source_uri="file:///docs/table1.xlsx",
                sheets=[
                    SheetData(
                        name="Data",
                        headers=["Month", "Revenue"],
                        rows=[["Jan", 10000], ["Feb", 15000]],
                        row_count=2,
                        col_count=2,
                    )
                ],
                format="xlsx",
                sheet_count=1,
            )
        ],
        page_count=10,
        author="John Doe",
        title="Annual Report 2024",
    )

    print("\n1. DOCXDocument")
    print(f"   Title: {doc.title}")
    print(f"   Author: {doc.author}")
    print(f"   Pages: {doc.page_count}")
    print(f"   Images: {len(doc.images)}")
    print(f"   Tables: {len(doc.tables)}")

    # PPTXDocument example
    pptx = PPTXDocument(
        slides=[
            SlideDocument(
                slide_number=1,
                title="Introduction",
                text=TextDocument(
                    source_uri="file:///slides/intro.pptx",
                    content="Welcome to the presentation",
                    format=TextFormat.PLAIN,
                ),
            ),
            SlideDocument(
                slide_number=2,
                title="Agenda",
                text=TextDocument(
                    source_uri="file:///slides/intro.pptx",
                    content="1. Overview\n2. Details\n3. Q&A",
                    format=TextFormat.PLAIN,
                ),
            ),
        ],
        slide_count=2,
        title="Company Presentation",
    )

    print("\n2. PPTXDocument")
    print(f"   Title: {pptx.title}")
    print(f"   Slides: {pptx.slide_count}")
    for slide in pptx.slides:
        print(f"   - Slide {slide.slide_number}: {slide.title}")

    # WebPageDocument example
    webpage = WebPageDocument(
        url="https://example.com/article",
        title="Example Article",
        body=TextDocument(
            source_uri="https://example.com/article",
            content="This is the clean extracted article text...",
            format=TextFormat.MARKDOWN,
        ),
        images=[
            ImageDocument(
                source_uri="https://example.com/hero.jpg",
                format="JPEG",
                width=1200,
                height=630,
            )
        ],
        author="Jane Smith",
        language="en",
        status_code=200,
    )

    print("\n3. WebPageDocument")
    print(f"   URL: {webpage.url}")
    print(f"   Title: {webpage.title}")
    print(f"   Author: {webpage.author}")
    print(f"   Language: {webpage.language}")
    print(f"   Status: {webpage.status_code}")
    print(f"   Images: {len(webpage.images)}")
    print(f"   Body format: {webpage.body.format}")

    print("\n" + "=" * 60)
    print("v0.4.0: DOCX, PPTX, WebPage fetchers ready!")
    print("Install: pip install omni_fetcher[office,web]")


if __name__ == "__main__":
    main()
