"""Encrypted connector credential storage — Prism-inspired, file-backed with Fernet."""

import json
import logging
from pathlib import Path
from typing import Any

import aiofiles

from ..utils.security import SecureStorage

logger = logging.getLogger(__name__)

BROKER_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "upstox": [
        {
            "field": "api_key",
            "label": "API Key",
            "type": "string",
            "required": True,
            "secret": False,
            "hint": "From Upstox Developer Console → Apps → API Key",
        },
        {
            "field": "api_secret",
            "label": "API Secret",
            "type": "password",
            "required": True,
            "secret": True,
            "hint": "From Upstox Developer Console → Apps → API Secret",
        },
        {
            "field": "redirect_uri",
            "label": "Redirect URI",
            "type": "string",
            "required": True,
            "secret": False,
            "default": "http://localhost:8000/api/v1/auth/callback",
            "hint": "Must match the redirect URI in your Upstox app settings",
        },
    ],
    "zerodha": [
        {
            "field": "api_key",
            "label": "API Key",
            "type": "string",
            "required": True,
            "secret": False,
            "hint": "From Kite Connect → Apps → API Key",
        },
        {
            "field": "api_secret",
            "label": "API Secret",
            "type": "password",
            "required": True,
            "secret": True,
            "hint": "From Kite Connect → Apps → API Secret",
        },
        {
            "field": "redirect_uri",
            "label": "Redirect URI",
            "type": "string",
            "required": True,
            "secret": False,
            "default": "http://localhost:8000/api/v1/auth/callback/zerodha",
            "hint": "Must match the redirect URI in your Kite Connect app",
        },
    ],
    "groww": [
        {
            "field": "api_key",
            "label": "API Key",
            "type": "string",
            "required": True,
            "secret": False,
            "hint": "From Groww Developer Portal",
        },
        {
            "field": "api_secret",
            "label": "API Secret",
            "type": "password",
            "required": True,
            "secret": True,
            "hint": "From Groww Developer Portal",
        },
    ],
    "miraeasset": [
        {
            "field": "api_key",
            "label": "API Key",
            "type": "string",
            "required": True,
            "secret": False,
            "hint": "From Mira Asset Developer Portal",
        },
        {
            "field": "api_secret",
            "label": "API Secret",
            "type": "password",
            "required": True,
            "secret": True,
            "hint": "From Mira Asset Developer Portal",
        },
    ],
}


class ConnectorStore:
    """Encrypted file-backed storage for broker connector credentials."""

    def __init__(self, storage_dir: str | None = None) -> None:
        self._secure = SecureStorage()
        base = Path(storage_dir) if storage_dir else self._secure.storage_dir
        self._file = base / "connectors.json"
        base.mkdir(exist_ok=True, parents=True)

    async def _load(self) -> dict[str, dict[str, str]]:
        if not self._file.exists():
            return {}
        try:
            async with aiofiles.open(self._file) as f:
                raw = await f.read()
                if not raw.strip():
                    return {}
                return json.loads(raw)
        except Exception as e:
            logger.error("Failed to load connector store: %s", e)
            return {}

    async def _save(self, data: dict[str, dict[str, str]]) -> None:
        tmp = self._file.with_suffix(".tmp")
        try:
            async with aiofiles.open(tmp, "w") as f:
                await f.write(json.dumps(data, indent=2))
                await f.flush()
            tmp.replace(self._file)
            self._file.chmod(0o600)
        except Exception as e:
            logger.error("Failed to save connector store: %s", e)
            tmp.unlink(missing_ok=True)
            raise

    async def save_config(self, broker_id: str, values: dict[str, str]) -> None:
        """Save connector credentials (secrets are encrypted)."""
        schema = BROKER_SCHEMAS.get(broker_id, [])
        secret_fields = {f["field"] for f in schema if f.get("secret")}

        encrypted: dict[str, str] = {}
        for k, v in values.items():
            if k in secret_fields and v:
                encrypted[k] = self._secure._encrypt_data(v)
            else:
                encrypted[k] = v

        store = await self._load()
        existing = store.get(broker_id, {})

        for k, v in encrypted.items():
            if v:
                existing[k] = v
        existing["_configured"] = "true"

        store[broker_id] = existing
        await self._save(store)
        logger.info("Connector config saved for broker=%s", broker_id)

    async def get_config(self, broker_id: str) -> dict[str, str] | None:
        """Load connector credentials (secrets are decrypted)."""
        store = await self._load()
        entry = store.get(broker_id)
        if not entry or entry.get("_configured") != "true":
            return None

        schema = BROKER_SCHEMAS.get(broker_id, [])
        secret_fields = {f["field"] for f in schema if f.get("secret")}

        decrypted: dict[str, str] = {}
        for k, v in entry.items():
            if k.startswith("_"):
                continue
            if k in secret_fields and v:
                try:
                    decrypted[k] = self._secure._decrypt_data(v)
                except Exception:
                    logger.warning("Failed to decrypt field %s for broker %s", k, broker_id)
                    decrypted[k] = ""
            else:
                decrypted[k] = v
        return decrypted

    async def get_config_redacted(self, broker_id: str) -> dict[str, Any] | None:
        """Load connector config with secrets masked (for API responses)."""
        store = await self._load()
        entry = store.get(broker_id)
        if not entry or entry.get("_configured") != "true":
            return None

        schema = BROKER_SCHEMAS.get(broker_id, [])
        secret_fields = {f["field"] for f in schema if f.get("secret")}

        redacted: dict[str, Any] = {}
        for k, v in entry.items():
            if k.startswith("_"):
                continue
            if k in secret_fields:
                redacted[k] = {"has_value": bool(v)}
            else:
                redacted[k] = v
        return redacted

    async def delete_config(self, broker_id: str) -> bool:
        store = await self._load()
        if broker_id in store:
            del store[broker_id]
            await self._save(store)
            logger.info("Connector config deleted for broker=%s", broker_id)
            return True
        return False

    async def is_configured(self, broker_id: str) -> bool:
        store = await self._load()
        entry = store.get(broker_id)
        return bool(entry and entry.get("_configured") == "true")
