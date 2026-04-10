#!/usr/bin/env python3
"""
Simple test to verify bot components work correctly
"""
import sys
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_config():
    """Test configuration loading"""
    logger.info("Testing configuration...")
    from cryptonia.config import Config
    
    try:
        config = Config('config.yaml')
        assert config.get('exchange.name') == 'binance'
        assert config.get('trading.initial_capital') == 10000
        logger.info("✓ Configuration loaded successfully")
        return True
    except Exception as e:
        logger.error(f"✗ Configuration test failed: {e}")
        return False


def test_strategy():
    """Test strategy creation and analysis"""
    logger.info("Testing strategies...")
    from cryptonia.strategy import create_strategy, SMACrossoverStrategy, RSIStrategy
    from cryptonia.config import Config
    import numpy as np
    
    try:
        config = Config('config.yaml')
        
        # Test SMA strategy
        strategy = create_strategy(config.config)
        assert isinstance(strategy, SMACrossoverStrategy)
        
        # Create sample OHLCV data (100 candles)
        np.random.seed(42)
        base_price = 50000
        base_timestamp = int(datetime.now().timestamp() * 1000)
        ohlcv_data = []
        for i in range(100):
            price = base_price + np.random.randn() * 100 + i * 10  # Uptrend
            ohlcv_data.append([
                base_timestamp + (i * 3600000),  # timestamp (increment by 1 hour)
                price,  # open
                price + abs(np.random.randn() * 50),  # high
                price - abs(np.random.randn() * 50),  # low
                price,  # close
                np.random.rand() * 100  # volume
            ])
        
        # Test analysis