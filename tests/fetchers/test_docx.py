"""Tests for DOCX fetcher."""


from omni_fetcher.fetchers.docx import DOCXFetcher


class TestDOCXFetcher:
    def test_creation(self):
        """Can create DOCXFetcher."""
        fetcher = DOCXFetcher()
        assert fetcher.name == "docx"
        assert fetcher.priority == 20

    def test_can_handle_docx(self):
        """DOCXFetcher identifies DOCX URLs."""
        assert DOCXFetcher.can_handle("file:///test.docx")
        assert DOCXFetcher.can_handle("https://example.com/file.docx")
        assert DOCXFetcher.can_handle("http://example.com/document.DOCX")
        assert DOCXFetcher.can_handle("file:///path/to/document.docx")

    def test_cannot_handle_non_docx(self):
        """DOCXFetcher rejects non-DOCX URLs."""
        assert not DOCXFetcher.can_handle("file:///test.pdf")
        assert not DOCXFetcher.can_handle("https://example.com/file.xlsx")
        assert not DOCXFetcher.can_handle("https://example.com/page.html")
        assert not DOCXFetcher.can_handle("https://example.com/image.jpg")

    def test_priority(self):
        """DOCXFetcher has correct priority."""
        assert DOCXFetcher.priority == 20
