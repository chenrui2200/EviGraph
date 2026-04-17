"""
API Routes Module
"""

from flask import Blueprint

graph_bp = Blueprint('graph', __name__)
ai_app_bp = Blueprint('ai_app', __name__)

from . import graph  # noqa: E402, F401
from . import ai_app  # noqa: E402, F401
from . import report  # noqa: E402, F401
from . import project_routes  # noqa: E402, F401
from . import task_routes  # noqa: E402, F401
from . import entity_routes  # noqa: E402, F401
from . import ai_qa_routes  # noqa: E402, F401
from . import ontology_routes  # noqa: E402, F401
from . import chunk_routes  # noqa: E402, F401
from . import graph_ops_routes  # noqa: E402, F401
from . import kb_pipeline_routes  # noqa: E402, F401

# Export report_bp from report.py (it registers routes on this instance)
from .report import report_bp  # noqa: E402, F401

