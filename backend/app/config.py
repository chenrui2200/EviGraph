"""
Configuration Management
Loads configuration from .env file in project root directory
"""

import os
from dotenv import load_dotenv

# Load .env file from project root
# Path: Knowledge EviGraph/.env (relative to backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # If no .env in root, try to load environment variables (for production)
    load_dotenv(override=True)


class Config:
    """Flask configuration class"""

    # Flask configuration
    # SECURITY: SECRET_KEY must be set via environment variable, no default
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable is required. Set it in .env file or environment.")
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'

    # JSON configuration - disable ASCII escaping to display Chinese directly (not as \uXXXX)
    JSON_AS_ASCII = False

    # LLM configuration (unified OpenAI format)
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'http://localhost:11434/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'qwen2.5:32b')
    LLM_MAX_RETRIES = int(os.environ.get('LLM_MAX_RETRIES', '3'))

    # VLM configuration (vision-capable model for image analysis)
    VLM_API_KEY = os.environ.get('VLM_API_KEY')
    VLM_BASE_URL = os.environ.get('VLM_BASE_URL', 'http://localhost:11434/v1')
    VLM_MODEL_NAME = os.environ.get('VLM_MODEL_NAME', 'qwen2.5-vl:7b')

    # Neo4j configuration
    NEO4J_URI = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
    NEO4J_USER = os.environ.get('NEO4J_USER', 'neo4j')
    # SECURITY: NEO4J_PASSWORD must be set via environment variable, no default
    NEO4J_PASSWORD = os.environ.get('NEO4J_PASSWORD')
    if not NEO4J_PASSWORD:
        raise ValueError("NEO4J_PASSWORD environment variable is required. Set it in .env file or environment.")

    # MinerU PDF parsing API
    MINERU_API_URL = os.environ.get('MINERU_API_URL', 'http://192.168.110.126:8188/pdf_parse?parse_method=auto&is_json_md_dump=true')

    # Embedding configuration
    EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'nomic-embed-text')
    EMBEDDING_BASE_URL = os.environ.get('EMBEDDING_BASE_URL', 'http://localhost:11434')
    EMBEDDING_API_KEY = os.environ.get('EMBEDDING_API_KEY')
    EMBEDDING_DIMENSION = int(os.environ.get('EMBEDDING_DIMENSION', '768'))

    # Reranker configuration (SiliconFlow bge-reranker-v2-m3)
    RERANKER_MODEL = os.environ.get('RERANKER_MODEL', 'BAAI/bge-reranker-v2-m3')
    RERANKER_BASE_URL = os.environ.get('RERANKER_BASE_URL', 'https://api.siliconflow.cn/v1/rerank')
    RERANKER_API_KEY = os.environ.get('RERANKER_API_KEY')

    # File upload configuration
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}

    # Text processing configuration
    DEFAULT_CHUNK_SIZE = 1500  # Increased for engineering standards to keep clauses intact
    DEFAULT_CHUNK_OVERLAP = 150  # Balanced overlap for 1500 chunk size
    TESSERACT_CMD = os.environ.get('TESSERACT_CMD', 'tesseract')  # cross-platform: 'tesseract' (Linux) or '/usr/bin/tesseract' or full Windows path
    TESSDATA_DIR = os.path.join(os.path.dirname(__file__), 'tessdata')


    # Report Agent configuration
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))

    # Frontend URL (for generating correct client-side links like /chat/{app_id})
    FRONTEND_URL = os.environ.get('FRONTEND_URL')

    # KB Pipeline callback URL (optional)
    KB_PIPELINE_CALLBACK_URL = os.environ.get('KB_PIPELINE_CALLBACK_URL')

    # MinIO configuration
    MINIO_ENDPOINT = os.environ.get('MINIO_ENDPOINT', 'http://localhost:9000')
    MINIO_ACCESS_KEY = os.environ.get('MINIO_ACCESS_KEY', 'minioadmin')
    MINIO_SECRET_KEY = os.environ.get('MINIO_SECRET_KEY', 'minioadmin')
    MINIO_BUCKET_NAME = os.environ.get('MINIO_BUCKET_NAME', 'knowledge-base')
    MINIO_SECURE = os.environ.get('MINIO_SECURE', 'False').lower() == 'true'

