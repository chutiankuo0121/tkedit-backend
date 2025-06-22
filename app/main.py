# 应用入口, 全局依赖管理
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.api.v1 import api_router
from app.config import settings
from app.database.session import init_db, get_db_session_context
from app.utils.r2_client import r2_client
from app.services.session_cleaner import cleanup_expired_sessions
from app.middleware.performance_middleware import PerformanceMiddleware, ResourceLimitMiddleware
from app.services.adaptive_queue_manager import adaptive_queue_manager
from app.utils.logger_config import setup_logging
from app.services.system_monitor import system_monitor

# 假设v1的路由定义在下面的文件中
# from .core.exceptions import ResourceNotFound, ValidationError

# ============================= 日志配置 ============================= #
# 设置美化日志系统
setup_logging()
logger = logging.getLogger("app.main")

# ============================= 全局调度器 ============================= #
scheduler = AsyncIOScheduler()

async def run_cleanup_job():
    """执行清理任务的包装函数，负责创建和关闭数据库会话。"""
    logger.info("🧹 开始执行定期的会话清理任务...")
    try:
        async with get_db_session_context() as db:
            await cleanup_expired_sessions(db)
        logger.info("✅ 会话清理任务执行完毕")
    except Exception as e:
        logger.error(f"❌ 执行会话清理任务时发生错误: {e}", exc_info=True)

# ============================= Lifespan Manager ============================= #
@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理应用的启动和关闭事件"""
    # Startup logic
    logger.info("🚀 服务开始启动...")
    try:
        await init_db()
        logger.info("✅ 数据库连接成功并完成初始化")
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")
        raise RuntimeError("Database initialization failed") from e

    try:
        await r2_client.check_connection()
        logger.info(f"✅ Cloudflare R2 连接成功，存储桶 '{settings.R2_BUCKET_NAME}' 可访问")
    except Exception as e:
        logger.error(f"❌ Cloudflare R2 连接失败: {e}")
        raise RuntimeError("R2 connection failed") from e
    
    # 启动后台调度任务
    scheduler.add_job(run_cleanup_job, 'interval', hours=1, id="session_cleanup_job")
    scheduler.start()
    logger.info("🗓️ 已启动会话清理后台任务，每小时运行一次")
    
    # 启动系统资源监控
    await system_monitor.start()
    logger.info("📊 系统资源监控已启动")

    logger.info("🎉 服务已就绪，等待请求...")
    
    yield
    
    # Shutdown logic
    logger.info("🌙 服务正在关闭...")
    
    # 停止系统监控
    await system_monitor.stop()
    logger.info("📊 系统资源监控已停止")
    
    if scheduler.running:
        scheduler.shutdown()
        logger.info("🗓️ 后台任务调度器已关闭")
    await r2_client.close_client()
    logger.info("☁️ R2 客户端已关闭")
    logger.info("👋 服务已成功关闭")

# ============================= FastAPI 应用实例 ============================= #
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="一个基于FastAPI的TKedit视频编辑后端系统",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# ============================= 中间件 ============================= #
# 注意：PerformanceMiddleware已经包含了美化日志功能，这里不再需要重复的中间件

# ============================= 全局异常处理 ============================= #
# 这是一个示例，后续需要从 core.exceptions 导入真实的异常类
# @app.exception_handler(ValidationError)
# async def validation_exception_handler(request: Request, exc: ValidationError):
#     logger.warning(f"Validation error: {exc} for request {request.url.path}")
#     return JSONResponse(
#         status_code=400,
#         content={
#             "status": "error",
#             "code": "VALIDATION_ERROR",
#             "message": "输入验证失败",
#             "details": str(exc)
#         }
#     )

# @app.exception_handler(ResourceNotFound)
# async def not_found_exception_handler(request: Request, exc: ResourceNotFound):
#     logger.warning(f"Resource not found: {exc} for request {request.url.path}")
#     return JSONResponse(
#         status_code=404,
#         content={
#             "status": "error",
#             "code": "RESOURCE_NOT_FOUND",
#             "message": str(exc),
#             "details": {}
#         }
#     )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    # 如果异常已经是 FastAPI 的 HTTPException，直接重新抛出，让 FastAPI 自己处理
    if isinstance(exc, HTTPException):
        raise exc

    logger.error(f"💥 服务器内部错误 | {request.method} {request.url.path} | {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "code": "INTERNAL_SERVER_ERROR",
            "message": "服务器发生内部错误",
            "details": str(exc)
        }
    )

# ============================= 路由 ============================= #
@app.get("/", summary="根路径", description="一个简单的问候，用于确认服务正在运行。")
async def root():
    return {"message": f"Welcome to {settings.PROJECT_NAME}"}

# 包含 v1 版本的API路由
app.include_router(api_router, prefix=settings.API_V1_STR)

# 添加中间件 (注意顺序：后添加的先执行)
app.add_middleware(ResourceLimitMiddleware, max_request_size_mb=50)
app.add_middleware(PerformanceMiddleware)

# 添加性能监控端点
@app.get("/api/v1/system/performance")
async def get_performance_status():
    """获取系统性能状态"""
    return adaptive_queue_manager.get_status()

@app.get("/api/v1/system/health")
async def health_check():
    """健康检查 - 包含性能信息"""
    status = adaptive_queue_manager.get_status()
    
    # 根据负载水平确定健康状态
    if status["load_level"] == "critical":
        return {"status": "degraded", "performance": status}
    elif status["load_level"] == "high":
        return {"status": "warning", "performance": status}
    else:
        return {"status": "healthy", "performance": status}

# 在这里，我们可以添加对数据库、R2客户端等的初始化和关闭逻辑
# @app.on_event("shutdown")
# async def shutdown_event():
#     logger.info("Application shutdown...")
#     # 例如: await close_db_connection() 