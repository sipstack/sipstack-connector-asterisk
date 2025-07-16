import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional

import yaml

logger = logging.getLogger(__name__)

def load_config(config_path: Path) -> Dict[str, Any]:
    """
    Load configuration from a YAML file
    
    Args:
        config_path: Path to the configuration file
        
    Returns:
        Dict containing configuration
        
    Raises:
        FileNotFoundError: If the config file doesn't exist
        ValueError: If the config is invalid
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
            
        # Validate essential configuration
        validate_config(config)
        
        return config
    
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in configuration file: {e}")
    
def validate_config(config: Dict[str, Any]) -> None:
    """
    Validate that the configuration contains all required fields
    
    Args:
        config: Configuration dictionary to validate
        
    Raises:
        ValueError: If any required configuration is missing
    """
    # Check AMI configuration
    if 'ami' not in config:
        raise ValueError("AMI configuration is missing")
    
    required_ami_fields = ['host', 'username', 'password']
    for field in required_ami_fields:
        if field not in config['ami']:
            raise ValueError(f"Required AMI configuration missing: {field}")
    
    # Check API configuration
    if 'api' not in config:
        raise ValueError("API configuration is missing")
    
    required_api_fields = ['url', 'token']
    for field in required_api_fields:
        if field not in config['api']:
            raise ValueError(f"Required API configuration missing: {field}")
    
    # Validate recording paths exist if specified
    if 'recordings' in config and 'paths' in config['recordings']:
        for path in config['recordings']['paths']:
            if not os.path.isdir(path):
                logger.warning(f"Recording path does not exist: {path}")
    
    # Validate voicemail paths exist if specified
    if 'voicemail' in config and 'paths' in config['voicemail']:
        for path in config['voicemail']['paths']:
            if not os.path.isdir(path):
                logger.warning(f"Voicemail path does not exist: {path}")

def get_default_config() -> Dict[str, Any]:
    """
    Get a default configuration with sensible defaults
    """
    return {
        'ami': {
            'host': 'localhost',
            'port': 5038,
            'username': 'admin',
            'password': 'password',
        },
        'api': {
            'url': 'https://api.example.com/sip-scribe/sentiment',
            'token': 'YOUR_API_TOKEN',
            'timeout': 30,
            'retry_attempts': 3
        },
        'recordings': {
            'paths': ['/var/spool/asterisk/monitor'],
            'process_all_calls': False,
            'queue_whitelist': [],
            'queue_blacklist': []
        },
        'voicemail': {
            'enabled': True,
            'paths': ['/var/spool/asterisk/voicemail']
        },
        'logging': {
            'level': 'INFO',
            'file': '/var/log/asterisk-sentiment-connector/connector.log',
            'max_size': 10485760,  # 10 MB
            'backup_count': 5
        },
        'monitoring': {
            'enabled': False,
            'port': 8000,
            'endpoint': '/metrics'
        }
    }