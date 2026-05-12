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
    """重新执行 Pipeline 的图谱构建阶段"""
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

    # 更新 graph_building stage
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

    # 重置后续阶段为 pending，回退 pipeline 状态
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
    """列出 MinIO 中的 PDF 文件"""
    prefix = request.args.get('prefix', '')
    files = list_pdf_objects(prefix=prefix, recursive=True)
    return success_response(data=files)


@graph_bp.route('/kb-pipeline/start', methods=['POST'])
@api_handler
def start_kb_pipeline():
    """启动一个新的 KB Pipeline"""
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
    """列出所有 KB Pipeline"""
    limit = request.args.get('limit', 100, type=int)
    pipelines = KbPipelineManager.list_all(limit=limit)
    return success_response(data=[p.to_dict() for p in pipelines])


@graph_bp.route('/kb-pipeline/<pipeline_id>', methods=['GET'])
@api_handler
def get_kb_pipeline(pipeline_id: str):
    """获取单个 Pipeline 状态"""
    pipeline = KbPipelineManager.get(pipeline_id)
    if not pipeline:
        return jsonify({"success": False, "error": "Pipeline 不存在"}), 404
    return success_response(data=pipeline.to_dict())


@graph_bp.route('/kb-pipeline/<pipeline_id>/events', methods=['GET'])
def kb_pipeline_events(pipeline_id: str):
    """SSE 实时推送 Pipeline 阶段更新"""
    pipeline = KbPipelineManager.get(pipeline_id)
    if not pipeline:
        return jsonify({"success": False, "error": "Pipeline 不存在"}), 404

    def event_stream():
        last_dict = pipeline.to_dict()
        yield f"data: {json.dumps({'type': 'init', 'data': last_dict}, ensure_ascii=False)}\n\n"

        # 轮询文件变更，直到结束状态
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
            # 超时保护由客户端控制，这里发送 keep-alive
            yield ": ping\n\n"

    return Response(event_stream(), mimetype='text/event-stream')
