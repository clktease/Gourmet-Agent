"""Tiny JSON-file-backed key/value caches (station coordinates, past analyses)."""

import json
import threading
from pathlib import Path
from typing import Optional

_DATA_DIR = Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)


class JsonCache:
    def __init__(self, filename: str):
        self._path = _DATA_DIR / filename
        self._lock = threading.Lock()
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    def set(self, key: str, value: dict) -> None:
        with self._lock:
            self._data[key] = value
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )


station_coords_cache = JsonCache("station_coords_cache.json")
analysis_cache = JsonCache("analysis_result_cache.json")
