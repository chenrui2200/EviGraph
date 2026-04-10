"""
Project Context Management
Persists project state on server to avoid frontend passing large data between interfaces
"""

import os
import json
import uuid
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional
from enum import Enum
from dataclasses import dataclass, field, asdict
from ..config import Config


class ProjectStatus(str, Enum):
    """Project status"""
    CREATED = "created"              # Just created, files uploaded
    ONTOLOGY_GENERATION = "ontology_generation" # Ontology generation in progress
    ONTOLOGY_GENERATED = "ontology_generated"  # Ontology generated
    GRAPH_CHUNKING = "graph_chunking"        # LLM 智能分块中 (新增)
    GRAPH_CHUNKED = "graph_chunked"          # LLM 智能分块完成 (新增)
    GRAPH_EMBEDDING = "graph_embedding"      # Embedding generation in progress
    GRAPH_INDEXING = "graph_indexing"        # Index creation in progress
    GRAPH_BUILDING = "graph_building"         # Graph building in progress
    GRAPH_COMPLETED = "graph_completed"       # Graph build completed
    FAILED = "failed"                # Failed


class ChapterStatus(str, Enum):
    """章节处理状态"""
    PENDING = "pending"           # 待处理
    PROCESSING = "processing"    # 处理中
    COMPLETED = "completed"      # 已完成
    FAILED = "failed"            # 失败


@dataclass
class ChapterPlan:
    """章节计划 - 定义每个章节的位置边界和处理状态"""
    chapter_number: int
    title: str
    start_position: int
    end_position: int
    status: ChapterStatus = ChapterStatus.PENDING
    clauses_count: int = 0
    elements_count: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    chapter_type: str = "normative"   # normative / term_definition / appendix

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "chapter_number": self.chapter_number,
            "title": self.title,
            "start_position": self.start_position,
            "end_position": self.end_position,
            "status": self.status.value if isinstance(self.status, ChapterStatus) else self.status,
            "clauses_count": self.clauses_count,
            "elements_count": self.elements_count,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "chapter_type": self.chapter_type
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'ChapterPlan':
        """从字典创建"""
        status = data.get('status', 'pending')
        if isinstance(status, str):
            status = ChapterStatus(status)
        return cls(
            chapter_number=data['chapter_number'],
            title=data.get('title', ''),
            start_position=data.get('start_position', 0),
            end_position=data.get('end_position', 0),
            status=status,
            clauses_count=data.get('clauses_count', 0),
            elements_count=data.get('elements_count', 0),
            started_at=data.get('started_at'),
            completed_at=data.get('completed_at'),
            chapter_type=data.get('chapter_type', 'normative')
        )


@dataclass
class ChunkCheckpoint:
    """分块检查点 - 用于断点恢复（增强版）"""
    chapter_plan: List[ChapterPlan] = field(default_factory=list)
    current_chapter_index: int = -1
    total_chapters: int = 0
    completed_clauses: List[Dict] = field(default_factory=list)
    completed_elements: List[Dict] = field(default_factory=list)
    processing_clauses: List[Dict] = field(default_factory=list)
    processing_elements: List[Dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "chapter_plan": [c.to_dict() if isinstance(c, ChapterPlan) else c for c in self.chapter_plan],
            "current_chapter_index": self.current_chapter_index,
            "total_chapters": self.total_chapters,
            "completed_clauses": self.completed_clauses,
            "completed_elements": self.completed_elements,
            "processing_clauses": self.processing_clauses,
            "processing_elements": self.processing_elements,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'ChunkCheckpoint':
        """从字典创建"""
        chapter_plan = [
            ChapterPlan.from_dict(c) if isinstance(c, dict) else c
            for c in data.get('chapter_plan', [])
        ]
        return cls(
            chapter_plan=chapter_plan,
            current_chapter_index=data.get('current_chapter_index', -1),
            total_chapters=data.get('total_chapters', 0),
            completed_clauses=data.get('completed_clauses', []),
            completed_elements=data.get('completed_elements', []),
            processing_clauses=data.get('processing_clauses', []),
            processing_elements=data.get('processing_elements', []),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', '')
        )


@dataclass
class Project:
    """Project data model"""
    project_id: str
    name: str
    status: ProjectStatus
    created_at: str
    updated_at: str

    # Workflow state
    current_step: int = 1            # Current step (1-5)

    # File information
    files: List[Dict[str, str]] = field(default_factory=list)  # [{filename, path, size}]
    total_text_length: int = 0

    # Ontology information (populated after interface 1 generates)
    ontology: Optional[Dict[str, Any]] = None
    analysis_summary: Optional[str] = None
    ontology_task_id: Optional[str] = None

    # NEW: Intermediate state for ontology discovery (breakpoint resume)
    # Stores: {"last_window_index": int, "discovered_ontology": dict}
    ontology_discovery_state: Dict[str, Any] = field(default_factory=lambda: {"last_window_index": -1, "discovered_ontology": {"entity_types": [], "edge_types": []}})

    # Graph information (populated after interface 2 completes)
    graph_id: Optional[str] = None
    graph_build_task_id: Optional[str] = None

    # Configuration
    simulation_requirement: Optional[str] = None
    chunk_size: int = 500
    chunk_overlap: int = 50
    use_semantic: bool = False

    # Error information
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "status": self.status.value if isinstance(self.status, ProjectStatus) else self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_step": self.current_step,
            "files": self.files,
            "total_text_length": self.total_text_length,
            "ontology": self.ontology,
            "analysis_summary": self.analysis_summary,
            "ontology_task_id": self.ontology_task_id,
            "graph_id": self.graph_id,
            "graph_build_task_id": self.graph_build_task_id,
            "simulation_requirement": self.simulation_requirement,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "use_semantic": self.use_semantic,
            "error": self.error
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Project':
        """Create from dictionary"""
        status = data.get('status', 'created')
        if isinstance(status, str):
            status = ProjectStatus(status)

        return cls(
            project_id=data['project_id'],
            name=data.get('name', 'Unnamed Project'),
            status=status,
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
            current_step=data.get('current_step', 1),
            files=data.get('files', []),
            total_text_length=data.get('total_text_length', 0),
            ontology=data.get('ontology'),
            analysis_summary=data.get('analysis_summary'),
            ontology_task_id=data.get('ontology_task_id'),
            graph_id=data.get('graph_id'),
            graph_build_task_id=data.get('graph_build_task_id'),
            simulation_requirement=data.get('simulation_requirement'),
            chunk_size=data.get('chunk_size', 500),
            chunk_overlap=data.get('chunk_overlap', 50),
            use_semantic=data.get('use_semantic', False),
            error=data.get('error')
        )


class ProjectManager:
    """Project Manager - handles project persistence and retrieval"""

    # Project storage root directory
    PROJECTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'projects')

    @classmethod
    def _ensure_projects_dir(cls):
        """Ensure project directory exists"""
        os.makedirs(cls.PROJECTS_DIR, exist_ok=True)

    @classmethod
    def _get_project_dir(cls, project_id: str) -> str:
        """Get project directory path, ensure it exists"""
        if not project_id:
            import traceback
            from ..utils.logger import get_logger
            logger = get_logger('mirofish.project')
            logger.error(f"[BUG] _get_project_dir called with empty project_id!\n{traceback.format_stack()}")
            raise ValueError("project_id cannot be empty")
        project_dir = os.path.join(cls.PROJECTS_DIR, project_id)
        os.makedirs(project_dir, exist_ok=True)
        return project_dir

    @classmethod
    def _get_project_meta_path(cls, project_id: str) -> str:
        """Get project metadata file path"""
        return os.path.join(cls.PROJECTS_DIR, project_id, 'project.json')

    @classmethod
    def _get_project_files_dir(cls, project_id: str) -> str:
        """Get project file storage directory"""
        return os.path.join(cls._get_project_dir(project_id), 'files')

    @classmethod
    def _get_project_text_path(cls, project_id: str) -> str:
        """Get project extracted text storage path"""
        return os.path.join(cls._get_project_dir(project_id), 'extracted_text.txt')

    @classmethod
    def _get_project_chunks_path(cls, project_id: str) -> str:
        """Get project chunks storage path (JSON)"""
        return os.path.join(cls._get_project_dir(project_id), 'chunks.json')

    @classmethod
    def create_project(cls, name: str = "Unnamed Project") -> Project:
        """
        Create new project (in-memory only, no directory/file I/O).

        Directory structure is created separately in generate_ontology after files are saved.

        Args:
            name: Project name

        Returns:
            Newly created Project object
        """
        project_id = f"proj_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()

        project = Project(
            project_id=project_id,
            name=name,
            status=ProjectStatus.CREATED,
            created_at=now,
            updated_at=now
        )

        return project

    @classmethod
    def init_project_dirs(cls, project_id: str) -> None:
        """
        Create the on-disk directory structure for a project.
        Called explicitly after all files have been saved successfully.
        """
        cls._ensure_projects_dir()
        project_dir = cls._get_project_dir(project_id)
        files_dir = cls._get_project_files_dir(project_id)
        os.makedirs(project_dir, exist_ok=True)
        os.makedirs(files_dir, exist_ok=True)

    @classmethod
    def save_project(cls, project: Project) -> None:
        """Save project metadata atomically"""
        project.updated_at = datetime.now().isoformat()
        meta_path = cls._get_project_meta_path(project.project_id)
        temp_path = meta_path + ".tmp"

        try:
            # Ensure parent directory exists
            os.makedirs(os.path.dirname(meta_path), exist_ok=True)
            # Write to temporary file first to ensure atomicity
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(project.to_dict(), f, ensure_ascii=False, indent=2)

            # Atomic rename (replace existing file) — atomic on Linux
            os.replace(temp_path, meta_path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

    @classmethod
    def get_project(cls, project_id: str) -> Optional[Project]:
        """
        Get project with JSON error handling
        """
        meta_path = cls._get_project_meta_path(project_id)

        if not os.path.exists(meta_path):
            return None

        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return Project.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            # Prevent crashing the entire system if one project file is corrupted
            from ..utils.logger import get_logger
            get_logger('mirofish.project').error(f"Failed to load project {project_id}: {e}. File may be corrupted.")
            return None

    @classmethod
    def list_projects(cls, limit: int = 50) -> List[Project]:
        """
        List all projects

        Args:
            limit: Result count limit

        Returns:
            Project list, sorted by creation time (descending)
        """
        cls._ensure_projects_dir()

        projects = []
        for project_id in os.listdir(cls.PROJECTS_DIR):
            project = cls.get_project(project_id)
            if project:
                projects.append(project)

        # Sort by creation time (descending)
        projects.sort(key=lambda p: p.created_at, reverse=True)

        return projects[:limit]

    @classmethod
    def delete_project(cls, project_id: str) -> bool:
        """
        Delete project and all its files

        Args:
            project_id: Project ID

        Returns:
            Whether deletion succeeded
        """
        project_dir = cls._get_project_dir(project_id)

        if not os.path.exists(project_dir):
            return False

        shutil.rmtree(project_dir)
        return True

    @classmethod
    def save_file_to_project(cls, project_id: str, file_storage, original_filename: str) -> Dict[str, str]:
        """
        Save uploaded file to project directory
        """
        from werkzeug.utils import secure_filename
        files_dir = cls._get_project_files_dir(project_id)
        os.makedirs(files_dir, exist_ok=True)

        # Use sanitized original filename
        safe_filename = secure_filename(original_filename)

        # If sanitized name is empty or just dots, use a fallback
        if not safe_filename or safe_filename.startswith('.'):
            ext = os.path.splitext(original_filename)[1].lower()
            safe_filename = f"upload_{uuid.uuid4().hex[:8]}{ext}"

        file_path = os.path.join(files_dir, safe_filename)

        # Handle collisions (rare but possible with identical names)
        if os.path.exists(file_path):
            base, ext = os.path.splitext(safe_filename)
            safe_filename = f"{base}_{uuid.uuid4().hex[:4]}{ext}"
            file_path = os.path.join(files_dir, safe_filename)

        # Save file
        file_storage.save(file_path)

        # Get file size
        file_size = os.path.getsize(file_path)

        return {
            "original_filename": original_filename,
            "saved_filename": safe_filename,
            "path": file_path,
            "size": file_size
        }

    @classmethod
    def save_extracted_text(cls, project_id: str, text: str) -> None:
        """Save extracted text"""
        text_path = cls._get_project_text_path(project_id)
        with open(text_path, 'w', encoding='utf-8') as f:
            f.write(text)

    @classmethod
    def get_extracted_text(cls, project_id: str) -> Optional[str]:
        """Get extracted text"""
        text_path = cls._get_project_text_path(project_id)

        if not os.path.exists(text_path):
            return None

        with open(text_path, 'r', encoding='utf-8') as f:
            return f.read()

    @classmethod
    def save_chunks(cls, project_id: str, chunks: List[Dict[str, Any]]) -> None:
        """Save text chunks with metadata"""
        path = cls._get_project_chunks_path(project_id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)

    @classmethod
    def get_chunks(cls, project_id: str) -> Optional[List[Dict[str, Any]]]:
        """Get text chunks with metadata"""
        path = cls._get_project_chunks_path(project_id)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    @classmethod
    def get_project_files(cls, project_id: str) -> List[str]:
        """Get all project file paths"""
        files_dir = cls._get_project_files_dir(project_id)

        if not os.path.exists(files_dir):
            return []

        return [
            os.path.join(files_dir, f)
            for f in os.listdir(files_dir)
            if os.path.isfile(os.path.join(files_dir, f))
        ]

    @classmethod
    def _get_intelligent_chunks_path(cls, project_id: str) -> str:
        """Get path for LLM intelligent chunks"""
        project_dir = cls._get_project_dir(project_id)
        return os.path.join(project_dir, 'intelligent_chunks.json')

    @classmethod
    def _get_intelligent_chunks_jsonl_path(cls, project_id: str) -> str:
        """Get path for LLM intelligent chunks JSONL (incremental per-clause write)"""
        project_dir = cls._get_project_dir(project_id)
        return os.path.join(project_dir, 'intelligent_chunks.jsonl')

    @classmethod
    def _get_intelligent_chunks_tree_path(cls, project_id: str) -> str:
        """Get path for the complete chapter tree (used by frontend)"""
        project_dir = cls._get_project_dir(project_id)
        return os.path.join(project_dir, 'intelligent_chunks_tree.json')

    @classmethod
    def append_clause_to_jsonl(cls, project_id: str, clause_dict: Dict[str, Any]) -> None:
        """Append a single clause as one line in JSONL file"""
        path = cls._get_intelligent_chunks_jsonl_path(project_id)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(clause_dict, ensure_ascii=False) + '\n')

    @classmethod
    def assemble_intelligent_chunks_from_jsonl(cls, project_id: str) -> Dict[str, Any]:
        """Read all JSONL lines and assemble into final intelligent_chunks.json structure"""
        jsonl_path = cls._get_intelligent_chunks_jsonl_path(project_id)
        tree_path = cls._get_intelligent_chunks_tree_path(project_id)
        sections_path = os.path.join(cls._get_project_dir(project_id), 'intelligent_chunks_sections.json')

        if not os.path.exists(jsonl_path):
            return None

        # 优先从 tree 文件读取（预生成的完整数据）
        tree_path = cls._get_intelligent_chunks_tree_path(project_id)
        if os.path.exists(tree_path):
            with open(tree_path, 'r', encoding='utf-8') as f:
                tree_data = json.load(f)
            clauses = tree_data.get('clauses', [])
            sections = tree_data.get('sections', [])
            edges = tree_data.get('edges', [])
            elements = []
            # 按章节 + 条文编号排序（并发写入顺序不确定）
            clauses = sorted(clauses, key=lambda c: (
                c.get('parent_chapter') or 0,
                c.get('clause_id') or ''
            ))
            return {
                "source": "llm",
                "sections": sections,
                "clauses": clauses,
                "elements": elements,
                "edges": edges
            }

        # Fallback: 从 JSONL + sections JSON 重建
        clauses = []
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    clauses.append(json.loads(line))

        # 按章节 + 条文编号排序
        clauses = sorted(clauses, key=lambda c: (
            c.get('parent_chapter') or 0,
            c.get('clause_id') or ''
        ))

        sections = []
        elements = []
        edges = []
        if os.path.exists(sections_path):
            with open(sections_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
                sections = meta.get('sections', [])
                elements = meta.get('elements', [])
                edges = meta.get('edges', [])

        return {
            "source": "llm",
            "sections": sections,
            "clauses": clauses,
            "elements": elements,
            "edges": edges
        }

    @classmethod
    def save_chunks_result(cls, project_id: str, chunks_result: Dict[str, Any]) -> None:
        """
        将 chunk() 返回的结果写入 JSONL + sections JSON（替代一次性写 JSON）
        - clauses 逐条写入 JSONL（覆盖已有内容）
        - sections + edges 直接生成 intelligent_chunks_tree.json
        """
        # 清空并写入 clauses 到 JSONL
        jsonl_path = cls._get_intelligent_chunks_jsonl_path(project_id)
        with open(jsonl_path, 'w', encoding='utf-8') as f:
            for clause in chunks_result.get('clauses', []):
                f.write(json.dumps(clause, ensure_ascii=False) + '\n')

        # 生成 tree 文件（sections + edges + clauses 直接传入，不写 intermediate 文件）
        cls.build_intelligent_chunks_tree(
            project_id,
            sections=chunks_result.get('sections', []),
            edges=chunks_result.get('edges', []),
            clauses=chunks_result.get('clauses', [])
        )

    @classmethod
    def build_intelligent_chunks_tree(
        cls, project_id: str,
        sections: List[Dict[str, Any]] = None,
        edges: List[Dict] = None,
        clauses: List[Dict] = None
    ) -> Dict[str, Any]:
        """
        组装完整的章节树结构并写入 intelligent_chunks_tree.json
        - 章节树：sections + clauses 按 chapter_number 分组
        - 前端直接读取此文件，无需内存中再分组
        - sections/edges/clauses 优先从参数传入（调用方确保完整）
        - 兼容模式：参数为 None 时从旧文件读取（向后兼容）
        """
        jsonl_path = cls._get_intelligent_chunks_jsonl_path(project_id)
        sections_path = os.path.join(cls._get_project_dir(project_id), 'intelligent_chunks_sections.json')

        # clauses：优先用参数（调用方传入），否则从 JSONL 读取
        if clauses is None:
            clauses = []
            if os.path.exists(jsonl_path):
                with open(jsonl_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            clauses.append(json.loads(line))

        # sections/edges：优先用参数，否则从 sections JSON 读取
        if sections is None:
            sections = []
            _edges = edges or []
            if os.path.exists(sections_path):
                with open(sections_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                    sections = meta.get('sections', [])
                    _edges = meta.get('edges', [])
            edges = _edges

        # 构建章节树
        chapter_tree = {}

        if sections:
            # 有 sections 数据：按 sections 构建章节树
            for section in sections:
                chapter_num = section.get('chapter_number')
                if chapter_num is None:
                    continue
                chapter_clauses = [c for c in clauses if c.get('parent_chapter') == chapter_num]
                chapter_tree[chapter_num] = {
                    "chapter_number": chapter_num,
                    "title": section.get('title', ''),
                    "content": section.get('content', ''),
                    "clauses": chapter_clauses,
                    "clause_count": len(chapter_clauses)
                }
        else:
            # 无 sections 数据：从 clauses 的 parent_chapter 聚合推断章节结构
            chapter_nums = sorted(set(c.get('parent_chapter') for c in clauses if c.get('parent_chapter')))
            for cn in chapter_nums:
                chapter_clauses = [c for c in clauses if c.get('parent_chapter') == cn]
                chapter_tree[cn] = {
                    "chapter_number": cn,
                    "title": f"第 {cn} 章",
                    "content": "",
                    "clauses": chapter_clauses,
                    "clause_count": len(chapter_clauses)
                }

        # 按章节号排序
        def _sort_key(item):
            k = item[0]
            return (0, int(k)) if str(k).isdigit() else (1, str(k))
        sorted_chapters = sorted(chapter_tree.items(), key=_sort_key)

        tree = {
            "source": "llm",
            "sections": sections,
            "clauses": clauses,
            "chapter_tree": [v for _, v in sorted_chapters],
            "summary": {
                "total_chapters": len(sorted_chapters),
                "total_clauses": len(clauses)
            },
            "edges": edges
        }

        # 写入文件
        tree_path = cls._get_intelligent_chunks_tree_path(project_id)
        with open(tree_path, 'w', encoding='utf-8') as f:
            json.dump(tree, f, ensure_ascii=False, indent=2)

        return tree

    @classmethod
    def get_intelligent_chunks(cls, project_id: str) -> Optional[Dict[str, Any]]:
        """Get LLM intelligent chunks result - 优先从 JSONL 组装，fallback 到 JSON"""
        jsonl_path = cls._get_intelligent_chunks_jsonl_path(project_id)

        # 优先从 JSONL 组装
        if os.path.exists(jsonl_path):
            result = cls.assemble_intelligent_chunks_from_jsonl(project_id)
            if result:
                # 从 clauses 提取 elements（get_intelligent_chunks 需要完整 elements）
                clauses = result.get('clauses', [])
                elements = []
                seen_keys = set()
                for c in clauses:
                    for ent in c.get('entities', []):
                        key = str(ent) + "|" + c.get('clause_id', '')
                        if key not in seen_keys:
                            seen_keys.add(key)
                            elements.append({
                                "element_type": "noun_entity",
                                "key": ent if isinstance(ent, str) else "",
                                "value": "",
                                "unit": "",
                                "source_clause_id": c.get('clause_id', ''),
                                "scope_prefix": c.get('scope_prefix'),
                                "chapter": c.get('chapter'),
                                "metadata": {}
                            })
                result['elements'] = elements
                return result

        # Fallback: 旧 JSON 格式
        path = cls._get_intelligent_chunks_path(project_id)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)

        return None

    # =========================================================================
    # MinerU 原始解析结果
    # =========================================================================

    @classmethod
    def _get_mineru_parsed_path(cls, project_id: str) -> str:
        """Get path for raw MinerU parsed result"""
        project_dir = cls._get_project_dir(project_id)
        return os.path.join(project_dir, 'mineru_parsed.json')

    @classmethod
    def save_mineru_parsed(cls, project_id: str, data: Dict[str, Any]) -> None:
        """Save raw MinerU API response for debugging/tracing"""
        path = cls._get_mineru_parsed_path(project_id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def get_mineru_parsed(cls, project_id: str) -> Optional[Dict[str, Any]]:
        """Get raw MinerU parsed result"""
        path = cls._get_mineru_parsed_path(project_id)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    # =========================================================================
    # 检查点机制 - 用于断点恢复
    # =========================================================================

    @classmethod
    def _get_checkpoint_path(cls, project_id: str) -> str:
        """Get path for chunk checkpoint data"""
        project_dir = cls._get_project_dir(project_id)
        return os.path.join(project_dir, 'chunk_checkpoint.json')

    @classmethod
    def save_chunk_checkpoint(
        cls,
        project_id: str,
        checkpoint_data: Dict[str, Any],
        progress: float = 0,
        message: str = ""
    ) -> None:
        """
        保存分块检查点数据

        Args:
            project_id: 项目ID
            checkpoint_data: 检查点数据，包含：
                - current_chapter: 当前处理的章节号
                - total_chapters: 总章节数
                - completed_chapters: 已完成的章节列表
                - processed_clauses: 已处理的条文列表
                - processed_elements: 已处理的要素列表
            progress: 当前进度 0-1
            message: 状态消息
        """
        path = cls._get_checkpoint_path(project_id)
        checkpoint = {
            "checkpoint_data": checkpoint_data,
            "progress": progress,
            "message": message,
            "updated_at": datetime.now().isoformat()
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(checkpoint, f, ensure_ascii=False, indent=2)

    @classmethod
    def get_chunk_checkpoint(cls, project_id: str) -> Optional[Dict[str, Any]]:
        """
        获取分块检查点数据

        Returns:
            检查点数据或 None
        """
        path = cls._get_checkpoint_path(project_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("checkpoint_data")
        except Exception:
            return None

    @classmethod
    def delete_chunk_checkpoint(cls, project_id: str) -> None:
        """删除分块检查点数据"""
        path = cls._get_checkpoint_path(project_id)
        if os.path.exists(path):
            os.remove(path)

    # =========================================================================
    # 增强版检查点机制 - 用于断点恢复 V2
    # =========================================================================

    @classmethod
    def _get_checkpoint_v2_path(cls, project_id: str) -> str:
        """获取新版检查点文件路径"""
        project_dir = cls._get_project_dir(project_id)
        return os.path.join(project_dir, 'chunk_checkpoint_v2.json')

    @classmethod
    def save_chunk_checkpoint_v2(cls, project_id: str, checkpoint: ChunkCheckpoint) -> None:
        """
        保存增强版分块检查点

        Args:
            project_id: 项目ID
            checkpoint: 增强版检查点数据
        """
        path = cls._get_checkpoint_v2_path(project_id)
        checkpoint.updated_at = datetime.now().isoformat()
        if not checkpoint.created_at:
            checkpoint.created_at = checkpoint.updated_at

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(checkpoint.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def get_chunk_checkpoint_v2(cls, project_id: str) -> Optional[ChunkCheckpoint]:
        """
        获取增强版分块检查点

        Returns:
            增强版检查点或 None
        """
        path = cls._get_checkpoint_v2_path(project_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return ChunkCheckpoint.from_dict(data)
        except Exception:
            return None

    @classmethod
    def delete_chunk_checkpoint_v2(cls, project_id: str) -> None:
        """删除增强版分块检查点"""
        path = cls._get_checkpoint_v2_path(project_id)
        if os.path.exists(path):
            os.remove(path)

    @classmethod
    def get_chapter_progress(cls, project_id: str) -> Optional[Dict[str, Any]]:
        """
        获取章节处理进度详情

        Returns:
            包含章节进度信息的字典
        """
        checkpoint = cls.get_chunk_checkpoint_v2(project_id)
        if not checkpoint:
            return None

        total = checkpoint.total_chapters
        completed = sum(
            1 for c in checkpoint.chapter_plan
            if c.status == ChapterStatus.COMPLETED
        )
        processing = sum(
            1 for c in checkpoint.chapter_plan
            if c.status == ChapterStatus.PROCESSING
        )
        failed = sum(
            1 for c in checkpoint.chapter_plan
            if c.status == ChapterStatus.FAILED
        )

        return {
            "total_chapters": total,
            "completed_chapters": completed,
            "processing_chapters": processing,
            "failed_chapters": failed,
            "pending_chapters": total - completed - processing - failed,
            "progress_ratio": completed / total if total > 0 else 0,
            "current_chapter_index": checkpoint.current_chapter_index,
            "current_chapter": (
                checkpoint.chapter_plan[checkpoint.current_chapter_index].to_dict()
                if 0 <= checkpoint.current_chapter_index < len(checkpoint.chapter_plan)
                else None
            ),
            "chapter_plan": [c.to_dict() for c in checkpoint.chapter_plan],
            "completed_clauses_count": len(checkpoint.completed_clauses),
            "completed_elements_count": len(checkpoint.completed_elements),
            "created_at": checkpoint.created_at,
            "updated_at": checkpoint.updated_at
        }

    @classmethod
    def init_chapter_plan(cls, project_id: str, toc_data: List[Dict]) -> ChunkCheckpoint:
        """
        从目录数据初始化章节计划

        Args:
            project_id: 项目ID
            toc_data: 目录数据列表 [{chapter_number, title, start_position, end_position}, ...]

        Returns:
            初始化好的检查点
        """
        chapter_plan = [
            ChapterPlan(
                chapter_number=ch.get("chapter_number", i + 1),
                title=ch.get("title", f"章节{i + 1}"),
                start_position=ch.get("start_position", 0),
                end_position=ch.get("end_position", 0),
                status=ChapterStatus.PENDING
            )
            for i, ch in enumerate(toc_data)
        ]

        checkpoint = ChunkCheckpoint(
            chapter_plan=chapter_plan,
            current_chapter_index=-1,
            total_chapters=len(chapter_plan),
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )

        cls.save_chunk_checkpoint_v2(project_id, checkpoint)
        return checkpoint

