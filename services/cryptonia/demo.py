#!/usr/bin/env python3
"""
Demo script showing bot workflow with simulated market data
"""
import logging
import time
from datetime import datetime
import numpy as np

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def simulate_market_analysis():
    """Simulate market analysis"""
    logger.info("=" * 70)
    logger.info("📊 CRYPTONIA TRADING BOT DEMO")
    logger.info("=" * 70)
    logger.info("")
    
    logger.info("🔧 Initializing bot components...")
    time.sleep(1)
    logger.info("  ✓ Configuration loaded from config.yaml")
    logger.info("  ✓ Exchange API initialized (Binance)")
    logger.info("  ✓ Strategy: SMA Crossover (20/50 periods)")
    logger.info("  ✓ Risk Manager: 2% risk per trade, 2% stop-loss, 4% take-profit")
    logger.info("")
    
    logger.info("💰 Current Account Status:")
    logger.info("  Balance: $10,000.00 USDT")
    logger.info("  Open Positions: 0")
    logger.info("  Available Slots: 3")
    logger.info("")
    
    # Simulate monitoring pairs
    pairs = ["BTC/USDT", "ETH/USDT"]
    
    for iteration in range(1, 4):
        logger.info(f"🔄 Iteration {iteration} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("-" * 70)
        
        for pair in pairs:
            logger.info(f"  📈 Analyzing {pair}...")
            time.sleep(0.5)
            
            # Simulate price data
            if pair == "BTC/USDT":
                price = 50000 + np.random.randn() * 500
                sma_short = 49800
                sma_long = 49500
            else:
                price = 3000 + np.random.randn() * 50
                sma_short = 2980
                sma_long = 2990
            
            logger.info(f"     Current Price: ${price:.2f}")