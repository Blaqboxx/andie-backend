"""
Exchange handler for interacting with cryptocurrency exchanges
"""
import ccxt
import logging
from typing import Dict, List, Optional, Any


logger = logging.getLogger(__name__)


class Exchange:
    """Wrapper for exchange operations using CCXT"""
    
    def __init__(self, exchange_name: str, config: Dict[str, Any], sandbox: bool = False):
        """
        Initialize exchange connection
        
        Args:
            exchange_name: Name of the exchange (e.g., 'binance', 'kraken')
            config: Exchange configuration including API credentials
            sandbox: Use sandbox/testnet mode if available
        """
        self.exchange_name = exchange_name
        
        # Get the exchange class
        exchange_class = getattr(ccxt, exchange_name)
        self.exchange = exchange_class(config)
        
        # Enable sandbox mode if requested
        if sandbox:
            self.exchange.set_sandbox_mode(True)
            logger.info(f"Sandbox mode enabled for {exchange_name}")
        
        logger.info(f"Connected to {exchange_name} exchange")
    
    def get_balance(self, currency: str = None) -> Dict[str, float]:
        """
        Get account balance
        
        Args:
            currency: Specific currency to get balance for (optional)
            
        Returns:
            Dictionary of balances
        """
        try:
            balance = self.exchange.fetch_balance()
            if currency:
                return {
                    'free': balance['free'].get(currency, 0),
                    'used': balance['used'].get(currency, 0),
                    'total': balance['total'].get(currency, 0)
                }
            return balance
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            raise
    
    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        # ...existing code...
