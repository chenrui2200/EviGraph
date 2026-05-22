"""
KB Pipeline 模型与状态管理

用于自动执行知识库构建的全流程：
1. 项目创建 + MinIO 文件下载
2. MinerU 标注
3. 章节模式分析
4. 智能分析
5. 图谱构建
6. AI 应用创建
"""

import os
import json
import uuid
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from ..config import Config


class PipelineStageStatus(str, Enum):
    PENDING = "pending"

    # Stage 细分状态（Pipeline 外层 status 使用，标识当前执行到哪个阶段）
    PROJECT_CREATION_PROCESSING = "project_creation_processing"
    PROJECT_CREATION_COMPLETED = "project_creation_completed"
    PROJECT_CREATION_FAILED = "project_creation_failed"

    MINERU_ANNOTATION_PROCESSING = "mineru_annotation_processing"
    MINERU_ANNOTATION_COMPLETED = "mineru_annotation_completed"
    MINERU_ANNOTATION_FAILED = "mineru_annotation_failed"

    CHAPTER_ANALYSIS_PROCESSING = "chapter_analysis_processing"
    CHAPTER_ANALYSIS_COMPLETED = "chapter_analysis_completed"
    CHAPTER_ANALYSIS_FAILED = "chapter_analysis_failed"

    INTELLIGENT_ANALYSIS_PROCESSING = "intelligent_analysis_processing"
    INTELLIGENT_ANALYSIS_COMPLETED = "intelligent_analysis_completed"
    INTELLIGENT_ANALYSIS_FAILED = "intelligent_analysis_failed"

    GRAPH_BUILDING_PROCESSING = "graph_building_processing"
    GRAPH_BUILDING_COMPLETED = "graph_building_completed"
    GRAPH_BUILDING_FAILED = "graph_building_failed"

    APP_CREATION_PROCESSING = "app_creation_processing"
    APP_CREATION_COMPLETED = "app_creation_completed"
    APP_CREATION_FAILED = "app_creation_failed"

    TOTAL_COMPLETED = "total_completed"


@dataclass
class PipelineStage:
    name: str
    label: str
    status: PipelineStageStatus = PipelineStageStatus.PENDING
    message: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    processing_at: Optional[str] = None  # 进入 processing 状态的时间
    completed_at: Optional[str] = None   # 进入 completed 状态的时间
    duration_ms: Optional[int] = None  # stage 总耗时（毫秒）
    tasks_timing: List[Dict[str, Any]] = field(default_factory=list)  # 内部 task 耗时明细
    link: Optional[str] = None  # 完成后的跳转链接

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "status": self.status.value,
            "message": self.message,
            "result": self.result,
            "processing_at": self.processing_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "tasks_timing": self.tasks_timing,
            "link": self.link,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PipelineStage":
        return cls(
            name=data.get("name", ""),
            label=data.get("label", ""),
            status=PipelineStageStatus(data.get("status", "pending")),
            message=data.get("message", ""),
            result=data.get("result", {}),
            processing_at=data.get("processing_at"),
            completed_at=data.get("completed_at"),
            duration_ms=data.get("duration_ms"),
            tasks_timing=data.get("tasks_timing", []),
            link=data.get("link"),
        )


@dataclass
class KbPipeline:
    pipeline_id: str
    minio_object: str
    project_id: Optional[str] = None
    graph_id: Optional[str] = None
    app_id: Optional[str] = None
    target_app_id: Optional[str] = None  # 用户指定的已有 App（非自动创建）
    proj_name: Optional[str] = None  # 用户指定的项目自定义名称（可选）
    kb_pipeline_callback_url: Optional[str] = None  # 阶段完成后的回调地址（创建时绑定）
    chunks_count: Optional[int] = None  # 智能分析生成的 chunks（clauses）数量
    status: PipelineStageStatus = PipelineStageStatus.PENDING
    current_stage_index: int = 0
    stages: List[PipelineStage] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    total_completed: Optional[str] = None  # 全部阶段完成时间
    total_duration_ms: Optional[int] = None  # pipeline 总耗时（毫秒）
    pending_at: Optional[str] = None  # 进入 PENDING（等待队列）的时间
    pending_duration_ms: Optional[int] = None  # 从 PENDING 到开始执行的等待耗时（毫秒）
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "minio_object": self.minio_object,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "app_id": self.app_id,
            "target_app_id": self.target_app_id,
            "proj_name": self.proj_name,
            "kb_pipeline_callback_url": self.kb_pipeline_callback_url,
            "chunks_count": self.chunks_count,
            "status": self.status.value,
            "current_stage_index": self.current_stage_index,
            "stages": [s.to_dict() for s in self.stages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_completed": self.total_completed,
            "total_duration_ms": self.total_duration_ms,
            "pending_at": self.pending_at,
            "pending_duration_ms": self.pending_duration_ms,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "KbPipeline":
        return cls(
            pipeline_id=data["pipeline_id"],
            minio_object=data.get("minio_object", ""),
            project_id=data.get("project_id"),
            graph_id=data.get("graph_id"),
            app_id=data.get("app_id"),
            target_app_id=data.get("target_app_id"),
            proj_name=data.get("proj_name"),
            kb_pipeline_callback_url=data.get("kb_pipeline_callback_url"),
            chunks_count=data.get("chunks_count"),
            status=PipelineStageStatus(data.get("status", "pending")),
            current_stage_index=data.get("current_stage_index", 0),
            stages=[PipelineStage.from_dict(s) for s in data.get("stages", [])],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            total_completed=data.get("total_completed"),
            total_duration_ms=data.get("total_duration_ms"),
            pending_at=data.get("pending_at"),
            pending_duration_ms=data.get("pending_duration_ms"),
            error=data.get("error"),
        )

    @classmethod
    def create_default(
        cls,
        minio_object: str,
        target_app_id: Optional[str] = None,
        proj_name: Optional[str] = None,
        kb_pipeline_callback_url: Optional[str] = None,
    ) -> "KbPipeline":
        now = datetime.now().isoformat()
        pipeline_id = f"kbpipe_{uuid.uuid4().hex[:12]}"
        # 根据是否有 target_app_id 决定最后阶段标签
        app_stage_label = "应用关联" if target_app_id else "应用创建"
        return cls(
            pipeline_id=pipeline_id,
            minio_object=minio_object,
            target_app_id=target_app_id,
            proj_name=proj_name,
            kb_pipeline_callback_url=kb_pipeline_callback_url,
            stages=[
                PipelineStage(name="project_creation", label="项目创建"),
                PipelineStage(name="mineru_annotation", label="MinerU 标注"),
                PipelineStage(name="chapter_analysis", label="章节模式分析"),
                PipelineStage(name="intelligent_analysis", label="智能分析"),
                PipelineStage(name="graph_building", label="图谱构建"),
                PipelineStage(name="app_creation", label=app_stage_label),
            ],
            created_at=now,
            updated_at=now,
        )


class KbPipelineManager:
    """KB Pipeline 持久化管理器"""

    PIPELINES_DIR = os.path.join(Config.UPLOAD_FOLDER, "kb_pipelines")

    @classmethod
    def _ensure_dir(cls):
        os.makedirs(cls.PIPELINES_DIR, exist_ok=True)

    @classmethod
    def _get_path(cls, pipeline_id: str) -> str:
        return os.path.join(cls.PIPELINES_DIR, f"{pipeline_id}.json")

    @classmethod
    def save(cls, pipeline: KbPipeline) -> None:
        cls._ensure_dir()
        pipeline.updated_at = datetime.now().isoformat()
        path = cls._get_path(pipeline.pipeline_id)
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(pipeline.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)

    @classmethod
    def get(cls, pipeline_id: str) -> Optional[KbPipeline]:
        path = cls._get_path(pipeline_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return KbPipeline.from_dict(json.load(f))
        except Exception:
            return None

    @classmethod
    def delete(cls, pipeline_id: str) -> bool:
        path = cls._get_path(pipeline_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    @classmethod
    def list_all(cls, limit: int = 100) -> List[KbPipeline]:
        cls._ensure_dir()
        pipelines = []
        for fname in os.listdir(cls.PIPELINES_DIR):
            if fname.endswith(".json"):
                pid = fname.replace(".json", "")
                p = cls.get(pid)
                if p:
                    pipelines.append(p)
        pipelines.sort(key=lambda x: x.created_at, reverse=True)
        return pipelines[:limit]
