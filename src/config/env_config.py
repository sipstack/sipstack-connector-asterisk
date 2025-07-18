"""
Environment-based configuration for Docker deployment
"""
import os
import socket
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
    
    # Get hostname - try environment variable first, then fall back to system hostname
    hostname = os.getenv('HOST_HOSTNAME', '').strip()
    if not hostname:
        try:
            # Try to get the actual system hostname
            hostname = socket.gethostname()
            # If we're in a container, this might return the container ID
            # Try to read from /etc/hostname if it exists
            if os.path.exists('/etc/hostname'):
                with open('/etc/hostname', 'r') as f:
                    hostname = f.read().strip()
        except Exception as e:
            logger.warning(f"Failed to get hostname: {e}")
            hostname = 'unknown'
    
    config = {
        'hostname': hostname,  # Host that's running this connector
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
            'mode': os.getenv('CDR_MODE', 'batch'),  # 'batch' or 'direct'
            'batch_size': int(os.getenv('CDR_BATCH_SIZE', '200')),
            'batch_timeout': int(os.getenv('CDR_BATCH_TIMEOUT', '30')),
            'batch_force_timeout': int(os.getenv('CDR_BATCH_FORCE_TIMEOUT', '5')),  # Force flush interval
            'queue_size': int(os.getenv('CDR_QUEUE_SIZE', '10000')),
            'max_retries': int(os.getenv('CDR_MAX_RETRIES', '3')),
            'max_concurrent': int(os.getenv('CDR_MAX_CONCURRENT', '10')),  # For direct mode
            # Filtering options
            'filter': {
                'enabled': os.getenv('CDR_FILTER_ENABLED', 'true').lower() == 'true',
                'queue_attempts': os.getenv('CDR_FILTER_QUEUE_ATTEMPTS', 'false').lower() == 'true',
                'zero_duration': os.getenv('CDR_FILTER_ZERO_DURATION', 'false').lower() == 'true',
                'internal_only': os.getenv('CDR_FILTER_INTERNAL_ONLY', 'false').lower() == 'true',
                'min_duration': int(os.getenv('CDR_FILTER_MIN_DURATION', '0')),  # Minimum duration in seconds
                'exclude_destinations': [d.strip() for d in os.getenv('CDR_FILTER_EXCLUDE_DST', 'h').split(',') if d.strip()],  # Comma-separated list
            }
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
            'paths': os.getenv('RECORDING_PATHS', '/var/spool/asterisk/recording').split(','),
            'watcher_enabled': os.getenv('RECORDING_WATCHER_ENABLED', 'false').lower() == 'true',
            'watch_paths': os.getenv('RECORDING_WATCH_PATHS', '/var/spool/asterisk/monitor').split(','),
            'file_extensions': [f'.{ext.strip()}' if not ext.strip().startswith('.') else ext.strip() 
                                for ext in os.getenv('RECORDING_FILE_EXTENSIONS', 'wav,mp3,gsm').split(',')],
            'min_file_size': int(os.getenv('RECORDING_MIN_FILE_SIZE', '1024')),  # 1KB minimum
            'stabilization_time': float(os.getenv('RECORDING_STABILIZATION_TIME', '2.0')),
            'process_existing': os.getenv('RECORDING_PROCESS_EXISTING', 'false').lower() == 'true',
            'delete_after_upload': os.getenv('RECORDING_DELETE_AFTER_UPLOAD', 'false').lower() == 'true',
            'filter': {
                'include_patterns': os.getenv('RECORDING_INCLUDE_PATTERNS', '').split(',') if os.getenv('RECORDING_INCLUDE_PATTERNS') else [],
                'exclude_patterns': os.getenv('RECORDING_EXCLUDE_PATTERNS', '').split(',') if os.getenv('RECORDING_EXCLUDE_PATTERNS') else [],
                'min_duration': int(os.getenv('RECORDING_MIN_DURATION', '0')),
                'max_age_hours': int(os.getenv('RECORDING_MAX_AGE_HOURS', '24'))
            }
        },
        'voicemail': {
            'enabled': os.getenv('VOICEMAIL_ENABLED', 'false').lower() == 'true',
            'spool_dir': os.getenv('VOICEMAIL_SPOOL_DIR', '/var/spool/asterisk/voicemail')
        }
    }
    
    # Log configuration (without sensitive data)
    logger.info(f"Configuration loaded from environment:")
    logger.info(f"  Hostname: {hostname}")
    logger.info(f"  Region: {region}")
    logger.info(f"  API URL: {api_base_url}")
    logger.info(f"  AMI Host: {config['ami']['host']}:{config['ami']['port']}")
    logger.info(f"  CDR Batch Size: {config['cdr']['batch_size']}")
    logger.info(f"  Monitoring: {'Enabled' if config['monitoring']['enabled'] else 'Disabled'}")
    
    return config