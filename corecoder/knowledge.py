"""Knowledge base - semantic search with local embeddings + ChromaDB.

Two-phase architecture:
  Index: knowledge files → chunk → embed → ChromaDB
  Search: query → embed → ChromaDB similarity → results
"""

import json
import re
import yaml
from pathlib import Path

# Collection-level metadata key storing {filename: mtime} so scan() can skip
# the expensive model load + re-index when knowledge files are unchanged.
FINGERPRINT_KEY = "index_fingerprints"


class KnowledgeManager:
    """Manages a local knowledge base with semantic search.

    Uses sentence-transformers for embeddings and ChromaDB for vector storage,
    both running entirely locally with no external service dependencies.
    """

    def __init__(self, knowledge_dir: Path, persist_dir: Path):
        self.knowledge_dir = knowledge_dir
        self.persist_dir = persist_dir
        self._model = None
        self._collection = None

    # -- Public API --------------------------------------------------

    def scan(self) -> str:
        """Scan knowledge directory and index all files into ChromaDB.

        Called once at startup. Re-indexes all files from scratch only when
        the file fingerprints have changed; otherwise skips the expensive
        model load + rebuild.
        """
        if not self.knowledge_dir.exists():
            return "Knowledge directory not found. Create .corecoder/knowledge/ and add .md files."

        files = sorted(self.knowledge_dir.glob("*.md"))
        if not files:
            return "No knowledge files found (.md)."

        collection = self._get_collection()

        # Skip the rebuild when every file is unchanged since last index.
        saved = collection.metadata or {}
        if self._fingerprints_match(files, saved):
            chunks = len(collection.get()["ids"])
            return f"Index up to date ({chunks} chunks from {len(files)} file(s))"

        chunks = []
        metadatas = []
        ids = []

        for f in files:
            raw = f.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(raw)
            file_chunks = self._chunk_text(body)
            for i, chunk in enumerate(file_chunks):
                chunks.append(chunk)
                metadatas.append({
                    "source": str(f.name),
                    "title": meta.get("title", f.stem),
                    "chunk_index": i,
                })
                ids.append(f"{f.stem}_{i}")

        if not chunks:
            return "No content to index."

        # Clear and re-index
        existing = collection.get()
        if existing["ids"]:
            collection.delete(ids=existing["ids"])

        embeddings = self._embed(chunks)
        collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
        self._save_fingerprints(files)

        return f"Indexed {len(chunks)} chunks from {len(files)} file(s)"

    def search(self, query: str, top_k: int = 5) -> str:
        """Semantic search the knowledge base. Returns formatted results."""
        collection = self._get_collection()
        existing = collection.get()
        if not existing["ids"]:
            return "(knowledge base is empty)"

        query_embedding = self._embed([query])
        results = collection.query(query_embeddings=query_embedding, n_results=min(top_k, len(existing["ids"])))

        if not results["ids"][0]:
            return "(no relevant results found)"

        lines = []
        for i in range(len(results["ids"][0])):
            meta = results["metadatas"][0][i]
            doc = results["documents"][0][i]
            dist = results["distances"][0][i] if results.get("distances") else 0
            # Skip results from deleted files
            if not (self.knowledge_dir / meta["source"]).exists():
                continue
            lines.append(f"## {meta['title']} ({meta['source']}, relevance: {1 - dist:.2f})\n{doc}")

        if not lines:
            return "(no relevant results found — source files may have been deleted)"
        return "\n\n---\n\n".join(lines)

    def add(self, title: str, content: str) -> str:
        """Add a new knowledge entry as a file and index it."""
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-zA-Z0-9_-]", "-", title.lower())[:50]
        filepath = self.knowledge_dir / f"{slug}.md"

        frontmatter = f"---\ntitle: {title}\n---\n"
        filepath.write_text(frontmatter + content, encoding="utf-8")

        # Index the new file
        chunks = self._chunk_text(content)
        collection = self._get_collection()
        ids = []
        embeddings = []
        metadatas = []
        docs = []

        for i, chunk in enumerate(chunks):
            chunk_id = f"{slug}_{i}"
            ids.append(chunk_id)
            docs.append(chunk)
            metadatas.append({"source": str(filepath.name), "title": title, "chunk_index": i})

        if chunks:
            embeddings = self._embed(chunks)
            collection.add(ids=ids, embeddings=embeddings, documents=docs, metadatas=metadatas)

        # Keep fingerprint in sync so the next startup scan() can skip the rebuild.
        self._save_fingerprints(sorted(self.knowledge_dir.glob("*.md")))
        return f"Added '{title}' ({len(chunks)} chunks) to knowledge base."

    def ingest(self, file_path: str) -> str:
        """Convert a file to markdown and index it into the knowledge base.

        Supported: PDF, DOCX, CSV, XLSX, HTML, TXT, MD.
        """
        path = Path(file_path)
        if not path.exists():
            return f"Error: file not found: {file_path}"

        ext = path.suffix.lower()
        handlers = {
            ".pdf": self._ingest_pdf,
            ".docx": self._ingest_docx,
            ".csv": self._ingest_csv,
            ".xlsx": self._ingest_xlsx,
            ".html": self._ingest_html,
            ".htm": self._ingest_html,
            ".txt": self._ingest_text,
            ".md": self._ingest_text,
        }
        handler = handlers.get(ext)
        if handler is None:
            return f"Error: unsupported format '{ext}'. Supported: {', '.join(sorted(handlers))}"

        try:
            content = handler(path)
        except Exception as e:
            return f"Error converting {path.name}: {e}"

        if not content.strip():
            return "Error: no text extracted from file"

        title = path.stem
        return self.add(title, content)

    def _ingest_pdf(self, path: Path) -> str:
        import fitz
        doc = fitz.open(str(path))
        parts = []
        for page in doc:
            text = page.get_text("text")
            if text:
                parts.append(text)
        doc.close()
        return "\n\n".join(parts)

    def _ingest_docx(self, path: Path) -> str:
        from docx import Document
        doc = Document(str(path))
        parts = []
        for para in doc.paragraphs:
            if para.style.name.startswith("Heading"):
                level = para.style.name.split()[-1]
                prefix = "#" * max(1, min(6, int(level) if level.isdigit() else 2))
                parts.append(f"\n{prefix} {para.text}\n")
            elif para.text.strip():
                parts.append(para.text)
        return "\n\n".join(parts)

    def _ingest_csv(self, path: Path) -> str:
        import csv, io
        text = path.read_text(encoding="utf-8")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return ""
        # Convert to markdown table
        header = rows[0]
        lines = ["| " + " | ".join(header) + " |",
                 "| " + " | ".join("---" for _ in header) + " |"]
        for row in rows[1:]:
            padded = row + [""] * (len(header) - len(row))
            lines.append("| " + " | ".join(padded[:len(header)]) + " |")
        return "\n".join(lines)

    def _ingest_xlsx(self, path: Path) -> str:
        from openpyxl import load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        parts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            parts.append(f"## {sheet_name}\n")
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            header = [str(c or "") for c in rows[0]]
            parts.append("| " + " | ".join(header) + " |")
            parts.append("| " + " | ".join("---" for _ in header) + " |")
            for row in rows[1:]:
                cells = [str(c or "") for c in row]
                padded = cells + [""] * (len(header) - len(cells))
                parts.append("| " + " | ".join(padded[:len(header)]) + " |")
            parts.append("")
        wb.close()
        return "\n".join(parts)

    def _ingest_html(self, path: Path) -> str:
        from markdownify import markdownify as md
        html = path.read_text(encoding="utf-8")
        return md(html, heading_style="ATX")

    def _ingest_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def list_entries(self) -> str:
        """List all knowledge entries that still exist on disk."""
        collection = self._get_collection()
        existing = collection.get()
        if not existing["ids"]:
            return "(knowledge base is empty)"

        seen = {}
        for meta in existing["metadatas"]:
            src = meta["source"]
            if src not in seen:
                filepath = self.knowledge_dir / src
                if filepath.exists():
                    seen[src] = meta

        if not seen:
            return "(knowledge base is empty — all source files deleted)"

        lines = []
        for src, meta in sorted(seen.items()):
            lines.append(f"  {src}: {meta.get('title', src)}")
        return "\n".join(lines)

    def remove_entry(self, name: str) -> str:
        """Remove a knowledge entry by file name. Deletes the .md file and index."""
        if not name.endswith(".md"):
            name = name + ".md"

        filepath = self.knowledge_dir / name
        if not filepath.exists():
            matches = [f for f in self.knowledge_dir.glob("*.md")
                      if name.lower() in f.name.lower()]
            if not matches:
                return f"Error: no knowledge entry matching '{name}' found"
            if len(matches) > 1:
                found = ", ".join(f.name for f in matches)
                return f"Multiple matches: {found}. Please be more specific."
            filepath = matches[0]

        filepath.unlink()

        # Remove from ChromaDB
        collection = self._get_collection()
        existing = collection.get()
        if existing["ids"]:
            to_delete = []
            for i, meta in enumerate(existing["metadatas"]):
                if meta["source"] == filepath.name:
                    to_delete.append(existing["ids"][i])
            if to_delete:
                collection.delete(ids=to_delete)

        # Keep fingerprint in sync so the next startup scan() can skip the rebuild.
        self._save_fingerprints(sorted(self.knowledge_dir.glob("*.md")))
        return f"Removed '{filepath.name}' from knowledge base"

    # -- Internal: fingerprints ---------------------------------------

    @staticmethod
    def _fingerprints_match(files: list[Path], metadata: dict) -> bool:
        """True when every knowledge file's mtime matches the stored fingerprint."""
        saved_raw = metadata.get(FINGERPRINT_KEY)
        if not saved_raw:
            return False
        try:
            saved = json.loads(saved_raw)
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(saved, dict):
            return False
        return all(str(f.name) in saved and saved[str(f.name)] == f.stat().st_mtime for f in files)

    def _save_fingerprints(self, files: list[Path]):
        """Record each knowledge file's mtime so scan() can skip unchanged files."""
        fp = {f.name: f.stat().st_mtime for f in files}
        collection = self._get_collection()
        # Pass only the fingerprint key: modify() rejects re-specifying
        # immutable collection settings like 'hnsw:space' (distance function).
        collection.modify(metadata={FINGERPRINT_KEY: json.dumps(fp)})

    # -- Internal: chunking ------------------------------------------

    @staticmethod
    def _chunk_text(text: str, min_size: int = 100) -> list[str]:
        """Split text by ## headings. Merge small chunks into previous."""
        # Split on ## headings (at start of line)
        sections = []
        current = []
        for line in text.split("\n"):
            if line.startswith("## "):
                if current:
                    sections.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            sections.append("\n".join(current))

        chunks = []
        for sec in sections:
            sec = sec.strip()
            if not sec:
                continue
            if chunks and len(sec) < min_size:
                chunks[-1] += "\n\n" + sec
            else:
                chunks.append(sec)
        return chunks

    # -- Internal: embedding -----------------------------------------

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        model = self._load_model()
        return model.encode(texts, normalize_embeddings=True).tolist()

    def _load_model(self):
        """Lazy-load the embedding model on first use."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("BAAI/bge-small-zh")
        return self._model

    # -- Internal: ChromaDB ------------------------------------------

    def _get_collection(self):
        """Get or create the ChromaDB collection."""
        if self._collection is None:
            import chromadb
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(self.persist_dir))
            self._collection = client.get_or_create_collection(
                name="knowledge",
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    # -- Internal: frontmatter ---------------------------------------

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[dict, str]:
        """Parse YAML frontmatter from markdown text."""
        if not text.startswith("---"):
            return {}, text
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text
        try:
            meta = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            meta = {}
        return meta, parts[2].strip()