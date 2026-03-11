import asyncio
from datetime import datetime, timezone

import src.services.license_service as license_service_module
from src.services.license_service import LicenseService


def test_license_service_blocks_expired_policy(monkeypatch):
    service = LicenseService(
        "https://example.com/license.json",
        cache_ttl_seconds=60,
        timeout_seconds=3,
        fail_open=False,
    )

    async def fake_fetch_policy():
        return {
            "version": 1,
            "enabled": True,
            "expires_at": "2026-01-01T00:00:00+08:00",
            "block_message": "授权已过期",
        }

    monkeypatch.setattr(service, "_fetch_policy", fake_fetch_policy)

    status = asyncio.run(service.get_status(force_refresh=True))

    assert status.allowed is False
    assert status.expired is True
    assert status.reason == "expired"
    assert status.block_message == "授权已过期"


def test_license_service_refetches_after_cached_expired_policy(monkeypatch):
    service = LicenseService(
        "https://example.com/license.json",
        cache_ttl_seconds=60,
        timeout_seconds=3,
        fail_open=False,
    )
    policies = [
        {
            "version": 1,
            "enabled": True,
            "expires_at": "2026-01-01T00:00:00+08:00",
            "block_message": "授权已过期",
        },
        {
            "version": 2,
            "enabled": True,
            "expires_at": "2099-01-01T00:00:00+08:00",
            "message": "授权恢复有效",
        },
    ]
    call_count = {"value": 0}

    async def fake_fetch_policy():
        current = policies[min(call_count["value"], len(policies) - 1)]
        call_count["value"] += 1
        return current

    monkeypatch.setattr(service, "_fetch_policy", fake_fetch_policy)

    first_status = asyncio.run(service.get_status(force_refresh=True))
    second_status = asyncio.run(service.get_status())

    assert first_status.allowed is False
    assert second_status.allowed is True
    assert second_status.reason == "active"
    assert call_count["value"] == 2


def test_license_service_blocks_when_remote_file_missing(monkeypatch):
    service = LicenseService(
        "https://example.com/license.json",
        cache_ttl_seconds=60,
        timeout_seconds=3,
        fail_open=False,
    )

    async def fake_fetch_policy():
        raise RuntimeError("404 Not Found")

    monkeypatch.setattr(service, "_fetch_policy", fake_fetch_policy)

    status = asyncio.run(service.get_status(force_refresh=True))

    assert status.allowed is False
    assert status.reason == "fetch-error"
    assert status.source == "fail-closed"


def test_license_service_blocks_when_config_url_missing():
    service = LicenseService(
        "",
        cache_ttl_seconds=60,
        timeout_seconds=3,
        fail_open=False,
    )

    status = asyncio.run(service.get_status())

    assert status.allowed is False
    assert status.reason == "not-configured"
    assert status.source == "fail-closed"


def test_license_service_cache_expires_at_license_deadline_without_restart(monkeypatch):
    service = LicenseService(
        "https://example.com/license.json",
        cache_ttl_seconds=86400,
        timeout_seconds=3,
        fail_open=False,
    )
    current_time = {
        "value": datetime(2026, 3, 11, 12, 0, tzinfo=timezone.utc),
    }
    call_count = {"value": 0}

    async def fake_fetch_policy():
        call_count["value"] += 1
        if call_count["value"] == 1:
            return {
                "version": 1,
                "enabled": True,
                "expires_at": "2026-03-12T00:00:00+00:00",
                "message": "授权有效",
            }
        raise RuntimeError("R2 timeout")

    monkeypatch.setattr(service, "_fetch_policy", fake_fetch_policy)
    monkeypatch.setattr(
        license_service_module,
        "_now_utc",
        lambda: current_time["value"],
    )

    first_status = asyncio.run(service.get_status(force_refresh=True))
    current_time["value"] = datetime(2026, 3, 12, 0, 1, tzinfo=timezone.utc)
    second_status = asyncio.run(service.get_status())

    assert first_status.allowed is True
    assert second_status.allowed is False
    assert second_status.reason == "fetch-error"
    assert second_status.source == "fail-closed"
    assert call_count["value"] == 2
