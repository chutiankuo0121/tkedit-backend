import os
import json
import math
from copy import deepcopy

from typing import Optional, Literal, Union, overload
from typing import Type, Dict, List, Any

from . import util
from . import exceptions
from .time_util import Timerange, tim, srt_tstamp
from .local_materials import Video_material, Audio_material
from .segment import Base_segment, Speed, Clip_settings
from .audio_segment import Audio_segment, Audio_fade, Audio_effect
from .video_segment import Video_segment, Sticker_segment, Segment_animations, Video_effect, Transition, Filter, BackgroundFilling
from .effect_segment import Effect_segment, Filter_segment
from .text_segment import Text_segment, Text_style, TextBubble
from .track import Track_type, Base_track, Track

from .metadata import Video_scene_effect_type, Video_character_effect_type, Filter_type, Intro_type, Outro_type, Group_animation_type

class Script_material:
    """草稿文件中的素材信息部分"""

    audios: List[Audio_material]
    """音频素材列表"""
    videos: List[Video_material]
    """视频素材列表"""
    stickers: List[Dict[str, Any]]
    """贴纸素材列表"""
    texts: List[Dict[str, Any]]
    """文本素材列表"""

    audio_effects: List[Audio_effect]
    """音频特效列表"""
    audio_fades: List[Audio_fade]
    """音频淡入淡出效果列表"""
    animations: List[Segment_animations]
    """动画素材列表"""
    video_effects: List[Video_effect]
    """视频特效列表"""

    speeds: List[Speed]
    """变速列表"""
    masks: List[Dict[str, Any]]
    """蒙版列表"""
    transitions: List[Transition]
    """转场效果列表"""
    filters: List[Union[Filter, TextBubble]]
    """滤镜/文本花字/文本气泡列表, 导出到`effects`中"""
    canvases: List[BackgroundFilling]
    """背景填充列表"""

    def __init__(self):
        self.audios = []
        self.videos = []
        self.stickers = []
        self.texts = []

        self.audio_effects = []
        self.audio_fades = []
        self.animations = []
        self.video_effects = []

        self.speeds = []
        self.masks = []
        self.transitions = []
        self.filters = []
        self.canvases = []

    @overload
    def __contains__(self, item: Union[Video_material, Audio_material]) -> bool: ...
    @overload
    def __contains__(self, item: Union[Audio_fade, Audio_effect]) -> bool: ...
    @overload
    def __contains__(self, item: Union[Segment_animations, Video_effect, Transition, Filter]) -> bool: ...

    def __contains__(self, item) -> bool:
        if isinstance(item, Video_material):
            return item.material_id in [video.material_id for video in self.videos]
        elif isinstance(item, Audio_material):
            return item.material_id in [audio.material_id for audio in self.audios]
        elif isinstance(item, Audio_fade):
            return item.fade_id in [fade.fade_id for fade in self.audio_fades]
        elif isinstance(item, Audio_effect):
            return item.effect_id in [effect.effect_id for effect in self.audio_effects]
        elif isinstance(item, Segment_animations):
            return item.animation_id in [ani.animation_id for ani in self.animations]
        elif isinstance(item, Video_effect):
            return item.global_id in [effect.global_id for effect in self.video_effects]
        elif isinstance(item, Transition):
            return item.global_id in [transition.global_id for transition in self.transitions]
        elif isinstance(item, Filter):
            return item.global_id in [filter_.global_id for filter_ in self.filters]
        else:
            raise TypeError("Invalid argument type '%s'" % type(item))

    def export_json(self) -> Dict[str, List[Any]]:
        return {
            "ai_translates": [],
            "audio_balances": [],
            "audio_effects": [effect.export_json() for effect in self.audio_effects],
            "audio_fades": [fade.export_json() for fade in self.audio_fades],
            "audio_track_indexes": [],
            "audios": [audio.export_json() for audio in self.audios],
            "beats": [],
            "canvases": [canvas.export_json() for canvas in self.canvases],
            "chromas": [],
            "color_curves": [],
            "digital_humans": [],
            "drafts": [],
            "effects": [_filter.export_json() for _filter in self.filters],
            "flowers": [],
            "green_screens": [],
            "handwrites": [],
            "hsl": [],
            "images": [],
            "log_color_wheels": [],
            "loudnesses": [],
            "manual_deformations": [],
            "masks": self.masks,
            "material_animations": [ani.export_json() for ani in self.animations],
            "material_colors": [],
            "multi_language_refs": [],
            "placeholders": [],
            "plugin_effects": [],
            "primary_color_wheels": [],
            "realtime_denoises": [],
            "shapes": [],
            "smart_crops": [],
            "smart_relights": [],
            "sound_channel_mappings": [],
            "speeds": [spd.export_json() for spd in self.speeds],
            "stickers": self.stickers,
            "tail_leaders": [],
            "text_templates": [],
            "texts": self.texts,
            "time_marks": [],
            "transitions": [transition.export_json() for transition in self.transitions],
            "video_effects": [effect.export_json() for effect in self.video_effects],
            "video_trackings": [],
            "videos": [video.export_json() for video in self.videos],
            "vocal_beautifys": [],
            "vocal_separations": []
        }

class Script_file:
    """剪映草稿文件, 大部分接口定义在此"""

    content: Dict[str, Any]
    """草稿文件内容"""

    width: int
    """视频的宽度, 单位为像素"""
    height: int
    """视频的高度, 单位为像素"""
    fps: int
    """视频的帧率"""
    duration: int
    """视频的总时长, 单位为微秒"""

    materials: Script_material
    """草稿文件中的素材信息部分"""
    tracks: Dict[str, Track]
    """轨道信息"""

    def __init__(self, width: int, height: int, fps: int = 30):
        """创建一个剪映草稿

        Args:
            width (int): 视频宽度, 单位为像素
            height (int): 视频高度, 单位为像素
            fps (int, optional): 视频帧率. 默认为30.
        """
        self.width = width
        self.height = height
        self.fps = fps
        self.duration = 0

        self.materials = Script_material()
        self.tracks = {}

        # 创建基础的草稿内容结构
        self.content = {
            "materials": {},
            "tracks": [],
            "fps": fps,
            "duration": 0,
            "canvas_config": {"width": width, "height": height, "ratio": "original"}
        }

    def add_material(self, material: Union[Video_material, Audio_material]) -> "Script_file":
        """向草稿文件中添加一个素材"""
        if material in self.materials:  # 素材已存在
            return self
        if isinstance(material, Video_material):
            self.materials.videos.append(material)
        elif isinstance(material, Audio_material):
            self.materials.audios.append(material)
        else:
            raise TypeError("错误的素材类型: '%s'" % type(material))
        return self

    def add_track(self, track_type: Track_type, track_name: Optional[str] = None, *,
                  mute: bool = False,
                  relative_index: int = 0, absolute_index: Optional[int] = None) -> "Script_file":
        """向草稿文件中添加一个指定类型、指定名称的轨道, 可以自定义轨道层级

        注意: 主视频轨道(最底层的视频轨道)上的视频片段必须从0s开始, 否则会被剪映强制对齐至0s.

        为避免混淆, 仅在创建第一个同类型轨道时允许不指定名称

        Args:
            track_type (Track_type): 轨道类型
            track_name (str, optional): 轨道名称. 仅在创建第一个同类型轨道时允许不指定.
            mute (bool, optional): 轨道是否静音. 默认不静音.
            relative_index (int, optional): 相对(同类型轨道的)图层位置, 越高越接近前景. 默认为0.
            absolute_index (int, optional): 绝对图层位置, 越高越接近前景. 此参数将直接覆盖相应片段的`render_index`属性, 供有经验的用户使用.
                此参数不能与`relative_index`同时使用.

        Raises:
            `NameError`: 已存在同类型轨道且未指定名称, 或已存在同名轨道
        """

        if track_name is None:
            if track_type in [track.track_type for track in self.tracks.values()]:
                raise NameError("'%s' 类型的轨道已存在, 请为新轨道指定名称以避免混淆" % track_type)
            track_name = track_type.name
        if track_name in [track.name for track in self.tracks.values()]:
            raise NameError("名为 '%s' 的轨道已存在" % track_name)

        render_index = track_type.value.render_index + relative_index
        if absolute_index is not None:
            render_index = absolute_index

        self.tracks[track_name] = Track(track_type, track_name, render_index, mute)
        return self

    def _get_track(self, segment_type: Type[Base_segment], track_name: Optional[str]) -> Track:
        # 指定轨道名称
        if track_name is not None:
            if track_name not in self.tracks:
                raise NameError("不存在名为 '%s' 的轨道" % track_name)
            return self.tracks[track_name]
        # 寻找唯一的同类型的轨道
        count = sum([1 for track in self.tracks.values() if track.accept_segment_type == segment_type])
        if count == 0: raise NameError("不存在接受 '%s' 的轨道" % segment_type)
        if count > 1: raise NameError("存在多个接受 '%s' 的轨道, 请指定轨道名称" % segment_type)

        return next(track for track in self.tracks.values() if track.accept_segment_type == segment_type)

    @staticmethod
    def from_dict(data: Dict[str, Any], draft_root: str) -> "Script_file":
        """从字典(通常是json加载而来)重建Script_file对象"""
        
        canvas_config = data.get("canvas_config", {})
        instance = Script_file(
            width=canvas_config.get("width", 1920),
            height=canvas_config.get("height", 1080),
            fps=data.get("fps", 30)
        )
        instance.duration = data.get("duration", 0)

        materials_data = data.get("materials", {})
        animation_map = {m.get("id"): m for m in materials_data.get("material_animations", [])}
        filter_map = {m.get("id"): m for m in materials_data.get("effects", []) if m.get("type") == "filter"}
        
        for track_data in data.get("tracks", []):
            track_type = Track_type[track_data.get("type").upper()]
            track_name = track_data.get("extra_info", {}).get("name")
            if track_name not in instance.tracks:
                instance.add_track(
                    track_type=track_type, 
                    track_name=track_name, 
                    absolute_index=track_data.get("render_index")
                )

            for segment_data in track_data.get("segments", []):
                material_id = segment_data.get("material_id")
                
                if segment_data.get("type") == "video":
                    video_info = next((v for v in materials_data.get("videos", []) if v.get("id") == material_id), None)
                    if not video_info: continue
                    
                    material_path = video_info.get("path")
                    if not os.path.isabs(material_path):
                        material_path = os.path.join(draft_root, material_path.lstrip('./'))
                    
                    if os.path.exists(material_path):
                        video_mat = Video_material(path=material_path)
                        video_mat.material_id = material_id
                        
                        target_timerange = Timerange(
                            start=segment_data.get("target_timerange", {}).get("start", 0),
                            duration=segment_data.get("target_timerange", {}).get("duration", 0)
                        )
                        segment = Video_segment(video_mat, target_timerange)

                        anim_id = segment_data.get("material_animation")
                        if anim_id and anim_id in animation_map:
                            anim_data = animation_map[anim_id]
                            if anim_data.get("intro_name"):
                                segment.add_animation(Intro_type[anim_data.get("intro_name")], anim_data.get("intro_duration"))
                            if anim_data.get("outro_name"):
                                segment.add_animation(Outro_type[anim_data.get("outro_name")], anim_data.get("outro_duration"))
                            if anim_data.get("overall_name"):
                                segment.add_animation(Group_animation_type[anim_data.get("overall_name")])

                        filter_ids = segment_data.get("extra_material_refs", [])
                        for f_id in filter_ids:
                            if f_id in filter_map:
                                filter_data = filter_map[f_id]
                                try:
                                    segment.add_filter(Filter_type[filter_data.get("name")], filter_data.get("value", 1.0) * 100)
                                except KeyError: pass
                        
                        instance.add_segment(segment, track_name=track_name)

        return instance

    def add_segment(self, segment: Union[Video_segment, Sticker_segment, Audio_segment, Text_segment],
                    track_name: Optional[str] = None) -> "Script_file":
        """向指定轨道中添加一个片段

        Args:
            segment (`Video_segment`, `Sticker_segment`, `Audio_segment`, or `Text_segment`): 要添加的片段
            track_name (`str`, optional): 添加到的轨道名称. 当此类型的轨道仅有一条时可省略.

        Raises:
            `NameError`: 未找到指定名称的轨道, 或必须提供`track_name`参数时未提供
            `TypeError`: 片段类型不匹配轨道类型
            `SegmentOverlap`: 新片段与已有片段重叠
        """
        target = self._get_track(type(segment), track_name)

        # 加入轨道并更新时长
        target.add_segment(segment)
        self.duration = max(self.duration, segment.end)

        # 自动添加相关素材
        if isinstance(segment, Video_segment):
            # 出入场等动画
            if (segment.animations_instance is not None) and (segment.animations_instance not in self.materials):
                self.materials.animations.append(segment.animations_instance)
            # 特效
            for effect in segment.effects:
                if effect not in self.materials:
                    self.materials.video_effects.append(effect)
            # 滤镜
            for filter_ in segment.filters:
                if filter_ not in self.materials:
                    self.materials.filters.append(filter_)
            # 蒙版
            if segment.mask is not None:
                self.materials.masks.append(segment.mask.export_json())
            # 转场
            if (segment.transition is not None) and (segment.transition not in self.materials):
                self.materials.transitions.append(segment.transition)
            # 背景填充
            if segment.background_filling is not None:
                self.materials.canvases.append(segment.background_filling)

            self.materials.speeds.append(segment.speed)
        elif isinstance(segment, Sticker_segment):
            self.materials.stickers.append(segment.export_material())
        elif isinstance(segment, Audio_segment):
            # 淡入淡出
            if (segment.fade is not None) and (segment.fade not in self.materials):
                self.materials.audio_fades.append(segment.fade)
            # 特效
            for effect in segment.effects:
                if effect not in self.materials:
                    self.materials.audio_effects.append(effect)
            self.materials.speeds.append(segment.speed)
        elif isinstance(segment, Text_segment):
            # 出入场等动画
            if (segment.animations_instance is not None) and (segment.animations_instance not in self.materials):
                self.materials.animations.append(segment.animations_instance)
            # 气泡效果
            if segment.bubble is not None:
                self.materials.filters.append(segment.bubble)
            # 花字效果
            if segment.effect is not None:
                self.materials.filters.append(segment.effect)
            # 字体样式
            # self.materials.texts.append(segment.export_material()) # 此行代码已被移除

        # 添加片段素材
        if isinstance(segment, (Video_segment, Audio_segment)):
            self.add_material(segment.material_instance)

        return self

    def add_effect(self, effect: Union[Video_scene_effect_type, Video_character_effect_type],
                   t_range: Timerange, track_name: Optional[str] = None, *,
                   params: Optional[List[Optional[float]]] = None) -> "Script_file":
        """向指定的特效轨道中添加一个特效片段

        Args:
            effect (`Video_scene_effect_type` or `Video_character_effect_type`): 特效类型
            t_range (`Timerange`): 特效片段的时间范围
            track_name (`str`, optional): 添加到的轨道名称. 当特效轨道仅有一条时可省略.
            params (`List[Optional[float]]`, optional): 特效参数列表, 参数列表中未提供或为None的项使用默认值.
                参数取值范围(0~100)与剪映中一致. 某个特效类型有何参数以及具体参数顺序以枚举类成员的annotation为准.

        Raises:
            `NameError`: 未找到指定名称的轨道, 或必须提供`track_name`参数时未提供
            `TypeError`: 指定的轨道不是特效轨道
            `ValueError`: 新片段与已有片段重叠、提供的参数数量超过了该特效类型的参数数量, 或参数值超出范围.
        """
        target = self._get_track(Effect_segment, track_name)

        # 加入轨道并更新时长
        segment = Effect_segment(effect, t_range, params)
        target.add_segment(segment)
        self.duration = max(self.duration, t_range.start + t_range.duration)

        # 自动添加相关素材
        if segment.effect_inst not in self.materials:
            self.materials.video_effects.append(segment.effect_inst)
        return self

    def add_filter(self, filter_meta: Filter_type, t_range: Timerange,
                   track_name: Optional[str] = None, intensity: float = 100.0) -> "Script_file":
        """向指定的滤镜轨道中添加一个滤镜片段

        Args:
            filter_meta (`Filter_type`): 滤镜类型
            t_range (`Timerange`): 滤镜片段的时间范围
            track_name (`str`, optional): 添加到的轨道名称. 当滤镜轨道仅有一条时可省略.
            intensity (`float`, optional): 滤镜强度(0-100). 仅当所选滤镜能够调节强度时有效. 默认为100.

        Raises:
            `NameError`: 未找到指定名称的轨道, 或必须提供`track_name`参数时未提供
            `TypeError`: 指定的轨道不是滤镜轨道
            `ValueError`: 新片段与已有片段重叠
        """
        target = self._get_track(Filter_segment, track_name)

        # 加入轨道并更新时长
        segment = Filter_segment(filter_meta, t_range, intensity / 100.0)  # 转换为0-1范围
        target.add_segment(segment)
        self.duration = max(self.duration, t_range.end)

        # 自动添加相关素材
        self.materials.filters.append(segment.material)
        return self

    def import_srt(self, srt_path: str, track_name: str, *,
                   time_offset: Union[str, float] = 0.0,
                   style_reference: Optional[Text_segment] = None,
                   text_style: Text_style = Text_style(size=5, align=1),
                   clip_settings: Optional[Clip_settings] = Clip_settings(transform_y=-0.8)) -> "Script_file":
        """从SRT文件中导入字幕, 支持传入一个`Text_segment`作为样式参考

        注意: 默认不会使用参考片段的`clip_settings`属性, 若需要请显式为此函数传入`clip_settings=None`

        Args:
            srt_path (`str`): SRT文件路径
            track_name (`str`): 导入到的文本轨道名称, 若不存在则自动创建
            style_reference (`Text_segment`, optional): 作为样式参考的文本片段, 若提供则使用其样式.
            time_offset (`Union[str, float]`, optional): 字幕整体时间偏移, 单位为微秒, 默认为0.
            text_style (`Text_style`, optional): 字幕样式, 默认模仿剪映导入字幕时的样式, 会被`style_reference`覆盖.
            clip_settings (`Clip_settings`, optional): 图像调节设置, 默认模仿剪映导入字幕时的设置, 会覆盖`style_reference`的设置除非指定为`None`.

        Raises:
            `NameError`: 已存在同名轨道
            `TypeError`: 轨道类型不匹配
        """
        if style_reference is None and clip_settings is None:
            raise ValueError("未提供样式参考时请提供`clip_settings`参数")

        time_offset = tim(time_offset)
        if track_name not in self.tracks:
            self.add_track(Track_type.text, track_name, relative_index=999)  # 在所有文本轨道的最上层

        with open(srt_path, "r", encoding="utf-8-sig") as srt_file:
            lines = srt_file.readlines()

        def __add_text_segment(text: str, t_range: Timerange) -> None:
            if style_reference:
                seg = Text_segment.create_from_template(text, t_range, style_reference)
                if clip_settings is not None:
                    seg.clip_settings = deepcopy(clip_settings)
            else:
                seg = Text_segment(text, t_range, style=text_style, clip_settings=clip_settings)
            self.add_segment(seg, track_name)

        index = 0
        text: str = ""
        text_trange: Timerange
        read_state: Literal["index", "timestamp", "content"] = "index"
        while index < len(lines):
            line = lines[index].strip()
            if read_state == "index":
                if len(line) == 0:
                    index += 1
                    continue
                if not line.isdigit():
                    raise ValueError("Expected a number at line %d, got '%s'" % (index+1, line))
                index += 1
                read_state = "timestamp"
            elif read_state == "timestamp":
                # 读取时间戳
                start_str, end_str = line.split(" --> ")
                start, end = srt_tstamp(start_str), srt_tstamp(end_str)
                text_trange = Timerange(start + time_offset, end - start)

                index += 1
                read_state = "content"
            elif read_state == "content":
                # 内容结束, 生成片段
                if len(line) == 0:
                    __add_text_segment(text.strip(), text_trange)

                    text = ""
                    read_state = "index"
                else:
                    text += line + "\n"
                index += 1

        # 添加最后一个片段
        if len(text) > 0:
            __add_text_segment(text.strip(), text_trange)

        return self

    def dumps(self) -> str:
        """将草稿文件内容导出为JSON字符串"""
        self.content["fps"] = self.fps
        self.content["duration"] = self.duration
        self.content["canvas_config"] = {"width": self.width, "height": self.height, "ratio": "original"}

        # [修复] 在导出前, 重建所有文本素材以确保其为最新状态
        self.materials.texts = []
        for track in self.tracks.values():
            if track.accept_segment_type == Text_segment:
                for segment in track.segments:
                    if isinstance(segment, Text_segment):
                        self.materials.texts.append(segment.export_material())
        
        self.content["materials"] = self.materials.export_json()

        # 对轨道排序并导出
        track_list: List[Base_track] = list(self.tracks.values())
        track_list.sort(key=lambda track: track.render_index)
        self.content["tracks"] = [track.export_json() for track in track_list]

        return json.dumps(self.content, ensure_ascii=False, indent=4)

    def dump(self, file_path: str) -> None:
        """将草稿文件内容写入文件"""
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(self.dumps())
