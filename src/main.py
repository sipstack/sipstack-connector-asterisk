#!/usr/bin/env python3
import asyncio
import logging
import signal
import sys
from pathlib import Path

from config.config_loader import load_config
from config.env_config import load_config_from_env
from ami.connector import AmiConnector
from api.unified_client import UnifiedApiClient
from utils.logger import setup_logging
from utils.metrics import initialize_metrics_server, record_ami_connection_status
from utils.compat import run_async
from __version__ import VERSION_STRING

# Global variables
config = None
ami_connector = None
api_client = None
shutdown_event = None

async def main():
    global config, ami_connector, api_client, shutdown_event
    
    try:
        # Create shutdown event
        shutdown_event = asyncio.Event()
        
        # Load configuration - prefer environment variables for Docker
        if len(sys.argv) > 1:
            # Config file provided as argument
            config_path = Path(sys.argv[1])
            config = load_config(config_path)
        else:
            # Try environment variables first (Docker mode)
            try:
                config = load_config_from_env()
            except ValueError as e:
                # Fall back to config file
                config_path = Path("/etc/sipstack-connector/config.yaml")
                if config_path.exists():
                    config = load_config(config_path)
                else:
                    raise ValueError(f"No configuration found. {e}")
        
        # Setup logging
        setup_logging(config.get('logging', {}))
        logger = logging.getLogger(__name__)
        logger.info(f"Starting {VERSION_STRING}")
        logger.info("Initializing services...")
        
        # Initialize metrics server if enabled
        if config.get('monitoring', {}).get('enabled', False):
            metrics_port = config.get('monitoring', {}).get('port', 8000)
            initialize_metrics_server(port=metrics_port)
            logger.info(f"Prometheus metrics endpoint available at http://localhost:{metrics_port}/metrics")
        
        # Initialize unified API client
        sentiment_config = None
        if 'api' in config:
            sentiment_config = {
                'enabled': True,
                'base_url': config['api']['url'],
                'token': config['api']['token'],
                'timeout': config['api'].get('timeout', 30),
                'retry_attempts': config['api'].get('retry_attempts', 3)
            }
            
        # Setup CDR configuration
        cdr_config = config.get('cdr', {})
        if 'api' in config and cdr_config.get('enabled', True):
            cdr_config.update({
                'api_base_url': config['api']['url'],
                'api_key': config['api']['token'],
                'timeout': config['api'].get('timeout', 30),
                'max_retries': config['api'].get('retry_attempts', 3)
            })
        
        api_client = UnifiedApiClient(
            sentiment_config=sentiment_config,
            cdr_config=cdr_config
        )
        
        # Start API clients
        await api_client.start()
        
        # Initialize AMI connector
        ami_connector = AmiConnector(
            host=config['ami']['host'],
            port=config['ami'].get('port', 5038),
            username=config['ami']['username'],
            password=config['ami']['password'],
            api_client=api_client,
            recording_config=config.get('recordings', {}),
            voicemail_config=config.get('voicemail', {}),
            cdr_config=config.get('cdr', {})
        )
        
        # Setup signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda s, f: asyncio.create_task(shutdown()))
        
        # Connect to AMI
        connected = await ami_connector.connect()
        
        # Update connection status in metrics
        if config.get('monitoring', {}).get('enabled', False):
            record_ami_connection_status(connected)
        
        # Wait for shutdown signal
        await shutdown_event.wait()
        
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        # Cleanup
        if ami_connector:
            await ami_connector.disconnect()
            
            # Update connection status in metrics
            if config and config.get('monitoring', {}).get('enabled', False):
                record_ami_connection_status(False)
                
        if api_client:
            await api_client.close()
                
        logging.info("Asterisk Sentiment Connector shutdown complete")

async def shutdown():
    logging.info("Shutdown requested")
    shutdown_event.set()

if __name__ == "__main__":
    run_async(main())