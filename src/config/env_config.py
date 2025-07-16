"""
Environment-based configuration for Docker deployment
"""
import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def load_config_from_env() -> Dict[str, Any]:
    """Load configuration from environment variables"""
    
    # Required environment variables
    required_vars = ['API_KEY', 'AMI_HOST', 'AMI_USERNAME', 'AMI_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
    
    # Get region and construct API URL
    region = os.getenv('REGION', 'us1')
    api_base_url = f"https://api-{region}.sipstack.com/v1"
    
    config = {
        'ami': {
            'host': os.getenv('AMI_HOST'),
            'port': int(os.getenv('AMI_PORT', '5038')),
            'username': os.getenv('AMI_USERNAME'),
            'password': os.getenv('AMI_PASSWORD')
        },
        'api': {
            'url': api_base_url,
            'token': os.getenv('API_KEY'),
            'timeout': int(os.getenv('API_TIMEOUT', '30')),
            'retry_attempts': int(os.getenv('API_RETRY_ATTEMPTS', '3'))
        },
        'cdr': {
            'enabled': os.getenv('CDR_ENABLED', 'true').lower() == 'true',
            'batch_size': int(os.getenv('CDR_BATCH_SIZE', '100')),
            'batch_timeout': int(os.getenv('CDR_BATCH_TIMEOUT', '30')),
            'queue_size': int(os.getenv('CDR_QUEUE_SIZE', '10000')),
            'max_retries': int(os.getenv('CDR_MAX_RETRIES', '3'))
        },
        'logging': {
            'level': os.getenv('LOG_LEVEL', 'INFO'),
            'file': os.getenv('LOG_FILE'),  # Optional file logging
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        },
        'monitoring': {
            'enabled': os.getenv('MONITORING_ENABLED', 'true').lower() == 'true',
            'port': int(os.getenv('MONITORING_PORT', '8000'))
        },
        'recordings': {
            'enabled': os.getenv('RECORDINGS_ENABLED', 'false').lower() == 'true',
            'paths': os.getenv('RECORDING_PATHS', '/var/spool/asterisk/recording').split(',')
        },
        'voicemail': {
            'enabled': os.getenv('VOICEMAIL_ENABLED', 'false').lower() == 'true',
            'spool_dir': os.getenv('VOICEMAIL_SPOOL_DIR', '/var/spool/asterisk/voicemail')
        }
    }
    
    # Log configuration (without sensitive data)
    logger.info(f"Configuration loaded from environment:")
    logger.info(f"  Region: {region}")
    logger.info(f"  API URL: {api_base_url}")
    logger.info(f"  AMI Host: {config['ami']['host']}:{config['ami']['port']}")
    logger.info(f"  CDR Batch Size: {config['cdr']['batch_size']}")
    logger.info(f"  Monitoring: {'Enabled' if config['monitoring']['enabled'] else 'Disabled'}")
    
    return config