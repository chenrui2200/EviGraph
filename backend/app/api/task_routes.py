"""
Task Management API Routes
Handles task status query and real-time events (SSE)
"""

import json
import queue
from flask import request, jsonify, Response

from . import graph_bp
from ..utils.logger import get_logger
from ..utils.api_utils import api_handler, success_response
from ..models.task import TaskManager

logger = get_logger('mirofish.api')


@graph_bp.route('/task/<task_id>', methods=['GET'])
@api_handler
def get_task(task_id: str):
    """
    Query task status
    """
    task = TaskManager().get_task(task_id)

    if not task:
        return jsonify({
            "success": False,
            "error": f"Task does not exist: {task_id}"
        }), 404

    return jsonify({
        "success": True,
        "data": task.to_dict()
    })


@graph_bp.route('/task/<task_id>/events', methods=['GET'])
def task_events(task_id: str):
    """
    Server-Sent Events (SSE) for real-time task updates.
    Provides incremental logs and status changes without polling full JSON.
    """
    task_manager = TaskManager()
    task = task_manager.get_task(task_id)

    if not task:
        return jsonify({"success": False, "error": "Task not found"}), 404

    def event_stream():
        # 1. First, send current full state once
        yield f"data: {json.dumps({'type': 'init', 'data': task.to_dict()})}\n\n"

        # 2. Subscribe to new events
        q = task_manager.subscribe(task_id)
        try:
            while True:
                # Wait for next event with timeout to prevent ghost connections
                try:
                    event_data = q.get(timeout=30.0)
                    yield f"data: {json.dumps({'type': 'update', 'data': event_data}, ensure_ascii=False)}\n\n"

                    # Close connection if task is finished
                    if event_data.get('status') in ['completed', 'failed']:
                        break
                except queue.Empty:
                    # Send keep-alive ping
                    yield ": ping\n\n"
        finally:
            task_manager.unsubscribe(task_id, q)

    return Response(event_stream(), mimetype='text/event-stream')


@graph_bp.route('/tasks', methods=['GET'])
@api_handler
def list_tasks():
    """
    List all tasks
    """
    tasks = TaskManager().list_tasks()

    return jsonify({
        "success": True,
        "data": [t.to_dict() for t in tasks],
        "count": len(tasks)
    })
