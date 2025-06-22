# 会话、轨道、片段、特效等核心操作API
import uuid
import os
import shutil
import logging
from typing import List, Literal, Optional, Dict
from enum import Enum

from fastapi import APIRouter, Body, Path, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.script_file import Script_file
from app.core.track import Track_type
from app.core.time_util import Timerange
from app.core.segment import Clip_settings
from app.core.video_segment import Video_segment
from app.core.audio_segment import Audio_segment
from app.core.text_segment import Text_segment as CoreTextSegment, Text_style, Text_border, Text_background
from app.core.metadata import (
    Video_scene_effect_type, Video_character_effect_type, Transition_type,
    Audio_scene_effect_type, Tone_effect_type, Speech_to_song_type, Font_type,
    Text_intro, Text_outro, Text_loop_anim, Intro_type, Outro_type,
    Group_animation_type, Filter_type
)
from app.core.keyframe import Keyframe_property
from app.database.session import get_db_session
from app.services.session_state_manager import session_state_manager
from app.services.script_file_manager import script_file_manager
from app.utils.zip_manager import zip_manager
from app.utils.r2_client import r2_client
from app.utils.path_manager import path_manager
from app.config import settings


# ============================= Router ============================= #
router = APIRouter()
# ============================= 公共模型与工具函数 ============================= #

class GeneralEffectResponse(BaseModel):
    """通用效果API的响应模型"""
    segment_id: Optional[str] = None
    message: str = "效果添加成功"

class TimerangeModel(BaseModel):
    start: int = Field(..., description="开始时间 (微秒)")
    duration: int = Field(..., description="持续时间 (微秒)")

def find_segment_in_session(script_file: Script_file, segment_id: str) -> Optional[Video_segment]:
    """在会话的所有轨道中查找指定ID的视频片段"""
    for track in script_file.tracks.values():
        for segment in track.segments:
            # 确保只对视频片段操作
            if segment.segment_id == segment_id and isinstance(segment, Video_segment):
                return segment
    return None

def find_audio_segment_in_session(script_file: Script_file, segment_id: str) -> Optional[Audio_segment]:
    """在会话的所有轨道中查找指定ID的音频片段"""
    for track in script_file.tracks.values():
        for segment in track.segments:
            if segment.segment_id == segment_id and isinstance(segment, Audio_segment):
                return segment
    return None

def find_text_segment_in_session(script_file: Script_file, segment_id: str) -> Optional[CoreTextSegment]:
    """在会话的所有轨道中查找指定ID的文本片段"""
    for track in script_file.tracks.values():
        for segment in track.segments:
            if segment.segment_id == segment_id and isinstance(segment, CoreTextSegment):
                return segment
    return None

def hex_to_rgb_normalized(hex_color: str) -> tuple[float, float, float]:
    """将 #RRGGBB 格式的颜色字符串转换为归一化的RGB元组"""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))


# ============================= 依赖注入 ============================= #

async def get_script_file_from_session_id(
    session_id: str = Path(..., description="会话ID"),
    db: AsyncSession = Depends(get_db_session)
) -> Script_file:
    """
    一个依赖注入函数，用于：
    1. 验证 session_id 是否存在于数据库中。
    2. 使用 ScriptFileManager 获取或创建 Script_file 实例。
    """
    session_db_obj = await session_state_manager.get_session(db, session_id)
    if not session_db_obj:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    if session_db_obj.status != 'active':
        raise HTTPException(status_code=400, detail=f"会话状态为 '{session_db_obj.status}'，无法进行操作。")

    # 修复：将数据库会话传递给script_file_manager，支持素材重建
    return await script_file_manager.get_script_file(session_db_obj, db)


# =================================================================== #
# ======================== API Endpoints ============================ #
# =================================================================== #


# ============================= 1. 会话管理 API (Session Management) ============================= #

class CreateSessionRequest(BaseModel):
    """创建新会话的请求体"""
    width: int = Field(1920, description="画布宽度", example=1920)
    height: int = Field(1080, description="画布高度", example=1080)
    fps: int = Field(30, description="视频帧率", example=30)
    project_name: Optional[str] = Field(None, description="项目名称", example="我的第一个项目")

class CreateSessionResponse(BaseModel):
    """创建新会話的响应体"""
    session_id: str = Field(..., description="唯一的会话ID", example="a1b2c3d4-e5f6-7890-1234-567890abcdef")
    message: str = Field("会话创建成功", description="操作结果信息")

class ActionType(str, Enum):
    """会话操作的类型"""
    SAVE_DRAFT = "save_draft" # 保存、打包并上传草稿

class UpdateSessionActionRequest(BaseModel):
    action_type: ActionType = Field(..., description="要执行的操作类型")
    payload: Optional[Dict] = Field(None, description="操作所需的数据")

class UpdateSessionActionResponse(BaseModel):
    status: str
    message: str
    session_id: Optional[str] = Field(None, description="操作成功后返回的会话ID，可用于构建下载链接")


@router.post(
    "/create",
    response_model=CreateSessionResponse,
    summary="节点1: 创建新会话"
)
async def create_session(
    request: CreateSessionRequest = Body(...),
    db: AsyncSession = Depends(get_db_session)
):
    """
    创建一个新的剪辑会话。

    此端点会在数据库中创建一条新的会话记录，并从 'draft_template' 
    完整复制一份草稿工程到会话目录 '{path_manager.output_dir}/{session_id}/'。
    这是所有剪辑操作的起点。
    """
    # 如果未提供项目名称，则动态生成一个
    project_name = request.project_name if request.project_name else f"session_{str(uuid.uuid4())[:8]}"
    
    # 1. 在数据库中创建会话记录
    new_session = await session_state_manager.create_session(
        db=db,
        width=request.width,
        height=request.height,
        fps=request.fps,
        project_name=project_name
    )
    
    # 2. 复制草稿模板到会话目录
    template_dir = "draft_template"
    if not os.path.isdir(template_dir):
        # 这是一个严重的配置错误，如果模板不存在，服务无法正常工作
        raise HTTPException(status_code=500, detail=f"服务器内部错误：草稿模板目录 '{template_dir}' 未找到。")
        
    session_dir = path_manager.get_session_dir(new_session.session_id)
    
    try:
        # 检查目标目录是否已存在
        if os.path.exists(session_dir):
            # 如果目录已存在，先删除它
            shutil.rmtree(session_dir)
            logging.info(f"🗑️ [SESSION CREATE] 清理已存在的会话目录: {session_dir}")
        
        # 复制模板到会话目录
        shutil.copytree(template_dir, session_dir)
        logging.info(f"✅ [SESSION CREATE] 已成功将模板 '{template_dir}' 复制到 '{session_dir}'")
    except OSError as e:
        # 如果复制失败，也应视为服务器内部错误
        logging.error(f"❌ [SESSION CREATE] 无法创建会话目录或复制模板文件: {e}")
        raise HTTPException(status_code=500, detail=f"服务器内部错误：无法创建会话目录或复制模板文件: {e}")

    return CreateSessionResponse(session_id=new_session.session_id)


@router.post(
    "/{session_id}/actions",
    response_model=UpdateSessionActionResponse,
    summary="节点6: 对会话执行操作（如保存）",
    description="对指定的会话执行各种操作。目前支持 'save_draft'，用于保存、打包并上传草稿。",
)
async def update_session_action(
    session_id: str = Path(..., description="会话ID"),
    action: UpdateSessionActionRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id),
):
    if action.action_type == ActionType.SAVE_DRAFT:
        try:
            # 1. 保存草稿文件
            session_dir = path_manager.get_session_dir(session_id)
            output_path = path_manager.get_draft_content_path(session_id)
            script_file.dump(output_path)
            
            # 2. 打包会话目录
            zip_filename = f"{session_id}.zip"
            # 将 zip 文件存储在特定的 zips 目录下，以保持根目录清洁
            zip_output_dir = path_manager.get_zips_dir()
            os.makedirs(zip_output_dir, exist_ok=True)
            zip_output_path = path_manager.get_zip_path(zip_filename)

            await zip_manager.create_zip_from_directory(session_dir, zip_output_path)
            
            # 3. 上传到 R2
            await r2_client.upload_file(file_path=zip_output_path, object_key=zip_filename)

            # 4. 构造 R2 公开访问 URL
            public_url = f"{settings.R2_PUBLIC_URL}/{zip_filename}"
            
            # 5. 清理本地的 zip 文件和会话草稿目录
            os.remove(zip_output_path)
            shutil.rmtree(session_dir)

            # 6. 打印成功日志
            logging.info(f"打包成功，上传R2 成功，草稿文件已清理。R2 zip url为：{public_url}")

            return UpdateSessionActionResponse(
                status="success", 
                message="草稿已成功保存、打包并上传。",
                session_id=session_id
            )
        except Exception as e:
            # 记录详细错误日志
            logging.error(f"❌ [SAVE & UPLOAD FAILED] Session: {session_id}, Error: {e}")
            # 失败后也尝试清理，避免垃圾文件残留
            if os.path.exists(zip_output_path):
                os.remove(zip_output_path)
            if os.path.isdir(session_dir):
                shutil.rmtree(session_dir)
            raise HTTPException(status_code=500, detail=f"保存、打包或上传草稿时发生严重错误: {str(e)}")

    raise HTTPException(status_code=400, detail=f"不支持的操作类型: '{action.action_type}'")


# ============================= 3. 轨道管理 API (Track Management) ============================= #

TrackTypeLiteral = Literal["video", "audio", "text", "effect", "filter"]

class AddTrackRequest(BaseModel):
    """添加轨道的请求体"""
    track_type: TrackTypeLiteral = Field(..., description="轨道类型")
    track_name: Optional[str] = Field(None, description="轨道名称。如果未提供，将使用默认名称。")
    mute: bool = Field(False, description="轨道是否静音")
    relative_index: int = Field(0, description="相对(同类型轨道的)图层位置, 越高越接近前景")
    absolute_index: Optional[int] = Field(None, description="绝对图层位置, 直接覆盖渲染层级, 会忽略relative_index")

@router.post(
    "/{session_id}/tracks",
    response_model=dict,
    summary="节点2: 添加轨道"
)
async def add_track(
    session_id: str = Path(..., description="会话ID"),
    request: AddTrackRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """
    向草稿中添加一条新的轨道。
    """
    try:
        core_track_type = Track_type.from_name(request.track_type)
        
        script_file.add_track(
            track_type=core_track_type,
            track_name=request.track_name,
            mute=request.mute,
            relative_index=request.relative_index,
            absolute_index=request.absolute_index
        )
        
        # 获取刚创建的轨道
        track_name = request.track_name if request.track_name else core_track_type.name
        new_track = script_file.tracks[track_name]
        
        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path) # 持久化
        
        return {
            "track_id": new_track.track_id,
            "track_name": new_track.name,
            "track_type": new_track.track_type.name,
            "render_index": new_track.render_index,
            "mute": new_track.mute,
            "segment_count": len(new_track.segments)
        }
    except (ValueError, TypeError, NameError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================= 4. 片段管理 API (Segment Management) ============================= #

SegmentTypeLiteral = Literal["video", "audio", "text"]

class ClipSettingsModel(BaseModel):
    alpha: Optional[float] = Field(1.0, description="图像不透明度 (0-1)")
    rotation: Optional[float] = Field(0.0, description="顺时针旋转角度")
    flip_horizontal: Optional[bool] = Field(False, description="是否水平翻转")
    flip_vertical: Optional[bool] = Field(False, description="是否垂直翻转")
    transform_x: Optional[float] = Field(0.0, description="水平位移")
    transform_y: Optional[float] = Field(0.0, description="垂直位移")
    scale_x: Optional[float] = Field(1.0, description="水平缩放")
    scale_y: Optional[float] = Field(1.0, description="垂直缩放")

class TextStyleParams(BaseModel):
    """文本样式的参数, 用于在创建时直接指定"""
    size: Optional[float] = Field(None, description="字体大小")
    bold: Optional[bool] = Field(None, description="是否加粗")
    italic: Optional[bool] = Field(None, description="是否斜体")
    underline: Optional[bool] = Field(None, description="是否下划线")
    color: Optional[str] = Field(None, description="字体颜色, 格式'#RRGGBB'")
    alpha: Optional[float] = Field(None, description="不透明度 (0-1)")
    align: Optional[Literal[0, 1, 2]] = Field(None, description="对齐方式(0-左, 1-中, 2-右)")
    vertical: Optional[bool] = Field(None, description="是否为竖排文本")
    letter_spacing: Optional[int] = Field(None, description="字符间距")
    line_spacing: Optional[int] = Field(None, description="行间距")
    font_id: Optional[str] = Field(None, description="字体ID (对应Font_type元数据)")
    
class TextBorderParams(BaseModel):
    """文本描边的参数, 用于在创建时直接指定"""
    width: float = Field(..., description="描边宽度")
    color: str = Field("#000000", description="描边颜色, 格式'#RRGGBB'")
    alpha: float = Field(1.0, description="描边不透明度 (0-1)", ge=0, le=1)
    
class TextBackgroundParams(BaseModel):
    """文本背景的参数, 用于在创建时直接指定"""
    color: str = Field(..., description="背景颜色, 格式'#RRGGBB'")
    style: Literal[1, 2] = Field(1, description="背景样式(1或2)")
    alpha: float = Field(1.0, description="背景不透明度 (0-1)", ge=0, le=1)
    round_radius: float = Field(0.0, description="背景圆角半径 (0-1)", ge=0, le=1)
    height: float = Field(0.14, description="背景高度 (0-1)", ge=0, le=1)
    width: float = Field(0.14, description="背景宽度 (0-1)", ge=0, le=1)
    horizontal_offset: float = Field(0.5, description="背景水平偏移 (0-1)", ge=0, le=1)
    vertical_offset: float = Field(0.5, description="背景竖直偏移 (0-1)", ge=0, le=1)

class TextParams(BaseModel):
    content: str = Field(..., description="文本内容")
    style: Optional[TextStyleParams] = Field(None, description="文本样式")
    border: Optional[TextBorderParams] = Field(None, description="文本描边")
    background: Optional[TextBackgroundParams] = Field(None, description="文本背景")
    clip_settings: Optional[ClipSettingsModel] = Field(None, description="图像调整设置")

class AudioParams(BaseModel):
    volume: Optional[float] = Field(1.0, description="音量 (0-2)")
    speed: Optional[float] = Field(None, description="播放速度, 默认为1.0")

class VideoParams(BaseModel):
    clip_settings: Optional[ClipSettingsModel] = Field(None, description="图像调整设置")
    volume: Optional[float] = Field(1.0, description="音量 (0-2)")

class SegmentParams(BaseModel):
    video: Optional[VideoParams] = None
    audio: Optional[AudioParams] = None
    text: Optional[TextParams] = None

class AddSegmentRequest(BaseModel):
    """添加片段到轨道的请求体"""
    type: SegmentTypeLiteral = Field(..., description="片段类型")
    track_name: str = Field(..., description="目标轨道名称")
    material_id: Optional[str] = Field(None, description="素材ID (对视频/音频/贴纸类型是必须的)")
    target_timerange: TimerangeModel = Field(..., description="在轨道上的时间范围")
    source_timerange: Optional[TimerangeModel] = Field(None, description="可选，素材裁剪范围 (视频/音频需要)")
    params: SegmentParams = Field(..., description="根据类型不同的参数")

class AddSegmentResponse(BaseModel):
    """添加片段的响应体"""
    segment_id: str
    material_id: Optional[str]
    track_name: str
    start: int
    end: int

class AddTrackEffectRequest(BaseModel):
    """在轨道上添加独立特效的请求体"""
    track_name: str = Field(..., description="目标轨道名称")
    effect_type: Literal["video_scene", "video_character"] = Field(..., description="特效大类型")
    effect_id: str = Field(..., description="具体特效的ID")
    target_timerange: TimerangeModel = Field(..., description="特效在轨道上的时间范围")
    params: Optional[List[Optional[float]]] = Field(None, description="特效参数列表 (0-100)")

class AddTrackFilterRequest(BaseModel):
    """在轨道上添加独立滤镜的请求体"""
    track_name: str = Field(..., description="目标轨道名称")
    filter_id: str = Field(..., description="滤镜ID")
    target_timerange: TimerangeModel = Field(..., description="滤镜在轨道上的时间范围")
    intensity: float = Field(100.0, description="滤镜强度 (0-100)", ge=0, le=100)

@router.post(
    "/{session_id}/segments",
    response_model=AddSegmentResponse,
    summary="节点4: 添加片段(视频/音频/文本等)"
)
async def add_segment(
    session_id: str = Path(..., description="会话ID"),
    request: AddSegmentRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """向轨道中添加各种类型片段的统一接口。"""
    try:
        # 1. 验证轨道存在
        if request.track_name not in script_file.tracks:
            raise HTTPException(status_code=404, detail=f"名为 '{request.track_name}' 的轨道不存在")
        track = script_file.tracks[request.track_name]

        # 2. 准备通用参数
        target_tr = Timerange(request.target_timerange.start, request.target_timerange.duration)
        source_tr = Timerange(request.source_timerange.start, request.source_timerange.duration) if request.source_timerange else None
        
        segment = None
        
        # 3. 根据类型创建不同的片段实例
        if request.type == "video":
            if not request.material_id or not request.params.video:
                raise HTTPException(status_code=400, detail="视频片段需要 material_id 和 params.video")
            # 找到对应的素材实例 (这里简化了，实际需要从素材管理器获取)
            video_material = next((m for m in script_file.materials.videos if m.material_id == request.material_id), None)
            if not video_material:
                raise HTTPException(status_code=404, detail=f"视频素材 '{request.material_id}' 不存在")

            cs = request.params.video.clip_settings
            clip_settings = Clip_settings(
                transform_x=cs.transform_x, transform_y=cs.transform_y, 
                scale_x=cs.scale_x, scale_y=cs.scale_y,
                alpha=cs.alpha, rotation=cs.rotation,
                flip_horizontal=cs.flip_horizontal, flip_vertical=cs.flip_vertical
            ) if cs else None
            
            segment = Video_segment(
                material=video_material,
                target_timerange=target_tr,
                source_timerange=source_tr,
                volume=request.params.video.volume,
                clip_settings=clip_settings
            )

        elif request.type == "audio":
            if not request.material_id or not request.params.audio:
                raise HTTPException(status_code=400, detail="音频片段需要 material_id 和 params.audio")
            audio_material = next((m for m in script_file.materials.audios if m.material_id == request.material_id), None)
            if not audio_material:
                raise HTTPException(status_code=404, detail=f"音频素材 '{request.material_id}' 不存在")
            
            segment = Audio_segment(
                material=audio_material,
                target_timerange=target_tr,
                source_timerange=source_tr,
                volume=request.params.audio.volume,
                speed=request.params.audio.speed
            )

        elif request.type == "text":
            if not request.params.text:
                raise HTTPException(status_code=400, detail="文本片段需要 params.text")

            # --- 构造文本片段所需参数 ---
            text_params = request.params.text
            
            # 1. 字体
            font_enum = None
            if text_params.style and text_params.style.font_id:
                try:
                    font_enum = getattr(Font_type, text_params.style.font_id)
                except AttributeError:
                    raise HTTPException(status_code=404, detail=f"字体 '{text_params.style.font_id}' 不存在")
            
            # 2. 文本样式
            text_style_instance = None
            if text_params.style:
                style_args = text_params.style.model_dump(exclude_none=True, exclude={'font_id'})
                if 'color' in style_args and style_args['color']:
                    style_args['color'] = hex_to_rgb_normalized(style_args['color'])
                text_style_instance = Text_style(**style_args)

            # 3. 描边
            border_instance = None
            if text_params.border:
                border_args = text_params.border.model_dump()
                border_args['color'] = hex_to_rgb_normalized(border_args['color'])
                border_instance = Text_border(**border_args)

            # 4. 背景
            background_instance = None
            if text_params.background:
                background_instance = Text_background(**text_params.background.model_dump())
            
            # 5. 图像设置
            clip_settings_instance = None
            if text_params.clip_settings:
                clip_settings_instance = Clip_settings(**text_params.clip_settings.model_dump())

            # --- 创建文本片段 ---
            segment = CoreTextSegment(
                text=text_params.content,
                timerange=target_tr,
                font=font_enum,
                style=text_style_instance,
                border=border_instance,
                background=background_instance,
                clip_settings=clip_settings_instance
            )
        
        # 更多类型如 'sticker' 在此添加
        else:
            raise HTTPException(status_code=400, detail=f"不支持的片段类型: {request.type}")

        # 4. 将片段添加到轨道
        if segment:
            script_file.add_segment(segment, track.name)
        
        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path) # 持久化
        
        return AddSegmentResponse(
            segment_id=segment.segment_id,
            material_id=segment.material_id,
            track_name=track.name,
            start=segment.start,
            end=segment.end
        )
    except (TypeError, ValueError) as e:
        # 捕获核心库可能抛出的类型或值错误
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/{session_id}/track_effects",
    response_model=GeneralEffectResponse,
    summary="在轨道上添加独立特效"
)
async def add_track_effect(
    session_id: str = Path(..., description="会话ID"),
    request: AddTrackEffectRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """在指定轨道的特定时间范围上添加一个独立的特效，不依附于任何片段。"""
    if request.track_name not in script_file.tracks:
        raise HTTPException(status_code=404, detail=f"轨道 '{request.track_name}' 不存在")

    try:
        effect_enum = Video_scene_effect_type if request.effect_type == "video_scene" else Video_character_effect_type
        effect_meta = getattr(effect_enum, request.effect_id)
        
        target_tr = Timerange(request.target_timerange.start, request.target_timerange.duration)

        script_file.add_effect(
            effect=effect_meta,
            t_range=target_tr,
            track_name=request.track_name,
            params=request.params
        )
        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path)
        return GeneralEffectResponse(message="独立特效添加成功")
    except AttributeError:
        raise HTTPException(
            status_code=404,
            detail=f"类型为'{request.effect_type}'的特效 '{request.effect_id}' 不存在"
        )
    except (ValueError, TypeError, NameError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/{session_id}/track_filters",
    response_model=GeneralEffectResponse,
    summary="在轨道上添加独立滤镜"
)
async def add_track_filter(
    session_id: str = Path(..., description="会话ID"),
    request: AddTrackFilterRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """在指定轨道的特定时间范围上添加一个独立的滤镜，不依附于任何片段。"""
    if request.track_name not in script_file.tracks:
        raise HTTPException(status_code=404, detail=f"轨道 '{request.track_name}' 不存在")

    try:
        filter_meta = getattr(Filter_type, request.filter_id)
        target_tr = Timerange(request.target_timerange.start, request.target_timerange.duration)

        script_file.add_filter(
            filter_meta=filter_meta,
            t_range=target_tr,
            track_name=request.track_name,
            intensity=request.intensity
        )
        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path)
        return GeneralEffectResponse(message="独立滤镜添加成功")
    except AttributeError:
        raise HTTPException(status_code=404, detail=f"滤镜 '{request.filter_id}' 不存在")
    except (ValueError, TypeError, NameError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================= 5. 视频效果 API (Video Effects) ============================= #

EffectTypeLiteral = Literal["video_scene", "video_character"] # 简化版，后续可扩展

class AddEffectRequest(BaseModel):
    """添加视频特效的请求体"""
    type: EffectTypeLiteral = Field(..., description="特效大类型")
    effect_type_id: str = Field(..., description="具体特效的ID (对应元数据枚举的名称)")
    segment_id: str = Field(..., description="要应用特效的片段ID")
    params: Optional[List[Optional[float]]] = Field(None, description="特效参数列表 (0-100)")

class AddTransitionRequest(BaseModel):
    """添加视频转场效果的请求体"""
    transition_type_id: str = Field(..., description="转场类型ID (对应元数据枚举的名称)")
    segment_id: str = Field(..., description="转场应用的片段ID (转场发生在此片段的末尾)")
    duration: Optional[int] = Field(None, description="转场持续时间(微秒)，不提供则使用默认值")


class AddBackgroundFillingRequest(BaseModel):
    """添加背景填充的请求体, 对齐 core.video_segment.add_background_filling"""
    segment_id: str = Field(..., description="视频片段ID")
    fill_type: Literal["blur", "color"] = Field(..., description="背景填充类型")
    blur: Optional[float] = Field(0.0625, description="模糊程度(当fill_type为blur时有效, 0-1)")
    color: Optional[str] = Field("#00000000", description="背景颜色(当fill_type为color时有效, 格式'#RRGGBBAA')")

class AddVideoAnimationRequest(BaseModel):
    """添加视频动画的请求体"""
    segment_id: str = Field(..., description="视频片段ID")
    animation_type: Literal["intro", "outro", "group"] = Field(..., description="动画类型")
    animation_id: str = Field(..., description="动画ID (对应元数据枚举的名称)")
    duration: Optional[int] = Field(None, description="动画持续时间(微秒)，不提供则使用默认值")

class AddVideoFilterRequest(BaseModel):
    """为视频片段添加滤镜的请求体"""
    segment_id: str = Field(..., description="视频片段ID")
    filter_id: str = Field(..., description="滤镜ID (对应Filter_type元数据)")
    intensity: float = Field(100.0, description="滤镜强度 (0-100)", ge=0, le=100)


@router.post(
    "/{session_id}/effects",
    response_model=GeneralEffectResponse,
    summary="节点5: 添加视频特效"
)
async def add_video_effect(
    session_id: str = Path(..., description="会话ID"),
    request: AddEffectRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """为指定的视频片段添加特效。"""
    segment = find_segment_in_session(script_file, request.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail=f"ID为 '{request.segment_id}' 的视频片段不存在")

    try:
        effect_enum = Video_scene_effect_type if request.type == "video_scene" else Video_character_effect_type
        effect_meta = getattr(effect_enum, request.effect_type_id)
        
        segment.add_effect(effect_meta, request.params)

        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path) # 持久化
        return GeneralEffectResponse(segment_id=segment.segment_id)
    except AttributeError:
        raise HTTPException(
            status_code=404,
            detail=f"类型为'{request.type}'的特效 '{request.effect_type_id}' 不存在"
        )
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/{session_id}/transitions",
    response_model=GeneralEffectResponse,
    summary="添加视频转场效果"
)
async def add_transition(
    session_id: str = Path(..., description="会话ID"),
    request: AddTransitionRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """为视频片段的末尾添加转场效果"""
    segment = find_segment_in_session(script_file, request.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="片段未找到")

    try:
        # 使用getattr通过字符串名称从枚举中获取成员
        transition_meta = getattr(Transition_type, request.transition_type_id)
    except AttributeError:
        # 如果提供的ID在枚举中不存在，返回404
        raise HTTPException(
            status_code=404, 
            detail=f"转场效果 '{request.transition_type_id}' 不存在"
        )

    segment.add_transition(
        transition_type=transition_meta,
        duration=request.duration
    )
    
    # 持久化修改到草稿文件
    session_dir = path_manager.get_session_dir(session_id)
    output_path = path_manager.get_draft_content_path(session_id)
    script_file.dump(output_path)

    return GeneralEffectResponse(segment_id=request.segment_id)



@router.post(
    "/{session_id}/background_filling",
    response_model=GeneralEffectResponse,
    summary="添加背景填充"
)
async def add_background_filling(
    session_id: str = Path(..., description="会话ID"),
    request: AddBackgroundFillingRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """为视频片段添加背景填充（模糊或颜色）"""
    segment = find_segment_in_session(script_file, request.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="片段未找到")
    
    try:
        segment.add_background_filling(
            fill_type=request.fill_type,
            blur=request.blur,
            color=request.color
        )
    
        # 手动将背景填充添加到全局素材库
        if segment.background_filling is not None:
            script_file.materials.canvases.append(segment.background_filling)
    
        # 持久化修改到草稿文件
        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path)
    
        return GeneralEffectResponse(segment_id=request.segment_id, message="背景填充添加成功")
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/{session_id}/video/animation",
    response_model=GeneralEffectResponse,
    summary="添加视频动画"
)
async def add_video_animation(
    session_id: str = Path(..., description="会话ID"),
    request: AddVideoAnimationRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """为指定的视频片段添加入场、出场或组合动画。"""
    segment = find_segment_in_session(script_file, request.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail=f"ID为 '{request.segment_id}' 的视频片段不存在")

    try:
        if request.animation_type == "intro":
            animation_enum = Intro_type
        elif request.animation_type == "outro":
            animation_enum = Outro_type
        elif request.animation_type == "group":
            animation_enum = Group_animation_type
        else:
            # This case should not be reached due to Pydantic's Literal validation
            raise HTTPException(status_code=400, detail="无效的动画类型")

        animation_meta = getattr(animation_enum, request.animation_id)
        segment.add_animation(animation_meta, duration=request.duration)
        
        # [最终修复 - 正确版]: 将新创建的动画素材注册到全局素材列表中
        if segment.animations_instance and segment.animations_instance not in script_file.materials:
            script_file.materials.animations.append(segment.animations_instance)

        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path)
        return GeneralEffectResponse(segment_id=request.segment_id, message="视频动画添加成功")
    except AttributeError:
        raise HTTPException(
            status_code=404,
            detail=f"类型为'{request.animation_type}'的动画 '{request.animation_id}' 不存在"
        )
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/{session_id}/video/filter",
    response_model=GeneralEffectResponse,
    summary="为视频片段添加滤镜"
)
async def add_video_filter(
    session_id: str = Path(..., description="会话ID"),
    request: AddVideoFilterRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """为指定的视频片段添加一个滤镜效果。"""
    segment = find_segment_in_session(script_file, request.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail=f"ID为 '{request.segment_id}' 的视频片段不存在")

    try:
        filter_meta = getattr(Filter_type, request.filter_id)
        segment.add_filter(filter_meta, intensity=request.intensity)

        # [最终修复 - 正确版]: 将新创建的滤镜素材注册到全局素材列表中
        for f in segment.filters:
            if f not in script_file.materials:
                script_file.materials.filters.append(f)

        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path)
        return GeneralEffectResponse(segment_id=request.segment_id, message="滤镜添加成功")
    except AttributeError:
        raise HTTPException(status_code=404, detail=f"滤镜 '{request.filter_id}' 不存在")
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================= 6. 音频效果 API (Audio Effects) ============================= #

class AddAudioFadeRequest(BaseModel):
    """添加音频淡入淡出效果的请求体"""
    segment_id: str = Field(..., description="音频片段ID")
    fade_in: int = Field(0, description="淡入时长(微秒)，0表示不使用")
    fade_out: int = Field(0, description="淡出时长(微秒)，0表示不使用")

AudioEffectTypeLiteral = Literal["sound_effect", "tone", "speech_to_song"]

class AddAudioEffectRequest(BaseModel):
    """添加音频特效的请求体"""
    segment_id: str = Field(..., description="音频片段ID")
    effect_type: AudioEffectTypeLiteral = Field(..., description="特效类型")
    effect_id: str = Field(..., description="具体特效ID (对应元数据枚举的名称)")
    params: Optional[List[Optional[float]]] = Field(None, description="特效参数列表 (0-100)")

class AdjustVolumeRequest(BaseModel):
    """调整音量的请求体"""
    segment_id: str = Field(..., description="音频或视频片段ID")
    volume: float = Field(..., description="音量 (0-2)")


@router.post(
    "/{session_id}/audio/fade",
    response_model=GeneralEffectResponse,
    summary="添加音频淡入淡出效果"
)
async def add_audio_fade(
    session_id: str = Path(..., description="会话ID"),
    request: AddAudioFadeRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """为指定的音频片段添加淡入淡出效果。"""
    segment = find_audio_segment_in_session(script_file, request.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail=f"ID为 '{request.segment_id}' 的音频片段不存在")

    try:
        segment.add_fade(in_duration=request.fade_in, out_duration=request.fade_out)
        
        # 手动将新添加的淡入淡出效果同步到materials中
        if segment.fade is not None and segment.fade not in script_file.materials.audio_fades:
            script_file.materials.audio_fades.append(segment.fade)
        
        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path) # 持久化
        return GeneralEffectResponse(segment_id=segment.segment_id)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/{session_id}/audio/effects",
    response_model=GeneralEffectResponse,
    summary="添加音频特效"
)
async def add_audio_effect(
    session_id: str = Path(..., description="会话ID"),
    request: AddAudioEffectRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """为指定的音频片段添加一个音频特效。"""
    segment = find_audio_segment_in_session(script_file, request.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail=f"ID为 '{request.segment_id}' 的音频片段不存在")

    try:
        effect_enum = None
        if request.effect_type == "sound_effect":
            effect_enum = Audio_scene_effect_type
        elif request.effect_type == "tone":
            effect_enum = Tone_effect_type
        elif request.effect_type == "speech_to_song":
            effect_enum = Speech_to_song_type
        
        effect_meta = getattr(effect_enum, request.effect_id)
        
        segment.add_effect(effect_meta, request.params)
        
        # 手动将新添加的音频特效同步到materials中
        # segment.effects列表的最后一个元素就是刚刚添加的特效
        if segment.effects and segment.effects[-1] not in script_file.materials.audio_effects:
            script_file.materials.audio_effects.append(segment.effects[-1])
        
        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path) # 持久化
        return GeneralEffectResponse(segment_id=segment.segment_id)
    except AttributeError:
        raise HTTPException(
            status_code=404,
            detail=f"类型为'{request.effect_type}'的音频特效 '{request.effect_id}' 不存在"
        )
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/{session_id}/volume",
    response_model=GeneralEffectResponse,
    summary="调整音频音量"
)
async def adjust_volume(
    session_id: str = Path(..., description="会话ID"),
    request: AdjustVolumeRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """
    调整指定音频片段的音量。
    理论上也适用于视频片段，但文档将其归类于音频效果。
    """
    try:
        segment = find_segment_in_session(script_file, request.segment_id)
        if not isinstance(segment, (Video_segment, Audio_segment)):
            # Fallback to audio segment if video segment not found
            segment = find_audio_segment_in_session(script_file, request.segment_id)
            if not segment:
                 raise HTTPException(status_code=404, detail=f"ID为 '{request.segment_id}' 的音频或视频片段不存在")
            
        segment.volume = request.volume
        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path) # 持久化
        return GeneralEffectResponse(segment_id=request.segment_id, message="音量调整成功")
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================= 7. 文本与字幕 API (Text & Subtitles) ============================= #

class SetTextStyleRequest(BaseModel):
    """设置文本样式的请求体, 参数对齐 core.text_segment.Text_style"""
    segment_id: str = Field(..., description="文本片段ID")
    size: Optional[float] = Field(None, description="字体大小")
    bold: Optional[bool] = Field(None, description="是否加粗")
    italic: Optional[bool] = Field(None, description="是否斜体")
    underline: Optional[bool] = Field(None, description="是否下划线")
    color: Optional[str] = Field(None, description="字体颜色, 格式'#RRGGBB'")
    alpha: Optional[float] = Field(None, description="不透明度 (0-1)")
    align: Optional[Literal[0, 1, 2]] = Field(None, description="对齐方式(0-左, 1-中, 2-右)")
    vertical: Optional[bool] = Field(None, description="是否为竖排文本")
    letter_spacing: Optional[int] = Field(None, description="字符间距")
    line_spacing: Optional[int] = Field(None, description="行间距")
    font_id: Optional[str] = Field(None, description="字体ID (对应Font_type元数据)")

class SetTextBorderRequest(BaseModel):
    """添加文本描边的请求体"""
    segment_id: str = Field(..., description="文本片段ID")
    width: float = Field(..., description="描边宽度")
    color: str = Field("#000000", description="描边颜色, 格式'#RRGGBB'")
    alpha: float = Field(1.0, description="描边不透明度 (0-1)", ge=0, le=1)

class SetTextBackgroundRequest(BaseModel):
    """添加文本背景的请求体"""
    segment_id: str = Field(..., description="文本片段ID")
    color: str = Field(..., description="背景颜色, 格式'#RRGGBB'")
    style: Literal[1, 2] = Field(1, description="背景样式(1或2)")
    alpha: float = Field(1.0, description="背景不透明度 (0-1)", ge=0, le=1)
    round_radius: float = Field(0.0, description="背景圆角半径 (0-1)", ge=0, le=1)
    height: float = Field(0.14, description="背景高度 (0-1)", ge=0, le=1)
    width: float = Field(0.14, description="背景宽度 (0-1)", ge=0, le=1)
    horizontal_offset: float = Field(0.5, description="背景水平偏移 (0-1)", ge=0, le=1)
    vertical_offset: float = Field(0.5, description="背景竖直偏移 (0-1)", ge=0, le=1)

class AddTextAnimationRequest(BaseModel):
    """添加文本动画的请求体"""
    segment_id: str = Field(..., description="文本片段ID")
    animation_type: Literal["intro", "outro", "loop"] = Field(..., description="动画类型")
    animation_id: str = Field(..., description="动画ID (对应元数据枚举的名称)")
    duration: Optional[int] = Field(500000, description="动画持续时间(微秒)")



class BatchTextStyleParams(BaseModel):
    """批量设置字幕样式的文本样式参数(不含segment_id)"""
    size: Optional[float] = Field(None, description="字体大小")
    bold: Optional[bool] = Field(None, description="是否加粗")
    italic: Optional[bool] = Field(None, description="是否斜体")
    underline: Optional[bool] = Field(None, description="是否下划线")
    color: Optional[str] = Field(None, description="字体颜色, 格式'#RRGGBB'")
    alpha: Optional[float] = Field(None, description="不透明度 (0-1)")
    align: Optional[Literal[0, 1, 2]] = Field(None, description="对齐方式(0-左, 1-中, 2-右)")
    vertical: Optional[bool] = Field(None, description="是否为竖排文本")
    letter_spacing: Optional[int] = Field(None, description="字符间距")
    line_spacing: Optional[int] = Field(None, description="行间距")
    font_id: Optional[str] = Field(None, description="字体ID (对应Font_type元数据)")
    
class BatchTextBorderParams(BaseModel):
    """批量设置字幕样式的描边参数(不含segment_id)"""
    width: float = Field(..., description="描边宽度")
    color: str = Field("#000000", description="描边颜色, 格式'#RRGGBB'")
    alpha: float = Field(1.0, description="描边不透明度 (0-1)", ge=0, le=1)

class BatchTextBackgroundParams(BaseModel):
    """批量设置字幕样式的背景参数(不含segment_id)"""
    color: str = Field(..., description="背景颜色, 格式'#RRGGBB'")
    style: Literal[1, 2] = Field(1, description="背景样式(1或2)")
    alpha: float = Field(1.0, description="背景不透明度 (0-1)", ge=0, le=1)
    round_radius: float = Field(0.0, description="背景圆角半径 (0-1)", ge=0, le=1)
    height: float = Field(0.14, description="背景高度 (0-1)", ge=0, le=1)
    width: float = Field(0.14, description="背景宽度 (0-1)", ge=0, le=1)
    horizontal_offset: float = Field(0.5, description="背景水平偏移 (0-1)", ge=0, le=1)
    vertical_offset: float = Field(0.5, description="背景竖直偏移 (0-1)", ge=0, le=1)

class StyleSubtitlesRequest(BaseModel):
    """批量设置字幕样式的请求体"""
    track_name: str = Field(..., description="要修改样式的字幕轨道名称")
    text_style: Optional[BatchTextStyleParams] = Field(None, description="要应用的文本样式")
    text_border: Optional[BatchTextBorderParams] = Field(None, description="要应用的文本描边")
    text_background: Optional[BatchTextBackgroundParams] = Field(None, description="要应用的文本背景")

class StyleSubtitlesResponse(BaseModel):
    """批量设置字幕样式的响应体"""
    track_name: str
    updated_segments: int = Field(..., description="成功更新样式的片段数量")
    message: str = "样式更新成功"


@router.post(
    "/{session_id}/text/style",
    response_model=GeneralEffectResponse,
    summary="设置文本样式"
)
async def set_text_style(
    session_id: str = Path(..., description="会话ID"),
    request: SetTextStyleRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """
    为指定的文本片段设置字体和样式。
    此接口会根据传入的参数创建一个新的Text_style对象并替换原有的style。
    未提供的参数将使用Text_style类的默认值。
    """
    segment = find_text_segment_in_session(script_file, request.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="文本片段未找到")

    try:
        # 1. 处理字体ID
        if request.font_id:
            try:
                font_enum_member = getattr(Font_type, request.font_id)
                segment.font = font_enum_member.value
            except AttributeError:
                raise HTTPException(status_code=404, detail=f"字体 '{request.font_id}' 不存在")

        # 2. 创建一个新的 Text_style 实例来替换旧的
        current_style = segment.style
        style_params = {
            "size": request.size if request.size is not None else current_style.size,
            "bold": request.bold if request.bold is not None else current_style.bold,
            "italic": request.italic if request.italic is not None else current_style.italic,
            "underline": request.underline if request.underline is not None else current_style.underline,
            "color": hex_to_rgb_normalized(request.color) if request.color is not None else current_style.color,
            "alpha": request.alpha if request.alpha is not None else current_style.alpha,
            "align": request.align if request.align is not None else current_style.align,
            "vertical": request.vertical if request.vertical is not None else current_style.vertical,
            "letter_spacing": request.letter_spacing if request.letter_spacing is not None else current_style.letter_spacing,
            "line_spacing": request.line_spacing if request.line_spacing is not None else current_style.line_spacing,
        }
        segment.style = Text_style(**style_params)

        # 持久化
        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path)

        return GeneralEffectResponse(segment_id=request.segment_id, message="文本样式更新成功")
    except (ValueError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"设置文本样式失败: {e}")

@router.post(
    "/{session_id}/text/border",
    response_model=GeneralEffectResponse,
    summary="添加文本描边"
)
async def set_text_border(
    session_id: str = Path(..., description="会话ID"),
    request: SetTextBorderRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """为指定的文本片段添加或更新描边。"""
    segment = find_text_segment_in_session(script_file, request.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail=f"ID为 '{request.segment_id}' 的文本片段不存在")

    try:
        border_color_rgb = hex_to_rgb_normalized(request.color)
        
        # 使用核心库的Text_border类
        border_instance = Text_border(
            width=request.width,
            color=border_color_rgb,
            alpha=request.alpha
        )
        segment.border = border_instance # 直接赋值

        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path) # 持久化
        return GeneralEffectResponse(segment_id=segment.segment_id, message="文本描边设置成功")
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/{session_id}/text/background",
    response_model=GeneralEffectResponse,
    summary="添加文本背景"
)
async def set_text_background(
    session_id: str = Path(..., description="会话ID"),
    request: SetTextBackgroundRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """为指定的文本片段添加或更新背景。"""
    segment = find_text_segment_in_session(script_file, request.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail=f"ID为 '{request.segment_id}' 的文本片段不存在")

    try:
        # 使用核心库的Text_background类
        background_instance = Text_background(
            color=request.color,
            style=request.style,
            alpha=request.alpha,
            round_radius=request.round_radius,
            height=request.height,
            width=request.width,
            horizontal_offset=request.horizontal_offset,
            vertical_offset=request.vertical_offset
        )
        segment.background = background_instance # 直接赋值

        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path) # 持久化
        return GeneralEffectResponse(segment_id=segment.segment_id, message="文本背景设置成功")
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/{session_id}/text/animation",
    response_model=GeneralEffectResponse,
    summary="添加文本动画"
)
async def add_text_animation(
    session_id: str = Path(..., description="会话ID"),
    request: AddTextAnimationRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """为文本片段添加入场、出场或循环动画。"""
    segment = find_text_segment_in_session(script_file, request.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="文本片段未找到")

    anim_enum = None
    if request.animation_type == "intro":
        anim_enum = Text_intro
    elif request.animation_type == "outro":
        anim_enum = Text_outro
    elif request.animation_type == "loop":
        anim_enum = Text_loop_anim

    if not anim_enum:
        raise HTTPException(status_code=400, detail="无效的动画类型")

    try:
        # 使用getattr通过字符串名称从枚举中获取成员
        anim_type = getattr(anim_enum, request.animation_id)
    except AttributeError:
        raise HTTPException(
            status_code=404,
            detail=f"类型为'{request.animation_type}'的动画 '{request.animation_id}' 不存在"
        )

    # 核心库中使用 add_animation 方法, 且参数为位置参数
    segment.add_animation(anim_type, request.duration)
    
    # [修复] 将新创建的动画素材注册到全局素材列表中
    if segment.animations_instance and segment.animations_instance not in script_file.materials.animations:
        script_file.materials.animations.append(segment.animations_instance)

    # 持久化
    session_dir = path_manager.get_session_dir(session_id)
    output_path = path_manager.get_draft_content_path(session_id)
    script_file.dump(output_path)

    return GeneralEffectResponse(segment_id=request.segment_id, message="文本动画添加成功")





@router.post(
    "/{session_id}/subtitles/style",
    response_model=StyleSubtitlesResponse,
    summary="批量设置字幕样式"
)
async def style_subtitles(
    session_id: str = Path(..., description="会话ID"),
    request: StyleSubtitlesRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """为指定轨道上的所有文本片段批量设置样式。"""
    if request.track_name not in script_file.tracks:
        raise HTTPException(status_code=404, detail=f"轨道 '{request.track_name}' 不存在")

    track = script_file.tracks[request.track_name]
    if track.track_type != Track_type.text:
        raise HTTPException(status_code=400, detail=f"轨道 '{request.track_name}' 不是文本轨道")

    updated_segments_count = 0
    for segment in track.segments:
        if isinstance(segment, CoreTextSegment):
            try:
                # 更新文本样式
                if request.text_style:
                    current_style = segment.style
                    style_params = {
                        "size": request.text_style.size if request.text_style.size is not None else current_style.size,
                        "bold": request.text_style.bold if request.text_style.bold is not None else current_style.bold,
                        "italic": request.text_style.italic if request.text_style.italic is not None else current_style.italic,
                        "underline": request.text_style.underline if request.text_style.underline is not None else current_style.underline,
                        "color": hex_to_rgb_normalized(request.text_style.color) if request.text_style.color is not None else current_style.color,
                        "alpha": request.text_style.alpha if request.text_style.alpha is not None else current_style.alpha,
                        "align": request.text_style.align if request.text_style.align is not None else current_style.align,
                        "vertical": request.text_style.vertical if request.text_style.vertical is not None else current_style.vertical,
                        "letter_spacing": request.text_style.letter_spacing if request.text_style.letter_spacing is not None else current_style.letter_spacing,
                        "line_spacing": request.text_style.line_spacing if request.text_style.line_spacing is not None else current_style.line_spacing,
                    }
                    segment.style = Text_style(**style_params)

                    # 处理字体
                    if request.text_style.font_id:
                        try:
                            font_enum_member = getattr(Font_type, request.text_style.font_id)
                            segment.font = font_enum_member.value
                        except AttributeError:
                            logging.warning(f"警告: 字体 '{request.text_style.font_id}' 无效，已跳过。")
                
                # 更新文本描边
                if request.text_border:
                    border_color_rgb = hex_to_rgb_normalized(request.text_border.color)
                    segment.border = Text_border(
                        width=request.text_border.width,
                        color=border_color_rgb,
                        alpha=request.text_border.alpha
                    )

                # 更新文本背景
                if request.text_background:
                    segment.background = Text_background(
                        color=request.text_background.color,
                        style=request.text_background.style,
                        alpha=request.text_background.alpha,
                        round_radius=request.text_background.round_radius,
                        height=request.text_background.height,
                        width=request.text_background.width,
                        horizontal_offset=request.text_background.horizontal_offset,
                        vertical_offset=request.text_background.vertical_offset
                    )
                
                updated_segments_count += 1
            except (ValueError, TypeError) as e:
                logging.warning(f"警告: 处理片段 {segment.segment_id} 样式失败，已跳过。错误: {e}")
                continue
            
    # 保存草稿
    session_dir = path_manager.get_session_dir(session_id)
    output_path = path_manager.get_draft_content_path(session_id)
    script_file.dump(output_path)

    return StyleSubtitlesResponse(
        track_name=request.track_name,
        updated_segments=updated_segments_count,
        message=f"轨道 '{request.track_name}' 上的 {updated_segments_count} 个片段样式已成功更新"
    )


# ============================= 8. 关键帧 API (Keyframes) ============================= #

# 从Keyframe_property枚举动态创建Literal类型
KeyframePropertyLiteral = Literal[
    "position_x", "position_y", "rotation", "scale_x", "scale_y", 
    "uniform_scale", "alpha", "volume"
]

class AddKeyframeRequest(BaseModel):
    """添加关键帧的请求体"""
    segment_id: str = Field(..., description="片段ID")
    property: KeyframePropertyLiteral = Field(..., description="要添加关键帧的属性")
    time_offset: int = Field(..., description="关键帧在片段内的时间偏移量 (微秒)")
    value: float = Field(..., description="属性在该时间点的值")

@router.post(
    "/{session_id}/keyframes",
    response_model=GeneralEffectResponse,
    summary="添加关键帧"
)
async def add_keyframe(
    session_id: str = Path(..., description="会话ID"),
    request: AddKeyframeRequest = Body(...),
    script_file: Script_file = Depends(get_script_file_from_session_id)
):
    """
    为视觉或音频片段的特定属性添加关键帧。
    
    支持的属性包括位置、缩放、旋转和音量。
    """
    # 查找目标片段
    target_segment = None
    for track in script_file.tracks.values():
        for segment in track.segments:
            if segment.segment_id == request.segment_id:
                target_segment = segment
                break
        if target_segment:
            break
            
    if not target_segment:
        raise HTTPException(status_code=404, detail=f"ID为 {request.segment_id} 的片段不存在")

    # 添加关键帧
    try:
        prop_enum = Keyframe_property[request.property]
        target_segment.add_keyframe(prop_enum, request.time_offset, request.value)
        
        # 持久化
        session_dir = path_manager.get_session_dir(session_id)
        output_path = path_manager.get_draft_content_path(session_id)
        script_file.dump(output_path) 
        
        return GeneralEffectResponse(segment_id=request.segment_id, message="关键帧添加成功")
    except KeyError:
        raise HTTPException(status_code=400, detail=f"不支持的属性: {request.property}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加关键帧失败: {e}")

