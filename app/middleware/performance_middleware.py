import time
import logging
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from app.utils.performance_monitor import performance_monitor
from app.services.adaptive_queue_manager import adaptive_queue_manager
from app.utils.logger_config import api_logger
from app.services.system_monitor import perf_logger
from app.config import settings

logger = logging.getLogger(__name__)

class PerformanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 记录请求开始时间
        start_time = time.time()
        session_id = request.headers.get("X-Session-ID") or request.path_params.get("session_id")
        
        # 记录请求开始
        api_logger.log_request_start(request.method, request.url.path, session_id)
        
        # 初始化response变量
        response = None
        
        # 检查是否为需要资源控制的端点
        if self.is_resource_intensive_endpoint(request.url.path):
            # 尝试获取任务槽位，支持排队等待
            if not await adaptive_queue_manager.acquire_task_slot(max_wait_time=settings.QUEUE_MAX_WAIT_TIME):
                # 等待超时，建议客户端重试
                api_logger.log_queue_wait(request.url.path, settings.QUEUE_MAX_WAIT_TIME, session_id)
                raise HTTPException(
                    status_code=429,  # Too Many Requests
                    detail={
                        "error": "请求处理中，请稍候",
                        "message": "服务器正在处理大量请求，已为您排队等待30秒，请稍后重试",
                        "retry_after": 15,
                        "queue_info": {
                            "waited_time": settings.QUEUE_MAX_WAIT_TIME,
                            "suggestion": "系统繁忙，建议15秒后重试"
                        }
                    }
                )
            
            try:
                # 处理请求
                response = await call_next(request)
            except Exception as e:
                # 记录请求错误
                api_logger.log_request_error(request.method, request.url.path, str(e), session_id)
                raise
            finally:
                # 释放任务槽位 - 无论成功还是失败都要释放
                adaptive_queue_manager.release_task_slot()
        else:
            # 对于非资源密集型端点，直接处理
            try:
                response = await call_next(request)
            except Exception as e:
                api_logger.log_request_error(request.method, request.url.path, str(e), session_id)
                raise
        
        # 记录响应时间
        process_time = time.time() - start_time
        performance_monitor.record_response_time(process_time)
        
        # 记录请求完成
        if response.status_code >= 400:
            api_logger.log_request_error(request.method, request.url.path, f"HTTP {response.status_code}", session_id)
        else:
            api_logger.log_request_success(request.method, request.url.path, process_time, session_id)
        
        # 在响应头中添加性能信息
        response.headers["X-Process-Time"] = str(process_time)
        response.headers["X-Load-Level"] = adaptive_queue_manager.assess_system_load().value
        
        # 记录队列状态（如果有变化）
        status = adaptive_queue_manager.get_status()
        perf_logger.log_queue_status(
            status["available_slots"], 
            status["active_tasks"], 
            status["load_level"]
        )
        
        return response
    
    def is_resource_intensive_endpoint(self, path: str) -> bool:
        """判断是否为资源密集型端点"""
        # 只有真正耗时的操作才需要并发控制
        resource_intensive_paths = [
            "/materials/upload",          # 素材上传 (下载+分析，2-5秒)
            "/actions",                   # 会话动作 (保存草稿，打包上传，1-3秒)  
        ]
        
        # 其他快速操作（0.01s）不需要并发控制：
        # - 创建轨道、添加片段、设置关键帧等都是内存操作
        # - 创建会话只是复制模板文件，很快
        
        return any(intensive_path in path for intensive_path in resource_intensive_paths)

class ResourceLimitMiddleware(BaseHTTPMiddleware):
    """资源限制中间件，提供更细粒度的控制"""
    
    def __init__(self, app, max_request_size_mb: int = 50):
        super().__init__(app)
        self.max_request_size_bytes = max_request_size_mb * 1024 * 1024
        
    async def dispatch(self, request: Request, call_next):
        # 检查请求大小
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_request_size_bytes:
            logger.warning(f"请求过大被拒绝: {content_length} bytes")
            raise HTTPException(
                status_code=413,
                detail="请求体过大，最大支持50MB"
            )
        
        # 检查当前系统状态，只有极端情况才拒绝
        from app.utils.performance_monitor import performance_monitor
        metrics = performance_monitor.get_current_metrics()
        
        # 只有在系统即将崩溃时才完全拒绝服务
        # 使用配置文件中的临界阈值，并且只拒绝重资源操作
        if (metrics.cpu_percent > settings.CPU_CRITICAL_THRESHOLD or 
            metrics.memory_available_mb < settings.MEMORY_MIN_RESERVE_MB):
            if self.is_heavy_resource_endpoint(request.url.path):
                logger.error(f"系统资源极限，拒绝重资源请求: CPU={metrics.cpu_percent}%, 可用内存={metrics.memory_available_mb}MB")
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "系统资源极限",
                        "message": "系统资源严重不足，请稍后重试",
                        "retry_after": 60,
                        "system_info": {
                            "cpu_percent": metrics.cpu_percent,
                            "memory_available_mb": metrics.memory_available_mb
                        }
                    }
                )
        
        return await call_next(request)
    
    def is_heavy_resource_endpoint(self, path: str) -> bool:
        """判断是否为重资源端点(只有这些才会在极限状态下被拒绝)"""
        heavy_resource_paths = [
            "/materials/upload",          # 素材上传操作(下载+分析，消耗大量CPU和内存)
            "/actions",                   # 会话动作(打包上传，I/O密集)
        ]
        # 注意：轨道创建、片段添加等都是快速内存操作，不会被拒绝
        return any(heavy_path in path for heavy_path in heavy_resource_paths) 