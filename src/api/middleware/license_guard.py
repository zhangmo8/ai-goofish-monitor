"""
许可证拦截中间件
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.services.license_service import LicenseService


EXEMPT_API_PATHS = frozenset({
    "/api/license/status",
})


def _should_check_license(path: str) -> bool:
    return path.startswith("/api/") and path not in EXEMPT_API_PATHS


def add_license_guard_middleware(app: FastAPI, license_service: LicenseService) -> None:
    @app.middleware("http")
    async def _license_guard(request: Request, call_next):
        if not _should_check_license(request.url.path):
            return await call_next(request)

        status = await license_service.get_status()
        request.state.license_status = status
        if not status.allowed:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": status.block_message,
                    "license": status.to_dict(),
                },
            )

        return await call_next(request)
