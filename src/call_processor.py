"""
Main call processor that polls database and ships formatted data to API.
"""

import os
import sys
import json
import time
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import asdict

from database_connector import DatabaseConnector, CallData
from recording_linker import RecordingLinker

logger = logging.getLogger(__name__)

class CallProcessor:
    """
    Main processor that:
    1. Polls Asterisk database for CDR/CEL updates
    2. Formats data locally to call_logs structure
    3. Ships to API with progressive updates
    """
    
    def __init__(self, config: Dict):
        self.config = config
        self.db_connector = DatabaseConnector(config)
        self.recording_linker = RecordingLinker(config)
        
        # API configuration - use what was built in main_db.py
        self.api_url = config.get('API_URL', 'https://api-us1.sipstack.com/v1/mqs/connectors/asterisk/calls')
        self.api_key = config.get('API_KEY', '')
        # Use the full URL directly since it already includes the path
        self.api_endpoint = self.api_url
        
        # Processing configuration
        self.poll_interval = int(config.get('POLL_INTERVAL', 30))
        self.batch_size = int(config.get('BATCH_SIZE', 100))
        
        # Runtime state
        self.running = False
        # Don't use "now - 5 minutes", let the database_connector handle the startup time
        self.last_poll_time = None  # Will be set by database_connector's startup_time
        self.session: Optional[aiohttp.ClientSession] = None
        # Initialize calls_processed to prevent NameError in adaptive polling
        self.calls_processed = 0
        
        logger.info(f"Call processor initialized - polling every {self.poll_interval}s")
        logger.info(f"API endpoint: {self.api_endpoint}")
    
    async def start(self):
        """Start the processor loop"""
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info("Call processor started")
        
        try:
            while self.running:
                try:
                    await self.process_batch()
                    
                    # Adaptive polling - speed up if busy, slow down if quiet
                    if self.calls_processed > 10:
                        self.poll_interval = max(10, self.poll_interval - 5)
                    elif self.calls_processed == 0:
                        self.poll_interval = min(300, self.poll_interval + 10)
                    
                    await asyncio.sleep(self.poll_interval)
                    
                except Exception as e:
                    logger.error(f"Error in processing loop: {e}", exc_info=True)
                    await asyncio.sleep(30)  # Wait before retry
        
        finally:
            if self.session:
                await self.session.close()
    
    async def stop(self):
        """Stop the processor"""
        logger.info("Stopping call processor...")
        self.running = False
    
    async def process_batch(self):
        """Process a batch of calls"""
        start_time = time.time()
        self.calls_processed = 0
        
        # Use startup_time if we haven't set last_poll_time yet
        if self.last_poll_time is None:
            # Get the startup time from database connector (last CDR time)
            self.last_poll_time = self.db_connector._get_startup_time()
            if self.last_poll_time is None:
                # Fallback to now - 5 minutes if no startup time
                self.last_poll_time = datetime.now() - timedelta(minutes=5)
            logger.info(f"Initial poll time set to: {self.last_poll_time}")
        
        # First, retry any previously failed calls (up to 48 hours with exponential backoff)
        failed_linkedids = self.db_connector.get_failed_calls()
        if failed_linkedids:
            logger.info(f"🔄 Retrying {len(failed_linkedids)} previously failed calls")
            for linkedid in failed_linkedids:
                await self.process_single_call(linkedid, is_retry=True)
        
        # Get updated calls since last poll
        updated_linkedids = self.db_connector.get_updated_calls(
            self.last_poll_time, 
            limit=self.batch_size
        )
        
        if not updated_linkedids:
            # Silent when no calls found - no logging spam
            return
        
        logger.info(f"Found {len(updated_linkedids)} updated calls")
        
        # Process each call
        tasks = []
        for linkedid in updated_linkedids:
            task = self.process_single_call(linkedid)
            tasks.append(task)
        
        # Process in parallel with limited concurrency
        results = await self._gather_with_concurrency(tasks, max_concurrent=10)
        
        # Update last poll time to the latest CDR timestamp, not current time
        # Get the latest timestamp from processed calls
        latest_timestamp = self.last_poll_time
        with self.db_connector.get_db_connection() as conn:
            cursor = conn.cursor()
            if updated_linkedids:
                # Get max timestamp from the CDRs we just processed
                linkedids_str = ','.join(['%s'] * len(updated_linkedids))
                query = f"SELECT MAX(calldate) as max_time FROM {self.db_connector.cdr_table} WHERE linkedid IN ({linkedids_str})"
                cursor.execute(query, updated_linkedids)
                result = cursor.fetchone()
                if result:
                    max_time = result['max_time'] if isinstance(result, dict) else result[0]
                    if max_time:
                        latest_timestamp = max_time if isinstance(max_time, datetime) else datetime.fromisoformat(str(max_time))
        
        self.last_poll_time = latest_timestamp
        
        # Log statistics
        success_count = sum(1 for r in results if r)
        process_time = time.time() - start_time
        
        logger.info(f"Processed {success_count}/{len(updated_linkedids)} calls in {process_time:.2f}s")
        self.calls_processed = success_count
    
    async def process_single_call(self, linkedid: str, is_retry: bool = False) -> bool:
        """Process a single call"""
        try:
            if is_retry:
                logger.debug(f"Retrying call {linkedid}")
            # Get CDR and CEL data
            cdrs = self.db_connector.get_call_cdrs(linkedid)
            cels = self.db_connector.get_call_cels(linkedid)
            
            if not cdrs:
                logger.warning(f"No CDRs found for linkedid {linkedid}")
                return False
            
            # Debug: Log data counts
            logger.debug(f"Call {linkedid}: {len(cdrs)} CDRs, {len(cels)} CEL events")
            
            # Check if call is complete
            is_complete = self.db_connector.is_call_complete(linkedid, cdrs, cels)
            
            # Determine if we should ship this call
            should_ship, phase = self.db_connector.should_ship_call(
                linkedid, is_complete, len(cdrs), len(cels)
            )
            
            if not should_ship:
                logger.debug(f"Call {linkedid} doesn't need shipping")
                return False
            
            logger.info(f"Processing call {linkedid} - phase: {phase}, complete: {is_complete}")
            
            # Format call data
            call_data = self.db_connector.format_call_data(
                linkedid, cdrs, cels, is_complete, self.config
            )
            
            # Debug: Log call formatting details
            logger.debug(f"Call {linkedid}: direction={call_data.direction}, "
                        f"src={call_data.src_number or call_data.src_extension}, "
                        f"dst={call_data.dst_number or call_data.dst_extension}, "
                        f"duration={call_data.duration_seconds}s")
            
            # Link recordings if available
            recordings = await self.recording_linker.find_recordings(linkedid)
            if recordings:
                call_data.recording_files = recordings
                logger.info(f"Linked {len(recordings)} recordings to call {linkedid}")
            
            # Ship to API
            logger.debug(f"Shipping call {linkedid} (phase: {phase}) to API")
            success = await self.ship_call_data(call_data, phase)
            
            if success:
                # Track as processed
                self.db_connector.track_processed_call(
                    linkedid, is_complete, len(cdrs), len(cels), shipped=True
                )
                logger.info(f"Successfully shipped call {linkedid} ({phase})")
            else:
                logger.error(f"Failed to ship call {linkedid}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error processing call {linkedid}: {e}", exc_info=True)
            return False
    
    async def ship_call_data(self, call_data: CallData, phase: str) -> bool:
        """Ship formatted call data to API"""
        try:
            # Convert dataclass to dict
            payload = asdict(call_data)
            
            # Add phase information
            payload['ship_phase'] = phase
            payload['shipped_at'] = datetime.now().isoformat()
            
            # Remove None values
            payload = {k: v for k, v in payload.items() if v is not None}
            
            # Log payload size for debugging
            payload_size = len(json.dumps(payload))
            logger.debug(f"Shipping {payload_size} bytes for call {call_data.linkedid}")
            
            # Make API request
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
                'User-Agent': f'Asterisk-Connector/{self.config.get("CONNECTOR_VERSION", "2.2.0")}'
            }
            
            async with self.session.post(
                self.api_endpoint,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                
                if response.status in [200, 201, 202]:
                    logger.debug(f"API accepted call {call_data.linkedid} - status: {response.status}")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"API rejected call {call_data.linkedid} - status: {response.status}, error: {error_text}")
                    
                    # Track error in SQLite tracker (not MySQL)
                    try:
                        import sqlite3
                        tracker_db = self.db_connector.tracker_db
                        with sqlite3.connect(tracker_db) as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                UPDATE processed_calls
                                SET error_count = error_count + 1,
                                    last_error = ?
                                WHERE linkedid = ?
                            """, (error_text[:500], call_data.linkedid))
                            conn.commit()
                    except Exception as e:
                        logger.debug(f"Could not update error tracking: {e}")
                    
                    return False
        
        except asyncio.TimeoutError:
            logger.error(f"Timeout shipping call {call_data.linkedid}")
            return False
        
        except Exception as e:
            logger.error(f"Error shipping call {call_data.linkedid}: {e}", exc_info=True)
            return False
    
    async def _gather_with_concurrency(self, tasks, max_concurrent=10):
        """Execute tasks with limited concurrency"""
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def bounded_task(task):
            async with semaphore:
                return await task
        
        bounded_tasks = [bounded_task(task) for task in tasks]
        return await asyncio.gather(*bounded_tasks, return_exceptions=True)


class RecordingLinker:
    """Link recordings to calls using multiple methods"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.recording_paths = config.get('RECORDING_PATHS', '/var/spool/asterisk/monitor').split(',')
        self.mixmonitor_db = config.get('MIXMONITOR_DB', '/tmp/mixmonitor_tracking.db')
    
    async def find_recordings(self, linkedid: str) -> List[Dict]:
        """Find recordings for a call"""
        recordings = []
        
        # Method 1: Check recordings table in database if configured
        recordings_table = self.config.get('DB_TABLE_RECORDINGS', '')
        if recordings_table:
            try:
                with self.config.get('db_connector').get_db_connection() as conn:
                    cursor = conn.cursor()
                    query = f"""
                        SELECT filename, file_path, file_size, created_at
                        FROM {recordings_table}
                        WHERE linkedid = %s
                    """
                    cursor.execute(query, (linkedid,))
                    
                    for row in cursor.fetchall():
                        recordings.append({
                            'filename': row.get('filename') if isinstance(row, dict) else row[0],
                            'file_path': row.get('file_path') if isinstance(row, dict) else row[1],
                            'file_size': row.get('file_size') if isinstance(row, dict) else row[2],
                            'started_at': row.get('created_at') if isinstance(row, dict) else row[3],
                            'source': 'database'
                        })
            except Exception as e:
                # Silently skip if table doesn't exist
                pass
        
        # Method 2: Search by filename pattern
        for path in self.recording_paths:
            if not os.path.exists(path):
                continue
            
            # Look for files with linkedid in name
            pattern = f"*{linkedid}*"
            import glob
            for filepath in glob.glob(os.path.join(path, pattern)):
                if os.path.isfile(filepath):
                    recordings.append({
                        'filename': os.path.basename(filepath),
                        'file_path': filepath,
                        'file_size': os.path.getsize(filepath),
                        'source': 'filesystem_search'
                    })
        
        # Remove duplicates
        seen = set()
        unique_recordings = []
        for rec in recordings:
            key = rec['filename']
            if key not in seen:
                seen.add(key)
                unique_recordings.append(rec)
        
        return unique_recordings


async def main():
    """Main entry point"""
    # Setup logging
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Load configuration from environment
    config = {
        'DB_TYPE': os.getenv('DB_TYPE', 'mysql'),
        'DB_HOST': os.getenv('DB_HOST', 'localhost'),
        'DB_PORT': os.getenv('DB_PORT', '3306'),
        'DB_NAME': os.getenv('DB_NAME', 'asterisk'),
        'DB_USER': os.getenv('DB_USER', 'asterisk_reader'),
        'DB_PASSWORD': os.getenv('DB_PASSWORD', ''),
        'DB_TABLE_CDR': os.getenv('DB_TABLE_CDR', 'cdr'),
        
        # CEL Mode configuration (REQUIRED)
        'CEL_MODE': os.getenv('CEL_MODE', ''),  # Required: db, csv, or ami
        'DB_TABLE_CEL': os.getenv('DB_TABLE_CEL', 'cel'),
        'CEL_CSV_PATH': os.getenv('CEL_CSV_PATH', '/var/log/asterisk/cel-custom/Master.csv'),
        'CEL_CSV_POLL_INTERVAL': os.getenv('CEL_CSV_POLL_INTERVAL', '2'),
        'AMI_HOST': os.getenv('AMI_HOST', 'localhost'),
        'AMI_PORT': os.getenv('AMI_PORT', '5038'),
        'AMI_USERNAME': os.getenv('AMI_USERNAME', ''),
        'AMI_PASSWORD': os.getenv('AMI_PASSWORD', ''),
        
        'API_URL': os.getenv('API_URL', 'https://api.sipstack.com'),
        'API_KEY': os.getenv('API_KEY', ''),
        'CONNECTOR_VERSION': os.getenv('CONNECTOR_VERSION', '0.13.0'),
        'CUSTOMER_ID': int(os.getenv('CUSTOMER_ID', '0')),
        'TENANT': os.getenv('TENANT', ''),
        'HOSTNAME': os.getenv('HOSTNAME', os.uname().nodename),
        'POLL_INTERVAL': os.getenv('POLL_INTERVAL', '30'),
        'BATCH_SIZE': os.getenv('BATCH_SIZE', '100'),
        'RECORDING_PATHS': os.getenv('RECORDING_PATHS', '/var/spool/asterisk/monitor'),
        'MIXMONITOR_DB': os.getenv('MIXMONITOR_DB', '/tmp/mixmonitor_tracking.db'),
        'INCLUDE_RAW_DATA': os.getenv('INCLUDE_RAW_DATA', 'false').lower() == 'true',
    }
    
    # Validate required configuration
    if not config['API_KEY']:
        logger.error("API_KEY is required")
        sys.exit(1)
    
    if not config['CEL_MODE']:
        logger.error("CEL_MODE is required. Options: db, csv, ami")
        sys.exit(1)
    
    if config['CEL_MODE'] not in ['db', 'csv', 'ami']:
        logger.error(f"Invalid CEL_MODE: {config['CEL_MODE']}. Options: db, csv, ami")
        sys.exit(1)
    
    # Create and start processor
    processor = CallProcessor(config)
    
    try:
        await processor.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await processor.stop()


if __name__ == '__main__':
    asyncio.run(main())