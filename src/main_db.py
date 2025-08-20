#!/usr/bin/env python3
"""
Main entry point for database-driven connector (v0.13.2+)
Reads CDR from database and CEL from configured source (db/csv/ami)
"""
import asyncio
import logging
import signal
import sys
import os
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_paths = [
        Path.cwd() / '.env',
        Path('/etc/sipstack-connector/.env'),
        Path(__file__).parent.parent / '.env',
    ]
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            print(f"Loaded environment from {env_path}")
            break
except ImportError:
    pass

# Setup logging first with LOG_LEVEL support
log_level = os.getenv('LOG_LEVEL', 'INFO').strip().upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def main():
    """Main entry point for database connector"""
    try:
        # Read version from VERSION file
        version = "0.13.25"  # fallback with current version
        try:
            # Try multiple possible locations
            possible_paths = [
                Path(__file__).parent / 'VERSION',  # Same directory as script (Docker)
                Path(__file__).parent.parent / 'VERSION',  # Parent directory (local dev)
                Path('/app/VERSION'),  # Explicit Docker path
                Path('./VERSION'),  # Current working directory
            ]
            for version_file in possible_paths:
                if version_file.exists():
                    version = version_file.read_text().strip()
                    logger.info(f"Read version {version} from {version_file}")
                    break
            else:
                # If no VERSION file found, log which paths were checked
                logger.warning(f"VERSION file not found. Checked: {[str(p) for p in possible_paths]}")
                logger.warning(f"Using fallback version: {version}")
        except Exception as e:
            logger.error(f"Error reading VERSION file: {e}")
        
        # Log startup banner
        logger.info("=" * 60)
        logger.info(f"SIPSTACK Connector v{version} - Database Mode")
        logger.info("=" * 60)
        
        # Check for required environment variables (strip whitespace first)
        if not os.getenv('API_KEY', '').strip():
            logger.error("API_KEY is required")
            sys.exit(1)
            
        if not os.getenv('CEL_MODE', '').strip():
            logger.error("CEL_MODE is required. Options: db, csv, ami")
            sys.exit(1)
            
        if not os.getenv('DB_NAME', '').strip():
            logger.error("DB_NAME is required (e.g., 'asterisk')")
            sys.exit(1)
        
        # Build configuration from environment (strip all whitespace)
        config = {
            'DB_TYPE': os.getenv('DB_TYPE', 'mysql').strip(),
            'DB_HOST': os.getenv('DB_HOST', 'localhost').strip(),
            'DB_PORT': os.getenv('DB_PORT', '3306').strip(),
            'DB_NAME': os.getenv('DB_NAME', 'asterisk').strip(),
            'DB_USER': os.getenv('DB_USER', 'asterisk').strip(),
            'DB_PASSWORD': os.getenv('DB_PASSWORD', '').strip(),
            'DB_TABLE_CDR': os.getenv('DB_TABLE_CDR', 'cdr').strip(),
            'DB_TABLE_RECORDINGS': os.getenv('DB_TABLE_RECORDINGS', '').strip(),
            
            # CEL Mode configuration
            'CEL_MODE': os.getenv('CEL_MODE', '').strip(),
            'DB_TABLE_CEL': os.getenv('DB_TABLE_CEL', 'cel').strip(),
            'CEL_CSV_PATH': os.getenv('CEL_CSV_PATH', '/var/log/asterisk/cel-custom/Master.csv').strip(),
            'CEL_CSV_POLL_INTERVAL': os.getenv('CEL_CSV_POLL_INTERVAL', '2').strip(),
            'AMI_HOST': os.getenv('AMI_HOST', 'localhost').strip(),
            'AMI_PORT': os.getenv('AMI_PORT', '5038').strip(),
            'AMI_USERNAME': os.getenv('AMI_USERNAME', '').strip(),
            'AMI_PASSWORD': os.getenv('AMI_PASSWORD', '').strip(),
            
            # Build API URL based on REGION if API_ENDPOINT is not set
            'REGION': os.getenv('REGION', 'us1').strip(),
            'API_URL': os.getenv('API_ENDPOINT', os.getenv('API_URL', '')).strip(),
            'API_KEY': os.getenv('API_KEY', '').strip(),
            'CONNECTOR_VERSION': version,
            'CUSTOMER_ID': int(os.getenv('CUSTOMER_ID', '0')),
            'TENANT': os.getenv('TENANT', '').strip(),
            'HOSTNAME': os.getenv('HOSTNAME', os.uname().nodename).strip(),
            'POLL_INTERVAL': int(os.getenv('CDR_POLL_INTERVAL', '5')),
            'BATCH_SIZE': int(os.getenv('CDR_BATCH_SIZE', '100')),
            'RECORDING_PATHS': os.getenv('RECORDING_PATHS', '/var/spool/asterisk/monitor').strip(),
            'INCLUDE_RAW_DATA': os.getenv('INCLUDE_RAW_DATA', 'false').strip().lower() == 'true',
        }
        
        logger.info("Initializing database connector...")
        # Build API URL based on REGION if not explicitly set
        if not config['API_URL']:
            region = config['REGION'].lower()
            if region == 'dev':
                config['API_URL'] = 'https://api-dev.sipstack.com/v1/mqs/connectors/asterisk/calls'
            elif region in ['ca1', 'us1', 'us2']:
                config['API_URL'] = f'https://api-{region}.sipstack.com/v1/mqs/connectors/asterisk/calls'
            else:
                # Default to us1 for unknown regions
                config['API_URL'] = 'https://api-us1.sipstack.com/v1/mqs/connectors/asterisk/calls'
        
        # Ensure API URL ends with /calls if it doesn't already
        if config['API_URL'] and not config['API_URL'].endswith('/calls'):
            if '/v1/mqs/connectors/asterisk' in config['API_URL']:
                config['API_URL'] = config['API_URL'].rstrip('/') + '/calls'
        
        logger.info(f"Configuration:")
        logger.info(f"  Database: {config['DB_TYPE']} @ {config['DB_HOST']}:{config['DB_PORT']}")
        logger.info(f"  Database Name: {config['DB_NAME']}")
        logger.info(f"  CDR Table: {config['DB_TABLE_CDR']}")
        logger.info(f"  CEL Mode: '{config['CEL_MODE']}'")
        logger.info(f"  Region: {config['REGION']}")
        logger.info(f"  API Endpoint: {config['API_URL']}")
        
        # Import and start the call processor (which uses database_connector)
        from call_processor import CallProcessor
        
        processor = CallProcessor(config)
        
        # Setup signal handlers
        shutdown_event = asyncio.Event()
        
        def handle_signal():
            logger.info("Shutdown signal received")
            shutdown_event.set()
            
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal)
        
        # Start processor in background
        processor_task = asyncio.create_task(processor.start())
        
        # Wait for shutdown
        await shutdown_event.wait()
        
        # Stop processor
        logger.info("Shutting down...")
        await processor.stop()
        await processor_task
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Shutdown complete")

if __name__ == "__main__":
    asyncio.run(main())