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
    GRAPH_CHUNKING = "graph_chunking"        # Text chunking in progress
    GRAPH_EMBEDDING = "graph_embedding"      # Embedding generation in progress
    GRAPH_INDEXING = "graph_indexing"        # Index creation in progress
    GRAPH_BUILDING = "graph_building"         # Graph building in progress
    GRAPH_COMPLETED = "graph_completed"       # Graph build completed
    FAILED = "failed"                # Failed


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
        """Get project directory path"""
        return os.path.join(cls.PROJECTS_DIR, project_id)

    @classmethod
    def _get_project_meta_path(cls, project_id: str) -> str:
        """Get project metadata file path"""
        return os.path.join(cls._get_project_dir(project_id), 'project.json')

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
        Create new project

        Args:
            name: Project name

        Returns:
            Newly created Project object
        """
        cls._ensure_projects_dir()

        project_id = f"proj_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()

        project = Project(
            project_id=project_id,
            name=name,
            status=ProjectStatus.CREATED,
            created_at=now,
            updated_at=now
        )

        # Create project directory structure
        project_dir = cls._get_project_dir(project_id)
        files_dir = cls._get_project_files_dir(project_id)
        os.makedirs(project_dir, exist_ok=True)
        os.makedirs(files_dir, exist_ok=True)

        # Save project metadata
        cls.save_project(project)

        return project

    @classmethod
    def save_project(cls, project: Project) -> None:
        """Save project metadata atomically"""
        project.updated_at = datetime.now().isoformat()
        meta_path = cls._get_project_meta_path(project.project_id)
        temp_path = meta_path + ".tmp"

        try:
            # Write to temporary file first to ensure atomicity
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(project.to_dict(), f, ensure_ascii=False, indent=2)

            # Atomic rename (replace existing file)
            if os.path.exists(meta_path):
                os.replace(temp_path, meta_path)
            else:
                os.rename(temp_path, meta_path)
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

