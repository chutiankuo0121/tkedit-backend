"""
路径管理工具 - 支持本地/Docker/HuggingFace Spaces等不同部署环境
确保在任何环境下都能正确处理文件路径
"""
import os
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

class PathManager:
    """
    统一的路径管理器，自动适配不同的部署环境：
    - 本地开发环境
    - Docker容器环境  
    - HuggingFace Spaces环境
    """
    
    def __init__(self):
        self._base_dir = None
        self._data_dir = None
        self._output_dir = None
        self._dir_creation_lock = threading.Lock()
        self._created_dirs = set()
        self._detect_environment()
    
    def _detect_environment(self):
        """自动检测当前运行环境并设置相应的路径"""
        # 获取当前工作目录
        current_dir = os.getcwd()
        
        # 检测是否在容器环境中（如 HuggingFace Spaces）
        is_container = any([
            os.environ.get('SPACE_ID'),  # HuggingFace Spaces
            os.environ.get('DOCKER_CONTAINER'),  # Docker标识
            current_dir.startswith('/app'),  # 典型容器路径
            not os.access('.', os.W_OK)  # 当前目录不可写
        ])
        
        if is_container:
            # 容器环境：使用 /tmp 作为数据目录，避免权限问题
            self._base_dir = current_dir
            self._data_dir = "/tmp/data"
            self._output_dir = "/tmp/data/output"
            logger.info(f"🐳 检测到容器环境，使用 /tmp 目录")
        else:
            # 本地环境：使用项目目录
            project_root = self._find_project_root(current_dir)
            
            if project_root:
                self._base_dir = project_root
                logger.info(f"✅ 检测到项目根目录: {self._base_dir}")
            else:
                # 如果找不到项目根目录，使用当前目录
                self._base_dir = current_dir
                logger.warning(f"⚠️ 未找到项目根目录，使用当前目录: {self._base_dir}")
            
            # 设置数据目录
            self._data_dir = os.path.join(self._base_dir, "data")
            self._output_dir = os.path.join(self._data_dir, "output")
        
        # 确保必要的目录存在
        self._ensure_directories()
        
        logger.info(f"📁 路径配置完成:")
        logger.info(f"   - 项目根目录: {self._base_dir}")
        logger.info(f"   - 数据目录: {self._data_dir}")  
        logger.info(f"   - 输出目录: {self._output_dir}")
    
    def _find_project_root(self, start_path: str) -> Optional[str]:
        """
        向上查找项目根目录（包含app目录的目录）
        """
        current_path = Path(start_path).resolve()
        
        # 最多向上查找5层目录
        for _ in range(5):
            app_dir = current_path / "app"
            if app_dir.exists() and app_dir.is_dir():
                return str(current_path)
            
            parent = current_path.parent
            if parent == current_path:  # 已经到根目录
                break
            current_path = parent
        
        return None
    
    def _ensure_directories(self):
        """确保必要的目录存在"""
        directories = [
            self._data_dir,
            self._output_dir,
            os.path.join(self._data_dir, "logs")
        ]
        
        for directory in directories:
            self._safe_makedirs(directory)
    
    def _safe_makedirs(self, directory: str):
        """
        线程安全的目录创建方法
        """
        # 快速路径：如果目录已经创建过，直接返回
        if directory in self._created_dirs:
            return
        
        # 慢速路径：需要创建目录时使用锁保护
        with self._dir_creation_lock:
            # 双重检查：可能在等待锁期间其他线程已经创建了
            if directory in self._created_dirs:
                return
                
            try:
                os.makedirs(directory, exist_ok=True)
                # 尝试设置目录权限（某些云平台可能不允许）
                if os.name != 'nt':  # 非Windows系统
                    try:
                        os.chmod(directory, 0o755)
                    except (OSError, PermissionError):
                        # 在 HuggingFace Spaces 等云平台，权限修改可能被限制
                        # 这不影响应用正常运行，忽略即可
                        pass
                # 标记为已创建
                self._created_dirs.add(directory)
                logger.debug(f"✅ 目录创建成功: {directory}")
            except Exception as e:
                logger.error(f"创建目录失败 {directory}: {e}")
                raise
    
    @property
    def base_dir(self) -> str:
        """项目根目录的绝对路径"""
        return self._base_dir
    
    @property
    def data_dir(self) -> str:
        """数据目录的绝对路径"""
        return self._data_dir
    
    @property  
    def output_dir(self) -> str:
        """输出目录的绝对路径"""
        return self._output_dir
    
    def get_session_dir(self, session_id: str) -> str:
        """获取特定会话的目录绝对路径"""
        session_dir = os.path.join(self._output_dir, session_id)
        self._safe_makedirs(session_dir)
        return session_dir
    
    def get_session_resources_dir(self, session_id: str) -> str:
        """获取特定会话的资源目录绝对路径"""
        resources_dir = os.path.join(self.get_session_dir(session_id), "Resources")
        self._safe_makedirs(resources_dir)
        return resources_dir
    
    def get_material_path(self, session_id: str, filename: str) -> str:
        """获取素材文件的绝对路径"""
        return os.path.join(self.get_session_resources_dir(session_id), filename)
    
    def get_draft_content_path(self, session_id: str) -> str:
        """获取草稿内容文件的绝对路径"""
        return os.path.join(self.get_session_dir(session_id), "draft_content.json")
    
    def get_zips_dir(self) -> str:
        """获取ZIP文件存储目录的绝对路径"""
        zips_dir = os.path.join(self._data_dir, "zips")
        self._safe_makedirs(zips_dir)
        return zips_dir
    
    def get_zip_path(self, zip_filename: str) -> str:
        """获取ZIP文件的绝对路径"""
        return os.path.join(self.get_zips_dir(), zip_filename)
    
    def normalize_path(self, path: str) -> str:
        """
        标准化路径：
        - 相对路径转换为绝对路径
        - 统一路径分隔符
        - 解析符号链接
        """
        if os.path.isabs(path):
            return os.path.normpath(path)
        else:
            # 相对路径基于项目根目录
            return os.path.normpath(os.path.join(self._base_dir, path))
    
    def relative_to_base(self, absolute_path: str) -> str:
        """将绝对路径转换为相对于项目根目录的路径"""
        try:
            return os.path.relpath(absolute_path, self._base_dir)
        except ValueError:
            # 如果路径不在项目根目录下，返回绝对路径
            return absolute_path
    
    def is_path_safe(self, path: str) -> bool:
        """检查路径是否安全（防止路径遍历攻击）"""
        try:
            # 标准化路径
            normalized = self.normalize_path(path)
            # 检查是否在项目目录范围内
            return normalized.startswith(self._base_dir)
        except Exception:
            return False

# 创建全局路径管理器实例
path_manager = PathManager() 