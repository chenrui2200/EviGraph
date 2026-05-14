"""
KB Pipeline API Routes
自动化知识库构建流水线接口
"""

import json
import time
from flask import request, jsonify, Response

from . import graph_bp
from ..utils.logger import get_logger
from ..utils.api_utils import api_handler, success_response
from ..utils.minio_client import list_pdf_objects
from ..models.kb_pipeline import KbPipelineManager, KbPipeline, PipelineStageStatus
from ..models.project import ProjectManager
from ..models.task import TaskManager
from ..services.kb_pipeline_runner import start_pipeline_runner

logger = get_logger('mirofish.api')


@graph_bp.route('/kb-pipeline/<pipeline_id>/retry-graph-building', methods=['POST'])
@api_handler
def retry_graph_building(pipeline_id: str):
    """
    ---
    post:
      summary: 重试图谱构建阶段
      description: 重新执行指定 Pipeline 的图谱构建阶段，重置后续阶段为 pending。
      tags:
        - KB Pipeline / 知识库流水线
      parameters:
        - name: pipeline_id
          in: path
          type: string
          required: true
          description: Pipeline ID
      responses:
        200:
          description: 重试成功，返回更新后的 Pipeline 状态
          schema:
            type: object
        400:
          description: Project 尚未创建
        404:
          description: Pipeline 不存在
    """
    pipeline = KbPipelineManager.get(pipeline_id)
    if not pipeline:
        return jsonify({"success": False, "error": "Pipeline 不存在"}), 404

    project_id = pipeline.project_id
    if not project_id:
        return jsonify({"success": False, "error": "Project 尚未创建"}), 400

    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({"success": False, "error": "Project 不存在"}), 404

    from .graph import _get_storage, _start_build_worker
    storage = _get_storage()
    task_manager = TaskManager()

    task_id = task_manager.create_task(
        task_type="graph_build",
        metadata={"project_id": project_id}
    )
    project.graph_build_task_id = task_id
    ProjectManager.save_project(project)
    _start_build_worker(project_id, task_id, storage, force=False)

    # Update graph_building stage
    stage = next((s for s in pipeline.stages if s.name == "graph_building"), None)
    graph_idx = 0
    if stage:
        graph_idx = pipeline.stages.index(stage)
        stage.status = PipelineStageStatus.PROCESSING
        stage.message = "重新构建中..."
        stage.completed_at = None
        if not stage.result:
            stage.result = {}
        stage.result["task_id"] = task_id

    # Reset subsequent stages to pending
    if pipeline.status in (PipelineStageStatus.COMPLETED, PipelineStageStatus.FAILED):
        pipeline.status = PipelineStageStatus.PROCESSING
        pipeline.error = None
    pipeline.current_stage_index = graph_idx
    for s in pipeline.stages[graph_idx + 1:]:
        s.status = PipelineStageStatus.PENDING
        s.message = ""
        s.result = {}
        s.completed_at = None
        s.link = None

    KbPipelineManager.save(pipeline)
    start_pipeline_runner(pipeline)
    logger.info(f"Pipeline {pipeline_id} graph_building retried with task {task_id}")
    return success_response(data=pipeline.to_dict())


@graph_bp.route('/kb-pipeline/minio-files', methods=['GET'])
@api_handler
def get_minio_files():
    """
    ---
    get:
      summary: 列出 MinIO 中的 PDF 文件
      description: 获取 MinIO 存储桶中所有 PDF 文件列表，支持前缀过滤。
      tags:
        - KB Pipeline / 知识库流水线
      parameters:
        - name: prefix
          in: query
          type: string
          default: ""
          description: 文件前缀过滤
      responses:
        200:
          description: 文件列表获取成功
          schema:
            type: object
            properties:
              success:
                type: boolean
              data:
                type: array
    """
    prefix = request.args.get('prefix', '')
    files = list_pdf_objects(prefix=prefix, recursive=True)
    return success_response(data=files)


@graph_bp.route('/kb-pipeline/start', methods=['POST'])
@api_handler
def start_kb_pipeline():
    """
    ---
    post:
      summary: 启动 KB Pipeline
      description: 基于 MinIO 中的 PDF 文件启动自动化知识库构建流水线。
      tags:
        - KB Pipeline / 知识库流水线
      parameters:
        - name: body
          in: body
          required: true
          schema:
            type: object
            required:
              - minio_object
            properties:
              minio_object:
                type: string
                description: MinIO 文件路径
              target_app_id:
                type: string
                description: 目标 AI 应用 ID（可选）
      responses:
        200:
          description: Pipeline 启动成功
          schema:
            type: object
        400:
          description: 缺少 minio_object 参数
    """
    data = request.get_json(silent=True) or {}
    minio_object = data.get('minio_object')
    if not minio_object:
        return jsonify({"success": False, "error": "缺少 minio_object 参数"}), 400

    target_app_id = data.get('target_app_id')
    pipeline = KbPipeline.create_default(minio_object=minio_object, target_app_id=target_app_id)
    KbPipelineManager.save(pipeline)

    start_pipeline_runner(pipeline)
    logger.info(f"KB Pipeline started: {pipeline.pipeline_id} for {minio_object}, target_app_id={target_app_id}")
    return success_response(data=pipeline.to_dict())


@graph_bp.route('/kb-pipeline/list', methods=['GET'])
@api_handler
def list_kb_pipelines():
    """
    ---
    get:
      summary: 获取 KB Pipeline 列表
      description: 列出所有已创建的 KB Pipeline。
      tags:
        - KB Pipeline / 知识库流水线
      parameters:
        - name: limit
          in: query
          type: integer
          default: 100
          description: 返回数量上限
      responses:
        200:
          description: 列表获取成功
          schema:
            type: object
            properties:
              success:
                type: boolean
              data:
                type: array
    """
    limit = request.args.get('limit', 100, type=int)
    pipelines = KbPipelineManager.list_all(limit=limit)
    return success_response(data=[p.to_dict() for p in pipelines])


@graph_bp.route('/kb-pipeline/<pipeline_id>', methods=['GET'])
@api_handler
def get_kb_pipeline(pipeline_id: str):
    """
    ---
    get:
      summary: 获取指定 Pipeline 状态
      description: 获取单个 KB Pipeline 的完整状态和各阶段进度。
      tags:
        - KB Pipeline / 知识库流水线
      parameters:
        - name: pipeline_id
          in: path
          type: string
          required: true
          description: Pipeline ID
      responses:
        200:
          description: 获取成功
          schema:
            type: object
        404:
          description: Pipeline 不存在
    """
    pipeline = KbPipelineManager.get(pipeline_id)
    if not pipeline:
        return jsonify({"success": False, "error": "Pipeline 不存在"}), 404
    return success_response(data=pipeline.to_dict())


@graph_bp.route('/kb-pipeline/<pipeline_id>/events', methods=['GET'])
def kb_pipeline_events(pipeline_id: str):
    """
    ---
    get:
      summary: Pipeline 实时事件流 (SSE)
      description: |
        Server-Sent Events 实时推送 Pipeline 阶段更新。
        轮询文件变更，直到 Pipeline 完成或失败。
      tags:
        - KB Pipeline / 知识库流水线
      parameters:
        - name: pipeline_id
          in: path
          type: string
          required: true
          description: Pipeline ID
      produces:
        - text/event-stream
      responses:
        200:
          description: SSE 事件流
        404:
          description: Pipeline 不存在
    """
    pipeline = KbPipelineManager.get(pipeline_id)
    if not pipeline:
        return jsonify({"success": False, "error": "Pipeline 不存在"}), 404

    def event_stream():
        last_dict = pipeline.to_dict()
        yield f"data: {json.dumps({'type': 'init', 'data': last_dict}, ensure_ascii=False)}\n\n"

        # Poll file changes until end state
        while last_dict.get('status') not in ('completed', 'failed'):
            time.sleep(2)
            p = KbPipelineManager.get(pipeline_id)
            if not p:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Pipeline 丢失'}, ensure_ascii=False)}\n\n"
                break
            current_dict = p.to_dict()
            if current_dict != last_dict:
                last_dict = current_dict
                yield f"data: {json.dumps({'type': 'update', 'data': current_dict}, ensure_ascii=False)}\n\n"
            # Timeout protection controlled by client
            yield ": ping\n\n"

    return Response(event_stream(), mimetype='text/event-stream')
