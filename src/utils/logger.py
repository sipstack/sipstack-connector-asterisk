import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any

def setup_logging(config: Dict[str, Any]) -> None:
    """
    Setup logging configuration
    
    Args:
        config: Logging configuration dictionary
    """
    level_name = config.get('level', 'INFO')
    level = getattr(logging, level_name.upper())
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Clear existing handlers to avoid duplicates
    if root_logger.handlers:
        root_logger.handlers.clear()
    
    # Add console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Add file handler if configured
    log_file = config.get('file')
    if log_file:
        # Create directory if it doesn't exist
        log_dir = os.path.dirname(log_file)
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
        
        # Set up rotating file handler
        max_size = config.get('max_size', 10 * 1024 * 1024)  # Default 10MB
        backup_count = config.get('backup_count', 5)
        
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_size,
            backupCount=backup_count
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    
    # Set library logging levels
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    
    # Set panoramisk to WARNING to reduce noise when in DEBUG mode
    if level == logging.DEBUG:
        logging.getLogger('panoramisk').setLevel(logging.WARNING)
        logging.getLogger('panoramisk.manager').setLevel(logging.WARNING)
        logging.getLogger('panoramisk.ami_protocol').setLevel(logging.WARNING)
    
    # Log startup message
    logging.info(f"Logging initialized at level {level_name}")