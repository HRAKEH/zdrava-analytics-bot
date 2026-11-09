from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
import zipfile

import pytest

from app.services.backups import BackupService, BackupError


def seed(root: Path, value=100):
    root.mkdir(parents=True, exist_ok=True)
    (root / "ad_spend.json").write_text(json.dumps({"mikozacid": {"2026-11-09": str(value)}}), encoding="utf-8")


def test_backup_contains_validated_json_and_manifest_but_no_credentials(tmp_path):
    seed(tmp_path)
    (tmp_path / "gsc-service-account.json").write_text('{"private_key":"TOPSECRET"}')
    (tmp_path / "config.json").write_text('{"password":"SECRET"}')
    srv = BackupService(tmp_path)
    backup = srv.create()
    assert backup.path.suffix == ".zip" and backup.kind == "manual"
    assert backup.files == 1 and backup.size > 10
    with zipfile.ZipFile(backup.path) as zf:
        assert set(zf.namelist()) == {"data/ad_spend.json", "manifest.json"}
        assert "TOPSECRET" not in str(zf.namelist())
        assert "SECRET" not in str(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
        assert "ad_spend.json" in manifest["files"]
    assert srv.inspect(backup.path).sha256 == backup.sha256


def test_empty_and_corrupted_json_never_create_archive(tmp_path):
    srv = BackupService(tmp_path)
    with pytest.raises(BackupError, match="Нет сохранённых"):
        srv.create()
    (tmp_path / "ad_spend.json").write_text("broken")
    with pytest.raises(BackupError, match="Не удалось"):
        srv.create()
    assert not list((tmp_path / "backups").glob("*.zip"))


def test_prune_by_count_keeps_latest_and_ignores_unmanaged(tmp_path):
    seed(tmp_path)
    srv = BackupService(tmp_path, max_count=2)
    copies = []
    for i in range(4):
        seed(tmp_path, i)
        copies.append(srv.create())
        time.sleep(0.003)
    assert len(srv.list_backups()) == 2
    assert copies[-1].path.exists()
    unrelated = srv.directory / "manual-other.zip"
    unrelated.write_bytes(b"keep")
    srv.prune()
    assert unrelated.read_bytes() == b"keep"


def test_prune_age_never_deletes_final_valid(tmp_path):
    seed(tmp_path)
    srv = BackupService(tmp_path, retention_days=1)
    old = srv.create()
    past = time.time()-5*86400
    os.utime(old.path, (past, past))
    assert srv.prune() == 0
    assert old.path.exists()
    newer = srv.create()
    assert newer.path.exists()
    assert not old.path.exists()


def test_prune_does_not_delete_archive_while_sending(tmp_path):
    seed(tmp_path)
    srv = BackupService(tmp_path, max_count=1)
    old = srv.create()
    with srv.preserve(old.path):
        newer = srv.create()
        assert newer.path.exists() and old.path.exists()
        assert srv.prune() == 0
    assert srv.prune() == 1
    assert newer.path.exists() and not old.path.exists()


def test_corrupted_newest_not_treated_as_valid_restore_point(tmp_path):
    seed(tmp_path)
    srv = BackupService(tmp_path, max_count=5)
    good = srv.create()
    bad = srv.create()
    bad.path.write_bytes(b"broken")
    assert srv.latest().path == good.path
    assert srv.prune() == 0
    assert good.path.exists()


def test_symlink_json_is_rejected(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-secret.json")
    outside.write_text('{"secret":"do not leak"}')
    (tmp_path / "ad_spend.json").symlink_to(outside)
    with pytest.raises(BackupError, match="ссылкой"):
        BackupService(tmp_path).create()


def test_backup_options_env(monkeypatch):
    from app.services.backups import BackupOptions
    for key in ("AUTO_BACKUP_ENABLED", "AUTO_BACKUP_TIME", "AUTO_BACKUP_SEND_TELEGRAM",
                "BACKUP_RETENTION_DAYS", "BACKUP_MAX_COUNT", "BACKUP_MAX_TOTAL_MB"):
        monkeypatch.delenv(key, raising=False)
    assert BackupOptions.from_env() == BackupOptions()
    monkeypatch.setenv("AUTO_BACKUP_TIME", "24:00")
    with pytest.raises(ValueError):
        BackupOptions.from_env()
    monkeypatch.setenv("AUTO_BACKUP_TIME", "02:45")
    monkeypatch.setenv("BACKUP_MAX_COUNT", "0")
    with pytest.raises(ValueError):
        BackupOptions.from_env()
