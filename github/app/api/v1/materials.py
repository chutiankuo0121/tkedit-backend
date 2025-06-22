# 素材管理API (上传、下载、分析)
import os
import asyncio
import logging
from typing import List, Optional, Literal

from fastapi import APIRouter, Body, Path, Depends, HTTPException, File, UploadFile, Form
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.script_file import Script_file
from app.database.session import get_db_session
from app.services.material_manager import MaterialManager
from app.utils.media_analyzer import MediaAnalysisResult
from app.utils.path_manager import path_manager
from .sessions import get_script_file_from_session_id

# ============================= Router & Manager Initialization ============================= #

router = APIRouter()
material_manager = MaterialManager()
logger = logging.getLogger(__name__)

# ============================= Pydantic Models ============================= #

MaterialTypeLiteral = Literal["video", "audio", "image", "srt"]

class MaterialUploadInfo(BaseModel):
    """单个待上传素材的信息"""
    r2_url: str = Field(..., description="素材在R2上的完整URL", example="https://<account_id>.r2.cloudflarestorage.com/<bucket>/video.mp4")
    material_type: MaterialTypeLiteral = Field(..., description="素材类型")

class UploadMaterialsRequest(BaseModel):
    """上传素材请求体"""
    materials: List[MaterialUploadInfo]

class MediaMetadata(BaseModel):
    """媒体元数据响应体"""
    duration: int = Field(..., description="时长(微秒)")
    width: Optional[int] = Field(None, description="宽度(像素)")
    height: Optional[int] = Field(None, description="高度(像素)")
    fps: Optional[float] = Field(None, description="帧率")
    sample_rate: Optional[int] = Field(None, description="采样率(音频)")

class UploadMaterialsResponseItem(BaseModel):
    """单个素材上传成功后的响应体"""
    material_id: str = Field(..., description="素材在系统中的唯一ID")
    material_type: MaterialTypeLiteral = Field(..., description="素材类型")
    jy_name: str = Field(..., description="在剪映草稿中使用的素材名称")
    relative_path: str = Field(..., description="在草稿文件中的相对路径")
    media_metadata: Optional[MediaMetadata] = Field(None, description="从文件中分析出的媒体元数据")

# ============================= API Endpoints ============================= #

@router.post(
    "/{session_id}/materials/upload",
    summary="【推荐】直接上传素材文件",
    response_model=UploadMaterialsResponseItem
)
async def upload_material_direct(
    session_id: str = Path(..., description="会话ID"),
    material_type: MaterialTypeLiteral = Form(..., description="素材类型"),
    file: UploadFile = File(..., description="要上传的素材文件"),
    script_file: Script_file = Depends(get_script_file_from_session_id),
    db: AsyncSession = Depends(get_db_session)
):
    """
    接收客户端上传的单个素材文件流，然后由后端完成所有处理：
    1.  上传文件到R2。
    2.  保存文件到本地供后续使用。
    3.  分析媒体元数据。
    4.  在数据库中创建记录。
    5.  将素材信息添加到剪映草稿数据结构中。
    """
    try:
        db_material, analysis_result = await material_manager.add_material_from_upload(
            db=db,
            script_file=script_file,
            session_id=session_id,
            material_type=material_type,
            file=file
        )
        

        # 将更新后的草稿文件内容保存回磁盘
        file_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(file_path)

        metadata_response = None
        if analysis_result:
            metadata_response = MediaMetadata(
                duration=analysis_result.duration_us,
                width=analysis_result.width,
                height=analysis_result.height,
                fps=analysis_result.fps,
                sample_rate=analysis_result.sample_rate,
            )
        
        return UploadMaterialsResponseItem(
            material_id=db_material.material_id,
            material_type=db_material.material_type,
            jy_name=db_material.jy_name,
            relative_path=f"{session_id}\\Resources\\{os.path.basename(db_material.local_path)}",
            media_metadata=metadata_response
        )
    except HTTPException:
        # 如果异常已经是HTTPException，直接重新抛出，以保留原始的、详细的错误信息
        raise
    except Exception as e:
        # 捕获在service层可能发生的任何其他未知错误
        logger.error(f"处理素材上传时发生未知错误: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"处理素材上传时发生未知错误: {str(e)}"
        )

@router.post(
    "/{session_id}/materials/upload-from-r2",
    response_model=List[UploadMaterialsResponseItem],
    summary="【已废弃】从R2 URL添加素材到会话",
    deprecated=True
)
async def upload_materials_from_r2(
    session_id: str = Path(..., description="会话ID"),
    request: UploadMaterialsRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id),
    db: AsyncSession = Depends(get_db_session)
):
    """
    接收客户端提供的素材R2 URL列表，然后对每个素材执行一站式处理流程：
    1.  从R2下载、分析元数据、保存到本地会话目录。
    2.  在数据库中为素材创建记录。
    3.  将素材的元信息添加到剪映草稿数据结构中。
    4.  并发处理所有素材，提高处理效率。
    """
    response_items = []

    async def process_material(material_info: MaterialUploadInfo):
        try:
            db_material, analysis_result = await material_manager.add_material_from_r2(
                db=db,
                script_file=script_file,
                session_id=session_id,
                r2_url=material_info.r2_url,
                material_type=material_info.material_type
            )

            metadata_response = None
            if analysis_result:
                metadata_response = MediaMetadata(
                    duration=analysis_result.duration_us,
                    width=analysis_result.width,
                    height=analysis_result.height,
                    fps=analysis_result.fps,
                    sample_rate=analysis_result.sample_rate,
                )
            
            response_items.append(UploadMaterialsResponseItem(
                material_id=db_material.material_id,
                material_type=db_material.material_type,
                jy_name=db_material.jy_name,
                relative_path=f"{session_id}\\Resources\\{os.path.basename(db_material.local_path)}",
                media_metadata=metadata_response
            ))
        except FileNotFoundError:
            # 这是一个可预见的错误，比如R2文件不存在或下载失败
            raise HTTPException(
                status_code=404,
                detail=f"处理失败：无法从R2下载或找到文件 {material_info.r2_url}"
            )
        except Exception as e:
            # 捕获其他未知错误
            raise HTTPException(
                status_code=500,
                detail=f"处理素材 {material_info.r2_url} 时发生未知错误: {str(e)}"
            )

    # 使用 asyncio.gather 并发处理所有素材
    await asyncio.gather(*(process_material(m) for m in request.materials))

    return response_items 