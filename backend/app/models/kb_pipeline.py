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
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PipelineStage:
    name: str
    label: str
    status: PipelineStageStatus = PipelineStageStatus.PENDING
    message: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    link: Optional[str] = None  # 完成后的跳转链接

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "status": self.status.value,
            "message": self.message,
            "result": self.result,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
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
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
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
    status: PipelineStageStatus = PipelineStageStatus.PENDING
    current_stage_index: int = 0
    stages: List[PipelineStage] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "minio_object": self.minio_object,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "app_id": self.app_id,
            "target_app_id": self.target_app_id,
            "status": self.status.value,
            "current_stage_index": self.current_stage_index,
            "stages": [s.to_dict() for s in self.stages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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
            status=PipelineStageStatus(data.get("status", "pending")),
            current_stage_index=data.get("current_stage_index", 0),
            stages=[PipelineStage.from_dict(s) for s in data.get("stages", [])],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            error=data.get("error"),
        )

    @classmethod
    def create_default(cls, minio_object: str, target_app_id: Optional[str] = None) -> "KbPipeline":
        now = datetime.now().isoformat()
        pipeline_id = f"kbpipe_{uuid.uuid4().hex[:12]}"
        # 根据是否有 target_app_id 决定最后阶段标签
        app_stage_label = "应用关联" if target_app_id else "应用创建"
        return cls(
            pipeline_id=pipeline_id,
            minio_object=minio_object,
            target_app_id=target_app_id,
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
