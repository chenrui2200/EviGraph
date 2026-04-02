"""
API Routes Module
"""

from flask import Blueprint

graph_bp = Blueprint('graph', __name__)
ai_app_bp = Blueprint('ai_app', __name__)

from . import graph  # noqa: E402, F401
from . import ai_app  # noqa: E402, F401
from . import report  # noqa: E402, F401

# Export report_bp from report.py (it registers routes on this instance)
from .report import report_bp  # noqa: E402, F401

