from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GrowthTracker:
    start_balance: float
    target_balance: float = 100000.0
    horizon_months: int = 12
    cumulative_deposits: float = 0.0

    def register_deposit(self, amount: float) -> None:
        self.cumulative_deposits += max(float(amount), 0.0)

    def evaluate(self, current_balance: float) -> dict:
        current = max(float(current_balance), 0.0)
        net_pnl = current - self.start_balance - self.cumulative_deposits
        performance_base = max(self.start_balance, 1e-9)
        net_return_pct = (net_pnl / performance_base) * 100.0
        total_growth_pct = ((current - self.start_balance) / performance_base) * 100.0

        needed_per_month = None
        if self.start_balance > 0 and self.horizon_months > 0 and self.target_balance > 0:
            ratio = self.target_balance / self.start_balance
            if ratio > 0:
                needed_per_month = (ratio ** (1.0 / self.horizon_months) - 1.0) * 100.0

        return {
            "start_balance": round(self.start_balance, 8),
            "current_balance": round(current, 8),
            "cumulative_deposits": round(self.cumulative_deposits, 8),
            "net_pnl_excluding_deposits": round(net_pnl, 8),
            "net_return_pct_excluding_deposits": round(net_return_pct, 4),
            "total_growth_pct_including_deposits": round(total_growth_pct, 4),
            "target_balance": round(self.target_balance, 8),
            "horizon_months": int(self.horizon_months),
            "required_monthly_growth_pct": round(needed_per_month, 4) if needed_per_month is not None else None,
            "target_progress_pct": round((current / self.target_balance) * 100.0, 4) if self.target_balance > 0 else None,
        }
