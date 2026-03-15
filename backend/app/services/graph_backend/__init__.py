"""
Graph backend abstraction: Zep Cloud or Neo4j.

Use get_graph_backend() to obtain the configured implementation (from GRAPH_BACKEND env).
"""

from .base import IGraphBackend, get_graph_backend
from .zep_backend import ZepGraphBackend
from .neo4j_backend import Neo4jGraphBackend

__all__ = [
    "IGraphBackend",
    "get_graph_backend",
    "ZepGraphBackend",
    "Neo4jGraphBackend",
]
