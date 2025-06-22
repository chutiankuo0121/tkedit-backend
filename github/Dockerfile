# Docker配置 - 针对2核16G高性能环境优化

# 使用官方Python 3.11镜像
FROM python:3.11-slim

# 设置容器内的工作目录
WORKDIR /app

# 设置Python环境变量，针对高性能环境优化
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOCKER_CONTAINER=true

# 安装系统依赖，包括媒体分析所需的ffmpeg和curl(用于健康检查)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 将依赖文件复制到容器中
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用文件
COPY ./app ./app
COPY ./draft_template ./draft_template
COPY ./run.py .

# 🔧 创建必要的目录并设置正确的权限
# 使用更宽松的权限以确保在不同主机系统上都能正常工作
RUN mkdir -p /app/data/logs /app/data/output /tmp && \
    chmod 777 /app/data && \
    chmod 777 /app/data/logs && \
    chmod 777 /app/data/output && \
    chmod 777 /tmp

# 🔧 为了兼容性，暂时使用root用户运行
# 在生产环境中可以切换到非root用户
# RUN groupadd -r appuser && useradd -r -g appuser appuser && \
#     chown -R appuser:appuser /app && \
#     chmod -R 755 /app/data
# USER appuser

# 设置端口环境变量
ENV PORT=7860

# 声明容器将监听的端口
EXPOSE 7860

# 健康检查
HEALTHCHECK --interval=60s --timeout=15s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:7860/api/v1/system/health || exit 1

# 🚀 针对2核16G高性能环境的启动优化
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "7860", \
     "--workers", "4", \
     "--access-log", \
     "--log-level", "warning", \
     "--loop", "uvloop"] 