#!/usr/bin/env python3
"""
Example script demonstrating Cryptonia bot usage
"""
import logging
from cryptonia.bot import TradingBot

# Setup simple logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    """
    Example: Running the bot in dry-run mode
    """
    logger.info("Starting Cryptonia example...")
    
    # Initialize the bot in dry-run mode
    bot = TradingBot(config_path='config.yaml', dry_run=True)
    
    # Run a single iteration
    logger.info("Running single iteration...")
    bot.run_iteration()
    
    # Get balance (in dry-run mode, this will use sandbox/testnet)
    try:
        balance = bot.get_balance('USDT')
        logger.info(f"Current USDT balance: {balance:.2f}")
    except Exception as e:
        logger.warning(f"Could not fetch balance: {e}")
        logger.info("This is expected if API credentials are not configured")
    
    # Show current positions
    if bot.positions:
        logger.info(f"Open positions: {len(bot.positions)}")
        for symbol, position in bot.positions.items():
            logger.info(f"  {position}")
    else:
        logger.info("No open positions")
    
    logger.info("Example completed!")


if __name__ == '__main__':
    main()
