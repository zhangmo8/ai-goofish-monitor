"""
许可证状态路由
"""
from fastapi import APIRouter, Depends

from src.api.dependencies import get_license_service
from src.services.license_service import LicenseService


router = APIRouter(prefix="/api/license", tags=["license"])


@router.get("/status")
async def get_remote_license_status(
    license_service: LicenseService = Depends(get_license_service),
):
    """返回当前远程许可证状态。"""
    status = await license_service.get_status(force_refresh=True)
    return {"license": status.to_dict()}
