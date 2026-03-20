"""
Graph-related API Routes
Uses project context mechanism with server-side state persistence
"""

import os
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
    if project.status == ProjectStatus.GRAPH_BUILDING and project.graph_build_task_id:
        from ..models.task import TaskManager
        if not TaskManager().get_task(project.graph_build_task_id):
            logger.warning(f"Project {project_id} is in building status but task {project.graph_build_task_id} is missing (restarted?). Auto-fixing status.")
            project.status = ProjectStatus.FAILED
            project.error = "Graph building task lost (likely due to server restart). Please rebuild."
            ProjectManager.save_project(project)

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

        # Start background thread
        def ontology_task():
            try:
                task_manager.update_task(task_id, status=TaskStatus.PROCESSING, progress=5, message="Starting text extraction...")

                document_texts = []
                all_text = ""
                all_chunks_data = []

                for file_info in saved_files:
                    orig_name = file_info["original_filename"]
                    task_manager.update_task(task_id, log=f"Extracting chunks from {orig_name}...")

                    try:
                        chunks = FileParser.extract_chunks(file_info["path"], override_filename=orig_name)
                        if not chunks:
                            task_manager.update_task(task_id, log=f"Warning: No text extracted from {orig_name}")
                        else:
                            task_manager.update_task(task_id, log=f"Successfully extracted {len(chunks)} chunks from {orig_name}")
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

                task_manager.update_task(task_id, progress=40, message=f"Extraction completed ({len(all_text)} chars). Calling LLM...")
                task_manager.update_task(task_id, log="Analyzing document structure for ontology generation...")

                # Generate ontology
                generator = OntologyGenerator()
                task_manager.update_task(task_id, log="Calling LLM (Phase 1: Global Semantic Analysis)...")

                ontology = generator.generate(
                    document_texts=document_texts,
                    simulation_requirement=simulation_requirement,
                    additional_context=additional_context if additional_context else None
                )

                task_manager.update_task(task_id, log="LLM generation completed. Saving ontology schema...")

                # Save to project
                if ontology:
                    project.ontology = {
                        "entity_types": ontology.get("entity_types", []),
                        "edge_types": ontology.get("edge_types", [])
                    }
                    project.analysis_summary = ontology.get("analysis_summary", "")
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

        # Check project status
        force = data.get('force', False)  # Force rebuild
        logger.info(f"Project {project_id} status: {project.status}, force={force}")

        # Intelligent status fix: check if ontology data exists even if status is CREATED
        # We check if ontology is a dict and has at least one entity type
        has_ontology = (isinstance(project.ontology, dict) and
                       len(project.ontology.get("entity_types", [])) > 0)

        if project.status == ProjectStatus.CREATED and has_ontology:
            logger.info(f"Project {project_id} has valid ontology despite CREATED status. Auto-fixing status to ONTOLOGY_GENERATED.")
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            ProjectManager.save_project(project)

        # If force rebuild, reset status
        if force and project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.FAILED, ProjectStatus.GRAPH_COMPLETED]:
            logger.info(f"Forcing rebuild for project {project_id}")
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            project.graph_id = None
            project.graph_build_task_id = None
            project.error = None

        # Get configuration
        graph_name = data.get('graph_name', project.name or 'MiroFish Graph')
        chunk_size = data.get('chunk_size', project.chunk_size or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = data.get('chunk_overlap', project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP)

        # Update project configuration
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap

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

        # Create async task
        task_manager = TaskManager()
        task_id = task_manager.create_task(f"Build graph: {graph_name}")
        logger.info(f"Graph build task created: task_id={task_id}, project_id={project_id}")
        
        # Update project status
        project.status = ProjectStatus.GRAPH_BUILDING
        project.graph_build_task_id = task_id
        ProjectManager.save_project(project)

        # Start background task
        def build_task():
            build_logger = get_logger('mirofish.build')
            try:
                build_logger.info(f"[{task_id}] Starting graph build...")
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    message="Initializing graph build service..."
                )

                # Create graph builder service (storage passed from outer closure)
                builder = GraphBuilderService(storage=storage)

                # Get data (chunks with metadata preferred)
                task_manager.update_task(
                    task_id,
                    message="Preparing text chunks with metadata...",
                    progress=5
                )
                chunks_data = ProjectManager.get_chunks(project_id)
                if chunks_data:
                    # Convert dicts back to TextChunk objects
                    from ..utils.file_parser import TextChunk
                    initial_chunks = [TextChunk(c["text"], c["metadata"]) for c in chunks_data]

                    # Split into smaller chunks preserving metadata
                    chunks = TextProcessor.split_chunks(
                        initial_chunks,
                        chunk_size=chunk_size,
                        overlap=chunk_overlap
                    )
                    build_logger.info(f"Using {len(chunks)} chunks with metadata from chunks.json")
                else:
                    # Fallback to plain text splitting
                    build_logger.warning("chunks.json not found, falling back to plain text splitting")
                    chunks = TextProcessor.split_text(
                        text,
                        chunk_size=chunk_size,
                        overlap=chunk_overlap
                    )

                total_chunks = len(chunks)

                # Create graph
                task_manager.update_task(
                    task_id,
                    message="Creating Zep graph...",
                    progress=10
                )
                graph_id = builder.create_graph(name=graph_name)

                # Update project graph_id
                project.graph_id = graph_id
                ProjectManager.save_project(project)

                # Set ontology
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

                episode_uuids = builder.add_text_batches(
                    graph_id,
                    chunks,
                    batch_size=3,
                    progress_callback=add_progress_callback
                )

                # Neo4j processing is synchronous, no need to wait
                task_manager.update_task(
                    task_id,
                    message="Text processing completed, generating graph data...",
                    progress=90
                )

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

        # Start background thread
        thread = threading.Thread(target=build_task, daemon=True)
        thread.start()

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


@graph_bp.route('/ai-qa', methods=['POST'])
def ai_qa():
    """
    AI Q&A Interface: retrieval from multiple graphs + LLM answering
    """
    try:
        data = request.get_json() or {}
        query = data.get('query')
        graph_ids = data.get('graph_ids', [])

        if not query:
            return jsonify({"success": False, "error": "Please provide query"}), 400

        if not graph_ids:
            return jsonify({"success": False, "error": "Please select at least one knowledge base (graph)"}), 400

        storage = _get_storage()
        tools = GraphToolsService(storage=storage)

        # 1. Retrieval
        logger.info(f"AI Q&A Retrieval: {query[:50]}... from {len(graph_ids)} graphs")
        search_result = tools.search_multi_graphs(graph_ids=graph_ids, query=query, limit=20)

        # 2. LLM Answer
        facts_text = search_result.to_text()

        system_prompt = "你是一个专业的知识库问答助手。请基于提供的知识库内容（事实和实体）回答用户的问题。如果知识库中没有相关信息，请明确告知用户。请保持回答的专业性、准确性和简洁性。"
        user_prompt = f"### 知识库内容：\n{facts_text}\n\n### 用户问题：\n{query}\n\n请基于上述知识库内容进行回答："

        from ..utils.llm_client import LLMClient
        llm = LLMClient()

        logger.info("Calling LLM for AI Q&A answer...")
        answer = llm.chat(messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])

        return jsonify({
            "success": True,
            "data": {
                "query": query,
                "answer": answer,
                "retrieved_facts": search_result.facts,
                "graph_ids": graph_ids
            }
        })

    except Exception as e:
        logger.error(f"AI Q&A failed: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
