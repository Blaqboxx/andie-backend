"""
Main trading bot implementation
"""
import logging
import time
from typing import Dict, List, Optional
from datetime import datetime

from cryptonia.config import Config
from cryptonia.exchange import Exchange
from cryptonia.strategy import create_strategy
from cryptonia.risk_manager import RiskManager


logger = logging.getLogger(__name__)


class Position:
    """Represents an open trading position"""
    
    def __init__(self, symbol: str, side: str, entry_price: float, 
                 size: float, stop_loss: float, take_profit: float):
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.size = size
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.entry_time = datetime.now()
        self.order_id = None
    
    def __repr__(self):
        return (f"Position({self.symbol}, {self.side}, "
                f"entry={self.entry_price:.2f}, size={self.size:.6f}, "
                f"sl={self.stop_loss:.2f}, tp={self.take_profit:.2f})")


class TradingBot:
    """Main trading bot class"""
    
    def __init__(self, config_path: str = "config.yaml", dry_run: bool = True):
        """
        Initialize trading bot
        
        Args:
            config_path: Path to configuration file
            dry_run: If True, simulate trades without executing
        """
        self.config = Config(config_path)
        self.dry_run = dry_run
        
        # Initialize components
        self.exchange = Exchange(
            self.config.exchange_name,
            self.config.get_exchange_config(),
            sandbox=self.config.get('exchange.sandbox', False)
        )
        
        self.strategy = create_strategy(self.config.config)
        self.risk_manager = RiskManager(self.config.config)
