"""JSON-only storage; Decimal for money and explicit coverage of manual input."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


class DataIntegrityError(ValueError):
    """Stored data is missing/corrupt: never manufacture an aggregate."""


@dataclass(frozen=True)
class SpendCoverage:
    recorded_amount: Decimal
    recorded_days: int
    expected_days: int

    @property
    def complete(self) -> bool:
        return self.recorded_days == self.expected_days

    @property
    def has_data(self) -> bool:
        return self.recorded_days > 0


def money_amount(raw: str) -> Decimal:
    """Rubles and up to two decimal digits, never rounded invisibly."""
    normalized = raw.strip().replace(",", ".")
    if not re.fullmatch(r"\d{1,9}(?:\.\d{1,2})?", normalized):
        raise ValueError("Сумма должна содержать не более двух знаков после запятой")
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        raise ValueError("Некорректная сумма") from None
    if amount < 0 or amount > Decimal("100000000"):
        raise ValueError("Сумма вне допустимого диапазона")
    return amount.quantize(Decimal("0.01"))


def stored_amount(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise DataIntegrityError("Неверная сумма в /app/data/ad_spend.json")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise DataIntegrityError("Повреждённая сумма в файле расходов") from None
    if not amount.is_finite() or amount < 0 or amount > 100000000 or amount.as_tuple().exponent < -2:
        raise DataIntegrityError("Неверная точность или диапазон сохранённой суммы")
    return amount.quantize(Decimal("0.01"))


class JsonStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def read(self, name: str, default: Any) -> Any:
        path = self._path(name)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataIntegrityError(f"Ошибка чтения JSON {name}; данные не подменяются") from exc

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
        if not name.endswith(".json") or "/" in name or "\\" in name or name.startswith("."):
            raise ValueError("Invalid JSON filename")
        return self.root / name

    def _spend_data(self) -> dict:
        data = self.read("ad_spend.json", {})
        if not isinstance(data, dict):
            raise DataIntegrityError("Файл расходов не является объектом JSON")
        for p, days in data.items():
            if not isinstance(days, dict):
                raise DataIntegrityError(f"Повреждена группа расходов {p}")
            for d, amount in days.items():
                try:
                    if date.fromisoformat(d).isoformat() != d:
                        raise ValueError
                except (TypeError, ValueError):
                    raise DataIntegrityError("Некорректная дата в расходах") from None
                stored_amount(amount)
        return data

    def set_spend(self, product: str, day: str, amount: Decimal) -> None:
        # API accepts already-validated monetary Decimal only.
        if not isinstance(amount, Decimal):
            raise ValueError("Spend must be Decimal")
        stored_amount(str(amount))
        data = self._spend_data()
        data.setdefault(product, {})[day] = f"{amount:.2f}"
        self.write("ad_spend.json", data)

    def spend_summary(self, product: str, begin: str, end: str) -> SpendCoverage:
        data = self._spend_data()
        start = date.fromisoformat(begin)
        finish = date.fromisoformat(end)
        if start > finish:
            raise ValueError("Invalid period")
        values = data.get(product, {})
        records = []
        day = start
        while day <= finish:
            key = day.isoformat()
            if key in values:
                records.append(stored_amount(values[key]))
            day += timedelta(days=1)
        return SpendCoverage(sum(records, Decimal("0.00")), len(records), (finish-start).days+1)
