from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings


class EvolutionAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class EvolutionClient:
    """Cliente HTTP enxuto para Evolution API v2.

    A chave global é usada apenas para provisionar a instância. As operações
    da instância usam EVOLUTION_INSTANCE_TOKEN.
    """

    def __init__(self) -> None:
        self.base_url = settings.evolution_api_url.rstrip("/")
        self.instance_name = settings.evolution_instance_name
        self.global_key = settings.evolution_global_api_key
        self.instance_token = settings.evolution_instance_token or self.global_key

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.global_key and self.instance_token)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        use_global_key: bool = False,
        timeout: float = 35.0,
    ) -> Any:
        if not self.base_url:
            raise EvolutionAPIError("EVOLUTION_API_URL não está configurada.")
        token = self.global_key if use_global_key else self.instance_token
        if not token:
            raise EvolutionAPIError("A chave da Evolution API não está configurada.")
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = httpx.request(
                method,
                url,
                headers={"apikey": token, "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise EvolutionAPIError(f"Não foi possível conectar à Evolution API: {exc}") from exc
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {"detail": response.text[:1000]}
        if response.is_error:
            detail = body.get("response", body) if isinstance(body, dict) else body
            message = str(detail)[:1200]
            raise EvolutionAPIError(
                f"Evolution API respondeu HTTP {response.status_code}: {message}",
                status_code=response.status_code,
            )
        if isinstance(body, dict) and body.get("error"):
            message = str(body.get("message") or body.get("error"))[:1200]
            raise EvolutionAPIError(
                f"Evolution API retornou erro: {message}",
                status_code=response.status_code,
            )
        return body

    def initialize_instance(self) -> dict[str, Any]:
        if not self.global_key:
            raise EvolutionAPIError("Configure EVOLUTION_GLOBAL_API_KEY.")
        if not settings.evolution_instance_token:
            raise EvolutionAPIError(
                "Configure EVOLUTION_INSTANCE_TOKEN. Use um token exclusivo para a instância."
            )
        if not re.fullmatch(r"[A-Za-z0-9-]{20,200}", settings.evolution_instance_token):
            raise EvolutionAPIError(
                "EVOLUTION_INSTANCE_TOKEN deve ter entre 20 e 200 caracteres alfanuméricos ou hífens."
            )
        if settings.evolution_instance_token == self.global_key:
            raise EvolutionAPIError(
                "EVOLUTION_INSTANCE_TOKEN deve ser diferente da chave global da Evolution API."
            )
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.instance_name):
            raise EvolutionAPIError(
                "EVOLUTION_INSTANCE_NAME deve conter apenas letras minúsculas sem acento e números."
            )
        webhook_url = self._webhook_url()
        webhook_events = self._webhook_events()
        payload = {
            "instanceName": self.instance_name,
            "token": settings.evolution_instance_token,
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS",
            "webhook": {
                "enabled": True,
                "url": webhook_url,
                "byEvents": False,
                "base64": False,
                "events": webhook_events,
            },
        }
        try:
            created = self._request(
                "POST", "/instance/create", payload=payload, use_global_key=True
            )
        except EvolutionAPIError as exc:
            if exc.status_code not in {400, 403, 409} or not any(
                phrase in str(exc).casefold()
                for phrase in (
                    "already exists",
                    "already registered",
                    "instance exists",
                    "duplicate instance",
                    "already in use",
                )
            ):
                raise
            created = {"already_exists": True}
        webhook = self.configure_webhook()
        return {"instance": created, "webhook": webhook}

    @staticmethod
    def _webhook_events() -> list[str]:
        return [
            "QRCODE_UPDATED",
            "CONNECTION_UPDATE",
            "MESSAGES_UPSERT",
            "MESSAGES_UPDATE",
            "SEND_MESSAGE",
        ]

    @staticmethod
    def _webhook_url() -> str:
        if not settings.evolution_webhook_secret:
            raise EvolutionAPIError("Configure EVOLUTION_WEBHOOK_SECRET.")
        base_url = settings.evolution_webhook_base_url.strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise EvolutionAPIError(
                "EVOLUTION_WEBHOOK_BASE_URL precisa começar com http:// ou https://."
            )
        return (
            f"{base_url}/api/whatsapp/webhook/"
            f"{quote(settings.evolution_webhook_secret, safe='')}"
        )

    def configure_webhook(self) -> dict[str, Any]:
        webhook_url = self._webhook_url()
        payload = {
            "webhook": {
                "enabled": True,
                "url": webhook_url,
                "byEvents": False,
                "base64": False,
                "events": self._webhook_events(),
            }
        }
        return self._request(
            "POST",
            f"/webhook/set/{quote(self.instance_name, safe='')}",
            payload=payload,
        )

    def connect(self) -> dict[str, Any]:
        return self._request(
            "GET", f"/instance/connect/{quote(self.instance_name, safe='')}"
        )

    def connection_state(self) -> dict[str, Any]:
        return self._request(
            "GET", f"/instance/connectionState/{quote(self.instance_name, safe='')}"
        )

    def send_text(self, number: str, text: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/message/sendText/{quote(self.instance_name, safe='')}",
            payload={"number": number, "text": text, "delay": 500},
            timeout=45.0,
        )

    def download_media(self, message_data: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/chat/getBase64FromMediaMessage/{quote(self.instance_name, safe='')}",
            payload={"message": message_data, "convertToMp4": False},
            timeout=90.0,
        )
