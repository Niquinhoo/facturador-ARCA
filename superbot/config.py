from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _boolean(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si", "on"}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable obligatoria {name}.")
    return value


@dataclass(frozen=True)
class Settings:
    calma_api_url: str
    bot_service_token: str
    telegram_token: str
    gemini_api_key: str
    gemini_model: str
    afip_access_token: str
    afip_cuit: int
    afip_punto_venta: int
    afip_cert_path: Path | None
    afip_key_path: Path | None
    arca_production: bool
    worker_id: str
    data_dir: Path
    fiscal_poll_seconds: int
    event_poll_seconds: int
    heartbeat_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        production = _boolean("ARCA_PRODUCTION")
        cert_raw = os.getenv("AFIP_CERT_PATH", "").strip()
        key_raw = os.getenv("AFIP_KEY_PATH", "").strip()
        if production and (not cert_raw or not key_raw):
            raise RuntimeError("Produccion requiere AFIP_CERT_PATH y AFIP_KEY_PATH.")

        settings = cls(
            calma_api_url=_required("CALMA_API_URL").rstrip("/"),
            bot_service_token=_required("BOT_SERVICE_TOKEN"),
            telegram_token=_required("TELEGRAM_TOKEN"),
            gemini_api_key=_required("GEMINI_API_KEY"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip(),
            afip_access_token=_required("AFIP_ACCESS_TOKEN"),
            afip_cuit=int(_required("AFIP_CUIT")),
            afip_punto_venta=int(_required("AFIP_PUNTO_VENTA")),
            afip_cert_path=Path(cert_raw) if cert_raw else None,
            afip_key_path=Path(key_raw) if key_raw else None,
            arca_production=production,
            worker_id=os.getenv("WORKER_ID", f"calma-bot-{socket.gethostname()}").strip(),
            data_dir=Path(os.getenv("DATA_DIR", "/data")),
            fiscal_poll_seconds=max(5, int(os.getenv("FISCAL_POLL_SECONDS", "10"))),
            event_poll_seconds=max(5, int(os.getenv("EVENT_POLL_SECONDS", "10"))),
            heartbeat_seconds=max(15, int(os.getenv("HEARTBEAT_SECONDS", "30"))),
        )
        if settings.afip_punto_venta <= 0:
            raise RuntimeError("AFIP_PUNTO_VENTA debe ser mayor a cero.")
        if production:
            for path in (settings.afip_cert_path, settings.afip_key_path):
                if not path or not path.is_file():
                    raise RuntimeError(f"No se encontro el archivo fiscal {path}.")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        return settings
