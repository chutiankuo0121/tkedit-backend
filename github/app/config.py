from pydantic_settings import BaseSettings
from pydantic import Field, model_validator
from typing import Optional

class Settings(BaseSettings):
    """
    应用程序的全局配置。
    使用 Pydantic 的 BaseSettings，可以自动从环境变量或 .env 文件中读取配置。
    """
    PROJECT_NAME: str = "TKedit Backend"
    API_V1_STR: str = "/api/v1"

    # 🔧 数据库配置 - 使用统一的路径管理器
    DATABASE_URL: str = Field(default="", description="数据库连接URL")
    ASYNC_DATABASE_URL: Optional[str] = None
    DB_ECHO: bool = Field(False, description="是否打印SQLAlchemy日志")
    
    @model_validator(mode='after')
    def build_database_config(self) -> 'Settings':
        """构建数据库配置，使用path_manager统一管理路径"""
        
        # 如果已经明确设置了DATABASE_URL，直接使用
        if self.DATABASE_URL:
            if 'sqlite' in self.DATABASE_URL and 'aiosqlite' not in self.DATABASE_URL:
                self.ASYNC_DATABASE_URL = self.DATABASE_URL.replace('sqlite://', 'sqlite+aiosqlite://')
            else:
                self.ASYNC_DATABASE_URL = self.DATABASE_URL
        else:
            # 延迟导入，避免循环依赖
            from app.utils.path_manager import path_manager
            
            # 使用统一的路径管理器获取数据库路径
            db_file_path = path_manager.normalize_path("data/app.db")
            self.DATABASE_URL = f"sqlite+aiosqlite:///{db_file_path}"
            self.ASYNC_DATABASE_URL = self.DATABASE_URL
        
        if not self.ASYNC_DATABASE_URL:
            raise ValueError("ASYNC_DATABASE_URL must be set.")
             
        return self

    # 🗂️ 数据目录配置 - 使用path_manager统一管理
    DATA_DIR: str = Field(default="", description="用于存储会话草稿、素材等数据的目录")
    
    @model_validator(mode='after') 
    def build_data_dir(self) -> 'Settings':
        """构建数据目录路径，使用path_manager统一管理"""
        if not self.DATA_DIR:
            # 延迟导入，避免循环依赖
            from app.utils.path_manager import path_manager
            self.DATA_DIR = path_manager.output_dir
        return self

    # Cloudflare R2 配置
    # 您需要将这些值设置为您的R2存储桶的真实凭证
    R2_ENDPOINT_URL: str
    R2_ACCESS_KEY_ID: str
    R2_SECRET_ACCESS_KEY: str
    R2_BUCKET_NAME: str
    R2_REGION: str = "auto"
    R2_PUBLIC_URL: str

    # Server Configuration
    SERVER_HOST: str = "0.0.0.0"  # HuggingFace Spaces需要绑定所有接口
    SERVER_PORT: int = 8000  # 本地测试端口
    
    # 日志和监控配置 - 高并发优化
    SYSTEM_MONITOR_INTERVAL: int = Field(60, description="系统资源监控间隔(秒) - 减少监控开销")
    LOG_LEVEL: str = Field("WARNING", description="日志级别 - 高并发减少日志输出")
    ENABLE_COLORED_LOGS: bool = Field(False, description="是否启用彩色日志 - 高并发关闭减少开销")
    ENABLE_EMOJI_LOGS: bool = Field(False, description="是否启用表情符号")
    SLOW_REQUEST_THRESHOLD: float = Field(5.0, description="慢请求阈值(秒) - 适应I/O延迟")
    MEMORY_CHANGE_THRESHOLD: float = Field(200.0, description="内存变化监控阈值(MB) - 高并发减少敏感度")
    
    # 🚀 智能队列管理配置 - 高并发优化
    # 并发控制参数 - 针对I/O密集型应用优化 (2核16GB HuggingFace Spaces)
    QUEUE_INITIAL_CONCURRENT_TASKS: int = Field(150, description="初始并发任务数 - 大幅提升支持高并发I/O")
    QUEUE_MIN_CONCURRENT_TASKS: int = Field(75, description="最小并发任务数 - 提升基础性能")  
    QUEUE_MAX_CONCURRENT_TASKS: int = Field(300, description="最大并发任务数 - I/O密集型可支持高并发")
    QUEUE_MAX_WAIT_TIME: float = Field(120.0, description="最大排队等待时间(秒) - 适应高并发场景")
    
    # 系统负载阈值配置 - 适配高并发I/O场景
    CPU_HIGH_THRESHOLD: float = Field(85.0, description="CPU高负载阈值(%) - 充分利用CPU资源")
    CPU_CRITICAL_THRESHOLD: float = Field(95.0, description="CPU临界阈值(%) - 提高临界线")
    MEMORY_HIGH_THRESHOLD: float = Field(80.0, description="内存高负载阈值(%) - 充分利用16GB内存")
    MEMORY_CRITICAL_THRESHOLD: float = Field(95.0, description="内存临界阈值(%) - 高并发内存管理")
    MEMORY_MIN_RESERVE_MB: float = Field(1024.0, description="最小内存预留(MB) - 为高并发预留更多内存")
    RESPONSE_TIME_HIGH_THRESHOLD: float = Field(5.0, description="响应时间高负载阈值(秒) - 适应I/O延迟")
    RESPONSE_TIME_CRITICAL_THRESHOLD: float = Field(15.0, description="响应时间临界阈值(秒) - 高并发容忍")
    
    # 负载评估参数 - 针对高并发环境优化
    LOW_LOAD_CPU_THRESHOLD: float = Field(50.0, description="低负载CPU阈值(%) - 适配高并发基线")
    LOW_LOAD_MEMORY_THRESHOLD: float = Field(60.0, description="低负载内存阈值(%) - 高并发内存基线")
    LOW_LOAD_RESPONSE_TIME_THRESHOLD: float = Field(2.0, description="低负载响应时间阈值(秒) - I/O操作基线")
    SLOT_RESERVE_COUNT: int = Field(50, description="槽位预留数量 - 为突发流量预留充足槽位(300并发中预留50)")
    
    class Config:
        # Pydantic-settings 会自动尝试从 .env 文件中加载环境变量
        # 文件路径相对于项目根目录
        env_file = ".env"
        env_file_encoding = 'utf-8'

# 创建一个全局可用的配置实例
settings = Settings() 