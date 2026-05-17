"""
Knowledge EviGraph Backend - Flask Application Factory
"""

import os
import warnings

# Suppress multiprocessing resource_tracker warnings (from third-party libraries like transformers)
# Must be set before all other imports
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Flask application factory function"""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Configure JSON encoding: ensure Chinese displays directly (not as \uXXXX)
    # Flask >= 2.3 uses app.json.ensure_ascii, older versions use JSON_AS_ASCII config
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False

    # Setup logging
    logger = setup_logger('mirofish')

    # Only print startup info in reloader subprocess (avoid printing twice in debug mode)
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process

    if should_log_startup:
        logger.info("=" * 50)
        logger.info("Knowledge EviGraph Backend starting...")
        logger.info("=" * 50)

    # Enable CORS
    CORS(app, resources={r"/*": {"origins": "*"}})

    # --- Initialize Neo4jStorage singleton (DI via app.extensions) ---
    from .storage import Neo4jStorage
    try:
        neo4j_storage = Neo4jStorage()
        app.extensions['neo4j_storage'] = neo4j_storage
        if should_log_startup:
            logger.info("Neo4jStorage initialized (connected to %s)", Config.NEO4J_URI)
    except Exception as e:
        logger.error("Neo4jStorage initialization failed: %s", e)
        # Store None so endpoints can return 503 gracefully
        app.extensions['neo4j_storage'] = None

    # Request logging middleware
    @app.before_request
    def log_request():
        logger = get_logger('mirofish.request')
        logger.debug(f"Request: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"Request body: {request.get_json(silent=True)}")

    @app.after_request
    def log_response(response):
        logger = get_logger('mirofish.request')
        logger.debug(f"Response: {response.status_code}")
        return response

    # Register blueprints
    from .api import graph_bp, ai_app_bp, report_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(ai_app_bp, url_prefix='/api/ai-app')
    app.register_blueprint(report_bp, url_prefix='/api/report')

    # Initialize Swagger (auto-generates API docs from docstrings)
    from flasgger import Swagger
    Swagger(app, template={
        "info": {
            "title": "Knowledge EviGraph API",
            "description": "基于 Neo4j 和 LLM 的知识图谱构建与管理系统 API",
            "version": "1.0.0",
            "contact": {
                "name": "EviGraph Team",
                "url": "https://github.com/chenrui2200/EviGraph"
            }
        },
        "basePath": "/api",
        "swagger_ui_config": {
            "deepLinking": True,
            "displayOperationId": False,
            "docExpansion": "list"
        }
    })

    # Health check
    @app.route('/health')
    @app.route('/api/health')
    def health():
        """
        服务健康检查
        检查后端服务及其依赖（Neo4j、Embedding、LLM）的运行状态。
        ---
        tags:
          - System / 系统
        responses:
          200:
            description: 服务正常（依赖状态在响应体中）
            schema:
              type: object
              properties:
                status:
                  type: string
                  example: ok
                service:
                  type: string
                dependencies:
                  type: object
                  properties:
                    neo4j:
                      type: object
                    embedding:
                      type: object
                    llm:
                      type: object
        """
        from .storage.embedding_service import EmbeddingService

        # Check Neo4j
        neo4j_status = "ok"
        neo4j_error = None
        storage = app.extensions.get('neo4j_storage')
        if not storage:
            neo4j_status = "not_initialized"
        else:
            try:
                # Simple query to check connection
                with storage.driver.session() as session:
                    session.run("RETURN 1").single()
            except Exception as e:
                neo4j_status = "error"
                neo4j_error = str(e)

        # Check Embedding (non-blocking: just verify service is instantiable, skip actual HTTP call)
        try:
            _es = EmbeddingService()
            embedding_provider = _es.provider
            embedding_url = _es._embed_url
            embedding_status = "ok"  # Service is configured; actual health checked on /health endpoint
        except Exception as e:
            embedding_status = f"error: {e}"
            embedding_provider = None
            embedding_url = None

        # Check LLM (non-blocking: just verify client can be instantiated; skip actual chat call)
        llm_status = "ok"
        llm_error = None
        llm_provider = None
        llm_model = None
        if Config.LLM_API_KEY:
            try:
                from .utils.llm_client import LLMClient
                llm_client = LLMClient()
                llm_provider = "ollama" if llm_client._is_ollama() else "openai-compatible"
                llm_model = llm_client.model
                # NOTE: Skip actual chat() call to avoid blocking startup if LLM is unavailable
            except Exception as e:
                llm_status = "error"
                llm_error = str(e)
        else:
            llm_status = "not_configured"

        return {
            'status': 'ok',
            'service': 'Knowledge EviGraph Backend',
            'dependencies': {
                'neo4j': {
                    'status': neo4j_status,
                    'uri': Config.NEO4J_URI,
                    'error': neo4j_error
                },
                'embedding': {
                    'status': embedding_status,
                    'model': Config.EMBEDDING_MODEL,
                    'provider': embedding_provider,
                    'url': embedding_url
                },
                'llm': {
                    'status': llm_status,
                    'model': llm_model,
                    'provider': llm_provider,
                    'error': llm_error
                }
            }
        }

    if should_log_startup:
        logger.info("Knowledge EviGraph Backend startup complete")

    # Startup: auto-cleanup old completed tasks (7 days old)
    try:
        from .models.task import TaskManager
        tm = TaskManager()
        removed = tm.cleanup_old_tasks(max_age_hours=168)  # 7 days
        if removed > 0 and should_log_startup:
            logger.info(f"Startup task cleanup: removed {removed} old completed tasks")
    except Exception as e:
        logger.warning(f"Startup task cleanup skipped: {e}")

    return app
