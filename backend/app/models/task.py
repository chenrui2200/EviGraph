"""
Task Status Management
Tracks long-running tasks (like graph building)
"""

import os
import json
import uuid
import threading
import queue
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field
from ..config import Config


class TaskStatus(str, Enum):
    """Task status enumeration"""
    PENDING = "pending"          # Pending
    PROCESSING = "processing"    # Processing
    COMPLETED = "completed"      # Completed
    FAILED = "failed"            # Failed


@dataclass
class Task:
    """Task data class"""
    task_id: str
    task_type: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    progress: int = 0              # Overall progress percentage 0-100
    message: str = ""              # Status message
    result: Optional[Dict] = None  # Task result
    error: Optional[str] = None    # Error message
    metadata: Dict = field(default_factory=dict)  # Additional metadata
    progress_detail: Dict = field(default_factory=dict)  # Detailed progress information
    logs: List[Dict[str, str]] = field(default_factory=list) # Task execution logs

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "progress": self.progress,
            "message": self.message,
            "progress_detail": self.progress_detail,
            "logs": self.logs,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Task':
        """Create Task instance from dictionary"""
        return cls(
            task_id=data['task_id'],
            task_type=data.get('task_type', 'unknown'),
            status=TaskStatus(data.get('status', 'pending')),
            created_at=datetime.fromisoformat(data['created_at']),
            updated_at=datetime.fromisoformat(data['updated_at']),
            progress=data.get('progress', 0),
            message=data.get('message', ''),
            result=data.get('result'),
            error=data.get('error'),
            metadata=data.get('metadata', {}),
            progress_detail=data.get('progress_detail', {}),
            logs=data.get('logs', [])
        )


class TaskManager:
    """
    Task Manager
    Thread-safe task status management with:
    1. Event Pub/Sub for SSE support
    2. Disk persistence for crash recovery
    """

    _instance = None
    _lock = threading.Lock()
    TASKS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'tasks')

    def __new__(cls):
        """Singleton pattern"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._tasks: Dict[str, Task] = {}
                    cls._instance._task_lock = threading.Lock()
                    # Pub/Sub storage: task_id -> set of event queues
                    cls._instance._subscribers: Dict[str, Set[queue.Queue]] = {}
                    cls._instance._sub_lock = threading.Lock()

                    # Ensure tasks directory exists and load existing tasks
                    os.makedirs(cls.TASKS_DIR, exist_ok=True)
                    cls._instance._load_tasks_from_disk()
        return cls._instance

    def _load_tasks_from_disk(self):
        """Initial load of all tasks from the tasks directory on startup"""
        if not os.path.exists(self.TASKS_DIR):
            return

        loaded_count = 0
        for filename in os.listdir(self.TASKS_DIR):
            if filename.endswith(".json") and not filename.endswith(".tmp"):
                task_path = os.path.join(self.TASKS_DIR, filename)
                try:
                    with open(task_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        task = Task.from_dict(data)
                        self._tasks[task.task_id] = task
                        loaded_count += 1
                except Exception as e:
                    from ..utils.logger import get_logger
                    get_logger('mirofish.task').error(f"Failed to load task file {filename}: {e}")

        if loaded_count > 0:
            from ..utils.logger import get_logger
            get_logger('mirofish.task').info(f"Successfully loaded {loaded_count} tasks from disk persistence.")

    def _save_task_to_disk(self, task: Task):
        """Save a single task to disk atomically"""
        task_path = os.path.join(self.TASKS_DIR, f"{task.task_id}.json")
        temp_path = task_path + ".tmp"

        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)

            # Atomic swap
            if os.path.exists(task_path):
                os.replace(temp_path, task_path)
            else:
                os.rename(temp_path, task_path)
        except Exception as e:
            from ..utils.logger import get_logger
            get_logger('mirofish.task').error(f"Failed to persist task {task.task_id} to disk: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def subscribe(self, task_id: str) -> queue.Queue:
        """Subscribe to a task's events"""
        q = queue.Queue()
        with self._sub_lock:
            if task_id not in self._subscribers:
                self._subscribers[task_id] = set()
            self._subscribers[task_id].add(q)
        return q

    def unsubscribe(self, task_id: str, q: queue.Queue):
        """Unsubscribe from a task's events"""
        with self._sub_lock:
            if task_id in self._subscribers:
                self._subscribers[task_id].discard(q)
                if not self._subscribers[task_id]:
                    del self._subscribers[task_id]

    def _publish(self, task_id: str, event_data: Dict[str, Any]):
        """Internal: Publish event to all subscribers of a task"""
        with self._sub_lock:
            if task_id in self._subscribers:
                # Event includes standard SSE format fields
                for q in self._subscribers[task_id]:
                    q.put(event_data)

    def create_task(self, task_type: str, metadata: Optional[Dict] = None) -> str:
        """
        Create new task
        """
        task_id = str(uuid.uuid4())
        now = datetime.now()

        task = Task(
            task_id=task_id,
            task_type=task_type,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
            metadata=metadata or {}
        )

        with self._task_lock:
            self._tasks[task_id] = task
            self._save_task_to_disk(task)

        return task_id

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task"""
        with self._task_lock:
            return self._tasks.get(task_id)

    def update_task(
        self,
        task_id: str,
        status: Optional[TaskStatus] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        result: Optional[Dict] = None,
        error: Optional[str] = None,
        progress_detail: Optional[Dict] = None,
        log: Optional[str] = None
    ):
        """
        Update task status and notify SSE subscribers + Disk sync
        """
        with self._task_lock:
            task = self._tasks.get(task_id)
            if task:
                task.updated_at = datetime.now()
                event_payload = {"task_id": task_id, "timestamp": task.updated_at.isoformat()}

                if status is not None:
                    task.status = status
                    event_payload["status"] = status.value
                if progress is not None:
                    task.progress = progress
                    event_payload["progress"] = progress

                new_logs = []
                if message is not None:
                    task.message = message
                    event_payload["message"] = message
                    log_entry = {"timestamp": datetime.now().strftime("%H:%M:%S"), "message": message}
                    task.logs.append(log_entry)
                    new_logs.append(log_entry)

                if log is not None:
                    log_entry = {"timestamp": datetime.now().strftime("%H:%M:%S"), "message": log}
                    task.logs.append(log_entry)
                    new_logs.append(log_entry)

                if new_logs:
                    event_payload["new_logs"] = new_logs

                if result is not None:
                    task.result = result
                    event_payload["result"] = result
                if error is not None:
                    task.error = error
                    log_entry = {"timestamp": datetime.now().strftime("%H:%M:%S"), "message": f"ERROR: {error}"}
                    task.logs.append(log_entry)
                    event_payload["error"] = error
                    event_payload["new_logs"] = event_payload.get("new_logs", []) + [log_entry]

                if progress_detail is not None:
                    task.progress_detail = progress_detail
                    event_payload["progress_detail"] = progress_detail

                # Limit memory logs (slightly larger for disk version)
                if len(task.logs) > 1000:
                    task.logs = task.logs[-1000:]

                # Sync to Disk
                self._save_task_to_disk(task)

                # Notify all SSE listeners
                self._publish(task_id, event_payload)

    def complete_task(self, task_id: str, result: Dict):
        """Mark task as completed"""
        self.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            message="Task completed",
            result=result
        )

    def fail_task(self, task_id: str, error: str):
        """Mark task as failed"""
        self.update_task(
            task_id,
            status=TaskStatus.FAILED,
            message="Task failed",
            error=error
        )

    def list_tasks(self, task_type: Optional[str] = None) -> list:
        """List tasks"""
        with self._task_lock:
            tasks = list(self._tasks.values())
            if task_type:
                tasks = [t for t in tasks if t.task_type == task_type]
            return [t.to_dict() for t in sorted(tasks, key=lambda x: x.created_at, reverse=True)]

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """Clean up old tasks from memory AND disk"""
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(hours=max_age_hours)

        with self._task_lock:
            old_ids = [
                tid for tid, task in self._tasks.items()
                if task.created_at < cutoff and task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]
            ]
            for tid in old_ids:
                # Remove from memory
                del self._tasks[tid]
                # Remove from disk
                task_path = os.path.join(self.TASKS_DIR, f"{tid}.json")
                if os.path.exists(task_path):
                    os.remove(task_path)


