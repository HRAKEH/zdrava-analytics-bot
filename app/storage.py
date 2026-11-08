"""Small JSON-only persistent storage under /app/data; no database/tables."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


class JsonStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def read(self, name: str, default: Any) -> Any:
        path = self._path(name)
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, name: str, value: Any) -> None:
        path = self._path(name)
        fd, tmp = tempfile.mkstemp(prefix=".atomic-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _path(self, name: str) -> Path:
        # All filenames are fixed by the application, not supplied by users.
        if not name.endswith(".json") or "/" in name or "\\" in name or name.startswith("."):
            raise ValueError("Invalid JSON filename")
        return self.root / name

    def set_spend(self, product: str, day: str, amount: float) -> None:
        report = self.read("ad_spend.json", {})
        report.setdefault(product, {})[day] = round(amount, 2)
        self.write("ad_spend.json", report)

    def spend_for(self, product: str, begin: str, end: str) -> float:
        report = self.read("ad_spend.json", {})
        daily = report.get(product, {})
        return round(sum(float(value) for day, value in daily.items() if begin <= day <= end), 2)
