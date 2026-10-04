"""KDP-Intelligence-Engine: research, write, fact-check and typeset Italian
non-fiction for Amazon KDP.

Four agents share one state, wired as a LangGraph graph:

    Analyst ─► Review Miner ─► Outline ─► Writer ⇄ Fact-Checker ─► Typesetter

Every number in the book has to come from a document in the vector store, and
the fact-checker proves it with code before a model is asked anything. See
``docs/intel-architecture.md``.
"""

__version__ = "0.1.0"
