from __future__ import annotations

import ctypes
import json
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol


class AccessTokenProvider(Protocol):
    def __call__(self) -> str: ...


class OutlookCalendarAdapter:
    """Read a Microsoft Graph calendar view behind one synchronization interface."""

    provider = "outlook"
    graph_root = "https://graph.microsoft.com/v1.0"

    def __init__(self, token_provider: AccessTokenProvider, *, request_json: Callable[[str, str], dict[str, Any]] | None = None) -> None:
        self._token_provider = token_provider
        self._request_json = request_json or self._http_get_json

    def sync(self, *, delta_link: str | None, window_start: datetime, window_end: datetime) -> dict[str, Any]:
        if delta_link:
            url = delta_link
        else:
            query = urllib.parse.urlencode({
                "startDateTime": window_start.astimezone(UTC).isoformat(),
                "endDateTime": window_end.astimezone(UTC).isoformat(),
            })
            url = f"{self.graph_root}/me/calendarView/delta?{query}"
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        final_delta: str | None = None
        while next_url:
            page = self._request_json(next_url, self._token_provider())
            values = page.get("value", [])
            if not isinstance(values, list):
                raise ValueError("Microsoft Graph calendar response has no value list")
            items.extend(item for item in values if isinstance(item, dict))
            next_url = page.get("@odata.nextLink")
            final_delta = page.get("@odata.deltaLink") or final_delta
        if final_delta is None:
            raise ValueError("Microsoft Graph calendar synchronization returned no delta link")
        return {"events": [self._normalize(item) for item in items], "delta_link": final_delta}

    @staticmethod
    def _normalize(item: dict[str, Any]) -> dict[str, Any]:
        provider_event_id = item.get("id")
        if not isinstance(provider_event_id, str) or not provider_event_id:
            raise ValueError("Outlook event is missing id")
        removed = "@removed" in item
        start = OutlookCalendarAdapter._graph_datetime(item.get("start")) if not removed else None
        end = OutlookCalendarAdapter._graph_datetime(item.get("end")) if not removed else None
        response = item.get("responseStatus") if isinstance(item.get("responseStatus"), dict) else {}
        return {
            "provider_event_id": provider_event_id,
            "version": str(item.get("changeKey") or item.get("lastModifiedDateTime") or "removed"),
            "title": str(item.get("subject") or "Private Outlook meeting"),
            "start_at": start,
            "end_at": end,
            "response_status": str(response.get("response") or "none"),
            "cancelled": bool(item.get("isCancelled")) or removed,
            "online_meeting": bool(item.get("isOnlineMeeting")),
            "removed": removed,
        }

    @staticmethod
    def _graph_datetime(value: Any) -> str:
        if not isinstance(value, dict) or not isinstance(value.get("dateTime"), str):
            raise ValueError("Outlook event is missing start or end dateTime")
        raw = value["dateTime"]
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"invalid Outlook dateTime: {raw}") from error
        if parsed.tzinfo is None:
            # Graph returns UTC when Prefer: outlook.timezone is not supplied.
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()

    @staticmethod
    def _http_get_json(url: str, access_token: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Prefer": 'outlook.timezone="UTC"',
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise ValueError(f"Microsoft Graph calendar request failed ({error.code}): {detail}") from error
        except urllib.error.URLError as error:
            raise ValueError(f"Microsoft Graph calendar request failed: {error.reason}") from error
        if not isinstance(payload, dict):
            raise ValueError("Microsoft Graph returned a non-object response")
        return payload


class OutlookDeviceAuth:
    """Acquire delegated Calendars.Read tokens and protect the MSAL cache with Windows DPAPI."""

    scopes = ["Calendars.Read"]

    def __init__(self, client_id: str, cache_path: Path, *, tenant: str = "common") -> None:
        if not client_id.strip():
            raise ValueError("Outlook client_id must be configured")
        self.client_id = client_id.strip()
        self.tenant = tenant.strip() or "common"
        self.cache_path = cache_path.expanduser().resolve()
        try:
            import msal  # type: ignore
        except ImportError as error:
            raise ValueError("Outlook login requires the optional 'msal' package; install requirements-outlook.txt") from error
        self._msal = msal
        self._cache = msal.SerializableTokenCache()
        if self.cache_path.is_file():
            self._cache.deserialize(self._unprotect(self.cache_path.read_bytes()).decode("utf-8"))
        self._application = msal.PublicClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant}",
            token_cache=self._cache,
        )

    def access_token(self) -> str:
        accounts = self._application.get_accounts()
        result = self._application.acquire_token_silent(self.scopes, account=accounts[0]) if accounts else None
        if not result:
            flow = self._application.initiate_device_flow(scopes=self.scopes)
            if "user_code" not in flow:
                raise ValueError(f"Microsoft device login could not start: {flow.get('error_description', flow)}")
            print(flow["message"], flush=True)
            result = self._application.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise ValueError(f"Microsoft login failed: {result.get('error_description', result.get('error', 'unknown error'))}")
        self._persist()
        return str(result["access_token"])

    def _persist(self) -> None:
        if not self._cache.has_state_changed:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_bytes(self._protect(self._cache.serialize().encode("utf-8")))

    @staticmethod
    def _protect(data: bytes) -> bytes:
        return OutlookDeviceAuth._crypt(data, protect=True)

    @staticmethod
    def _unprotect(data: bytes) -> bytes:
        return OutlookDeviceAuth._crypt(data, protect=False)

    @staticmethod
    def _crypt(data: bytes, *, protect: bool) -> bytes:
        if not hasattr(ctypes, "windll"):
            raise ValueError("DPAPI token protection is available only on Windows")

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        buffer = ctypes.create_string_buffer(data)
        source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        destination = DataBlob()
        function = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
        if protect:
            ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination))
        else:
            ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination))
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(destination.pbData)
