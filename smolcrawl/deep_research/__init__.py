"""Deep Research Tool for Open WebUI.

Provides iterative RAG research with LLM-guided domain discovery,
web search exploration, and chain-of-thought synthesis.

Entry point: deep_research_tool.py (parent directory) imports
the Tools class from this package.
"""

from .tool import Tools

__all__ = ["Tools"]
