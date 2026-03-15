"""
Configuration management.
Loads from project root .env file.
"""

import os
from dotenv import load_dotenv

# Load .env from project root (MiroFish/.env relative to backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    load_dotenv(override=True)


class Config:
    """Flask config."""

    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'

    JSON_AS_ASCII = False

    # LLM (OpenAI-compatible)
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    # Embedding model (e.g. for Neo4j entity vectors)
    EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'text-embedding-3-small')
    
    # Graph backend: "zep" (Zep Cloud) or "neo4j"
    GRAPH_BACKEND = os.environ.get('GRAPH_BACKEND', 'zep').lower().strip()
    
    # Zep Cloud (when GRAPH_BACKEND=zep)
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # Neo4j (when GRAPH_BACKEND=neo4j)
    NEO4J_URI = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
    NEO4J_USER = os.environ.get('NEO4J_USER', 'neo4j')
    NEO4J_PASSWORD = os.environ.get('NEO4J_PASSWORD', '')
    
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}

    DEFAULT_CHUNK_SIZE = 500
    DEFAULT_CHUNK_OVERLAP = 50

    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')

    # OASIS available actions
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))

    @classmethod
    def validate(cls):
        """Validate required config."""
        errors = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY is not set")
        backend = cls.GRAPH_BACKEND
        if backend == 'zep':
            if not cls.ZEP_API_KEY:
                errors.append("ZEP_API_KEY is not set (GRAPH_BACKEND=zep)")
        elif backend == 'neo4j':
            if not cls.NEO4J_URI:
                errors.append("NEO4J_URI is not set (GRAPH_BACKEND=neo4j)")
            if not cls.NEO4J_PASSWORD:
                errors.append("NEO4J_PASSWORD is not set (GRAPH_BACKEND=neo4j)")
        else:
            errors.append(f"Invalid GRAPH_BACKEND: {backend}. Use zep or neo4j.")
        return errors

