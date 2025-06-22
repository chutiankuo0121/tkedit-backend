import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text, event
from contextlib import asynccontextmanager
from app.config import settings
from .models import Base
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# 【修复】简化SQLite连接配置，移除不支持的pragma参数
sqlite_connect_args = {
    "check_same_thread": False,  # SQLite特定配置
    "timeout": 30,  # 连接超时
} if "sqlite" in settings.ASYNC_DATABASE_URL else {}

# 创建异步数据库引擎
if "sqlite" in settings.ASYNC_DATABASE_URL:
    # SQLite不支持连接池参数，使用简化配置
    async_engine = create_async_engine(
        settings.ASYNC_DATABASE_URL,
        echo=settings.DB_ECHO,
        future=True,
        connect_args=sqlite_connect_args
    )
else:
    # 其他数据库使用连接池配置
    async_engine = create_async_engine(
        settings.ASYNC_DATABASE_URL,
        echo=settings.DB_ECHO,
        future=True,
        pool_size=20,  # 连接池大小
        max_overflow=30,  # 最大溢出连接数
        pool_timeout=30,  # 获取连接超时
        pool_recycle=3600,  # 连接回收时间（1小时）
        connect_args=sqlite_connect_args
    )

# 【修复】为SQLite添加PRAGMA设置的事件监听器
if "sqlite" in settings.ASYNC_DATABASE_URL:
    @event.listens_for(async_engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        """为每个新连接设置SQLite PRAGMA"""
        cursor = dbapi_connection.cursor()
        try:
            # 启用WAL模式和优化设置
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL") 
            cursor.execute("PRAGMA cache_size=-64000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            logger.info("✅ SQLite PRAGMA设置已应用")
        except Exception as e:
            logger.warning(f"⚠️ SQLite PRAGMA设置失败: {e}")
        finally:
            cursor.close()

# 创建一个异步的会话工厂
AsyncSessionLocal = sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

async def init_db():
    """
    在应用启动时初始化数据库，创建所有定义的表。
    路径管理由path_manager统一处理。
    """
    try:
        logger.info(f"🔧 开始初始化数据库: {settings.ASYNC_DATABASE_URL}")
        
        async with async_engine.begin() as conn:
            # 创建所有表
            await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, checkfirst=True))
            
        logger.info("✅ 数据库表结构初始化成功")
        
        # 测试数据库连接 - 使用text()函数明确声明文本SQL
        async with AsyncSessionLocal() as test_session:
            await test_session.execute(text("SELECT 1"))
            logger.info("✅ 数据库连接测试成功")
            
    except Exception as e:
        error_msg = str(e).lower()
        
        # 友好处理常见错误
        if "already exists" in error_msg:
            logger.info("ℹ️  数据库表已存在，跳过创建")
        elif "permission denied" in error_msg or "access denied" in error_msg:
            logger.error(f"❌ 数据库权限错误: {e}")
            logger.error(f"   请检查数据库目录权限")
            raise
        elif "no such file or directory" in error_msg:
            logger.error(f"❌ 数据库目录不存在: {e}")
            # 使用path_manager确保目录存在
            from app.utils.path_manager import path_manager
            logger.info(f"   数据库将创建在: {path_manager.data_dir}")
            raise
        else:
            logger.error(f"❌ 数据库初始化失败: {e}")
            raise

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依赖注入项，用于为每个请求提供一个数据库会话。
    """
    async with AsyncSessionLocal() as session:
        yield session 

@asynccontextmanager
async def get_db_session_context():
    """
    为非请求作用域（如后台任务）提供数据库会话的异步上下文管理器。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close() 