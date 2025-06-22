# 聚合v1所有路由
from fastapi import APIRouter

from . import sessions
from . import materials
from . import system

api_router = APIRouter()

# 在这里包含所有v1版本的路由
api_router.include_router(sessions.router, prefix="/sessions", tags=["Session Management"])
api_router.include_router(materials.router, prefix="/sessions", tags=["Material Management"])
api_router.include_router(system.router, prefix="/system", tags=["System"]) 