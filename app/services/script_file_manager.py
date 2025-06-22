import os
import json
import logging
import asyncio
import threading
from cachetools import LRUCache
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.script_file import Script_file
from app.core.local_materials import Video_material, Audio_material
from app.database.models import Session as SessionModel, Material as MaterialModel
from app.utils.media_analyzer import media_analyzer
from app.utils.path_manager import path_manager


logger = logging.getLogger(__name__)

class ScriptFileManager:
    """
    在内存中管理和缓存不同会话的 `Script_file` 实例。
    确保 API 在多次请求之间能够操作同一个草稿对象。
    """
    def __init__(self, capacity: int = 100):
        """
        初始化一个LRU (最近最少使用) 缓存来存储 Script_file 实例。

        Args:
            capacity (int): 缓存可以容纳的最大会话实例数量。
        """
        self.cache = LRUCache(maxsize=capacity)
        self.locks = {} # 为每个会話ID创建一个专用的锁
        # 【关键修复】添加全局锁保护locks字典的并发访问
        self._locks_creation_lock = threading.Lock()

    def _get_or_create_lock(self, session_id: str) -> asyncio.Lock:
        """
        【关键修复】获取或创建与特定session_id关联的锁
        使用线程锁确保在高并发下不会为同一个session_id创建多个Lock
        """
        # 先进行无锁检查（快速路径）
        if session_id in self.locks:
            return self.locks[session_id]
        
        # 需要创建新锁时，使用线程锁保护
        with self._locks_creation_lock:
            # 双重检查：可能在等待锁期间其他线程已经创建了
            if session_id not in self.locks:
                self.locks[session_id] = asyncio.Lock()
            return self.locks[session_id]

    def _load_script_from_file(self, path: str, session_db_obj: SessionModel) -> Script_file:
        """
        [最终修复版] 从磁盘加载并重建 Script_file 对象。
        此函数直接调用核心库中新增的 from_dict 方法。
        """
        with open(path, 'r', encoding='utf-8') as f:
            content = json.load(f)
        
        draft_root = os.path.dirname(path)
        instance = Script_file.from_dict(content, draft_root)

        # 确保数据库中的权威数据覆盖可能不一致的旧数据
        instance.width = session_db_obj.width
        instance.height = session_db_obj.height
        instance.fps = session_db_obj.fps
        
        logger.info(f"✅ 成功从 {path} 完整加载并重建了 Script_file 实例。")
        return instance

    async def get_script_file(self, session_db_obj: SessionModel, db: AsyncSession = None) -> Script_file:
        """
        获取或创建一个 Script_file 实例。

        此方法是异步的，并使用锁来防止并发请求对同一个会话文件产生竞争条件。
        【关键修复】每次都会从数据库同步最新的素材信息，确保数据一致性。

        Args:
            session_db_obj (SessionModel): 从数据库中获取的会话模型对象。
            db (AsyncSession): 数据库会话，用于查询素材

        Returns:
            Script_file: 对应的草稿文件实例。
        """
        session_id = session_db_obj.session_id
        lock = self._get_or_create_lock(session_id)

        async with lock:
            # 获取或创建Script_file实例
            script_file_instance = self.cache.get(session_id)
            
            if script_file_instance is None:
                # 缓存未命中，创建新实例
                logger.info(f"缓存未命中，为会话 {session_id} 创建一个新的 Script_file 实例。")
                script_file_instance = Script_file(
                    width=session_db_obj.width,
                    height=session_db_obj.height,
                    fps=session_db_obj.fps
                )
                self.cache[session_id] = script_file_instance
            else:
                logger.info(f"缓存命中，为会话 {session_id} 获取已存在的 Script_file 实例。")
            
            # 【关键修复】无论是否缓存命中，都要从数据库同步最新的素材信息
            if db is not None:
                try:
                    # 查询该会话的所有素材
                    stmt = select(MaterialModel).where(MaterialModel.session_id == session_id)
                    result = await db.execute(stmt)
                    materials = result.scalars().all()
                    
                    # 获取当前Script_file中已有的素材ID集合
                    existing_material_ids = set()
                    for video in script_file_instance.materials.videos:
                        existing_material_ids.add(video.material_id)
                    for audio in script_file_instance.materials.audios:
                        existing_material_ids.add(audio.material_id)
                    
                    # 只处理新增的素材，避免重复添加
                    new_materials_count = 0
                    for material_db in materials:
                        if material_db.material_id in existing_material_ids:
                            continue  # 跳过已存在的素材
                            
                        try:
                            logger.info(f"同步新素材: {material_db.material_id} ({material_db.jy_name})")
                            
                            # 检查本地文件是否存在 - 使用鲁棒的路径管理器
                            # 数据库中存储的是相对路径，需要转换为绝对路径
                            absolute_path = path_manager.normalize_path(material_db.local_path)
                            if not os.path.exists(absolute_path):
                                logger.warning(f"素材文件不存在: {absolute_path} (数据库路径: {material_db.local_path})")
                                continue

                            # 重新分析文件以获取元数据
                            analysis_result = await media_analyzer.analyze(absolute_path)
                            if not analysis_result:
                                logger.warning(f"无法分析素材文件: {material_db.local_path}")
                                continue

                            # 根据类型创建素材实例并添加到Script_file
                            material_instance = None
                            if material_db.material_type in ["video", "image"]:
                                material_instance = Video_material(
                                    path=absolute_path,
                                    material_name=material_db.jy_name
                                )
                                material_instance.duration = analysis_result.duration_us
                                material_instance.width = analysis_result.width
                                material_instance.height = analysis_result.height
                                material_instance.material_type = "video"
                                # 【关键修复】修改为剪映路径格式
                                material_instance.path = f"{session_id}\\Resources\\{os.path.basename(absolute_path)}"
                            elif material_db.material_type == "audio":
                                material_instance = Audio_material(
                                    path=absolute_path,
                                    material_name=material_db.jy_name
                                )
                                material_instance.duration = analysis_result.duration_us
                                # 【关键修复】修改为剪映路径格式
                                material_instance.path = f"{session_id}\\Resources\\{os.path.basename(absolute_path)}"
                            else:
                                logger.warning(f"未知素材类型: {material_db.material_type}")
                                continue

                            if material_instance:
                                # 设置数据库中的ID并添加到Script_file
                                material_instance.material_id = material_db.material_id
                                script_file_instance.add_material(material_instance)
                                new_materials_count += 1
                                logger.info(f"✅ 已同步新素材到内存: {material_db.material_id} ({material_db.jy_name})")

                        except Exception as e:
                            logger.error(f"同步素材时出错 {material_db.material_id}: {e}")
                            import traceback
                            logger.error(f"错误详情: {traceback.format_exc()}")
                            continue
                    
                    if new_materials_count > 0:
                        logger.info(f"会话 {session_id} 已同步 {new_materials_count} 个新素材到内存中")
                    else:
                        logger.info(f"会话 {session_id} 无新素材需要同步")
                        
                except Exception as e:
                    logger.error(f"从数据库同步素材时出错: {e}")
            
            return script_file_instance

    def remove_script_file(self, session_id: str):
        """
        从缓存中移除一个 Script_file 实例。
        当会话结束或被删除时调用。
        """
        if session_id in self.cache:
            del self.cache[session_id]
        if session_id in self.locks:
            del self.locks[session_id]
            
# 创建一个全局可用的脚本文件管理器实例
script_file_manager = ScriptFileManager() 