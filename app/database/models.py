from sqlalchemy import Column, Integer, String, TIMESTAMP, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class Session(Base):
    """会话表模型，对应 sessions 表"""
    __tablename__ = "sessions"

    session_id = Column(String, primary_key=True, index=True)
    project_name = Column(String, nullable=True)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    fps = Column(Integer, nullable=False, default=30)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    expire_at = Column(TIMESTAMP, nullable=True)
    status = Column(String, default='active')  # active, completed, expired, failed
    output_url = Column(String, nullable=True)


class Material(Base):
    """素材参考表模型，对应 materials 表"""
    __tablename__ = "materials"

    material_id = Column(String, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("sessions.session_id", ondelete="CASCADE"))
    r2_url = Column(String, nullable=False)      # 原始 R2 URL
    local_path = Column(String, nullable=False)   # 本地相对路径
    jy_name = Column(String, nullable=False) # 在剪映草稿中的标准化名称
    material_type = Column(String, nullable=False) # video, audio, image, srt
    
    # 【关键修复】添加复合唯一约束，确保同一会话中的jy_name不重复
    __table_args__ = (
        UniqueConstraint('session_id', 'jy_name', name='unique_session_jy_name'),
    )