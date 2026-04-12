"""
统一 API 响应格式和异常处理工具
"""

import functools
import logging
from flask import jsonify
from typing import Any, Dict, Optional, Callable

logger = logging.getLogger('mirofish.api_utils')


def success_response(data: Any = None, message: str = "OK", status_code: int = 200) -> tuple:
    """
    统一成功响应格式

    Args:
        data: 响应数据
        message: 成功消息
        status_code: HTTP 状态码

    Returns:
        (response, status_code) tuple
    """
    return jsonify({
        "success": True,
        "message": message,
        "data": data
    }), status_code


def error_response(
    error: str,
    message: str = "Error",
    status_code: int = 400,
    details: Optional[Dict] = None
) -> tuple:
    """
    统一错误响应格式

    Args:
        error: 错误类型/代码
        message: 错误消息
        status_code: HTTP 状态码
        details: 额外的错误详情

    Returns:
        (response, status_code) tuple
    """
    payload = {
        "success": False,
        "message": message,
        "error": error
    }
    if details:
        payload["details"] = details
    return jsonify(payload), status_code


def api_handler(func: Callable) -> Callable:
    """
    装饰器：统一 API 异常处理

    用法:
        @api_handler
        def my_endpoint():
            # ... 业务逻辑
            return success_response(data)

    效果:
        1. 自动捕获异常并返回统一格式错误响应
        2. 记录完整异常堆栈到日志
        3. 根据异常类型返回合适的 HTTP 状态码

    支持的异常类型:
        - ValueError, TypeError -> 400 Bad Request
        - KeyError -> 400 Bad Request (缺少必需参数)
        - PermissionError, Unauthorized -> 403/401
        - FileNotFoundError -> 404 Not Found
        - TimeoutError -> 408 Request Timeout
        - Exception (未分类) -> 500 Internal Server Error
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ValueError as e:
            logger.warning(f"[API] ValueError in {func.__name__}: {e}")
            return error_response(
                error="INVALID_VALUE",
                message=str(e),
                status_code=400
            )
        except TypeError as e:
            logger.warning(f"[API] TypeError in {func.__name__}: {e}")
            return error_response(
                error="INVALID_TYPE",
                message=str(e),
                status_code=400
            )
        except KeyError as e:
            logger.warning(f"[API] KeyError in {func.__name__}: {e}")
            return error_response(
                error="MISSING_FIELD",
                message=f"缺少必需字段: {e}",
                status_code=400
            )
        except FileNotFoundError as e:
            logger.warning(f"[API] FileNotFoundError in {func.__name__}: {e}")
            return error_response(
                error="NOT_FOUND",
                message=str(e),
                status_code=404
            )
        except PermissionError as e:
            logger.warning(f"[API] PermissionError in {func.__name__}: {e}")
            return error_response(
                error="PERMISSION_DENIED",
                message=str(e),
                status_code=403
            )
        except TimeoutError as e:
            logger.warning(f"[API] TimeoutError in {func.__name__}: {e}")
            return error_response(
                error="TIMEOUT",
                message=str(e),
                status_code=408
            )
        except Exception as e:
            logger.error(f"[API] Unhandled exception in {func.__name__}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return error_response(
                error="INTERNAL_ERROR",
                message="服务器内部错误",
                status_code=500
            )
    return wrapper
