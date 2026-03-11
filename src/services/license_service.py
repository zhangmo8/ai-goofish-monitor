"""
远程许可证状态校验服务
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx


DEFAULT_BLOCK_MESSAGE = "当前授权已过期或被停用，功能暂不可用。"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        dt = datetime.fromisoformat(normalized)
    else:
        raise ValueError("时间字段必须是 ISO 8601 字符串。")

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_optional_datetime(value: Any) -> Optional[datetime]:
    try:
        return _parse_datetime(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LicenseStatus:
    allowed: bool
    enabled: bool
    expired: bool
    reason: str
    source: str
    checked_at: str
    expires_at: Optional[str] = None
    updated_at: Optional[str] = None
    version: Optional[int] = None
    message: str = ""
    block_message: str = DEFAULT_BLOCK_MESSAGE
    fetch_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "enabled": self.enabled,
            "expired": self.expired,
            "reason": self.reason,
            "source": self.source,
            "checked_at": self.checked_at,
            "expires_at": self.expires_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "message": self.message,
            "block_message": self.block_message,
            "fetch_error": self.fetch_error,
        }


class LicenseService:
    """从远端 JSON 拉取许可证状态并做本地缓存。"""

    def __init__(
        self,
        config_url: str,
        *,
        cache_ttl_seconds: int = 300,
        timeout_seconds: int = 5,
        fail_open: bool = False,
    ):
        self.config_url = (config_url or "").strip()
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.fail_open = bool(fail_open)
        self._cache_until = 0.0
        self._cached_policy: Optional[Dict[str, Any]] = None
        self._cached_effective_expiry: Optional[datetime] = None
        self._lock = asyncio.Lock()

    async def get_status(self, *, force_refresh: bool = False) -> LicenseStatus:
        checked_at = _now_utc()
        if not self.config_url:
            return self._build_fetch_error_status(
                "未配置 LICENSE_REMOTE_JSON_URL",
                checked_at,
                reason="not-configured",
            )

        if not force_refresh:
            cached_status = self._get_usable_cached_status(checked_at)
            if cached_status is not None:
                return cached_status

        async with self._lock:
            checked_at = _now_utc()
            if not force_refresh:
                cached_status = self._get_usable_cached_status(checked_at)
                if cached_status is not None:
                    return cached_status

            try:
                policy = await self._fetch_policy()
            except Exception as exc:
                fetch_error = str(exc)
                return self._build_fetch_error_status(fetch_error, checked_at)

            self._cached_policy = policy
            self._cached_effective_expiry = self._resolve_effective_expiry(policy)
            self._cache_until = time.monotonic() + self.cache_ttl_seconds
            return self._build_status(
                policy,
                checked_at=checked_at,
                source="remote",
            )

    async def _fetch_policy(self) -> Dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(self.config_url)
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict):
            raise ValueError("远端许可证文件必须是 JSON 对象。")

        if isinstance(payload.get("license"), dict):
            return payload["license"]

        return payload

    def _get_usable_cached_status(self, checked_at: datetime) -> Optional[LicenseStatus]:
        if not self._cached_policy or time.monotonic() >= self._cache_until:
            return None
        if (
            self._cached_effective_expiry is not None
            and checked_at >= self._cached_effective_expiry
        ):
            return None

        cached_status = self._build_status(
            self._cached_policy,
            checked_at=checked_at,
            source="cache",
        )
        if not cached_status.allowed:
            return None
        return cached_status

    def _build_status(
        self,
        policy: Dict[str, Any],
        *,
        checked_at: datetime,
        source: str,
        fetch_error: Optional[str] = None,
    ) -> LicenseStatus:
        enabled = _as_bool(policy.get("enabled"), True)
        expires_at = _parse_datetime(policy.get("expires_at"))
        updated_at = _parse_optional_datetime(policy.get("updated_at"))
        effective_expiry = self._resolve_effective_expiry(policy)
        expired = effective_expiry is not None and effective_expiry <= checked_at
        allowed = enabled and not expired

        reason = "active"
        if not enabled:
            reason = "disabled"
        elif expired:
            reason = "expired"

        message = str(policy.get("message") or "").strip()
        block_message = str(policy.get("block_message") or DEFAULT_BLOCK_MESSAGE).strip()
        if not message:
            message = "授权有效。" if allowed else block_message

        if fetch_error:
            message = f"{message} 上次远端拉取失败: {fetch_error}"

        version = policy.get("version")
        if version is not None:
            version = _as_int(version, 0) or 0

        return LicenseStatus(
            allowed=allowed,
            enabled=enabled,
            expired=expired,
            reason=reason,
            source=source,
            checked_at=_to_iso(checked_at) or "",
            expires_at=_to_iso(expires_at),
            updated_at=_to_iso(updated_at),
            version=version,
            message=message,
            block_message=block_message,
            fetch_error=fetch_error,
        )

    def _resolve_effective_expiry(self, policy: Dict[str, Any]) -> Optional[datetime]:
        expires_at = _parse_datetime(policy.get("expires_at"))
        grace_period_seconds = max(0, _as_int(policy.get("grace_period_seconds"), 0))
        if expires_at is None:
            return None
        return expires_at + timedelta(seconds=grace_period_seconds)

    def _build_fetch_error_status(
        self,
        fetch_error: str,
        checked_at: datetime,
        *,
        reason: str = "fetch-error",
    ) -> LicenseStatus:
        allowed = self.fail_open
        message = f"远端许可证读取失败: {fetch_error}"
        block_message = (
            "远端许可证读取失败，已暂停功能使用。"
            if not allowed
            else DEFAULT_BLOCK_MESSAGE
        )
        return LicenseStatus(
            allowed=allowed,
            enabled=allowed,
            expired=False,
            reason=reason,
            source="fail-open" if allowed else "fail-closed",
            checked_at=_to_iso(checked_at) or "",
            message=message,
            block_message=block_message,
            fetch_error=fetch_error,
        )
