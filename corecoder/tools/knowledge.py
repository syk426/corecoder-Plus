"""Knowledge tools - semantic search and add entries to the knowledge base."""

from .base import Tool


class SearchKnowledgeTool(Tool):
    """Tool to search the knowledge base semantically."""

    name = "search_knowledge"
    description = (
        "Search the knowledge base for relevant information. "
        "Returns document chunks ranked by semantic similarity. "
        "Use this when you need to find reference material, documentation, or stored knowledge."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query in natural language",
            },
        },
        "required": ["query"],
    }

    def __init__(self, knowledge_manager):
        self.knowledge_manager = knowledge_manager

    def execute(self, query: str) -> str:
        return self.knowledge_manager.search(query)


class AddKnowledgeTool(Tool):
    """Tool to add an entry to the knowledge base."""

    name = "add_knowledge"
    description = (
        "Add a new entry to the knowledge base. "
        "The entry will be saved as a file and indexed for semantic search. "
        "Use this to persist important information for future reference."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title of the knowledge entry",
            },
            "content": {
                "type": "string",
                "description": "Content of the knowledge entry (markdown)",
            },
        },
        "required": ["title", "content"],
    }

    def __init__(self, knowledge_manager):
        self.knowledge_manager = knowledge_manager

    def execute(self, title: str, content: str) -> str:
        return self.knowledge_manager.add(title, content)


class IngestTool(Tool):
    """Tool to ingest a file into the knowledge base.

    Supports PDF, DOCX, CSV, XLSX, HTML, TXT, MD.
    """

    name = "ingest"
    description = (
        "Convert a file to text and index it into the knowledge base. "
        "Supports PDF, DOCX, CSV, XLSX, HTML, TXT, MD. "
        "Extracts text, converts to markdown, and indexes for semantic search."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to ingest (PDF, DOCX, CSV, XLSX, HTML, TXT, MD)",
            },
        },
        "required": ["path"],
    }

    def __init__(self, knowledge_manager):
        self.knowledge_manager = knowledge_manager

    def execute(self, path: str) -> str:
        return self.knowledge_manager.ingest(path)