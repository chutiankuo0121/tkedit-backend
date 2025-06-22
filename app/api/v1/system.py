# 系统状态API (健康检查)
from fastapi import APIRouter
from pydantic import BaseModel, Field

# ============================= Router ============================= #
router = APIRouter()

# ============================= Pydantic Models ============================= #

class HealthResponse(BaseModel):
    status: str = Field("ok", description="服务状态")
    version: str = Field("1.0.0", description="应用版本号")

# ============================= API Endpoints ============================= #

@router.get(
    "/health",
    response_model=HealthResponse,
    summary="健康检查"
)
async def health_check():
    """
    一个简单的健康检查端点。

    在更复杂的应用中，这里可以添加对数据库、缓存、R2连接等的检查。
    """
    return HealthResponse() 