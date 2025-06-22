import logging
import os
import shutil
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Session
from app.utils.path_manager import path_manager

logger = logging.getLogger(__name__)

async def cleanup_expired_sessions(db: AsyncSession):
    """
    清理并标记过期的会话。

    一个会话如果状态为 'active' 且创建时间已超过1小时，
    则被视为过期。此函数将：
    1. 删除其在服务器上的本地文件目录。
    2. 将其在数据库中的状态更新为 'expired'。
    """
    expiration_hours = 1
    expiration_threshold = datetime.utcnow() - timedelta(hours=expiration_hours)
    
    # 1. 查找所有过期的活跃会话
    expired_sessions_stmt = select(Session).where(
        Session.status == 'active',
        Session.created_at < expiration_threshold
    )
    result = await db.execute(expired_sessions_stmt)
    expired_sessions = result.scalars().all()

    if not expired_sessions:
        logger.debug("[SESSION CLEANER] 没有找到过期的会话。")
        return

    # 2. 遍历并处理每个过期的会话
    cleaned_count = 0
    for session in expired_sessions:
        age = datetime.utcnow() - session.created_at
        logger.info(f"[SESSION CLEANER] 正在清理过期会话: {session.session_id} (已存在 {age.total_seconds() / 3600:.2f} 小时，超过 {expiration_hours} 小时阈值)")
        
        # 2.1 删除本地目录 - 使用统一路径管理器
        session_dir = path_manager.get_session_dir(session.session_id)
        if os.path.isdir(session_dir):
            try:
                shutil.rmtree(session_dir)
                logger.info(f"[SESSION CLEANER] 已成功删除目录: {session_dir}")
            except OSError as e:
                logger.error(f"[SESSION CLEANER] 删除目录 {session_dir} 失败: {e}")
        else:
            logger.warning(f"[SESSION CLEANER] 目录 {session_dir} 不存在，跳过删除。")
            
        # 2.2 更新数据库状态
        session.status = 'expired'
        db.add(session)
        cleaned_count += 1

    # 3. 提交数据库变更
    if cleaned_count > 0:
        await db.commit()
        logger.info(f"[SESSION CLEANER] 清理完成，共处理了 {cleaned_count} 个过期会话。") 