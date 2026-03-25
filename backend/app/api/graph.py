"""
Graph-related API Routes
Uses project context mechanism with server-side state persistence
"""

import os
import json
import queue
import traceback
import threading
from flask import request, jsonify, current_app, send_from_directory

from . import graph_bp
from ..config import Config
from ..services.ontology_generator import OntologyGenerator
from ..services.graph_builder import GraphBuilderService
from ..services.graph_tools import GraphToolsService
from ..services.text_processor import TextProcessor
from ..utils.file_parser import FileParser, TextChunk
from ..utils.logger import get_logger
from ..models.task import TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus

# Get logger
logger = get_logger('mirofish.api')


def _get_storage():
    """Get Neo4jStorage from Flask app extensions."""
    storage = current_app.extensions.get('neo4j_storage')
    if not storage:
        raise ValueError("GraphStorage not initialized — check Neo4j connection")
    return storage


def allowed_file(filename: str) -> bool:
    """Check if file extension is allowed"""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS


# ============== Internal Helper: Background Worker Trigger ==============

def _start_build_worker(project_id: str, task_id: str, storage, force: bool = False):
    """
    Start a background thread for graph building.
    Used for both initial build and recovery.
    """
    # Task manager for the thread
    task_manager = TaskManager()

    def build_task():
        build_logger = get_logger('mirofish.build')
        builder = GraphBuilderService(storage=storage)
        # Register worker as active
        builder.register_worker(project_id)

        try:
            build_logger.info(f"[{task_id}] Worker thread attempting to start for graph build...")

            # Reload project to ensure we have the latest metadata and config
            project = ProjectManager.get_project(project_id)
            if not project:
                build_logger.error(f"[{task_id}] Project {project_id} not found by worker.")
                return

            # Check for existing graph_id from project if not force
            active_graph_id = project.graph_id if (project.graph_id and not force) else None

            # Acquire build lock to prevent concurrent workers for the same graph
            lock_id = active_graph_id or task_id
            lock = builder._get_build_lock(lock_id)

            if not lock.acquire(blocking=False):
                build_logger.warning(f"[{task_id}] Build worker already active for {lock_id}. Exiting duplicate thread.")
                return

            try:
                msg = "🚀 WORKER START: Processing graph building..."
                build_logger.info(f"[{task_id}] {msg}")
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    message=msg,
                    log=msg # Persist to task.logs
                )

                # Get data (chunks with metadata preferred)
                task_manager.update_task(
                    task_id,
                    message="Preparing text chunks with metadata...",
                    progress=5
                )
                chunks_data = ProjectManager.get_chunks(project_id)
                use_hierarchical = True  # 启用多层级分块

                if chunks_data:
                    # Convert dicts back to TextChunk objects
                    from ..utils.file_parser import TextChunk
                    initial_chunks = [TextChunk(c["text"], c["metadata"]) for c in chunks_data]

                    if use_hierarchical:
                        # 使用多层级语义分块（替代原有split_chunks）
                        build_logger.info(f"Using hierarchical chunking (Level-1/2/3)")
                        task_manager.update_task(
                            task_id,
                            message="Performing hierarchical semantic chunking...",
                            progress=5
                        )
                        hierarchical_result = TextProcessor.hierarchical_chunk(initial_chunks)
                        total_chunks = hierarchical_result.total_chunks
                        build_logger.info(f"Hierarchical chunking complete: {total_chunks} chunks")
                    else:
                        # Split into smaller chunks preserving metadata (legacy)
                        chunks = TextProcessor.split_chunks(
                            initial_chunks,
                            chunk_size=project.chunk_size,
                            overlap=project.chunk_overlap,
                            semantic=project.use_semantic
                        )
                        total_chunks = len(chunks)
                        build_logger.info(f"Using {len(chunks)} chunks with metadata from chunks.json")
                else:
                    # Fallback to plain text splitting
                    build_logger.warning("chunks.json not found, falling back to plain text splitting")
                    text = ProjectManager.get_extracted_text(project_id)
                    if use_hierarchical:
                        build_logger.info("Using hierarchical chunking for fallback text")
                        hierarchical_result = TextProcessor.hierarchical_chunk_text(text)
                        total_chunks = hierarchical_result.total_chunks
                    else:
                        chunks = TextProcessor.split_text(
                            text,
                            chunk_size=project.chunk_size,
                            overlap=project.chunk_overlap
                        )
                        total_chunks = len(chunks)

                # Create graph (OR RESUME EXISTING)
                if project.graph_id and not force:
                    graph_id = project.graph_id
                    build_logger.info(f"[{task_id}] Resuming graph build for existing graph_id: {graph_id}")
                    task_manager.update_task(
                        task_id,
                        message=f"Resuming build for graph: {graph_id}",
                        progress=10
                    )
                else:
                    task_manager.update_task(
                        task_id,
                        message="Creating Zep graph...",
                        progress=10
                    )
                    graph_id = builder.create_graph(name=project.name or 'MiroFish Graph')
                    # Update project graph_id
                    project.graph_id = graph_id
                    ProjectManager.save_project(project)

                # Update status to chunking and set ontology
                project.status = ProjectStatus.GRAPH_CHUNKING
                ProjectManager.save_project(project)

                task_manager.update_task(
                    task_id,
                    message="Setting ontology definition...",
                    progress=15
                )
                builder.set_ontology(graph_id, project.ontology)

                # Add text (progress_callback signature is (msg, progress_ratio, log=None))
                def add_progress_callback(msg, progress_ratio, log=None):
                    progress = 15 + int(progress_ratio * 75)  # 15% - 90%
                    task_manager.update_task(
                        task_id,
                        message=msg,
                        progress=progress,
                        log=log
                    )

                task_manager.update_task(
                    task_id,
                    message=f"Starting to add {total_chunks} text chunks...",
                    progress=15
                )

                if use_hierarchical and 'hierarchical_result' in dir():
                    # 使用多层级分块存储
                    episode_uuids = builder.add_hierarchical_chunks(
                        graph_id,
                        hierarchical_result,
                        progress_callback=add_progress_callback
                    )
                else:
                    episode_uuids = builder.add_text_batches(
                        graph_id,
                        chunks,
                        batch_size=5,
                        progress_callback=add_progress_callback
                    )

                # Update status to embedding generation
                project.status = ProjectStatus.GRAPH_EMBEDDING
                ProjectManager.save_project(project)

                # Neo4j processing is synchronous, no need to wait
                task_manager.update_task(
                    task_id,
                    message="Text processing completed, generating graph data...",
                    progress=90
                )

                # Update status to indexing
                project.status = ProjectStatus.GRAPH_INDEXING
                ProjectManager.save_project(project)

                # Get graph data
                task_manager.update_task(
                    task_id,
                    message="Retrieving graph data...",
                    progress=95
                )
                graph_data = builder.get_graph_data(graph_id)

                # Update project status
                project.status = ProjectStatus.GRAPH_COMPLETED
                ProjectManager.save_project(project)

                node_count = graph_data.get("node_count", 0)
                edge_count = graph_data.get("edge_count", 0)
                build_logger.info(f"[{task_id}] Graph build completed: graph_id={graph_id}, nodes={node_count}, edges={edge_count}")

                # Complete
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    message="Graph build completed",
                    progress=100,
                    result={
                        "project_id": project_id,
                        "graph_id": graph_id,
                        "node_count": node_count,
                        "edge_count": edge_count,
                        "chunk_count": total_chunks
                    }
                )

            except Exception as e:
                # Update project status to failed
                build_logger.error(f"[{task_id}] Graph build failed: {str(e)}")
                build_logger.debug(traceback.format_exc())

                project.status = ProjectStatus.FAILED
                project.error = str(e)
                ProjectManager.save_project(project)

                task_manager.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    message=f"Build failed: {str(e)}",
                    error=traceback.format_exc()
                )
            finally:
                # Release lock when done (success or fail)
                if 'lock' in locals() and lock.locked():
                    lock.release()
                    build_logger.info(f"[{task_id}] Worker lock released.")

        except Exception as outer_e:
            build_logger.error(f"[{task_id}] Outer build worker error: {str(outer_e)}")
            build_logger.debug(traceback.format_exc())
        finally:
            # Unregister worker so it can be auto-recovered if needed
            builder.unregister_worker(project_id)
            build_logger.info(f"[{task_id}] Worker thread unregistered for project {project_id}.")

    # Start thread
    thread = threading.Thread(target=build_task, daemon=True)
    thread.start()
    return thread


# ============== Project Management Interface ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
def get_project(project_id: str):
    """
    Get project details
    """
    project = ProjectManager.get_project(project_id)

    if not project:
        return jsonify({
            "success": False,
            "error": f"Project does not exist: {project_id}"
        }), 404

    # Check if project is building but task is lost (e.g. server restart)
    from ..models.project import ProjectStatus
    from ..models.task import TaskManager

    # Check ontology task and AUTO-RECOVER if missing
    if project.status == ProjectStatus.ONTOLOGY_GENERATION and project.ontology_task_id:
        if not TaskManager().get_task(project.ontology_task_id):
            logger.warning(f"Project {project_id} lost its ontology task. Attempting auto-recovery...")

            # Re-trigger generate_ontology logic (simplified trigger)
            # Since we have breakpoint logic, this will resume from the analysis phase
            try:
                # We can't easily call the full generate_ontology route here without request context
                # But we can update the error to guide the user to click the button again
                # OR we could implement a dedicated recovery method.
                # For now, let's mark it as recoverable.
                project.error = "任务实例已过期，请点击按钮重新触发（系统将自动从提取进度恢复）。"
                ProjectManager.save_project(project)
            except Exception as re:
                logger.error(f"Auto-recovery failed for project {project_id}: {re}")

    # Check build task
    if project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.GRAPH_CHUNKING, ProjectStatus.GRAPH_EMBEDDING, ProjectStatus.GRAPH_INDEXING]:
        task_id = project.graph_build_task_id
        storage = _get_storage()
        builder = GraphBuilderService(storage=storage)

        # 1. If task ID is missing or task record is gone
        if not task_id or not TaskManager().get_task(task_id):
            logger.warning(f"Project {project_id} is in building status but task record is missing. Starting new recovery task...")
            new_task_id = TaskManager().create_task(f"Recover build: {project.name or project_id}")
            project.graph_build_task_id = new_task_id
            project.error = "检测到后台任务记录丢失，系统已自动创建新任务恢复进度。"
            ProjectManager.save_project(project)
            _start_build_worker(project_id, new_task_id, storage, force=False)

        # 2. Task exists but worker thread is not active
        elif not builder.is_worker_active(project_id):
            logger.info(f"🚀 Project {project_id} has active task {task_id} but NO active worker thread. Auto-starting worker...")
            try:
                _start_build_worker(project_id, task_id, storage, force=False)
                project.error = "检测到后台任务中断，系统已尝试自动恢复进度。"
                ProjectManager.save_project(project)
            except Exception as e:
                logger.error(f"Failed to auto-recover project {project_id}: {e}")

    return jsonify({
        "success": True,
        "data": project.to_dict()
    })


@graph_bp.route('/project/list', methods=['GET'])
def list_projects():
    """
    List all projects
    """
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)
    
    return jsonify({
        "success": True,
        "data": [p.to_dict() for p in projects],
        "count": len(projects)
    })


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
def delete_project(project_id: str):
    """
    Delete project
    """
    success = ProjectManager.delete_project(project_id)

    if not success:
        return jsonify({
            "success": False,
            "error": f"Project does not exist or deletion failed: {project_id}"
        }), 404

    return jsonify({
        "success": True,
        "message": f"Project deleted: {project_id}"
    })


@graph_bp.route('/project/<project_id>', methods=['PATCH'])
def update_project(project_id: str):
    """
    Update project details (e.g., name)
    """
    try:
        data = request.get_json() or {}
        project = ProjectManager.get_project(project_id)

        if not project:
            return jsonify({
                "success": False,
                "error": f"Project does not exist: {project_id}"
            }), 404

        if 'name' in data:
            project.name = data['name']

        if 'current_step' in data:
            try:
                project.current_step = int(data['current_step'])
            except:
                pass

        ProjectManager.save_project(project)

        return jsonify({
            "success": True,
            "message": "Project updated",
            "data": project.to_dict()
        })
    except Exception as e:
        logger.error(f"Failed to update project: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@graph_bp.route('/project/<project_id>/reset', methods=['POST'])
def reset_project(project_id: str):
    """
    Reset project status (for rebuilding graph)
    """
    project = ProjectManager.get_project(project_id)

    if not project:
        return jsonify({
            "success": False,
            "error": f"Project does not exist: {project_id}"
        }), 404

    # Reset to ontology generated state
    if project.ontology:
        project.status = ProjectStatus.ONTOLOGY_GENERATED
    else:
        project.status = ProjectStatus.CREATED

    project.graph_id = None
    project.graph_build_task_id = None
    project.error = None
    ProjectManager.save_project(project)

    return jsonify({
        "success": True,
        "message": f"Project reset: {project_id}",
        "data": project.to_dict()
    })


@graph_bp.route('/project/<project_id>/document/<path:filename>', methods=['GET'])
def get_project_document(project_id: str, filename: str):
    """
    Get a specific document (e.g. PDF) associated with a project for preview.
    Supports both project_id and graph_id as the first parameter.
    """
    # 1. Try to find the actual project object to map IDs
    project = ProjectManager.get_project(project_id)

    # 2. If not found by ID, search all projects for a matching graph_id
    if not project:
        all_projects = ProjectManager.list_projects(limit=500) # Increase limit to be safe
        for p in all_projects:
            if p.graph_id == project_id:
                project = p
                break

    # Determine the actual folder name on disk
    actual_folder_id = project.project_id if project else project_id
    base_dir = os.path.abspath(os.path.join(current_app.root_path, '../uploads/projects', actual_folder_id))

    logger.info(f"Looking for document '{filename}' in project folder: {base_dir}")

    if not os.path.exists(base_dir):
        return jsonify({
            "success": False,
            "error": f"Project directory not found: {base_dir}"
        }), 404

    # 3. Robust search for the file (recursive and fuzzy)
    target_file_path = None
    target_dir = None
    found_filename = None

    # Normalize search filename for comparison
    search_name = filename.lower().strip()

    logger.info(f"Searching for '{search_name}' in {base_dir}...")

    for root, dirs, files in os.walk(base_dir):
        for f in files:
            # Try multiple matching strategies
            f_lower = f.lower().strip()

            # Strategy A: Exact match
            # Strategy B: Search name is part of found file (or vice versa)
            # Strategy C: Ignore common issues with Chinese punctuation
            if f_lower == search_name or search_name in f_lower or f_lower in search_name:
                target_file_path = os.path.join(root, f)
                target_dir = root
                found_filename = f
                break
        if target_file_path:
            break

    if not target_file_path:
        # Debug: list what we DID find to help diagnose
        all_files = []
        for root, dirs, files in os.walk(base_dir):
            for f in files:
                all_files.append(f)

        logger.warning(f"File lookup failed. Files present in project: {all_files}")

        return jsonify({
            "success": False,
            "error": f"Document not found: {filename}. Searched {base_dir}. Found files: {all_files[:10]}...",
            "searched_id": project_id,
            "mapped_id": actual_folder_id
        }), 404

    logger.info(f"Serving document: {found_filename} from {target_dir}")

    from flask import send_file, make_response

    # Send file without any attachment/filename metadata to prevent download triggers
    response = make_response(send_file(target_file_path, mimetype='application/pdf'))

    # Force pure inline mode
    response.headers['Content-Disposition'] = 'inline'
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'ALLOWALL'

    # Remove any headers that might suggest a file download
    if 'Content-Transfer-Encoding' in response.headers:
        del response.headers['Content-Transfer-Encoding']

    return response


# ============== Interface 1: Upload Files and Generate Ontology ==============

@graph_bp.route('/ontology/generate', methods=['POST'])
def generate_ontology():
    """
    Interface 1: Upload files and analyze to generate ontology definition (Asynchronous)
    """
    try:
        logger.info("=== Starting asynchronous ontology generation ===")

        # Get parameters
        simulation_requirement = request.form.get('simulation_requirement', '')
        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')

        # Get uploaded files
        uploaded_files = request.files.getlist('files')
        if not uploaded_files or all(not f.filename for f in uploaded_files):
            return jsonify({
                "success": False,
                "error": "Please upload at least one document file"
            }), 400

        # Create project first
        project = ProjectManager.create_project(name=project_name)
        project.simulation_requirement = simulation_requirement

        # Save files to disk immediately (cannot do this in background thread as request context will be gone)
        saved_files = []
        for file in uploaded_files:
            if file and file.filename and allowed_file(file.filename):
                file_info = ProjectManager.save_file_to_project(
                    project.project_id,
                    file,
                    file.filename
                )
                saved_files.append(file_info)
                project.files.append({
                    "filename": file_info["original_filename"],
                    "size": file_info["size"]
                })

        if not saved_files:
            ProjectManager.delete_project(project.project_id)
            return jsonify({"success": False, "error": "No valid files uploaded"}), 400

        ProjectManager.save_project(project)

        # Create task
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="ontology_generation",
            metadata={"project_id": project.project_id}
        )

        # Update project status and task ID
        project.status = ProjectStatus.ONTOLOGY_GENERATION
        project.ontology_task_id = task_id
        ProjectManager.save_project(project)

        # Start background thread
        def ontology_task():
            try:
                # BREAKPOINT RESUME LOGIC: Check if text is already extracted
                existing_text = ProjectManager.get_extracted_text(project.project_id)
                existing_chunks = ProjectManager.get_chunks(project.project_id)

                document_texts = []
                all_text = ""
                all_chunks_data = []

                if existing_text and existing_chunks:
                    task_manager.update_task(task_id, progress=35, message="Found existing extracted text. Resuming from analysis...")
                    all_text = existing_text
                    all_chunks_data = existing_chunks
                    # Reconstruct document_texts (simplified for ontology)
                    document_texts = [existing_text]
                else:
                    task_manager.update_task(task_id, status=TaskStatus.PROCESSING, progress=5, message="Starting text extraction...")
                    total_files = len(saved_files)
                    for idx, file_info in enumerate(saved_files):
                        orig_name = file_info["original_filename"]
                        # Range 5% - 35%
                        current_progress = 5 + int((idx / total_files) * 30)

                        msg = f"Extracting text from {orig_name} ({idx + 1}/{total_files})..."
                        task_manager.update_task(
                            task_id,
                            progress=current_progress,
                            message=msg,
                            log=msg
                        )

                        try:
                            chunks = FileParser.extract_chunks(file_info["path"], override_filename=orig_name)
                            if not chunks:
                                task_manager.update_task(task_id, log=f"Warning: No text extracted from {orig_name}")
                            else:
                                success_msg = f"Successfully extracted {len(chunks)} chunks from {orig_name}"
                                task_manager.update_task(task_id, log=success_msg)
                        except Exception as ee:
                            task_manager.update_task(task_id, log=f"Extraction failed for {orig_name}: {str(ee)}")
                            # Fallback
                            from pathlib import Path
                            text = Path(file_info["path"]).read_text(encoding='utf-8', errors='replace')
                            chunks = [TextChunk(text=text, metadata={"source": orig_name, "type": "fallback"})]

                        doc_text = "\n\n".join([c.text for c in chunks])
                        doc_text = TextProcessor.preprocess_text(doc_text)
                        document_texts.append(doc_text)
                        all_text += f"\n\n=== {orig_name} ===\n{doc_text}"
                        all_chunks_data.extend([c.to_dict() for c in chunks])

                    # Save extracted data
                    project.total_text_length = len(all_text)
                    ProjectManager.save_extracted_text(project.project_id, all_text)
                    ProjectManager.save_chunks(project.project_id, all_chunks_data)

                task_manager.update_task(task_id, progress=40, message=f"Text ready ({len(all_text)} chars). Loading fixed ontology...")
                task_manager.update_task(task_id, log="Using fixed NormativeEngineeringOntology (replaces dynamic generation)...")

                # 使用固定本体替代动态生成
                from ..services.normative_ontology import NORMATIVE_ONTOLOGY
                ontology = NORMATIVE_ONTOLOGY

                task_manager.update_task(task_id, log="Fixed ontology loaded. Saving to project...")

                # Save to project
                if ontology:
                    project.ontology = {
                        "entity_types": ontology.get("entity_types", []),
                        "edge_types": ontology.get("edge_types", [])
                    }
                    project.analysis_summary = "使用固定工程规范本体定义（Section, Clause, Term, Component, Condition, Action, Requirement, Parameter, Formula）"
                else:
                    project.ontology = {"entity_types": [], "edge_types": []}
                    project.analysis_summary = ""

                project.status = ProjectStatus.ONTOLOGY_GENERATED
                ProjectManager.save_project(project)

                # Complete
                task_manager.complete_task(task_id, {
                    "project_id": project.project_id,
                    "ontology": project.ontology,
                    "analysis_summary": project.analysis_summary,
                    "total_text_length": project.total_text_length
                })
                logger.info(f"Ontology generation task {task_id} completed.")

            except Exception as e:
                logger.error(f"Ontology task failed: {str(e)}\n{traceback.format_exc()}")
                project.status = ProjectStatus.FAILED
                project.error = str(e)
                ProjectManager.save_project(project)
                task_manager.fail_task(task_id, str(e))

        thread = threading.Thread(target=ontology_task, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "task_id": task_id,
                "message": "Ontology generation started"
            }
        })

    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interface 2: Build Graph ==============

@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """
    Interface 2: Build graph based on project_id

    Request (JSON):
        {
            "project_id": "proj_xxxx",  // Required: from interface 1
            "graph_name": "Graph name",    // Optional
            "chunk_size": 500,          // Optional, default 500
            "chunk_overlap": 50         // Optional, default 50
        }

    Response:
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "task_id": "task_xxxx",
                "message": "Graph build task started"
            }
        }
    """
    try:
        logger.info("=== Starting graph build ===")

        # Parse request
        data = request.get_json() or {}
        project_id = data.get('project_id')
        logger.debug(f"Request parameters: project_id={project_id}")

        if not project_id:
            logger.warning("Build graph failed: missing project_id")
            return jsonify({
                "success": False,
                "error": "Please provide project_id"
            }), 400

        # Get project (Force reload from disk to avoid stale state in memory)
        project = ProjectManager.get_project(project_id)
        if not project:
            logger.warning(f"Build graph failed: project {project_id} not found")
            return jsonify({
                "success": False,
                "error": f"Project does not exist: {project_id}"
            }), 404

        # Get configuration
        graph_name = data.get('graph_name', project.name or 'MiroFish Graph')
        chunk_size = data.get('chunk_size', project.chunk_size or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = data.get('chunk_overlap', project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP)
        use_semantic = data.get('semantic', False) # New: option for semantic chunking
        force = data.get('force', False)  # Force rebuild

        # Check project status
        logger.info(f"Project {project_id} status: {project.status}, force={force}")

        # Intelligent status fix: check if ontology data exists even if status is CREATED
        # We check if ontology is a dict and has at least one entity type
        has_ontology = (isinstance(project.ontology, dict) and
                       len(project.ontology.get("entity_types", [])) > 0)

        if project.status == ProjectStatus.CREATED and has_ontology:
            logger.info(f"Project {project_id} has valid ontology despite CREATED status. Auto-fixing status to ONTOLOGY_GENERATED.")
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            ProjectManager.save_project(project)

        # Handle Existing Task vs New Task
        task_id = project.graph_build_task_id
        if not force and project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.GRAPH_CHUNKING, ProjectStatus.GRAPH_EMBEDDING, ProjectStatus.GRAPH_INDEXING] and task_id:
            logger.info(f"Project {project_id} building status detected. Attempting to ensure worker thread is active for task {task_id}.")
        else:
            # Create a brand new task
            task_manager = TaskManager()
            task_id = task_manager.create_task(f"Build graph: {graph_name}")
            logger.info(f"New graph build task created: task_id={task_id}, project_id={project_id}")

            # Update project status
            project.status = ProjectStatus.GRAPH_BUILDING
            project.graph_build_task_id = task_id
            ProjectManager.save_project(project)

        # If force rebuild, reset status and DELETE old graph data
        force_reset_statuses = [
            ProjectStatus.GRAPH_BUILDING,
            ProjectStatus.GRAPH_CHUNKING,
            ProjectStatus.GRAPH_EMBEDDING,
            ProjectStatus.GRAPH_INDEXING,
            ProjectStatus.FAILED,
            ProjectStatus.GRAPH_COMPLETED
        ]
        if force and project.status in force_reset_statuses:
            logger.info(f"Forcing rebuild for project {project_id}. Cleaning up old data...")

            # Physical cleanup of old graph in Neo4j if it exists
            if project.graph_id:
                try:
                    storage = _get_storage()
                    builder = GraphBuilderService(storage=storage)
                    builder.delete_graph(project.graph_id)
                    logger.info(f"Old graph {project.graph_id} deleted successfully.")
                except Exception as de:
                    logger.warning(f"Failed to delete old graph {project.graph_id}: {de}")

            # Reset project metadata for a clean start
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            project.graph_id = None
            project.graph_build_task_id = task_id # Use the task we just created or recovered
            project.error = None
            ProjectManager.save_project(project)

        # Update project configuration
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap
        project.use_semantic = use_semantic
        ProjectManager.save_project(project)

        # Get extracted text
        text = ProjectManager.get_extracted_text(project_id)
        if not text:
            logger.warning(f"Build graph failed: no extracted text for project {project_id}")
            return jsonify({
                "success": False,
                "error": "Extracted text not found"
            }), 400

        # Get storage in request context (background thread cannot access current_app)
        storage = _get_storage()

        # Start background task via helper
        _start_build_worker(project_id, task_id, storage, force=force)
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "message": "Graph build task started. Query progress via /task/{task_id}"
            }
        })
        
    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Task Query Interface ==============

@graph_bp.route('/task/<task_id>', methods=['GET'])
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
    from flask import Response
    import time

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


# ============== Graph Data Interface ==============

@graph_bp.route('/data/<graph_id>', methods=['GET'])
def get_graph_data(graph_id: str):
    """
    Get graph data (nodes and edges)
    """
    try:
        storage = _get_storage()
        builder = GraphBuilderService(storage=storage)
        graph_data = builder.get_graph_data(graph_id)

        return jsonify({
            "success": True,
            "data": graph_data
        })

    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
def delete_graph(graph_id: str):
    """
    Delete graph
    """
    try:
        storage = _get_storage()
        builder = GraphBuilderService(storage=storage)
        builder.delete_graph(graph_id)

        return jsonify({
            "success": True,
            "message": f"Graph deleted: {graph_id}"
        })

    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500



@graph_bp.route('/supplement', methods=['POST'])
def supplement_knowledge():
    """
    Supplement knowledge by processing specific regions of a PDF.
    Expects: { project_id, filename, regions: [{page, bbox: [x0, y0, x1, y1]}] }
    """
    try:
        data = request.get_json() or {}
        project_id = data.get('project_id')
        filename = data.get('filename')
        regions = data.get('regions', [])

        if not project_id or not filename or not regions:
            return jsonify({"success": False, "error": "Missing parameters"}), 400

        project = ProjectManager.get_project(project_id)
        if not project or not project.graph_id:
            return jsonify({"success": False, "error": "Project or Graph not found"}), 404

        # 1. Find the physical file
        project_dir = ProjectManager._get_project_dir(project_id)
        file_path = None
        found_filename = None

        # Robust recursive fuzzy search (same as get_project_document logic)
        search_name = filename.lower().strip()
        for root, dirs, files in os.walk(project_dir):
            for f in files:
                f_lower = f.lower().strip()
                # Strategy: Exact, substring or contains
                if f_lower == search_name or search_name in f_lower or f_lower in search_name:
                    file_path = os.path.join(root, f)
                    found_filename = f
                    break
            if file_path:
                break

        if not file_path:
            # Debug info: see what's actually there
            all_files = []
            for root, dirs, files in os.walk(project_dir):
                all_files.extend(files)
            logger.warning(f"Supplement lookup failed. Searching for '{search_name}'. Present files: {all_files}")
            return jsonify({
                "success": False,
                "error": f"Source file {filename} not found in project directory."
            }), 404

        logger.info(f"Supplementing using file: {file_path}")


        # 2. Extract text from specific regions using PyMuPDF
        import fitz
        from ..utils.file_parser import TextChunk

        supplementary_chunks = []
        doc = fitz.open(file_path)
        total_pages = len(doc)

        for i, reg in enumerate(regions):
            page_num = reg.get('page')
            bbox = reg.get('bbox')
            if not page_num or not bbox or len(bbox) != 4:
                continue

            # fitz pages are 0-indexed
            page = doc[page_num - 1]

            # Get page dimensions
            page_rect = page.rect
            pw, ph = page_rect.width, page_rect.height

            # Extract text from the specific rectangle
            text = page.get_textbox(fitz.Rect(bbox))

            if text.strip():
                supplementary_chunks.append(TextChunk(
                    text=text.strip(),
                    metadata={
                        "source": filename,
                        "page": page_num,
                        "total_pages": total_pages,
                        "bbox": bbox,
                        "page_width": pw,
                        "page_height": ph,
                        "type": "supplementary",
                        "supplement_index": i
                    }
                ))

        doc.close()

        if not supplementary_chunks:
            return jsonify({"success": False, "error": "No text found in selected regions"}), 400

        # 3. Trigger incremental graph building
        storage = _get_storage()
        builder = GraphBuilderService(storage=storage)

        # We process these chunks synchronously for the supplement feature to give immediate feedback
        episode_ids = builder.add_text_batches(
            project.graph_id,
            supplementary_chunks,
            batch_size=1
        )

        return jsonify({
            "success": True,
            "message": f"Successfully supplemented {len(supplementary_chunks)} knowledge fragments.",
            "episode_ids": episode_ids
        })

    except Exception as e:
        logger.error(f"Supplement failed: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@graph_bp.route('/ai-qa', methods=['POST'])
def ai_qa():
    """
    AI Q&A Interface: SSE streaming progress updates.
    """
    import time
    from flask import Response

    data = request.get_json() or {}
    query = data.get('query')
    graph_ids = data.get('graph_ids', [])
    try:
        rerank_threshold = int(data.get('rerank_threshold', 60))
    except (ValueError, TypeError):
        rerank_threshold = 60

    if not query:
        return jsonify({"success": False, "error": "Please provide query"}), 400
    if not graph_ids:
        return jsonify({"success": False, "error": "Please select at least one knowledge base"}), 400

    from flask import stream_with_context
    storage = _get_storage()

    @stream_with_context
    def generate():
        start_total = time.time()
        try:
            tools = GraphToolsService(storage=storage)
            from ..utils.llm_client import LLMClient
            llm = LLMClient()

            # 1. Start Optimization & Retrieval
            yield f"data: {json.dumps({'type': 'retrieval_start'})}\n\n"
            retrieval_start = time.time()

            # Perform retrieval
            search_result = tools.search_with_agentic_flow(graph_ids=graph_ids, query=query, limit=20)
            ret_dur = round(time.time() - retrieval_start, 2)

            # 2. Retrieval Complete
            msg_ret = {
                'type': 'retrieval_complete',
                'data': {
                    'facts': search_result.facts,
                    'duration': ret_dur
                }
            }
            yield f"data: {json.dumps(msg_ret, ensure_ascii=False)}\n\n"

            # 3. Rerank & Filtering
            # Filter facts based on threshold
            all_facts = search_result.facts
            filtered_facts = [f for f in all_facts if f.get('relevance_score', 0) >= rerank_threshold]

            # If nothing passes threshold, keep top 1 as safety
            if not filtered_facts and all_facts:
                filtered_facts = all_facts[:1]

            msg_rerank = {
                'type': 'rerank_complete',
                'data': {
                    'results': search_result.rerank_details,
                    'duration': 'incl.',
                    'filtered_count': len(filtered_facts),
                    'total_count': len(all_facts)
                }
            }
            yield f"data: {json.dumps(msg_rerank, ensure_ascii=False)}\n\n"

            # 4. LLM Generation Start
            yield f"data: {json.dumps({'type': 'llm_start'})}\n\n"
            llm_start = time.time()

            # Use FILTERED facts for the prompt
            from ..services.graph_tools import SearchResult
            # Temporary SearchResult object to use its to_text method
            temp_result = SearchResult(
                facts=filtered_facts,
                edges=[],
                nodes=[],
                query=query,
                total_count=len(filtered_facts)
            )

            facts_text = temp_result.to_text()
            system_prompt = "你是一个专业的工程标准知识助手。你的任务是基于提供的多跳检索到的【知识参考详情】深度回答用户问题。\n\n回答要求：\n1. 请先在 <thought> 标签内分析所有检索到的条文关联，确引用的完整性。\n2. 给出最终结论，必须引用具体的条款编号（如：根据 7.6.49 条规定...）。\n3. 如果知识涉及多个关联条款，请理清它们的逻辑先后关系。\n4. 若信息不足，请如实告知缺失的具体标准名称或编号。"
            user_prompt = f"### 多跳检索结果汇总 (Context from Knowledge Graph):\n{facts_text}\n\n### 用户当前问题 (User Query):\n{query}\n\n请进行深度推理并回答："

            # Store prompts for debugging/visibility in UI
            msg_prompts = {
                'type': 'prompts_ready',
                'data': {
                    'system': system_prompt,
                    'user': user_prompt
                }
            }
            yield f"data: {json.dumps(msg_prompts, ensure_ascii=False)}\n\n"

            answer = llm.chat(messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], temperature=data.get('temperature', 0.7))

            llm_dur = round(time.time() - llm_start, 2)

            # 5. Final LLM Complete
            msg_final = {
                'type': 'llm_complete',
                'data': {
                    'answer': answer,
                    'duration': llm_dur,
                    'total_duration': round(time.time() - start_total, 2)
                }
            }
            yield f"data: {json.dumps(msg_final, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"AI Q&A stream failed: {str(e)}\n{traceback.format_exc()}")
            err_msg = {'type': 'error', 'message': str(e)}
            yield f"data: {json.dumps(err_msg, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype='text/event-stream')

