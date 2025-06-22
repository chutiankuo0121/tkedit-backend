# 会话状态管理器 
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import uuid
from datetime import datetime, timedelta
from app.database.models import Session as SessionModel

class SessionStateManager:
    """
    负责管理数据库中会话状态的 CRUD 操作。
    """
    @staticmethod
    async def create_session(db: AsyncSession, width: int, height: int, fps: int, project_name: str) -> SessionModel:
        """
        在数据库中创建一条新的会话记录。

        Args:
            db (AsyncSession): 数据库会话。
            width (int): 画布宽度。
            height (int): 画布高度。
            fps (int): 帧率。
            project_name (str): 项目名称。

        Returns:
            SessionModel: 创建的会话对象。
        """
        session_id = str(uuid.uuid4())
        expire_at = datetime.utcnow() + timedelta(days=1) # 默认24小时后过期

        new_session = SessionModel(
            session_id=session_id,
            project_name=project_name,
            width=width,
            height=height,
            fps=fps,
            expire_at=expire_at,
            status='active'
        )
        db.add(new_session)
        await db.commit()
        await db.refresh(new_session)
        return new_session

    @staticmethod
    async def get_session(db: AsyncSession, session_id: str) -> SessionModel | None:
        """
        根据 session_id 从数据库中获取会话记录。
        """
        result = await db.execute(select(SessionModel).filter(SessionModel.session_id == session_id))
        return result.scalars().first()

    @staticmethod
    async def update_session_status(db: AsyncSession, session_id: str, status: str) -> SessionModel | None:
        """
        更新会话的状态。
        """
        session = await SessionStateManager.get_session(db, session_id)
        if session:
            session.status = status
            await db.commit()
            await db.refresh(session)
        return session

    @staticmethod
    async def update_session_output_url(db: AsyncSession, session_id: str, url: str) -> SessionModel | None:
        """
        更新会话的最终输出R2 URL。
        """
        session = await SessionStateManager.get_session(db, session_id)
        if session:
            session.output_url = url
            await db.commit()
            await db.refresh(session)
        return session

    @staticmethod
    async def delete_session(db: AsyncSession, session_id: str) -> bool:
        """
        从数据库中删除会话记录。
        """
        session = await SessionStateManager.get_session(db, session_id)
        if session:
            await db.delete(session)
            await db.commit()
            return True
        return False

# 创建一个全局可用的会话状态管理器实例
session_state_manager = SessionStateManager() 