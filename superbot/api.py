from __future__ import annotations

from typing import Any

import requests


class CalmaApiError(RuntimeError):
    def __init__(self, message: str, status_code: int = 0, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


class CalmaApi:
    def __init__(self, base_url: str, token: str, timeout: int = 30):
        self.base_url = f"{base_url.rstrip('/')}/api/internal/bot"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "calma-superbot/1.0",
        })

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.session.request(
                method, f"{self.base_url}{path}", timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise CalmaApiError(f"No se pudo conectar con Calma: {exc}") from exc
        if response.status_code == 204:
            return None
        try:
            data = response.json()
        except ValueError:
            data = None
        if not response.ok:
            message = data.get("message") if isinstance(data, dict) else response.text
            details = data.get("details") if isinstance(data, dict) else None
            raise CalmaApiError(message or "Error de Calma.", response.status_code, details)
        return data

    def link(self, code: str, telegram_user_id: int, chat_id: int,
             username: str | None, display_name: str) -> dict[str, Any]:
        return self._request("POST", "/link/consume", json={
            "code": code, "telegramUserId": str(telegram_user_id), "chatId": str(chat_id),
            "username": username, "displayName": display_name,
        })

    def identity(self, telegram_user_id: int) -> dict[str, Any]:
        return self._request("GET", f"/identity/{telegram_user_id}")

    def context(self, telegram_user_id: int, query: str = "") -> dict[str, Any]:
        return self._request("GET", f"/context/{telegram_user_id}", params={"q": query})

    def preview(self, telegram_user_id: int, chat_id: int, kind: str,
                payload: dict[str, Any], request_id: str) -> dict[str, Any]:
        return self._request("POST", "/actions/preview", json={
            "telegramUserId": str(telegram_user_id), "chatId": str(chat_id),
            "kind": kind, "payload": payload, "requestId": request_id,
        })

    def confirm(self, action_id: str, telegram_user_id: int) -> dict[str, Any]:
        return self._request("POST", f"/actions/{action_id}/confirm", json={
            "telegramUserId": str(telegram_user_id)
        })

    def cancel(self, action_id: str, telegram_user_id: int) -> None:
        self._request("POST", f"/actions/{action_id}/cancel", json={
            "telegramUserId": str(telegram_user_id)
        })

    def claim_job(self, worker_id: str) -> dict[str, Any] | None:
        return self._request("POST", "/jobs/claim", json={"workerId": worker_id})

    def complete_job(self, job_id: str, worker_id: str, result: dict[str, Any]) -> Any:
        return self._request("POST", f"/jobs/{job_id}/complete", json={
            "workerId": worker_id, "result": result
        })

    def prepare_job(self, job_id: str, worker_id: str, expected_number: int,
                    request: dict[str, Any]) -> Any:
        return self._request("POST", f"/jobs/{job_id}/prepare", json={
            "workerId": worker_id, "expectedCbteNro": expected_number,
            "request": request,
        })

    def fail_job(self, job_id: str, worker_id: str, error: str,
                 retryable: bool = True) -> None:
        self._request("POST", f"/jobs/{job_id}/fail", json={
            "workerId": worker_id, "error": error[:4000], "retryable": retryable
        })

    def claim_events(self, worker_id: str, limit: int = 20) -> list[dict[str, Any]]:
        data = self._request("POST", "/events/claim", json={
            "workerId": worker_id, "limit": limit
        })
        return data.get("events", []) if data else []

    def ack_event(self, event_id: str, worker_id: str) -> None:
        self._request("POST", f"/events/{event_id}/ack", json={"workerId": worker_id})

    def fail_event(self, event_id: str, worker_id: str, error: str) -> None:
        self._request("POST", f"/events/{event_id}/fail", json={
            "workerId": worker_id, "error": error[:4000]
        })

    def heartbeat(self, worker_id: str, version: str, production: bool,
                  metadata: dict[str, Any]) -> Any:
        return self._request("POST", "/heartbeat", json={
            "workerId": worker_id, "version": version,
            "arcaMode": "production" if production else "homologation",
            "metadata": metadata,
        })
