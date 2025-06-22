import asyncio
import logging
from typing import Optional
from enum import Enum
from app.utils.performance_monitor import performance_monitor, PerformanceMetrics
from app.config import settings

logger = logging.getLogger(__name__)

class SystemLoadLevel(Enum):
    LOW = "low"           # 系统负载低，可以增加并发
    NORMAL = "normal"     # 正常负载
    HIGH = "high"         # 高负载，需要限制并发
    CRITICAL = "critical" # 临界状态，拒绝新请求

class AdaptiveQueueManager:
    def __init__(self):
        # 从配置文件读取阈值参数
        self.thresholds = {
            "cpu_high": settings.CPU_HIGH_THRESHOLD,
            "cpu_critical": settings.CPU_CRITICAL_THRESHOLD,
            "memory_high": settings.MEMORY_HIGH_THRESHOLD,
            "memory_critical": settings.MEMORY_CRITICAL_THRESHOLD,
            "memory_min_mb": settings.MEMORY_MIN_RESERVE_MB,
            "response_time_high": settings.RESPONSE_TIME_HIGH_THRESHOLD,
            "response_time_critical": settings.RESPONSE_TIME_CRITICAL_THRESHOLD
        }
        
        # 从配置文件读取并发控制参数
        self.max_concurrent_tasks = settings.QUEUE_INITIAL_CONCURRENT_TASKS
        self.min_concurrent_tasks = settings.QUEUE_MIN_CONCURRENT_TASKS
        self.max_concurrent_limit = settings.QUEUE_MAX_CONCURRENT_TASKS
        
        # 从配置文件读取负载评估参数
        self.low_load_thresholds = {
            "cpu": settings.LOW_LOAD_CPU_THRESHOLD,
            "memory": settings.LOW_LOAD_MEMORY_THRESHOLD,
            "response_time": settings.LOW_LOAD_RESPONSE_TIME_THRESHOLD,
            "slot_reserve": settings.SLOT_RESERVE_COUNT
        }
        
        # 信号量控制并发 - 使用初始值
        self.semaphore = asyncio.Semaphore(self.max_concurrent_tasks)
        
        # 内部活跃任务计数器 - 服务启动时重置为0
        self._active_tasks_count = 0
        
        # 确保performance_monitor状态重置
        performance_monitor.reset_active_tasks()
        
        # 启动时输出配置信息
        logger.info(f"🔧 队列管理器初始化 | 初始并发: {self.max_concurrent_tasks} | 范围: {self.min_concurrent_tasks}-{self.max_concurrent_limit}")
        logger.info(f"📊 负载阈值 | CPU: {self.thresholds['cpu_high']}%/{self.thresholds['cpu_critical']}% | 内存: {self.thresholds['memory_high']}%/{self.thresholds['memory_critical']}%")
        logger.info(f"🔄 槽位状态重置 | 可用槽位: {self.semaphore._value} | 活跃任务: {self._active_tasks_count}")
        
    def assess_system_load(self) -> SystemLoadLevel:
        """评估系统负载水平"""
        try:
            metrics = performance_monitor.get_current_metrics()
            
            # 检查临界状态
            if (metrics.cpu_percent > self.thresholds["cpu_critical"] or 
                metrics.memory_percent > self.thresholds["memory_critical"] or
                metrics.memory_available_mb < self.thresholds["memory_min_mb"] or
                metrics.avg_response_time > self.thresholds["response_time_critical"]):
                return SystemLoadLevel.CRITICAL
            
            # 检查高负载状态  
            if (metrics.cpu_percent > self.thresholds["cpu_high"] or
                metrics.memory_percent > self.thresholds["memory_high"] or
                metrics.avg_response_time > self.thresholds["response_time_high"]):
                return SystemLoadLevel.HIGH
            
            # 检查低负载状态(可以增加并发) - 使用内部计数器
            if (metrics.cpu_percent < self.low_load_thresholds["cpu"] and 
                metrics.memory_percent < self.low_load_thresholds["memory"] and
                metrics.avg_response_time < self.low_load_thresholds["response_time"] and
                self._active_tasks_count < self.max_concurrent_tasks - self.low_load_thresholds["slot_reserve"]):
                return SystemLoadLevel.LOW
            
            return SystemLoadLevel.NORMAL
            
        except Exception as e:
            logger.error(f"评估系统负载失败: {e}")
            return SystemLoadLevel.NORMAL
    
    def adjust_concurrency(self):
        """根据负载动态调整并发数 - 不重新创建信号量"""
        load_level = self.assess_system_load()
        old_limit = self.max_concurrent_tasks
        
        if load_level == SystemLoadLevel.CRITICAL:
            # 临界状态：大幅减少并发上限，但不影响已获取的槽位
            self.max_concurrent_tasks = max(1, self.max_concurrent_tasks - 2)
            logger.warning(f"系统负载临界！并发上限调整: {old_limit} -> {self.max_concurrent_tasks}")
            
        elif load_level == SystemLoadLevel.HIGH:
            # 高负载：减少并发上限
            self.max_concurrent_tasks = max(self.min_concurrent_tasks, 
                                          self.max_concurrent_tasks - 1)
            logger.info(f"系统高负载，减少并发上限: {old_limit} -> {self.max_concurrent_tasks}")
            
        elif load_level == SystemLoadLevel.LOW:
            # 低负载：可以增加并发上限
            if self.max_concurrent_tasks < self.max_concurrent_limit:
                self.max_concurrent_tasks = min(self.max_concurrent_limit,
                                              self.max_concurrent_tasks + 1)
                logger.info(f"系统负载较低，增加并发上限: {old_limit} -> {self.max_concurrent_tasks}")
        
        # 注意：不再重新创建信号量，保持已获取的槽位不受影响
    
    async def can_accept_new_task(self) -> bool:
        """检查是否可以接受新任务"""
        load_level = self.assess_system_load()
        
        # 只有在极端临界状态才拒绝新任务(CPU>95% 或 内存<50MB)
        metrics = performance_monitor.get_current_metrics()
        if (metrics.cpu_percent > 95.0 or 
            metrics.memory_available_mb < 50):
            logger.warning("系统负载极限，暂时拒绝新任务")
            return False
            
        return True
    
    async def acquire_task_slot(self, max_wait_time: float = 30.0) -> bool:
        """获取任务执行槽位，支持排队等待"""
        # 首先调整并发数
        self.adjust_concurrency()
        
        # 检查是否可以接受新任务
        if not await self.can_accept_new_task():
            return False
        
        # 尝试获取信号量，支持等待
        try:
            # 根据负载水平调整等待时间
            load_level = self.assess_system_load()
            if load_level == SystemLoadLevel.CRITICAL:
                wait_time = min(max_wait_time, 10.0)  # 临界状态最多等10秒
                logger.info(f"系统负载临界，任务将等待 {wait_time}s")
            elif load_level == SystemLoadLevel.HIGH:
                wait_time = min(max_wait_time, 20.0)  # 高负载最多等20秒
                logger.info(f"系统高负载，任务将等待 {wait_time}s")
            else:
                wait_time = max_wait_time  # 正常情况等待完整时间
                
            await asyncio.wait_for(self.semaphore.acquire(), timeout=wait_time)
            
            # 同时更新两个计数器
            self._active_tasks_count += 1
            performance_monitor.increment_active_tasks()
            
            logger.info(f"✅ 任务获取到执行槽位 | 活跃任务: {self._active_tasks_count}/{self.max_concurrent_tasks}")
            return True
            
        except asyncio.TimeoutError:
            logger.warning(f"任务等待超时 ({wait_time}s)，请稍后重试")
            return False
    
    def release_task_slot(self):
        """释放任务执行槽位"""
        try:
            self.semaphore.release()
            
            # 同时更新两个计数器
            self._active_tasks_count = max(0, self._active_tasks_count - 1)
            performance_monitor.decrement_active_tasks()
            
            logger.info(f"✅ 任务完成，释放执行槽位 | 活跃任务: {self._active_tasks_count}/{self.max_concurrent_tasks}")
        except Exception as e:
            logger.error(f"❌ 释放任务槽位失败: {e}")
    
    def get_status(self) -> dict:
        """获取队列管理器状态"""
        metrics = performance_monitor.get_current_metrics()
        load_level = self.assess_system_load()
        
        # 计算预估等待时间
        if self.semaphore._value == 0:  # 所有槽位被占用
            if load_level == SystemLoadLevel.CRITICAL:
                estimated_wait = "10-15秒"
            elif load_level == SystemLoadLevel.HIGH:
                estimated_wait = "5-10秒"
            else:
                estimated_wait = "1-5秒"
        else:
            estimated_wait = "无需等待"
        
        return {
            "load_level": load_level.value,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "available_slots": self.semaphore._value,
            "active_tasks": self._active_tasks_count,  # 使用内部计数器
            "cpu_percent": metrics.cpu_percent,
            "memory_percent": metrics.memory_percent,
            "memory_available_mb": metrics.memory_available_mb,
            "avg_response_time": metrics.avg_response_time,
            "queue_info": {
                "estimated_wait_time": estimated_wait,
                "is_queue_full": self.semaphore._value == 0,
                "accepting_new_requests": self.semaphore._value > 0 or metrics.memory_available_mb > 50
            }
        }

# 全局队列管理器实例
adaptive_queue_manager = AdaptiveQueueManager() 