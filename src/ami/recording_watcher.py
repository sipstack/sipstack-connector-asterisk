import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Set, Optional, List, Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent

logger = logging.getLogger(__name__)

class RecordingEventHandler(FileSystemEventHandler):
    """Handles file system events for recording files"""
    
    def __init__(self, recording_watcher):
        self.recording_watcher = recording_watcher
        
    def on_created(self, event):
        if not event.is_directory and self._is_recording_file(event.src_path):
            asyncio.create_task(self.recording_watcher._handle_new_recording(event.src_path))
            
    def on_modified(self, event):
        # Handle modified events for files that might still be writing
        if not event.is_directory and self._is_recording_file(event.src_path):
            asyncio.create_task(self.recording_watcher._handle_new_recording(event.src_path))
            
    def _is_recording_file(self, file_path: str) -> bool:
        """Check if file matches recording patterns"""
        return file_path.endswith(('.wav', '.mp3', '.gsm', '.ulaw', '.alaw'))

class RecordingWatcher:
    """Watches for new recording files and processes them"""
    
    def __init__(self, api_client, recording_config: Dict[str, Any]):
        self.api_client = api_client
        self.recording_config = recording_config
        self.watch_paths = recording_config.get('watch_paths', ['/var/spool/asterisk/monitor'])
        self.file_extensions = recording_config.get('file_extensions', ['.wav', '.mp3', '.gsm'])
        self.min_file_size = recording_config.get('min_file_size', 1024)  # 1KB minimum
        self.stabilization_time = recording_config.get('stabilization_time', 2.0)  # Wait for file to finish writing
        self.processed_files = set()
        self.processing_files = set()
        self.observer = None
        self.event_handler = RecordingEventHandler(self)
        
        # Filtering options
        self.filter_config = recording_config.get('filter', {})
        self.include_patterns = self.filter_config.get('include_patterns', [])
        self.exclude_patterns = self.filter_config.get('exclude_patterns', [])
        self.min_duration = self.filter_config.get('min_duration', 0)
        self.max_age_hours = self.filter_config.get('max_age_hours', 24)  # Only process files newer than this
        
    async def start(self):
        """Start watching for recording files"""
        logger.info("Starting recording file watcher")
        
        # Create observer
        self.observer = Observer()
        
        # Add watches for each configured path
        for path in self.watch_paths:
            if os.path.exists(path):
                self.observer.schedule(self.event_handler, path, recursive=True)
                logger.info(f"Watching for recordings in: {path}")
            else:
                logger.warning(f"Recording path does not exist: {path}")
                
        # Start the observer
        self.observer.start()
        
        # Process any existing files on startup if configured
        if self.recording_config.get('process_existing', False):
            await self._scan_existing_recordings()
            
    async def stop(self):
        """Stop watching for recording files"""
        if self.observer:
            logger.info("Stopping recording file watcher")
            self.observer.stop()
            self.observer.join()
            
    async def _scan_existing_recordings(self):
        """Scan for existing recordings on startup"""
        logger.info("Scanning for existing recordings")
        
        for watch_path in self.watch_paths:
            if not os.path.exists(watch_path):
                continue
                
            for root, dirs, files in os.walk(watch_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    if self._should_process_file(file_path):
                        await self._handle_new_recording(file_path)
                        
    async def _handle_new_recording(self, file_path: str):
        """Handle a new recording file"""
        try:
            # Skip if already processing or processed
            if file_path in self.processing_files or file_path in self.processed_files:
                return
                
            # Mark as processing
            self.processing_files.add(file_path)
            
            # Wait for file to stabilize (finish writing)
            await self._wait_for_file_stable(file_path)
            
            # Check if we should process this file
            if not self._should_process_file(file_path):
                logger.debug(f"Skipping recording file: {file_path}")
                return
                
            # Extract metadata from file path and name
            metadata = self._extract_metadata_from_path(file_path)
            
            # Process the recording
            logger.info(f"Processing new recording: {file_path}")
            await self._process_recording(file_path, metadata)
            
            # Mark as processed
            self.processed_files.add(file_path)
            
        except Exception as e:
            logger.error(f"Error handling recording file {file_path}: {e}")
        finally:
            # Remove from processing set
            self.processing_files.discard(file_path)
            
    async def _wait_for_file_stable(self, file_path: str):
        """Wait for file to finish writing"""
        if not os.path.exists(file_path):
            return
            
        last_size = -1
        stable_checks = 0
        max_wait_time = 30  # Maximum 30 seconds
        start_time = datetime.now()
        
        while stable_checks < 2 and (datetime.now() - start_time).total_seconds() < max_wait_time:
            try:
                current_size = os.path.getsize(file_path)
                if current_size == last_size:
                    stable_checks += 1
                else:
                    stable_checks = 0
                    last_size = current_size
                    
                await asyncio.sleep(self.stabilization_time)
            except OSError:
                # File might have been moved/deleted
                break
                
    def _should_process_file(self, file_path: str) -> bool:
        """Check if file should be processed based on filters"""
        # Check file extension
        if not any(file_path.endswith(ext) for ext in self.file_extensions):
            return False
            
        # Check file exists and size
        if not os.path.exists(file_path):
            return False
            
        try:
            file_size = os.path.getsize(file_path)
            if file_size < self.min_file_size:
                return False
                
            # Check file age
            if self.max_age_hours > 0:
                file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                age_hours = (datetime.now() - file_mtime).total_seconds() / 3600
                if age_hours > self.max_age_hours:
                    return False
                    
        except OSError:
            return False
            
        # Check include patterns
        if self.include_patterns:
            if not any(pattern in file_path for pattern in self.include_patterns):
                return False
                
        # Check exclude patterns
        if self.exclude_patterns:
            if any(pattern in file_path for pattern in self.exclude_patterns):
                return False
                
        return True
        
    def _extract_metadata_from_path(self, file_path: str) -> Dict[str, Any]:
        """Extract metadata from file path and name"""
        metadata = {
            'file_path': file_path,
            'file_name': os.path.basename(file_path),
            'file_size': os.path.getsize(file_path),
            'recording_type': 'monitor',
            'timestamp': datetime.now().isoformat(),
            'source': 'file_watcher'
        }
        
        # Try to extract additional info from filename
        # Common formats: 
        # - monitor/20240116-123456-1234567890.12345.wav
        # - monitor/queue-sales-20240116-123456-1234567890.12345.wav
        # - monitor/out-1234-20240116-123456-1234567890.12345.wav
        # - monitor/1234567890.12345.wav (simple format)
        
        filename = os.path.basename(file_path)
        # Remove extension
        name_without_ext = os.path.splitext(filename)[0]
        
        # Try to extract UniqueID using regex
        import re
        # Pattern for Asterisk UniqueID: digits.digits (e.g., 1234567890.12345)
        uniqueid_pattern = r'(\d{10,}\.\d+)'
        match = re.search(uniqueid_pattern, name_without_ext)
        if match:
            metadata['uniqueid'] = match.group(1)
            logger.debug(f"Extracted UniqueID from filename: {match.group(1)}")
        
        # Parse filename parts
        parts = name_without_ext.split('-')
        
        if len(parts) >= 3:
            # Check for queue recording
            if parts[0] == 'queue' and len(parts) >= 4:
                metadata['queue'] = parts[1]
                metadata['recording_type'] = 'queue'
            # Check for outbound recording
            elif parts[0] == 'out':
                metadata['direction'] = 'outbound'
                if len(parts) >= 2:
                    metadata['extension'] = parts[1]
            # Check for inbound recording
            elif parts[0] == 'in':
                metadata['direction'] = 'inbound'
                if len(parts) >= 2:
                    metadata['extension'] = parts[1]
        
        # Try to extract timestamp from filename
        # Pattern: YYYYMMDD-HHMMSS
        timestamp_pattern = r'(\d{8})-(\d{6})'
        ts_match = re.search(timestamp_pattern, name_without_ext)
        if ts_match:
            try:
                date_str = ts_match.group(1)
                time_str = ts_match.group(2)
                # Parse and format timestamp
                year = date_str[:4]
                month = date_str[4:6]
                day = date_str[6:8]
                hour = time_str[:2]
                minute = time_str[2:4]
                second = time_str[4:6]
                metadata['recording_timestamp'] = f"{year}-{month}-{day}T{hour}:{minute}:{second}"
            except:
                pass
                    
        return metadata
        
    async def _process_recording(self, file_path: str, metadata: Dict[str, Any]):
        """Process and upload recording file"""
        try:
            # Submit recording to the API
            logger.info(f"Uploading recording to API: {file_path}")
            await self.api_client.upload_recording(file_path, metadata)
            logger.info(f"Successfully uploaded recording: {file_path}")
            
            # Optionally delete file after successful upload
            if self.recording_config.get('delete_after_upload', False):
                try:
                    os.remove(file_path)
                    logger.info(f"Deleted recording after upload: {file_path}")
                except OSError as e:
                    logger.error(f"Failed to delete recording {file_path}: {e}")
                    
        except Exception as e:
            logger.error(f"Error processing recording {file_path}: {e}")