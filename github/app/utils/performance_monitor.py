import psutil
from typing import List
from dataclasses import dataclass
from datetime import datetime

@dataclass
class PerformanceMetrics:
    cpu_percent: float
    memory_percent: float
    memory_available_mb: float
    disk_usage_percent: float
    active_tasks: int
    avg_response_time: float
    timestamp: datetime

class PerformanceMonitor:
    def __init__(self):
        self.response_times: List[float] = []
        self.max_response_time_samples = 50
        self.active_tasks = 0
        
    def get_current_metrics(self) -> PerformanceMetrics:
        """获取当前系统性能指标"""
        # 初始化默认�?
        cpu_percent = 0.0
        memory_percent = 0.0
        memory_available_mb = 0.0
        disk_usage_percent = 0.0
        
        try:
            # CPU使用�?- 不使用interval，避免阻�?
            cpu_percent = psutil.cpu_percent()
            
        except Exception as cpu_error:
            cpu_percent = 0.0
            
        try:
            # 内存使用情况
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_available_mb = memory.available / 1048576  # 直接除以1024*1024
            
        except Exception as mem_error:
            memory_percent = 0.0
            memory_available_mb = 0.0
            
        try:
            # 磁盘使用�?
            import os
            if os.name == 'nt':
                disk = psutil.disk_usage('C:')
            else:
                disk = psutil.disk_usage('/')
            disk_usage_percent = disk.percent
            
        except Exception as disk_error:
            disk_usage_percent = 0.0
        
        # 响应时间
        try:
            avg_response_time = sum(self.response_times) / len(self.response_times) if self.response_times else 0.0
        except:
            avg_response_time = 0.0
            
        # 活跃任务�?
        try:
            active_tasks = self.active_tasks
        except:
            active_tasks = 0
            
        return PerformanceMetrics(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            memory_available_mb=memory_available_mb,
            disk_usage_percent=disk_usage_percent,
            active_tasks=active_tasks,
            avg_response_time=avg_response_time,
            timestamp=datetime.now()
        )
    
    def record_response_time(self, response_time: float):
        """记录API响应时间"""
        self.response_times.append(response_time)
        # 保持固定数量的样�?
        if len(self.response_times) > self.max_response_time_samples:
            self.response_times.pop(0)
    
    def increment_active_tasks(self):
        """增加活跃任务计数"""
        self.active_tasks += 1
    
    def decrement_active_tasks(self):
        """减少活跃任务计数"""
        self.active_tasks = max(0, self.active_tasks - 1)
    
    def reset_active_tasks(self):
        """重置活跃任务计数 - 用于服务启动时清零"""
        self.active_tasks = 0

# 全局性能监控实例
performance_monitor = PerformanceMonitor() 
