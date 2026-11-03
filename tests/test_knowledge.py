"""Tests for knowledge base system."""

import pytest
from pathlib import Path


@pytest.fixture
def temp_knowledge_dir(tmp_path):
    """Create a temporary knowledge directory with test files."""
    kdir = tmp_path / "knowledge"
    kdir.mkdir()
    persist = tmp_path / "chroma_db"
    return kdir, persist


@pytest.fixture
def sample_knowledge_files(temp_knowledge_dir):
    """Create sample knowledge files."""
    kdir, persist = temp_knowledge_dir

    (kdir / "python.md").write_text("""---
title: Python Basics
---

## Variables
Python uses dynamic typing. Variables are created by assignment.

## Functions
Define functions with `def` keyword. Use `return` to return values.

## Classes
Classes are defined with `class` keyword. `__init__` is the constructor.
""")

    (kdir / "git.md").write_text("""---
title: Git Commands
---

## Branching
Use `git branch` to list branches and `git checkout -b` to create new ones.

## Commits
Use `git commit -m` to commit with a message. `git add` stages changes.

## Merging
`git merge` combines branches. Resolve conflicts manually.
""")

    return kdir, persist


class TestKnowledgeManager:
    """KnowledgeManager tests."""

    def test_init(self, temp_knowledge_dir):
        kdir, persist = temp_knowledge_dir
        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        assert km.knowledge_dir == kdir

    def test_scan_empty(self, temp_knowledge_dir):
        kdir, persist = temp_knowledge_dir
        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        result = km.scan()
        assert "No knowledge files" in result

    def test_scan_with_files(self, sample_knowledge_files):
        kdir, persist = sample_knowledge_files
        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        result = km.scan()
        assert "Indexed" in result
        assert "2 file" in result

    def test_list_entries_empty(self, temp_knowledge_dir):
        kdir, persist = temp_knowledge_dir
        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        result = km.list_entries()
        assert "empty" in result

    def test_list_entries(self, sample_knowledge_files):
        kdir, persist = sample_knowledge_files
        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        km.scan()
        result = km.list_entries()
        assert "python.md" in result
        assert "git.md" in result

    def test_search_semantic(self, sample_knowledge_files):
        kdir, persist = sample_knowledge_files
        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        km.scan()
        result = km.search("How do I create a function?")
        assert "Functions" in result or "def" in result

    def test_search_no_results(self, sample_knowledge_files):
        kdir, persist = sample_knowledge_files
        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        km.scan()
        result = km.search("rocket science quantum physics")
        # Should return something (even low relevance), not crash
        assert result is not None

    def test_add_entry(self, sample_knowledge_files):
        kdir, persist = sample_knowledge_files
        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        km.scan()
        result = km.add("Docker Guide", "## Containers\nDocker containers are lightweight.")
        assert "Added" in result
        assert "Docker Guide" in result

        # Verify it's searchable
        result = km.search("containers")
        assert "Docker" in result

    def test_chunk_text(self):
        from openloop.knowledge import KnowledgeManager
        text = "## Section 1\n" + "x" * 200 + "\n\n## Section 2\n" + "y" * 200
        chunks = KnowledgeManager._chunk_text(text)
        assert len(chunks) == 2
        assert "Section 1" in chunks[0]
        assert "Section 2" in chunks[1]

    def test_chunk_merges_small(self):
        from openloop.knowledge import KnowledgeManager
        text = "## Big Section\n" + "x" * 200 + "\n\n## Tiny\nhi"
        chunks = KnowledgeManager._chunk_text(text)
        # Tiny section should merge into Big Section
        assert len(chunks) == 1

    def test_parse_frontmatter(self):
        from openloop.knowledge import KnowledgeManager
        text = "---\ntitle: Test\n---\n\n# Body"
        meta, body = KnowledgeManager._parse_frontmatter(text)
        assert meta["title"] == "Test"
        assert "# Body" in body

    def test_parse_frontmatter_none(self):
        from openloop.knowledge import KnowledgeManager
        text = "# Just a heading"
        meta, body = KnowledgeManager._parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_persistence(self, sample_knowledge_files):
        kdir, persist = sample_knowledge_files
        from openloop.knowledge import KnowledgeManager

        km1 = KnowledgeManager(kdir, persist)
        km1.scan()

        # Create a new manager pointing to the same persist dir
        km2 = KnowledgeManager(kdir, persist)
        result = km2.list_entries()
        assert "python.md" in result


class TestKnowledgeToolSchemas:
    """Verify knowledge tools have valid OpenAI schemas."""

    def test_search_knowledge_schema(self):
        from openloop.tools.knowledge import SearchKnowledgeTool
        tool = SearchKnowledgeTool(None)
        schema = tool.schema()
        assert schema["function"]["name"] == "search_knowledge"
        assert "query" in schema["function"]["parameters"]["properties"]

    def test_add_knowledge_schema(self):
        from openloop.tools.knowledge import AddKnowledgeTool
        tool = AddKnowledgeTool(None)
        schema = tool.schema()
        assert schema["function"]["name"] == "add_knowledge"
        assert "title" in schema["function"]["parameters"]["properties"]
        assert "content" in schema["function"]["parameters"]["properties"]

    def test_ingest_schema(self):
        from openloop.tools.knowledge import IngestTool
        tool = IngestTool(None)
        schema = tool.schema()
        assert schema["function"]["name"] == "ingest"
        assert "path" in schema["function"]["parameters"]["properties"]


class TestFileIngestion:
    """Universal file ingestion tests."""

    def test_ingest_not_found(self, temp_knowledge_dir):
        kdir, persist = temp_knowledge_dir
        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        result = km.ingest("/nonexistent/file.pdf")
        assert "not found" in result

    def test_ingest_unsupported(self, temp_knowledge_dir):
        kdir, persist = temp_knowledge_dir
        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        result = km.ingest(__file__)
        assert "unsupported" in result.lower()

    def test_ingest_pdf(self, temp_knowledge_dir):
        kdir, persist = temp_knowledge_dir
        import fitz

        pdf_path = kdir / "test.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text(fitz.Point(50, 50), "PDF content here", fontsize=12)
        doc.save(str(pdf_path))
        doc.close()

        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        km.scan()
        result = km.ingest(str(pdf_path))
        assert "Added" in result

    def test_ingest_txt(self, temp_knowledge_dir):
        kdir, persist = temp_knowledge_dir
        txt_path = kdir / "notes.txt"
        txt_path.write_text("## Notes\nThis is text content.")

        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        km.scan()
        result = km.ingest(str(txt_path))
        assert "Added" in result

    def test_ingest_csv(self, temp_knowledge_dir):
        kdir, persist = temp_knowledge_dir
        csv_path = kdir / "data.csv"
        csv_path.write_text("name,age\nAlice,30\nBob,25")

        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        km.scan()
        result = km.ingest(str(csv_path))
        assert "Added" in result

    def test_ingest_xlsx(self, temp_knowledge_dir):
        kdir, persist = temp_knowledge_dir
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["Name", "Value"])
        ws.append(["X", "100"])
        xlsx_path = kdir / "data.xlsx"
        wb.save(str(xlsx_path))

        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        km.scan()
        result = km.ingest(str(xlsx_path))
        assert "Added" in result

    def test_ingest_html(self, temp_knowledge_dir):
        kdir, persist = temp_knowledge_dir
        html_path = kdir / "page.html"
        html_path.write_text("<h1>Title</h1><p>Some text here.</p>")

        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        km.scan()
        result = km.ingest(str(html_path))
        assert "Added" in result

    def test_ingest_md(self, temp_knowledge_dir):
        kdir, persist = temp_knowledge_dir
        md_path = kdir / "readme.md"
        md_path.write_text("## Readme\nThis is a readme file.")

        from openloop.knowledge import KnowledgeManager
        km = KnowledgeManager(kdir, persist)
        km.scan()
        result = km.ingest(str(md_path))
        assert "Added" in result