import asyncio
import logging
from typing import Optional
from app.utils.performance_monitor import performance_monitor
from app.utils.logger_config import system_logger
from app.config import settings

class SystemMonitor:
    """系统资源定时监控器"""
    
    def __init__(self, log_interval: int = 30):
        """
        初始化系统监控器
        
        Args:
            log_interval: 日志记录间隔(秒)，默认30秒
        """
        self.log_interval = log_interval
        self.is_running = False
        self.monitor_task: Optional[asyncio.Task] = None
        self.logger = logging.getLogger("system.monitor")
        
    async def start(self):
        """启动监控"""
        if self.is_running:
            self.logger.warning("⚠️ 系统监控已在运行中")
            return
            
        self.is_running = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        self.logger.info(f"🚀 系统监控启动 | 间隔: {self.log_interval}秒")
        
    async def stop(self):
        """停止监控"""
        if not self.is_running:
            return
            
        self.is_running = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
                
        self.logger.info("🛑 系统监控已停止")
        
    async def _monitor_loop(self):
        """监控循环"""
        self.logger.info(f"📊 开始资源监控 | 每 {self.log_interval} 秒记录一次")
        
        try:
            while self.is_running:
                # 获取系统指标
                metrics = performance_monitor.get_current_metrics()
                
                # 记录系统状态 - 使用简化版本避免格式化错误
                try:
                    # 简化的系统状态日志，不使用表情符号
                    cpu_val = float(metrics.cpu_percent) if metrics.cpu_percent else 0.0
                    mem_val = float(metrics.memory_percent) if metrics.memory_percent else 0.0
                    mem_avail = float(metrics.memory_available_mb) if metrics.memory_available_mb else 0.0
                    resp_time = float(metrics.avg_response_time) if metrics.avg_response_time else 0.0
                    tasks = int(metrics.active_tasks) if metrics.active_tasks else 0
                    
                    message = f"系统状态 CPU:{cpu_val:.1f}% 内存:{mem_val:.1f}% 可用:{mem_avail:.0f}MB 响应:{resp_time:.2f}s 任务:{tasks}"
                    self.logger.info(message)
                except Exception as log_error:
                    self.logger.info("系统状态记录异常")
                
                # 检查是否需要特别关注
                await self._check_alerts(metrics)
                
                # 等待下次检查
                await asyncio.sleep(self.log_interval)
                
        except asyncio.CancelledError:
            self.logger.info("📊 资源监控循环已取消")
            raise
        except Exception as e:
            self.logger.error(f"❌ 资源监控发生错误: {str(e)}")
            
    async def _check_alerts(self, metrics):
        """检查是否需要发送警报"""
        alerts = []
        
        # CPU警报
        if metrics.cpu_percent > 95:
            alerts.append(f"🔥 CPU使用率极高: {metrics.cpu_percent:.1f}%")
        elif metrics.cpu_percent > 85:
            alerts.append(f"😰 CPU使用率较高: {metrics.cpu_percent:.1f}%")
            
        # 内存警报
        if metrics.memory_available_mb < 50:
            alerts.append(f"💾 可用内存不足: {metrics.memory_available_mb:.0f}MB")
        elif metrics.memory_available_mb < 100:
            alerts.append(f"🧡 可用内存偏低: {metrics.memory_available_mb:.0f}MB")
            
        # 响应时间警报
        if metrics.avg_response_time > 10:
            alerts.append(f"🐌 响应时间过慢: {metrics.avg_response_time:.2f}s")
        elif metrics.avg_response_time > 5:
            alerts.append(f"⏱️ 响应时间偏慢: {metrics.avg_response_time:.2f}s")
            
        # 活跃任务警报
        if metrics.active_tasks > 8:
            alerts.append(f"🏃‍♂️ 活跃任务过多: {metrics.active_tasks}个")
            
        # 输出警报
        for alert in alerts:
            self.logger.warning(alert)
            
    def get_monitoring_stats(self) -> dict:
        """获取监控统计信息"""
        return {
            "is_running": self.is_running,
            "log_interval": self.log_interval,
            "start_time": getattr(self, 'start_time', None),
            "current_metrics": performance_monitor.get_current_metrics().__dict__ if self.is_running else None
        }

# 全局监控器实例 - 使用配置文件中的间隔
system_monitor = SystemMonitor(log_interval=settings.SYSTEM_MONITOR_INTERVAL)

class PerformanceLogger:
    """性能相关的日志记录器"""
    
    def __init__(self):
        self.logger = logging.getLogger("performance")
        
    def log_slow_operation(self, operation: str, duration: float, threshold: float = 2.0):
        """记录慢操作"""
        if duration > threshold:
            emoji = "🐌" if duration > 10 else "⏳"
            self.logger.warning(f"{emoji} 慢操作检测 | {operation} | 耗时: {duration:.2f}s")
            
    def log_memory_usage(self, operation: str, before_mb: float, after_mb: float):
        """记录内存使用变化"""
        diff = after_mb - before_mb
        if abs(diff) > 50:  # 变化超过50MB时记录
            emoji = "📈" if diff > 0 else "📉"
            self.logger.info(f"{emoji} 内存变化 | {operation} | {diff:+.1f}MB (当前: {after_mb:.1f}MB)")
            
    def log_queue_status(self, available_slots: int, active_tasks: int, load_level: str):
        """记录队列状态"""
        if available_slots == 0:
            self.logger.info(f"🔄 队列状态 | 槽位: 已满 | 活跃: {active_tasks} | 负载: {load_level}")
        elif load_level in ["high", "critical"]:
            self.logger.info(f"⚡ 队列状态 | 槽位: {available_slots} | 活跃: {active_tasks} | 负载: {load_level}")

# 全局性能日志器
perf_logger = PerformanceLogger() 