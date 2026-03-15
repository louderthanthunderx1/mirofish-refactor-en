"""
Graph API routes. Project context is persisted on the server.
"""

import os
import traceback
import threading
from flask import request, jsonify

from . import graph_bp
from ..config import Config
from ..services.ontology_generator import OntologyGenerator
from ..services.graph_builder import GraphBuilderService
from ..services.text_processor import TextProcessor
from ..utils.file_parser import FileParser
from ..utils.logger import get_logger
from ..models.task import TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus

logger = get_logger('mirofish.api')


def allowed_file(filename: str) -> bool:
    """Check if file extension is allowed."""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS


# ============== Project management ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
def get_project(project_id: str):
    """Get project details."""
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({
            "success": False,
            "error": f"Project not found: {project_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "data": project.to_dict()
    })


@graph_bp.route('/project/list', methods=['GET'])
def list_projects():
    """List all projects."""
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)
    
    return jsonify({
        "success": True,
        "data": [p.to_dict() for p in projects],
        "count": len(projects)
    })


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
def delete_project(project_id: str):
    """Delete project."""
    success = ProjectManager.delete_project(project_id)
    if not success:
        return jsonify({
            "success": False,
            "error": f"Project not found or delete failed: {project_id}"
        }), 404
    return jsonify({
        "success": True,
        "message": f"Project deleted: {project_id}"
    })


@graph_bp.route('/project/<project_id>/reset', methods=['POST'])
def reset_project(project_id: str):
    """Reset project state (e.g. to rebuild graph)."""
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({
            "success": False,
            "error": f"Project not found: {project_id}"
        }), 404
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


# ============== API 1: Upload files and generate ontology ==============

@graph_bp.route('/ontology/generate', methods=['POST'])
def generate_ontology():
    """Upload files and generate ontology (multipart/form-data: files, simulation_requirement, project_name, additional_context)."""
    try:
        logger.info("=== Generating ontology ===")
        simulation_requirement = request.form.get('simulation_requirement', '')
        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')
        logger.debug("project_name: %s", project_name)
        logger.debug("simulation_requirement: %s...", simulation_requirement[:100])
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "simulation_requirement is required"
            }), 400
        uploaded_files = request.files.getlist('files')
        if not uploaded_files or all(not f.filename for f in uploaded_files):
            return jsonify({
                "success": False,
                "error": "Upload at least one document file"
            }), 400
        project = ProjectManager.create_project(name=project_name)
        project.simulation_requirement = simulation_requirement
        logger.info("Created project: %s", project.project_id)
        document_texts = []
        all_text = ""
        for file in uploaded_files:
            if file and file.filename and allowed_file(file.filename):
                file_info = ProjectManager.save_file_to_project(
                    project.project_id, 
                    file, 
                    file.filename
                )
                project.files.append({
                    "filename": file_info["original_filename"],
                    "size": file_info["size"]
                })
                
                text = FileParser.extract_text(file_info["path"])
                text = TextProcessor.preprocess_text(text)
                document_texts.append(text)
                all_text += f"\n\n=== {file_info['original_filename']} ===\n{text}"
        
        if not document_texts:
            ProjectManager.delete_project(project.project_id)
            return jsonify({
                "success": False,
                "error": "No document was processed; check file format"
            }), 400
        project.total_text_length = len(all_text)
        ProjectManager.save_extracted_text(project.project_id, all_text)
        logger.info("Text extracted: %d chars", len(all_text))
        logger.info("Calling LLM to generate ontology...")
        generator = OntologyGenerator()
        ontology = generator.generate(
            document_texts=document_texts,
            simulation_requirement=simulation_requirement,
            additional_context=additional_context if additional_context else None
        )
        
        entity_count = len(ontology.get("entity_types", []))
        edge_count = len(ontology.get("edge_types", []))
        logger.info("Ontology done: %d entity types, %d edge types", entity_count, edge_count)
        
        project.ontology = {
            "entity_types": ontology.get("entity_types", []),
            "edge_types": ontology.get("edge_types", [])
        }
        project.analysis_summary = ontology.get("analysis_summary", "")
        project.status = ProjectStatus.ONTOLOGY_GENERATED
        ProjectManager.save_project(project)
        logger.info("=== Ontology complete === project_id=%s", project.project_id)
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "project_name": project.name,
                "ontology": project.ontology,
                "analysis_summary": project.analysis_summary,
                "files": project.files,
                "total_text_length": project.total_text_length
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== API 2: Build graph ==============

@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """Build graph for project_id. JSON: project_id, graph_name?, chunk_size?, chunk_overlap?, force?."""
    try:
        logger.info("=== Building graph ===")
        errors = Config.validate()
        if errors:
            logger.error("Config errors: %s", errors)
            return jsonify({
                "success": False,
                "error": "Config error: " + "; ".join(errors)
            }), 500
        data = request.get_json() or {}
        project_id = data.get('project_id')
        logger.debug("Request project_id=%s", project_id)
        if not project_id:
            return jsonify({
                "success": False,
                "error": "project_id is required"
            }), 400
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"Project not found: {project_id}"
            }), 404

        force = data.get('force', False)

        if project.status == ProjectStatus.CREATED:
            return jsonify({
                "success": False,
                "error": "Ontology not generated yet; call /ontology/generate first"
            }), 400

        if project.status == ProjectStatus.GRAPH_BUILDING and not force:
            return jsonify({
                "success": False,
                "error": "Graph build in progress; use force: true to rebuild",
                "task_id": project.graph_build_task_id
            }), 400
        
        if force and project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.FAILED, ProjectStatus.GRAPH_COMPLETED]:
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            project.graph_id = None
            project.graph_build_task_id = None
            project.error = None

        graph_name = data.get('graph_name', project.name or 'MiroFish Graph')
        chunk_size = data.get('chunk_size', project.chunk_size or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = data.get('chunk_overlap', project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP)
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap

        text = ProjectManager.get_extracted_text(project_id)
        if not text:
            return jsonify({
                "success": False,
                "error": "Extracted text not found"
            }), 400

        ontology = project.ontology
        if not ontology:
            return jsonify({
                "success": False,
                "error": "Ontology not found"
            }), 400

        task_manager = TaskManager()
        task_id = task_manager.create_task("graph_build", metadata={"graph_name": graph_name})
        logger.info("Graph build task created: task_id=%s, project_id=%s", task_id, project_id)
        project.status = ProjectStatus.GRAPH_BUILDING
        project.graph_build_task_id = task_id
        ProjectManager.save_project(project)

        def build_task():
            build_logger = get_logger('mirofish.build')
            try:
                build_logger.info("[%s] Starting graph build...", task_id)
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    message="Initializing graph build..."
                )
                builder = GraphBuilderService()
                task_manager.update_task(
                    task_id,
                    message="Splitting text...",
                    progress=5
                )
                chunks = TextProcessor.split_text(
                    text,
                    chunk_size=chunk_size,
                    overlap=chunk_overlap
                )
                total_chunks = len(chunks)
                graph_id = builder.backend.create_graph(name=graph_name)
                project.graph_id = graph_id
                ProjectManager.save_project(project)
                task_manager.update_task(
                    task_id,
                    message="Setting ontology...",
                    progress=15
                )
                builder.backend.set_ontology(graph_id, ontology)
                def add_progress_callback(msg, progress_ratio):
                    progress = 15 + int(progress_ratio * 40)
                    task_manager.update_task(task_id, message=msg, progress=progress)
                task_manager.update_task(
                    task_id,
                    message=f"Adding {total_chunks} chunks...",
                    progress=15
                )
                episode_uuids = builder.backend.add_text_batches(
                    graph_id,
                    chunks,
                    batch_size=3,
                    progress_callback=add_progress_callback
                )
                task_manager.update_task(
                    task_id,
                    message="Waiting for processing...",
                    progress=55
                )
                def wait_progress_callback(msg, progress_ratio):
                    progress = 55 + int(progress_ratio * 35)
                    task_manager.update_task(task_id, message=msg, progress=progress)
                builder.backend.wait_for_episodes(episode_uuids, wait_progress_callback)
                task_manager.update_task(
                    task_id,
                    message="Fetching graph data...",
                    progress=95
                )
                graph_data = builder.get_graph_data(graph_id)
                project.status = ProjectStatus.GRAPH_COMPLETED
                ProjectManager.save_project(project)
                node_count = graph_data.get("node_count", 0)
                edge_count = graph_data.get("edge_count", 0)
                build_logger.info("[%s] Graph build complete: graph_id=%s, nodes=%s, edges=%s", task_id, graph_id, node_count, edge_count)
                task_manager.complete_task(task_id, {
                    "project_id": project_id,
                    "graph_id": graph_id,
                    "node_count": node_count,
                    "edge_count": edge_count,
                    "chunk_count": total_chunks
                })
            except Exception as e:
                build_logger.error("[%s] Graph build failed: %s", task_id, str(e))
                build_logger.debug(traceback.format_exc())
                project.status = ProjectStatus.FAILED
                project.error = str(e)
                ProjectManager.save_project(project)
                task_manager.fail_task(task_id, str(e))

        thread = threading.Thread(target=build_task, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "message": "Graph build started; poll /task/{task_id} for progress"
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Task API ==============

@graph_bp.route('/task/<task_id>', methods=['GET'])
def get_task(task_id: str):
    """Get task status."""
    task = TaskManager().get_task(task_id)
    if not task:
        return jsonify({
            "success": False,
            "error": f"Task not found: {task_id}"
        }), 404
    return jsonify({
        "success": True,
        "data": task.to_dict()
    })


@graph_bp.route('/tasks', methods=['GET'])
def list_tasks():
    """List all tasks."""
    tasks = TaskManager().list_tasks()
    return jsonify({
        "success": True,
        "data": tasks,
        "count": len(tasks)
    })


# ============== Graph data API ==============

@graph_bp.route('/data/<graph_id>', methods=['GET'])
def get_graph_data(graph_id: str):
    """Get graph data (nodes and edges)."""
    try:
        errors = Config.validate()
        if errors:
            return jsonify({
                "success": False,
                "error": "; ".join(errors)
            }), 500
        builder = GraphBuilderService()
        graph_data = builder.get_graph_data(graph_id)
        
        return jsonify({
            "success": True,
            "data": graph_data
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
def delete_graph(graph_id: str):
    """Delete graph."""
    try:
        errors = Config.validate()
        if errors:
            return jsonify({
                "success": False,
                "error": "; ".join(errors)
            }), 500
        builder = GraphBuilderService()
        builder.delete_graph(graph_id)
        return jsonify({
            "success": True,
            "message": f"Graph deleted: {graph_id}"
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
