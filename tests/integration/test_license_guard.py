from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.license_guard import add_license_guard_middleware
from src.services.license_service import LicenseStatus


class FakeLicenseService:
    def __init__(self, status: LicenseStatus):
        self.status = status

    async def get_status(self, *, force_refresh: bool = False) -> LicenseStatus:
        return self.status


def test_license_guard_blocks_api_routes_when_expired():
    expired_status = LicenseStatus(
        allowed=False,
        enabled=True,
        expired=True,
        reason="expired",
        source="remote",
        checked_at="2026-03-11T00:00:00+00:00",
        expires_at="2026-03-10T16:00:00+00:00",
        message="授权已过期",
        block_message="授权已过期，请续费",
    )
    app = FastAPI()
    add_license_guard_middleware(app, FakeLicenseService(expired_status))

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/tasks")
    async def get_tasks():
        return {"items": []}

    @app.get("/api/license/status")
    async def get_license_status():
        return {"license": "visible"}

    client = TestClient(app)

    health_response = client.get("/health")
    assert health_response.status_code == 200

    blocked_response = client.get("/api/tasks")
    assert blocked_response.status_code == 403
    assert blocked_response.json()["detail"] == "授权已过期，请续费"
    assert blocked_response.json()["license"]["expired"] is True

    status_response = client.get("/api/license/status")
    assert status_response.status_code == 200
    assert status_response.json() == {"license": "visible"}
