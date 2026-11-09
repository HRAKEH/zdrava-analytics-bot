"""Verified and bounded ZIP backups for ZDRAVA's JSON-only storage.

No credentials, OAuth tokens or arbitrary JSON in /app/data are exported.
JSON snapshots live under /app/data/backups; only managed archive names are pruned.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import uuid
import zipfile


FILES = ("ad_spend.json", "daily_delivery.json", "last_report.json", "health.json",
         "user_settings.json", "direct_campaigns.json")
ARCHIVE_RE = re.compile(r"^zdrava_\d{8}T\d{12}Z_(?:manual|automatic)\.zip$")
SCHEMA = "zdrava-analytics-json-backup-v1"
MAX_JSON_BYTES = 50 * 1024 * 1024


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    kind: str
    sha256: str
    size: int
    files: int


class BackupService:
    """Single-process backup service; polling should have only one active instance."""

    def __init__(self, data_dir: Path, *, retention_days: int = 7,
                 max_count: int = 10, max_total_mb: int = 512):
        self.data_dir = Path(data_dir)
        self.directory = self.data_dir / "backups"
        self.retention_days = max(1, int(retention_days))
        self.max_count = max(1, int(max_count))
        self.max_total_bytes = max(1, int(max_total_mb)) * 1024 * 1024
        self._lock = threading.RLock()
        self._in_use: dict[Path, int] = {}
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fd:
            for chunk in iter(lambda: fd.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _managed(self) -> list[Path]:
        return [f for f in self.directory.iterdir()
                if ARCHIVE_RE.fullmatch(f.name) and f.is_file() and not f.is_symlink()]

    def inspect(self, path: Path) -> BackupInfo:
        """Read an archive defensively, without extracting untrusted ZIP paths."""
        path = Path(path)
        if not ARCHIVE_RE.fullmatch(path.name) or path.parent.resolve() != self.directory.resolve():
            raise BackupError("Неизвестный формат или путь архива")
        if not path.is_file() or path.is_symlink():
            raise BackupError("Архив не найден")
        try:
            with zipfile.ZipFile(path, "r") as zf:
                entries = zf.infolist()
                names = [e.filename for e in entries]
                if len(names) != len(set(names)) or "manifest.json" not in names:
                    raise BackupError("Архив повреждён: нет манифеста или повторяются файлы")
                if any(e.is_dir() or e.flag_bits & 1 or
                       e.compress_type not in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED) or
                       (stat.S_IFMT(e.external_attr >> 16) not in (0, stat.S_IFREG))
                       for e in entries):
                    raise BackupError("Архив содержит недопустимые элементы")
                if zf.getinfo("manifest.json").file_size > 65536:
                    raise BackupError("Слишком большой манифест")
                manifest = json.loads(zf.read("manifest.json"))
                if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
                    raise BackupError("Неизвестная схема архива")
                info = manifest.get("files")
                if not isinstance(info, dict) or not info or not set(info).issubset(FILES):
                    raise BackupError("Архив не содержит разрешённых файлов данных")
                expected = {f"data/{name}" for name in info} | {"manifest.json"}
                if set(names) != expected:
                    raise BackupError("Состав архива не совпадает с манифестом")
                for name, metadata in info.items():
                    entry = zf.getinfo(f"data/{name}")
                    if entry.file_size > MAX_JSON_BYTES or entry.file_size < 2:
                        raise BackupError("Недопустимый размер JSON")
                    if not isinstance(metadata, dict) or entry.file_size != metadata.get("size"):
                        raise BackupError("Размер JSON не совпадает с манифестом")
                    raw = zf.read(entry)
                    if self._sha256(raw) != metadata.get("sha256"):
                        raise BackupError("Контрольная сумма JSON не совпадает")
                    if not isinstance(json.loads(raw), dict):
                        raise BackupError("Файл данных не является JSON-объектом")
            return BackupInfo(path, str(manifest.get("kind", "unknown")),
                              self._file_sha256(path), path.stat().st_size, len(info))
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeError, EOFError,
                KeyError, TypeError, ValueError, RuntimeError) as exc:
            if isinstance(exc, BackupError):
                raise
            raise BackupError("Повреждённый или недоступный ZIP-архив") from exc

    def create(self, kind: str = "manual") -> BackupInfo:
        if kind not in ("manual", "automatic"):
            raise ValueError("Unknown backup kind")
        with self._lock:
            payloads = {}
            for name in FILES:
                path = self.data_dir / name
                if path.is_symlink():
                    raise BackupError(f"Отказ: {name} является ссылкой")
                if not path.exists():
                    continue
                if not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
                    raise BackupError(f"Некорректный файл {name}")
                try:
                    raw = path.read_bytes()
                    if not isinstance(json.loads(raw), dict):
                        raise ValueError("Expected JSON object")
                except (OSError, ValueError, UnicodeError) as exc:
                    raise BackupError(f"Не удалось прочитать {name}, бэкап не создан") from exc
                payloads[name] = raw
            if not payloads:
                raise BackupError("Нет сохранённых JSON-данных для резервной копии")
            now = datetime.now(timezone.utc)
            stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
            target = self.directory / f"zdrava_{stamp}_{kind}.zip"
            manifest = {
                "schema": SCHEMA, "created_at_utc": now.isoformat(), "kind": kind,
                "files": {name: {"size": len(data), "sha256": self._sha256(data)}
                          for name, data in payloads.items()},
            }
            fd, stage = tempfile.mkstemp(prefix=".zdrava-backup-", suffix=".tmp", dir=self.directory)
            stage_path = Path(stage)
            try:
                with os.fdopen(fd, "wb") as out:
                    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                        for name, raw in payloads.items():
                            zf.writestr(f"data/{name}", raw)
                        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
                    out.flush()
                    os.fsync(out.fileno())
                stage_path.chmod(0o600)
                # Inspect only after final name becomes canonical; rollback on failures.
                os.replace(stage_path, target)
                try:
                    result = self.inspect(target)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                try:
                    self.prune(preserve={target})
                except Exception:
                    # A correct backup must survive retention/cleanup failures.
                    import logging
                    logging.getLogger(__name__).exception("Backup saved; pruning failed")
                return result
            finally:
                stage_path.unlink(missing_ok=True)

    @contextmanager
    def preserve(self, path: Path):
        path = Path(path).resolve()
        with self._lock:
            if not path.is_file():
                raise BackupError("Архив уже удалён")
            self._in_use[path] = self._in_use.get(path, 0) + 1
        try:
            yield path
        finally:
            with self._lock:
                num = self._in_use[path] - 1
                if num:
                    self._in_use[path] = num
                else:
                    self._in_use.pop(path, None)

    def list_backups(self) -> list[BackupInfo]:
        with self._lock:
            files = sorted(self._managed(), key=lambda p: p.name, reverse=True)
            result = []
            for path in files:
                try:
                    result.append(self.inspect(path))
                except BackupError:
                    import logging
                    logging.getLogger(__name__).warning("Broken backup: %s", path.name)
            return result

    def latest(self) -> BackupInfo | None:
        backups = self.list_backups()
        return backups[0] if backups else None

    def prune(self, *, preserve: set[Path] | None = None) -> int:
        """Keep at least the latest VERIFIED archive even when limits are exceeded."""
        with self._lock:
            all_files = sorted(self._managed(), key=lambda p: p.name, reverse=True)
            if not all_files:
                return 0
            valid = []
            for path in all_files:
                try:
                    self.inspect(path)
                    valid.append(path)
                except BackupError:
                    continue
            if not valid:
                return 0
            protect = {p.resolve() for p in (preserve or set())} | set(self._in_use)
            protect.add(valid[0].resolve())
            cutoff = datetime.now(timezone.utc).timestamp() - self.retention_days * 86400
            removed = 0
            for path in list(all_files):
                if path.resolve() not in protect and path.stat().st_mtime < cutoff:
                    path.unlink()
                    all_files.remove(path)
                    removed += 1
            # Delete oldest first; never touch protected files or unrelated files.
            for path in reversed(list(all_files)):
                if len(all_files) <= self.max_count and sum(p.stat().st_size for p in all_files) <= self.max_total_bytes:
                    break
                if path.resolve() in protect:
                    continue
                path.unlink()
                all_files.remove(path)
                removed += 1
            return removed

    def usage(self) -> tuple[int, int]:
        with self._lock:
            files = self._managed()
            return len(files), sum(p.stat().st_size for p in files)


@dataclass(frozen=True)
class BackupOptions:
    enabled: bool = True
    time: str = "03:00"
    send_telegram: bool = False
    retention_days: int = 7
    max_count: int = 10
    max_total_mb: int = 512

    @classmethod
    def from_env(cls) -> "BackupOptions":
        def flag(name: str, default: bool) -> bool:
            raw = os.getenv(name)
            return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on", "да"}

        def bound(name: str, default: int, minimum: int, maximum: int) -> int:
            value = int(os.getenv(name, str(default)))
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} должен быть от {minimum} до {maximum}")
            return value

        tm = os.getenv("AUTO_BACKUP_TIME", "03:00")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", tm):
            raise ValueError("AUTO_BACKUP_TIME должен быть в формате ЧЧ:ММ")
        return cls(
            enabled=flag("AUTO_BACKUP_ENABLED", True),
            time=tm,
            send_telegram=flag("AUTO_BACKUP_SEND_TELEGRAM", False),
            retention_days=bound("BACKUP_RETENTION_DAYS", 7, 1, 365),
            max_count=bound("BACKUP_MAX_COUNT", 10, 1, 100),
            max_total_mb=bound("BACKUP_MAX_TOTAL_MB", 512, 1, 2048),
        )
