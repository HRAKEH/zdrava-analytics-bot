"""Editable non-secret preferences and explicit campaign-to-product assignments."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from app.storage import JsonStore, DataIntegrityError

PRODUCTS = ("mikozacid", "hot_ice")
PREF_TYPES = {
    "daily_enabled": bool, "daily_time": str,
    "backup_enabled": bool, "backup_time": str,
    "backup_send_telegram": bool,
    "retention_days": int, "backup_max_count": int,
    "backup_max_total_mb": int,
}
TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")

class Preferences:
    def __init__(self, store: JsonStore, defaults: dict[str, Any]):
        self.store = store
        self.defaults = dict(defaults)

    @staticmethod
    def validate(name: str, value: Any) -> None:
        expected = PREF_TYPES.get(name)
        if expected is None or type(value) is not expected:
            raise ValueError("Неверная настройка")
        if name.endswith("_time") and not TIME_RE.fullmatch(value):
            raise ValueError("Время должно быть в формате ЧЧ:ММ, например 09:00")
        if name == "retention_days" and not 1 <= value <= 365:
            raise ValueError("Срок хранения — от 1 до 365 дней")
        if name == "backup_max_count" and not 1 <= value <= 100:
            raise ValueError("Количество архивов — от 1 до 100")
        if name == "backup_max_total_mb" and not 1 <= value <= 2048:
            raise ValueError("Размер архивов — от 1 до 2048 МБ")

    def get(self) -> dict[str, Any]:
        raw = self.store.read("user_settings.json", {})
        if not isinstance(raw, dict):
            raise DataIntegrityError("Повреждён файл настроек")
        result = dict(self.defaults)
        for k, v in raw.items():
            self.validate(k, v)
            result[k] = v
        return result

    def set(self, name: str, value: Any) -> dict[str, Any]:
        self.validate(name, value)
        values = self.get()
        values[name] = value
        self.store.write("user_settings.json", values)
        return values

class CampaignAssignments:
    def __init__(self, store: JsonStore):
        self.store = store

    def get(self) -> dict[str, list[int]]:
        data = self.store.read("direct_campaigns.json", {})
        if not isinstance(data, dict) or set(data) - set(PRODUCTS):
            raise DataIntegrityError("Некорректный список кампаний")
        result = {product: [] for product in PRODUCTS}
        used = set()
        for product, ids in data.items():
            if (not isinstance(ids, list) or len(ids) > 500
                or any(type(i) is not int or i <= 0 or i in used for i in ids)):
                raise DataIntegrityError("Некорректные или повторные ID рекламных кампаний")
            used.update(ids)
            result[product] = sorted(ids)
        return result

    def assign(self, product: str, campaign_id: int) -> dict[str, list[int]]:
        if product not in PRODUCTS or type(campaign_id) is not int or campaign_id <= 0:
            raise ValueError("Неверная кампания")
        mapping = self.get()
        for ids in mapping.values():
            if campaign_id in ids:
                ids.remove(campaign_id)
        mapping[product].append(campaign_id)
        mapping[product].sort()
        self.store.write("direct_campaigns.json", mapping)
        return mapping

    def remove(self, product: str, campaign_id: int) -> dict[str, list[int]]:
        if product not in PRODUCTS or type(campaign_id) is not int:
            raise ValueError("Неверная кампания")
        mapping = self.get()
        if campaign_id in mapping[product]:
            mapping[product].remove(campaign_id)
            self.store.write("direct_campaigns.json", mapping)
        return mapping
