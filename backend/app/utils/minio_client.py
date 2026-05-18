"""
MinIO 客户端封装
"""

from urllib.parse import urlparse
from minio import Minio
from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.minio')


def get_minio_client() -> Minio:
    """获取配置好的 MinIO 客户端"""
    endpoint = Config.MINIO_ENDPOINT
    # 如果 endpoint 包含 scheme，提取 host:port
    if endpoint.startswith('http://') or endpoint.startswith('https://'):
        parsed = urlparse(endpoint)
        host = parsed.hostname or 'localhost'
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        secure = parsed.scheme == 'https'
        endpoint = f"{host}:{port}"
    else:
        secure = Config.MINIO_SECURE

    return Minio(
        endpoint,
        access_key=Config.MINIO_ACCESS_KEY,
        secret_key=Config.MINIO_SECRET_KEY,
        secure=secure,
    )


def list_pdf_objects(prefix: str = "", recursive: bool = True) -> list:
    """列出 MinIO 桶中的 PDF 对象"""
    try:
        client = get_minio_client()
        objects = client.list_objects(
            Config.MINIO_BUCKET_NAME,
            prefix=prefix,
            recursive=recursive,
        )
        pdf_objects = []
        for obj in objects:
            if obj.object_name.lower().endswith('.pdf'):
                pdf_objects.append({
                    "name": obj.object_name,
                    "size": obj.size,
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                })
        return pdf_objects
    except Exception as e:
        logger.error(f"MinIO list_objects failed: {e}")
        raise


def download_object(object_name: str, file_path: str) -> None:
    """下载 MinIO 对象到本地路径，支持对象键或完整 HTTP URL"""
    if object_name.startswith('http://') or object_name.startswith('https://'):
        import requests
        response = requests.get(object_name, timeout=300)
        response.raise_for_status()
        with open(file_path, 'wb') as f:
            f.write(response.content)
        logger.info(f"URL download: {object_name} -> {file_path}")
    else:
        client = get_minio_client()
        client.fget_object(Config.MINIO_BUCKET_NAME, object_name, file_path)
        logger.info(f"MinIO download: {object_name} -> {file_path}")
