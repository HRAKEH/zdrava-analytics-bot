"""Ozon external-traffic attribution is intentionally not auto-guessed.

Seller API sales statistics alone cannot prove that a sale originated from
our landing. Until an officially available external-traffic report API and
matching campaign dimensions are verified, do not surface inferred orders.
"""

OZON_ATTRIBUTION_STATUS = "Не подключено: требуется подтверждённый источник статистики внешнего трафика Ozon."
