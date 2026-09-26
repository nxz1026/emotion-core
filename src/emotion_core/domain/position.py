"""持仓类型（position 表）。手动录入实仓（R4）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Position:
    """position 行：手动录入实仓（R4）。"""
    code: str
    entry_date: date
    entry_price: float
    shares: int
    status: str = "OPEN"
    note: str = ""
    id: int | None = None
