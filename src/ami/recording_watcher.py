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
            # Schedule the async task in the main event loop
            loop = self.recording_watcher.loop
            if loop and not loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    self.recording_watcher._handle_new_recording(event.src_path), 
                    loop
                )
            
    def on_modified(self, event):
        # Handle modified events for files that might still be writing
        if not event.is_directory and self._is_recording_file(event.src_path):
            # Schedule the async task in the main event loop
            loop = self.recording_watcher.loop
            if loop and not loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    self.recording_watcher._handle_new_recording(event.src_path), 
                    loop
                )
            
    def _is_recording_file(self, file_path: str) -> bool:
        """Check if file matches recording patterns from configuration"""
        return any(file_path.lower().endswith(ext.lower()) for ext in self.recording_watcher.file_extensions)

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
        self.loop = None  # Will be set when start() is called
        
        # Filtering options
        self.filter_config = recording_config.get('filter', {})
        self.include_patterns = self.filter_config.get('include_patterns', [])
        self.exclude_patterns = self.filter_config.get('exclude_patterns', [])
        self.min_duration = self.filter_config.get('min_duration', 0)
        self.max_age_hours = self.filter_config.get('max_age_hours', 24)  # Only process files newer than this
        
    async def start(self):
        """Start watching for recording files"""
        logger.info("Starting recording file watcher")
        
        # Store the current event loop for thread-safe callbacks
        self.loop = asyncio.get_event_loop()
        
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
        # Check file extension (case-insensitive)
        if not any(file_path.lower().endswith(ext.lower()) for ext in self.file_extensions):
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
            'timestamp': datetime.now().isoformat(),
            'source': 'file_watcher'
        }
        
        filename = os.path.basename(file_path)
        # Remove extension
        name_without_ext = os.path.splitext(filename)[0]
        
        # PRIMARY METHOD: Extract Asterisk UniqueID
        # This is the most reliable way to link recordings to CDRs
        import re
        # Pattern for Asterisk UniqueID: digits.digits (e.g., 1234567890.12345)
        uniqueid_pattern = r'(\d{10,}\.\d+)'
        match = re.search(uniqueid_pattern, name_without_ext)
        if match:
            metadata['uniqueid'] = match.group(1)
            metadata['call_id'] = match.group(1)  # Also set call_id for API compatibility
            logger.debug(f"Extracted UniqueID from filename: {match.group(1)}")
        
        # SECONDARY: Try to extract any additional context from the path/filename
        # But don't rely on specific formats since they vary
        
        # Check if it's in a queue directory
        if '/queue' in file_path.lower():
            metadata['recording_type'] = 'queue'
            # Try to extract queue name from path
            path_parts = file_path.split('/')
            for i, part in enumerate(path_parts):
                if part.lower() == 'queues' and i + 1 < len(path_parts):
                    metadata['queue'] = path_parts[i + 1]
                    break
        
        # Check common prefixes
        parts = name_without_ext.split('-')
        if parts[0].lower() == 'queue':
            metadata['recording_type'] = 'queue'
            if len(parts) > 1:
                metadata['queue'] = parts[1]
        elif parts[0].lower() in ['out', 'outbound']:
            metadata['direction'] = 'outbound'
        elif parts[0].lower() in ['in', 'inbound']:
            metadata['direction'] = 'inbound'
        
        # Try to extract a timestamp (various formats)
        # Format 1: YYYYMMDD-HHMMSS
        timestamp_pattern = r'(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})'
        ts_match = re.search(timestamp_pattern, name_without_ext)
        if ts_match:
            try:
                year, month, day, hour, minute, second = ts_match.groups()
                metadata['recording_timestamp'] = f"{year}-{month}-{day}T{hour}:{minute}:{second}"
            except:
                pass
        
        # Format 2: YYYY-MM-DD-HH-MM-SS
        timestamp_pattern2 = r'(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})'
        ts_match2 = re.search(timestamp_pattern2, name_without_ext)
        if not ts_match and ts_match2:
            try:
                year, month, day, hour, minute, second = ts_match2.groups()
                metadata['recording_timestamp'] = f"{year}-{month}-{day}T{hour}:{minute}:{second}"
            except:
                pass
        
        # Try to extract phone numbers from filename
        # Pattern: phone-number-phone (e.g., 4164772004-0-2025-07-18...)
        phone_pattern = r'^(\d{10,})-(\d+)-'
        phone_match = re.match(phone_pattern, name_without_ext)
        if phone_match:
            metadata['caller_id_num'] = phone_match.group(1)
            # The second number might be an extension or destination
            if phone_match.group(2) != '0':
                metadata['connected_line_num'] = phone_match.group(2)
        
        # Enhanced extraction for complex filenames - flexible parsing for different Asterisk sources
        # Split filename into parts for analysis
        parts = name_without_ext.split('-')
        
        # Extract ALL phone numbers found in the filename (10-11 digits, but not timestamps)
        phone_numbers = []
        phone_pattern = r'\b(\d{10,11})\b'
        phone_matches = re.findall(phone_pattern, name_without_ext)
        if phone_matches:
            # Filter out numbers that are part of timestamps (unix epoch)
            filtered_numbers = []
            for num in phone_matches:
                # Skip if it's likely a unix timestamp (starts with 17 or 16 for recent years)
                if not (num.startswith('17') or num.startswith('16')):
                    filtered_numbers.append(num)
                # But keep it if it's exactly 11 digits (likely a phone number with country code)
                elif len(num) == 11:
                    filtered_numbers.append(num)
            
            phone_numbers = list(set(filtered_numbers))  # Remove duplicates
            
            # Enhanced phone number assignment with src/dst logic
            if len(phone_numbers) >= 1:
                # First number is typically the source (caller)
                if 'caller_id_num' not in metadata:
                    metadata['caller_id_num'] = phone_numbers[0]
                    metadata['src_number'] = phone_numbers[0]  # For API consistency
                    
            if len(phone_numbers) >= 2:
                # Second number is typically the destination (called)
                if 'connected_line_num' not in metadata:
                    metadata['connected_line_num'] = phone_numbers[1]
                    metadata['dst_number'] = phone_numbers[1]  # For API consistency
                    
            # Store all found numbers for reference
            if len(phone_numbers) > 2:
                metadata['additional_numbers'] = phone_numbers[2:]
                
        # Try to extract destination number from specific patterns
        # Pattern for direct dial: SRC-DST-timestamp (e.g., 4161234567-2125551234-20250718...)
        direct_pattern = r'^(\d{10,})-(\d{10,})-'
        direct_match = re.match(direct_pattern, name_without_ext)
        if direct_match:
            metadata['src_number'] = direct_match.group(1)
            metadata['dst_number'] = direct_match.group(2)
            metadata['caller_id_num'] = direct_match.group(1)
            metadata['connected_line_num'] = direct_match.group(2)
            
        # Pattern for extension-to-extension: ext-SRC-ext-DST (e.g., ext-101-ext-102-...)
        ext_pattern = r'ext-(\d{3,4})-ext-(\d{3,4})'
        ext_match = re.search(ext_pattern, name_without_ext, re.IGNORECASE)
        if ext_match:
            metadata['src_extension'] = ext_match.group(1)
            metadata['dst_extension'] = ext_match.group(2)
            metadata['src_number'] = ext_match.group(1)
            metadata['dst_number'] = ext_match.group(2)
            metadata['recording_type'] = 'extension'
            
        # Pattern for queue recordings with agent extension: queue-NAME-CALLER-agent-EXT
        queue_agent_pattern = r'queue-[^-]+-(\d{10,})-agent-(\d{3,4})'
        queue_agent_match = re.search(queue_agent_pattern, name_without_ext, re.IGNORECASE)
        if queue_agent_match:
            metadata['src_number'] = queue_agent_match.group(1)
            metadata['dst_number'] = queue_agent_match.group(2)
            metadata['caller_id_num'] = queue_agent_match.group(1)
            metadata['agent_extension'] = queue_agent_match.group(2)
            
        # If we have caller_id_num but no src_number, copy it
        if 'caller_id_num' in metadata and 'src_number' not in metadata:
            metadata['src_number'] = metadata['caller_id_num']
            
        # If we have connected_line_num but no dst_number, copy it
        if 'connected_line_num' in metadata and 'dst_number' not in metadata:
            metadata['dst_number'] = metadata['connected_line_num']
        
        # Look for potential tenant/company names
        # These are non-numeric, non-date parts that could be identifiers
        potential_tenants = []
        for part in parts:
            if (part and 
                not part.isdigit() and 
                part.lower() not in ['queue', 'out', 'in', 'outbound', 'inbound', 'ivr', 'vm', 'voicemail'] and
                not re.match(r'^\d{4}$', part) and  # Not a year
                not re.match(r'^\d{1,2}$', part) and  # Not month/day/hour/min/sec
                not re.match(r'^[0-9a-f]{8,}$', part.lower()) and  # Not a hex ID
                len(part) > 2):  # Meaningful name
                potential_tenants.append(part)
        
        # If we found potential tenant names, use the most likely one
        if potential_tenants:
            # Prefer names that appear multiple times or near the end of filename
            # (before the UniqueID)
            if 'uniqueid' in metadata:
                uid_pos = name_without_ext.find(metadata['uniqueid'])
                if uid_pos > 0:
                    before_uid = name_without_ext[:uid_pos].rstrip('-.')
                    # Check which tenant name appears closest to UniqueID
                    for tenant in reversed(potential_tenants):
                        if tenant in before_uid:
                            metadata['tenant_name'] = tenant
                            break
            else:
                # No UniqueID, just use the first potential tenant
                metadata['tenant_name'] = potential_tenants[0]
        
        # For queue recordings, try to extract queue-specific info
        if metadata.get('recording_type') == 'queue' or 'queue' in name_without_ext.lower():
            metadata['recording_type'] = 'queue'
            
            # Look for queue name after "queue-" prefix - multiple patterns
            if 'queue' not in metadata:
                # Pattern 1: queue-QUEUENAME-... (e.g., queue-global-gconnect-...)
                queue_match = re.search(r'queue-([^-]+)', name_without_ext, re.IGNORECASE)
                if queue_match:
                    metadata['queue'] = queue_match.group(1)
                
                # Pattern 2: /queues/QUEUENAME/ in path (e.g., /var/spool/asterisk/monitor/queues/global-gconnect/)
                elif '/queues/' in file_path.lower():
                    path_parts = file_path.split('/')
                    for i, part in enumerate(path_parts):
                        if part.lower() == 'queues' and i + 1 < len(path_parts):
                            queue_dir = path_parts[i + 1]
                            # Extract queue name from directory (handle formats like "global-gconnect")
                            if '-' in queue_dir:
                                metadata['queue'] = queue_dir.split('-')[0]  # Take first part
                            else:
                                metadata['queue'] = queue_dir
                            break
                
                # Pattern 3: Look for queue name in the middle of filename (e.g., ...global-gconnect-...)
                elif not queue_match:
                    # Try to find queue-like patterns in the filename
                    potential_queues = []
                    for part in parts:
                        if (part and 
                            not part.isdigit() and 
                            part.lower() not in ['queue', 'gconnect', 'recording', 'monitor'] and
                            not re.match(r'^\d{4}$', part) and  # Not a year
                            not re.match(r'^\d{1,2}$', part) and  # Not month/day/hour
                            len(part) > 2 and len(part) < 20):  # Reasonable queue name length
                            potential_queues.append(part)
                    
                    if potential_queues:
                        metadata['queue'] = potential_queues[0]  # Use first potential queue name
        
        # Extract extensions (3-4 digit numbers that aren't years)
        extension_pattern = r'\b(\d{3,4})\b'
        extension_matches = re.findall(extension_pattern, name_without_ext)
        extensions = []
        for ext in extension_matches:
            # Filter out years and other non-extension numbers
            if not (2000 <= int(ext) <= 2100):  # Not a year
                extensions.append(ext)
        
        if extensions:
            metadata['extensions'] = extensions
            # If we don't have a connected_line_num, check if an extension could be it
            if 'connected_line_num' not in metadata and len(extensions) > 0:
                metadata['connected_line_num'] = extensions[0]
        
        # Extract any session/call identifiers that look like UUIDs or hex strings
        # Pattern: 8+ char hex string (e.g., 0242036ff24c)
        hex_pattern = r'([0-9a-f]{8,})'
        hex_matches = re.findall(hex_pattern, name_without_ext.lower())
        if hex_matches:
            # Filter out phone numbers that happen to be all digits
            session_ids = []
            for hex_id in hex_matches:
                if not hex_id.isdigit() or len(hex_id) not in [10, 11]:  # Not a phone number
                    session_ids.append(hex_id)
            if session_ids:
                metadata['session_ids'] = session_ids
        
        # Extract agent/user IDs if present (common patterns)
        # Look for patterns like agent-XXX, user-XXX, ext-XXX
        agent_pattern = r'(?:agent|user|ext|extension)[_-]?(\w+)'
        agent_match = re.search(agent_pattern, name_without_ext, re.IGNORECASE)
        if agent_match:
            metadata['agent_id'] = agent_match.group(1)
            
        # Fallback: Check directory path for additional context
        # Some Asterisk setups put phone numbers or tenant info in directory names
        dir_path = os.path.dirname(file_path)
        dir_parts = dir_path.split('/')
        
        # Look for phone numbers in directory path if not found in filename
        if 'caller_id_num' not in metadata:
            for part in dir_parts:
                if re.match(r'^\d{10,11}$', part):
                    metadata['caller_id_num'] = part
                    break
                    
        # Look for tenant name in directory path if not found in filename
        if 'tenant_name' not in metadata:
            # Common patterns: /tenants/TENANT_NAME/ or /customers/TENANT_NAME/
            for i, part in enumerate(dir_parts):
                if part.lower() in ['tenant', 'tenants', 'customer', 'customers', 'client', 'clients'] and i + 1 < len(dir_parts):
                    candidate = dir_parts[i + 1]
                    if candidate and not candidate.isdigit() and len(candidate) > 2:
                        metadata['tenant_name'] = candidate
                        break
                        
        # Store the full path info for debugging
        metadata['recording_path'] = file_path
                    
        return metadata
        
    async def _process_recording(self, file_path: str, metadata: Dict[str, Any]):
        """Process and upload recording file"""
        try:
            # Submit recording to the API
            logger.info(f"Uploading recording to API: {file_path}")
            logger.debug(f"Recording metadata: {metadata}")
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