# 素材管理器 

import os
import uuid
from typing import Tuple, Optional
from urllib.parse import urlparse
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile, HTTPException
import logging
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import asyncio

from app.config import settings
from app.database.models import Material as MaterialModel
from app.core.script_file import Script_file
from app.core.local_materials import Video_material, Audio_material
from app.utils.r2_client import r2_client
from app.utils.media_analyzer import media_analyzer, MediaAnalysisResult
from app.utils.path_manager import path_manager

logger = logging.getLogger(__name__)

class MaterialNotFoundError(Exception):
    """当在数据库中找不到素材时引发的异常"""
    pass

def _get_object_key_from_r2_url(r2_url: str) -> str:
    """从完整的R2 URL中安全地提取object_key。"""
    parsed_url = urlparse(r2_url)
    # R2 URL path is typically /<bucket-name>/<object-key>
    # We split the path and drop the first element (which is empty) and the second (bucket name)
    path_parts = parsed_url.path.strip('/').split('/')
    if len(path_parts) < 2:
        raise ValueError(f"Invalid R2 URL format: cannot extract object key from {r2_url}")
    return '/'.join(path_parts[1:])

class MaterialManager:
    """
    负责素材的完整生命周期管理。
    """
    
    async def _generate_unique_jy_name(self, db: AsyncSession, session_id: str, material_type: str) -> str:
        """
        生成唯一的jy_name
        
        Args:
            db: 数据库会话
            session_id: 会话ID
            material_type: 素材类型
            
        Returns:
            str: 唯一的jy_name
        """
        # 查询当前同类型素材的最大序号
        if material_type == "video" or material_type == "image":
            # 对于视频和图片，查询所有video和image类型的最大序号
            result = await db.execute(
                text("""
                    SELECT COALESCE(MAX(
                        CAST(SUBSTR(jy_name, LENGTH(:type_prefix) + 1) AS INTEGER)
                    ), 0) as max_num
                    FROM materials 
                    WHERE session_id = :session_id 
                    AND material_type IN ('video', 'image')
                    AND jy_name LIKE :pattern
                """),
                {
                    "session_id": session_id,
                    "type_prefix": material_type,
                    "pattern": f"{material_type}_%"
                }
            )
        elif material_type == "audio":
            result = await db.execute(
                text("""
                    SELECT COALESCE(MAX(
                        CAST(SUBSTR(jy_name, LENGTH(:type_prefix) + 1) AS INTEGER)
                    ), 0) as max_num
                    FROM materials 
                    WHERE session_id = :session_id 
                    AND material_type = 'audio'
                    AND jy_name LIKE :pattern
                """),
                {
                    "session_id": session_id,
                    "type_prefix": material_type,
                    "pattern": f"{material_type}_%"
                }
            )
        else:
            result = await db.execute(
                text("""
                    SELECT COALESCE(MAX(
                        CAST(SUBSTR(jy_name, LENGTH(:type_prefix) + 1) AS INTEGER)
                    ), 0) as max_num
                    FROM materials 
                    WHERE session_id = :session_id 
                    AND material_type = :material_type
                    AND jy_name LIKE :pattern
                """),
                {
                    "session_id": session_id,
                    "material_type": material_type,
                    "type_prefix": material_type,
                    "pattern": f"{material_type}_%"
                }
            )
        
        max_num = result.scalar() or 0
        # 生成下一个序号的jy_name
        next_num = max_num + 1
        jy_name = f"{material_type}_{next_num:03d}"
        
        return jy_name

    async def add_material_from_r2(
        self,
        db: AsyncSession,
        script_file: Script_file,
        session_id: str,
        r2_url: str,
        material_type: str
    ) -> Tuple[MaterialModel, Optional[MediaAnalysisResult]]:
        """
        核心方法：处理从R2 URL传入的单个素材。

        流程:
        1.  为素材生成唯一的 material_id 和标准化的本地文件名。
        2.  构建本地保存路径。
        3.  从R2下载文件到该路径。
        4.  分析媒体文件
        5.  在数据库中创建素材记录。
        6.  将素材添加到 Script_file 实例中。
        7.  返回创建的数据库素材对象和分析结果。
        """
        # 1. 生成ID和名称
        material_id = str(uuid.uuid4())
        jy_name = await self._generate_unique_jy_name(db, session_id, material_type)
        
        # 2. 构建路径
        file_extension = os.path.splitext(urlparse(r2_url).path)[1]
        filename = f"{jy_name}{file_extension}"
        local_path = path_manager.get_material_path(session_id, filename)
        relative_path = path_manager.relative_to_base(local_path)

        # 3. 从R2下载
        object_key = _get_object_key_from_r2_url(r2_url)
        await r2_client.download_file(object_key, local_path)

        # 4. 分析媒体文件
        try:
            analysis_result = await media_analyzer.analyze(local_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"媒体文件分析失败: {e}")

        if not analysis_result:
            raise HTTPException(status_code=500, detail=f"媒体文件分析失败：无法获取 {local_path} 的元数据。")

        # 5. 在数据库中创建记录
        new_material_db = MaterialModel(
            material_id=material_id,
            session_id=session_id,
            r2_url=r2_url,
            local_path=local_path,
            jy_name=jy_name,
            material_type=material_type
        )
        db.add(new_material_db)
        await db.commit()
        await db.refresh(new_material_db)

        # 6. 添加到 Script_file 实例
        if analysis_result:
            # 剪映草稿需要的路径格式：{session_id}\Resources\filename
            jy_relative_path = f"{session_id}\\Resources\\{os.path.basename(local_path)}"
            material_instance = None
            
            # 根据类型创建核心库素材对象 - 先用绝对路径创建，再修改为剪映路径格式
            if material_type == "video" or material_type == "image":
                material_instance = Video_material(path=local_path, material_name=jy_name)
                material_instance.duration = analysis_result.duration_us
                material_instance.width = analysis_result.width
                material_instance.height = analysis_result.height
                material_instance.material_type = "video"
                # 修改为剪映路径格式
                material_instance.path = jy_relative_path
            
            elif material_type == "audio":
                material_instance = Audio_material(path=local_path, material_name=jy_name)
                material_instance.duration = analysis_result.duration_us
                # 修改为剪映路径格式
                material_instance.path = jy_relative_path
            
            if material_instance:
                material_instance.material_id = new_material_db.material_id
                script_file.add_material(material_instance)

        return new_material_db, analysis_result

    async def add_material_from_upload(
        self,
        db: AsyncSession,
        script_file: Script_file,
        session_id: str,
        material_type: str,
        file: UploadFile,
    ) -> Tuple[MaterialModel, Optional[MediaAnalysisResult]]:
        """
        核心方法: 处理客户端直接上传的文件流。

        流程:
        1.  生成素材ID, jy_name, 和在R2上的object_key。
        2.  将文件流直接上传到R2。
        3.  为了进行媒体分析和让核心库引用，需将文件流保存到本地临时路径。
        4.  分析媒体文件。
        5.  在数据库中创建素材记录。
        6.  将素材添加到 Script_file 实例中。
        7.  返回创建的数据库素材对象和分析结果。
        """
        # 1. 生成ID和名称
        material_id = str(uuid.uuid4())
        jy_name = await self._generate_unique_jy_name(db, session_id, material_type)

        file_extension = os.path.splitext(file.filename or 'default.bin')[1]
        object_key = f"{session_id}/{jy_name}{file_extension}"

        # 2. 将文件流上传到R2
        await file.seek(0)
        await r2_client.upload_fileobj(file.file, object_key)
        r2_url = f"r2://{settings.R2_BUCKET_NAME}/{object_key}"
        
        logging.info(f"✅ [R2 UPLOAD] 文件 {file.filename} 已成功上传. (内部引用: {r2_url})")

        # 3. 保存到本地以供分析
        filename = f"{jy_name}{file_extension}"
        local_path = path_manager.get_material_path(session_id, filename)
        
        await file.seek(0)
        
        with open(local_path, "wb") as buffer:
            while chunk := await file.read(8192):
                buffer.write(chunk)

        # 4. 分析媒体文件
        try:
            analysis_result = await media_analyzer.analyze(local_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"媒体文件分析失败: {e}")
        
        if not analysis_result:
            raise HTTPException(status_code=500, detail=f"媒体文件分析失败：无法获取 {local_path} 的元数据。")

        # 5. 创建数据库记录
        relative_path = path_manager.relative_to_base(local_path)
        new_material_db = MaterialModel(
            material_id=material_id, session_id=session_id, r2_url=r2_url,
            local_path=relative_path, jy_name=jy_name, material_type=material_type
        )
        db.add(new_material_db)
        await db.commit()
        await db.refresh(new_material_db)

        # 6. 添加到 Script_file 实例
        if analysis_result:
            # 剪映草稿需要的路径格式：{session_id}\Resources\filename
            jy_relative_path = f"{session_id}\\Resources\\{os.path.basename(local_path)}"
            material_instance = None
            
            # 根据类型创建核心库素材对象 - 先用绝对路径创建，再修改为剪映路径格式
            if material_type == "video" or material_type == "image":
                material_instance = Video_material(path=local_path, material_name=jy_name)
                material_instance.duration = analysis_result.duration_us
                material_instance.width = analysis_result.width
                material_instance.height = analysis_result.height
                material_instance.material_type = "video"
                # 修改为剪映路径格式
                material_instance.path = jy_relative_path
            
            elif material_type == "audio":
                material_instance = Audio_material(path=local_path, material_name=jy_name)
                material_instance.duration = analysis_result.duration_us
                # 修改为剪映路径格式
                material_instance.path = jy_relative_path
            
            if material_instance:
                material_instance.material_id = new_material_db.material_id
                script_file.add_material(material_instance)

        return new_material_db, analysis_result 