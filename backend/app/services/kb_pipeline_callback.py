"""
KB Pipeline 回调服务模块

Pipeline 每个阶段完成后触发外部 HTTP 回调通知。
回调目标 URL 从 Pipeline 创建时传入的 kb_pipeline_callback_url 获取，不再依赖 .env 硬编码。
回调在线程池中异步执行，失败仅记录日志，不阻断 Pipeline 执行。
"""

import json
import requests
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any

from ..utils.logger import get_logger

logger = get_logger('mirofish.kb_pipeline_callback')

# 默认超时时间（秒）
DEFAULT_TIMEOUT = 30

# 线程池用于异步发送回调（避免阻塞 Pipeline 主线程）
_callback_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kb-callback-")


def _send_callback(callback_url: str, body: Dict[str, Any]) -> None:
    """在线程中执行 HTTP POST 回调"""
    try:
        response = requests.post(
            callback_url,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=DEFAULT_TIMEOUT
        )
        if response.status_code < 300:
            logger.info(
                f"Pipeline {body.get('pipeline_id')} 回调成功: "
                f"status={response.status_code}"
            )
        else:
            logger.warning(
                f"Pipeline {body.get('pipeline_id')} 回调非成功状态: "
                f"status={response.status_code}, body={response.text[:200]}"
            )
    except requests.exceptions.Timeout:
        logger.warning(f"Pipeline {body.get('pipeline_id')} 回调超时")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Pipeline {body.get('pipeline_id')} 回调请求异常: {e}")
    except Exception as e:
        logger.error(f"Pipeline {body.get('pipeline_id')} 回调未知异常: {e}")


def invoke_callback(pipeline_data: Dict[str, Any]) -> None:
    """
    Pipeline 阶段完成后触发外部 HTTP 回调

    Args:
        pipeline_data: 完整的 Pipeline JSON 数据（KbPipeline.to_dict() 结果）

    注意：本方法立即返回，HTTP 请求在线程池中异步执行，不会阻塞调用方。
    回调地址仅使用 Pipeline 创建时传入的 kb_pipeline_callback_url，不再 fallback 到 .env。
    """
    callback_url = pipeline_data.get("kb_pipeline_callback_url")
    if not callback_url:
        logger.debug(f"Pipeline {pipeline_data.get('pipeline_id')}: 未传入 kb_pipeline_callback_url，跳过回调")
        return

    # 提交到线程池异步执行，不阻塞 Pipeline 主线程
    _callback_executor.submit(_send_callback, callback_url, pipeline_data)
