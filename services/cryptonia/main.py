#!/usr/bin/env python3
"""
Main entry point for Cryptonia trading bot
"""
import argparse
import logging
import os
import sys

from cryptonia.bot import TradingBot


def setup_logging(level: str = "INFO"):
    """Setup logging configuration"""
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)
    
    # Configure logging
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/trading.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Cryptonia - Cryptocurrency Trading Bot')
    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        default=True,
        help='Run in simulation mode without executing real trades (default: True)'
    )
    parser.add_argument(
        '--live',
        action='store_true',
        help='Run in live mode (executes real trades - use with caution!)'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=300,
        help='Time between iterations in seconds (default: 300)'
    )
    parser.add_argument(
        '--log-level',
        type=str,
        default='INFO',