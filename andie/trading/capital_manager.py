from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class CapitalManager:
    total_balance: float
    active_capital: float
    reserve_capital: float
    pending_deploy_capital: float = 0.0
    monthly_deposit: float = 0.0
    weekly_deploy_rate: float = 0.25
    risk_per_trade_pct: float = 0.005

    @classmethod
    def from_balance(
        cls,
        total_balance: float,
        active_ratio: float = 0.60,
        monthly_deposit: float = 0.0,
        weekly_deploy_rate: float = 0.25,
        risk_per_trade_pct: float = 0.005,
    ) -> "CapitalManager":
        total = max(float(total_balance), 0.0)
        ratio = min(max(float(active_ratio), 0.10), 0.90)
        active = total * ratio
        reserve = total - active
        return cls(
            total_balance=round(total, 8),
            active_capital=round(active, 8),
            reserve_capital=round(reserve, 8),
            pending_deploy_capital=0.0,
            monthly_deposit=max(float(monthly_deposit), 0.0),
            weekly_deploy_rate=min(max(float(weekly_deploy_rate), 0.05), 0.50),
            risk_per_trade_pct=min(max(float(risk_per_trade_pct), 0.001), 0.02),
        )

    def apply_deposit(self, amount: float) -> float:
        deposit = max(float(amount), 0.0)
        if deposit <= 0:
            return 0.0
        self.total_balance += deposit
        self.reserve_capital += deposit
        self.pending_deploy_capital += deposit
        return round(deposit, 8)

    def activate_pending_capital(self, weeks: float = 1.0) -> float:
        if self.pending_deploy_capital <= 0:
            return 0.0
        w = max(float(weeks), 0.0)
        deploy_fraction = min(self.weekly_deploy_rate * w, 1.0)
        deploy_amount = min(self.pending_deploy_capital * deploy_fraction, self.reserve_capital)
        self.pending_deploy_capital -= deploy_amount
        self.reserve_capital -= deploy_amount
        self.active_capital += deploy_amount
        return round(max(deploy_amount, 0.0), 8)

    def position_risk_usd(self) -> float:
        return round(max(self.active_capital, 0.0) * self.risk_per_trade_pct, 8)

    def apply_realized_pnl(self, pnl: float) -> float:
        value = float(pnl)
        self.total_balance += value
        if value >= 0:
            self.active_capital += value
        else:
            loss = abs(value)
            from_active = min(loss, self.active_capital)
            self.active_capital -= from_active
            spill = loss - from_active
            if spill > 0:
                self.reserve_capital = max(self.reserve_capital - spill, 0.0)
        return round(value, 8)

    def rebalance(self, active_ratio: float = 0.60) -> dict:
        ratio = min(max(float(active_ratio), 0.10), 0.90)
        target_active = self.total_balance * ratio
        delta = target_active - self.active_capital
        if delta > 0:
            move = min(delta, self.reserve_capital)
            self.reserve_capital -= move
            self.active_capital += move
            direction = "reserve_to_active"
        else:
            move = min(abs(delta), self.active_capital)
            self.active_capital -= move
            self.reserve_capital += move
            direction = "active_to_reserve"
        return {
            "direction": direction,
            "amount": round(move, 8),
            "target_active": round(target_active, 8),
        }

    def snapshot(self) -> dict:
        return asdict(self)
