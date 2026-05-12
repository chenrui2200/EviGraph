"""
KB Pipeline 回调服务模块

Pipeline 每个阶段完成后触发外部 HTTP 回调通知。
回调目标 URL 从环境变量 KB_PIPELINE_CALLBACK_URL 读取。
回调失败仅记录日志，不阻断 Pipeline 执行。
"""

import os
import json
import requests
from datetime import datetime
from typing import Optional, Dict, Any

from ..utils.logger import get_logger

logger = get_logger('mirofish.kb_pipeline_callback')

# 默认超时时间（秒）
DEFAULT_TIMEOUT = 30


def _get_callback_url() -> Optional[str]:
    """从环境变量获取回调目标 URL"""
    return os.environ.get('KB_PIPELINE_CALLBACK_URL')


def invoke_callback(
    pipeline_id: str,
    stage_name: str,
    stage_status: str,
    message: str,
    payload: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Pipeline 阶段完成后触发外部 HTTP 回调

    Args:
        pipeline_id: Pipeline ID
        stage_name: 阶段名称（如 project_creation, graph_building 等）
        stage_status: 阶段状态（pending/processing/completed/failed/skipped）
        message: 阶段消息
        payload: 可选的额外负载数据

    Returns:
        bool: 回调是否成功发送
    """
    callback_url = _get_callback_url()
    if not callback_url:
        logger.debug(f"Pipeline {pipeline_id} stage {stage_name}: KB_PIPELINE_CALLBACK_URL 未配置，跳过回调")
        return False

    body = {
        "pipeline_id": pipeline_id,
        "stage_name": stage_name,
        "stage_status": stage_status,
        "message": message,
        "payload": payload or {},
        "timestamp": datetime.now().isoformat(),
    }

    try:
        response = requests.post(
            callback_url,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=DEFAULT_TIMEOUT
        )
        if response.status_code < 300:
            logger.info(
                f"Pipeline {pipeline_id} stage {stage_name} 回调成功: "
                f"status={response.status_code}"
            )
            return True
        else:
            logger.warning(
                f"Pipeline {pipeline_id} stage {stage_name} 回调非成功状态: "
                f"status={response.status_code}, body={response.text[:200]}"
            )
            return False
    except requests.exceptions.Timeout:
        logger.warning(f"Pipeline {pipeline_id} stage {stage_name} 回调超时")
        return False
    except requests.exceptions.RequestException as e:
        logger.warning(f"Pipeline {pipeline_id} stage {stage_name} 回调请求异常: {e}")
        return False
    except Exception as e:
        logger.error(f"Pipeline {pipeline_id} stage {stage_name} 回调未知异常: {e}")
        return False
