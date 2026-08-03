"""Read-only, project-scoped tender evidence package.

Submodules are intentionally not imported eagerly. The database ingestion and
repository layers must remain usable by the existing FastAPI runtime even when
the optional MCP/LangGraph dependency set is installed in a separate worker.
"""
