"""
Configuration loader for Cryptonia trading bot
"""
import os
import yaml
from dotenv import load_dotenv
from typing import Dict, Any


class Config:
    """Configuration manager for the trading bot"""
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialize configuration
        
        Args:
            config_path: Path to the YAML configuration file
        """
        # Load environment variables
        load_dotenv()
        
        # Load YAML configuration
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        # Override with environment variables if available
        self.api_key = os.getenv('API_KEY')
        self.api_secret = os.getenv('API_SECRET')
        self.exchange_name = os.getenv('EXCHANGE', self.config['exchange']['name'])
        
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key"""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k, default)
            else:
                return default
        return value
    
    def get_exchange_config(self) -> Dict[str, Any]:
        """Get exchange configuration"""
        return {
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
            }
        }
