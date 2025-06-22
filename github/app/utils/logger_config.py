import logging
import sys
from datetime import datetime
from typing import Optional
import colorama
from colorama import Fore, Style

# 初始化colorama
colorama.init()

class ColoredFormatter(logging.Formatter):
    """带颜色和表情符号的日志格式化器"""
    
    # 日志级别对应的颜色和表情
    COLORS = {
        'DEBUG': Fore.CYAN,
        'INFO': Fore.GREEN, 
        'WARNING': Fore.YELLOW,
        'ERROR': Fore.RED,
        'CRITICAL': Fore.MAGENTA
    }
    
    EMOJIS = {
        'DEBUG': '🔍',
        'INFO': '✅', 
        'WARNING': '⚠️',
        'ERROR': '❌',
        'CRITICAL': '🚨'
    }
    
    def format(self, record):
        # 获取颜色和表情
        color = self.COLORS.get(record.levelname, '')
        emoji = self.EMOJIS.get(record.levelname, '')
        
        # 格式化时间
        timestamp = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
        
        # 构建日志消息
        message = super().format(record)
        
        # 应用颜色和格式
        formatted = f"{color}{emoji} {timestamp} [{record.levelname}] {message}{Style.RESET_ALL}"
        
        return formatted

class SystemResourceLogger:
    """系统资源监控日志器"""
    
    def __init__(self):
        self.logger = logging.getLogger("system.resources")
        
    def log_system_status(self, metrics):
        """记录系统状态"""
        try:
            # CPU状态表情
            cpu_emoji = self._get_cpu_emoji(metrics.cpu_percent)
            # 内存状态表情  
            memory_emoji = self._get_memory_emoji(metrics.memory_percent)
            # 负载状态表情
            load_emoji = self._get_load_emoji(metrics)
            
            # 格式化消息 - 安全处理所有值
            try:
                cpu_percent = float(metrics.cpu_percent) if metrics.cpu_percent is not None else 0.0
                memory_percent = float(metrics.memory_percent) if metrics.memory_percent is not None else 0.0
                memory_available_mb = float(metrics.memory_available_mb) if metrics.memory_available_mb is not None else 0.0
                avg_response_time = float(metrics.avg_response_time) if metrics.avg_response_time is not None else 0.0
                active_tasks = int(metrics.active_tasks) if metrics.active_tasks is not None else 0
                
                # 安全的字符串格式化
                message_parts = [
                    f"{load_emoji} 系统状态",
                    f"{cpu_emoji} CPU: {cpu_percent:.1f}%",
                    f"{memory_emoji} 内存: {memory_percent:.1f}% (可用: {memory_available_mb:.0f}MB)",
                    f"响应: {avg_response_time:.2f}s",
                    f"活跃任务: {active_tasks}"
                ]
                message = " | ".join(message_parts)
                
            except (ValueError, TypeError) as format_error:
                # 如果格式化失败，使用简化消息
                message = f"系统状态 | CPU: {getattr(metrics, 'cpu_percent', 'N/A')} | 内存: {getattr(metrics, 'memory_percent', 'N/A')}% | 错误: {str(format_error)}"
            
            # 根据整体状况选择日志级别
            if cpu_percent > 90 or memory_percent > 90:
                self.logger.warning(message)
            elif cpu_percent > 75 or memory_percent > 80:
                self.logger.info(message)
            else:
                self.logger.debug(message)
                
        except Exception as e:
            # 完全简化的无表情符号版本
            try:
                cpu_val = getattr(metrics, 'cpu_percent', 0)
                mem_val = getattr(metrics, 'memory_percent', 0) 
                mem_available = getattr(metrics, 'memory_available_mb', 0)
                tasks = getattr(metrics, 'active_tasks', 0)
                
                simple_message = f"系统状态 CPU:{cpu_val}% 内存:{mem_val}% 可用:{mem_available}MB 任务:{tasks}"
                self.logger.info(simple_message)
            except:
                self.logger.error("系统状态日志记录失败")
    
    def _get_cpu_emoji(self, cpu_percent: float) -> str:
        """根据CPU使用率返回表情"""
        try:
            cpu_val = float(cpu_percent) if cpu_percent is not None else 0.0
            if cpu_val < 30:
                return "😴"  # 很闲
            elif cpu_val < 50:
                return "😊"  # 正常
            elif cpu_val < 75:
                return "😐"  # 有点忙
            elif cpu_val < 90:
                return "😰"  # 忙碌
            else:
                return "🔥"  # 爆炸
        except (ValueError, TypeError):
            return "❓"  # 未知
    
    def _get_memory_emoji(self, memory_percent: float) -> str:
        """根据内存使用率返回表情"""
        try:
            mem_val = float(memory_percent) if memory_percent is not None else 0.0
            if mem_val < 50:
                return "💚"  # 充足
            elif mem_val < 70:
                return "💛"  # 正常
            elif mem_val < 85:
                return "🧡"  # 紧张
            else:
                return "❤️"  # 告急
        except (ValueError, TypeError):
            return "❓"  # 未知

    def _get_load_emoji(self, metrics) -> str:
        """根据整体负载返回表情"""
        try:
            cpu_val = float(getattr(metrics, 'cpu_percent', 0)) if hasattr(metrics, 'cpu_percent') else 0.0
            mem_val = float(getattr(metrics, 'memory_percent', 0)) if hasattr(metrics, 'memory_percent') else 0.0
            tasks_val = int(getattr(metrics, 'active_tasks', 0)) if hasattr(metrics, 'active_tasks') else 0
            
            if cpu_val > 90 or mem_val > 90:
                return "🚨"  # 紧急
            elif cpu_val > 75 or mem_val > 80:
                return "⚡"  # 繁忙
            elif tasks_val > 3:
                return "🏃"  # 忙碌
            else:
                return "🌟"  # 正常
        except (ValueError, TypeError, AttributeError):
            return "❓"  # 未知

class ApiLogger:
    """API请求日志器"""
    
    def __init__(self):
        self.logger = logging.getLogger("api.requests")
    
    def log_request_start(self, method: str, path: str, session_id: Optional[str] = None):
        """记录请求开始"""
        session_info = f"[会话:{session_id[:8]}]" if session_id else ""
        message = f"🚀 {method} {path} {session_info}"
        self.logger.debug(message)
    
    def log_request_success(self, method: str, path: str, duration: float, session_id: Optional[str] = None):
        """记录请求成功"""
        session_info = f"[会话:{session_id[:8]}]" if session_id else ""
        duration_emoji = "⚡" if duration < 1.0 else "🐌" if duration > 5.0 else "✅"
        message = f"{duration_emoji} {method} {path} {session_info} | 用时: {duration:.2f}s"
        
        if duration > 5.0:
            self.logger.warning(message)
        else:
            self.logger.info(message)
    
    def log_request_error(self, method: str, path: str, error: str, session_id: Optional[str] = None):
        """记录请求错误"""
        session_info = f"[会话:{session_id[:8]}]" if session_id else ""
        message = f"💥 {method} {path} {session_info} | 错误: {error}"
        self.logger.error(message)
    
    def log_queue_wait(self, path: str, wait_time: float, session_id: Optional[str] = None):
        """记录排队等待"""
        session_info = f"[会话:{session_id[:8]}]" if session_id else ""
        wait_emoji = "⏳" if wait_time < 10 else "⌛"
        message = f"{wait_emoji} {path} {session_info} | 排队等待: {wait_time:.1f}s"
        self.logger.info(message)

class SessionLogger:
    """会话操作日志器"""
    
    def __init__(self):
        self.logger = logging.getLogger("session.operations")
    
    def log_session_created(self, session_id: str, width: int, height: int):
        """记录会话创建"""
        message = f"🎬 会话创建成功 | ID: {session_id[:8]} | 画布: {width}x{height}"
        self.logger.info(message)
    
    def log_material_uploaded(self, session_id: str, material_type: str, file_size_mb: float):
        """记录素材上传"""
        type_emoji = "🎥" if material_type == "video" else "🎵" if material_type == "audio" else "🖼️"
        message = f"{type_emoji} 素材上传 | 会话: {session_id[:8]} | 类型: {material_type} | 大小: {file_size_mb:.1f}MB"
        self.logger.info(message)
    
    def log_track_added(self, session_id: str, track_type: str, track_name: str):
        """记录轨道添加"""
        type_emoji = "🎬" if track_type == "video" else "🎵" if track_type == "audio" else "📝"
        message = f"{type_emoji} 轨道添加 | 会话: {session_id[:8]} | 类型: {track_type} | 名称: {track_name}"
        self.logger.info(message)
    
    def log_segment_added(self, session_id: str, segment_type: str, duration: float):
        """记录片段添加"""
        type_emoji = "🎞️" if segment_type == "video" else "🎶" if segment_type == "audio" else "📄"
        message = f"{type_emoji} 片段添加 | 会话: {session_id[:8]} | 类型: {segment_type} | 时长: {duration:.1f}s"
        self.logger.info(message)
    
    def log_draft_saved(self, session_id: str, file_size_mb: float):
        """记录草稿保存"""
        message = f"💾 草稿保存完成 | 会话: {session_id[:8]} | 大小: {file_size_mb:.1f}MB"
        self.logger.info(message)
    
    def log_session_cleaned(self, session_id: str, reason: str):
        """记录会话清理"""
        message = f"🧹 会话清理 | ID: {session_id[:8]} | 原因: {reason}"
        self.logger.info(message)

def setup_logging():
    """设置日志系统"""
    # 设置根日志级别
    logging.basicConfig(level=logging.INFO)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(ColoredFormatter())
    
    # 设置各个日志器
    loggers = [
        "system.resources",
        "api.requests", 
        "session.operations",
        "app.services",
        "app.middleware"
    ]
    
    for logger_name in loggers:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.addHandler(console_handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    
    # 🔇 屏蔽第三方库的英文日志
    silence_loggers = [
        "apscheduler.scheduler",        # 定时任务调度器
        "apscheduler.executors.default", # 任务执行器
        "asyncio",                      # asyncio内部日志
        "uvicorn.access",              # uvicorn访问日志(如果不需要)
        "botocore",                    # AWS/R2底层日志
        "aioboto3"                     # aioboto3日志
    ]
    
    for logger_name in silence_loggers:
        silence_logger = logging.getLogger(logger_name)
        silence_logger.setLevel(logging.WARNING)  # 只显示警告及以上级别
        silence_logger.propagate = False

# 创建全局日志器实例
system_logger = SystemResourceLogger()
api_logger = ApiLogger()
session_logger = SessionLogger() 