import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from ..config import Config

@dataclass
class AiApp:
    """AI Application data model (Workflow configuration)"""
    app_id: str
    name: str
    nodes: List[Dict[str, Any]]
    workflow_data: Dict[str, Any]
    created_at: str
    updated_at: str
    description: Optional[str] = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app_id": self.app_id,
            "name": self.name,
            "nodes": self.nodes,
            "workflow_data": self.workflow_data,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "description": self.description
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AiApp':
        return cls(
            app_id=data['app_id'],
            name=data.get('name', 'New AI App'),
            nodes=data.get('nodes', []),
            workflow_data=data.get('workflow_data', {}),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
            description=data.get('description', '')
        )

class AiAppManager:
    """Manager for AI Knowledge Base Applications"""
    APPS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'ai_apps')

    @classmethod
    def _ensure_apps_dir(cls):
        os.makedirs(cls.APPS_DIR, exist_ok=True)

    @classmethod
    def _get_app_path(cls, app_id: str) -> str:
        return os.path.join(cls.APPS_DIR, f"{app_id}.json")

    @classmethod
    def save_app(cls, app_data: Dict[str, Any]) -> AiApp:
        cls._ensure_apps_dir()

        app_id = app_data.get('app_id')
        now = datetime.now().isoformat()

        if not app_id:
            app_id = f"app_{uuid.uuid4().hex[:12]}"
            app_data['app_id'] = app_id
            app_data['created_at'] = now

        app_data['updated_at'] = now
        app = AiApp.from_dict(app_data)

        path = cls._get_app_path(app_id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(app.to_dict(), f, ensure_ascii=False, indent=2)

        return app

    @classmethod
    def get_app(cls, app_id: str) -> Optional[AiApp]:
        path = cls._get_app_path(app_id)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return AiApp.from_dict(json.load(f))

    @classmethod
    def list_apps(cls, limit: int = 50) -> List[AiApp]:
        cls._ensure_apps_dir()
        apps = []
        for filename in os.listdir(cls.APPS_DIR):
            if filename.endswith('.json'):
                app_id = filename.replace('.json', '')
                app = cls.get_app(app_id)
                if app:
                    apps.append(app)

        apps.sort(key=lambda x: x.created_at, reverse=True)
        return apps[:limit]

    @classmethod
    def delete_app(cls, app_id: str) -> bool:
        path = cls._get_app_path(app_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
