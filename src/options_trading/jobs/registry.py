# src/options_trading/jobs/registry.py
import json
import threading
import uuid
from pathlib import Path
from typing import Any

from ..config.settings import settings

_lock = threading.Lock()
_path = Path(settings.JOB_REGISTRY_PATH)


def _load() -> dict[str, Any]:
    if not _path.exists():
        return {}
    try:
        return json.loads(_path.read_text())
    except Exception:
        return {}


def _save(data: dict[str, Any]):
    tmp = _path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_path)


def create_job(symbol: str, params: dict[str, Any]) -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        data = _load()
        data[job_id] = {
            "id": job_id,
            "symbol": symbol,
            "params": params,
            "status": "pending",
            "files": [],
            "created_at": None,
            "completed_at": None,
            "error": None,
        }
        _save(data)
    return job_id


def update_job(job_id: str, status: str, files: list[str] | None = None, error: str | None = None):
    with _lock:
        data = _load()
        if job_id not in data:
            return
        entry = data[job_id]
        entry["status"] = status
        if files is not None:
            entry["files"] = files
        if error:
            entry["error"] = error
        import datetime

        if status == "completed":
            entry["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        _save(data)


def get_jobs_for_symbol(symbol: str):
    data = _load()
    return [v for v in data.values() if v.get("symbol") == symbol]


def get_job(job_id: str):
    data = _load()
    return data.get(job_id)
