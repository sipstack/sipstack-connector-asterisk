import asyncio
import logging
import os
import socket
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Any

import panoramisk

from utils.filters import is_queue_call, is_voicemail
from utils.metrics import (
    record_processed_recording, 
    record_queue_recording, 
    record_voicemail_recording,
    record_ami_connection_status
)
from .cdr_monitor import CDRMonitor
from .http_worker import HTTPWorker
from .direct_sender import DirectCDRSender
from .mixmonitor_tracker import mixmonitor_tracker
from recording_uploader import RecordingUploader

logger = logging.getLogger(__name__)

class AmiConnector:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        api_client,
        recording_config: Dict[str, Any],
        voicemail_config: Dict[str, Any],
        cdr_config: Optional[Dict[str, Any]] = None
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.api_client = api_client
        self.recording_config = recording_config
        self.voicemail_config = voicemail_config
        self.cdr_config = cdr_config or {}
        self.ami_client = None
        self.connected = False
        self.queue_whitelist = recording_config.get('queue_whitelist', [])
        self.queue_blacklist = recording_config.get('queue_blacklist', [])
        self.recording_paths = recording_config.get('paths', ['/var/spool/asterisk/monitor'])
        self.voicemail_paths = voicemail_config.get('paths', ['/var/spool/asterisk/voicemail'])
        
        # Initialize CDR processing components if enabled
        self.cdr_monitor = None
        self.http_worker = None
        self.cdr_queue = None
        self.upload_check_task = None
        
        # Recording uploader disabled in v0.9.0 - now using AMI MixMonitor events
        self.recording_uploader = None
        # Legacy cron-based uploader disabled - AMI events handle uploads immediately
        # if recording_config.get('upload_enabled', True):
        #     self.recording_uploader = RecordingUploader(interval_seconds=60)
        
        if self.cdr_config.get('enabled', False):
            # Create async queue for CDR/CEL records
            queue_size = self.cdr_config.get('queue_size', 10000)
            self.cdr_queue = asyncio.Queue(maxsize=queue_size)
            
            # Create CDR monitor that adds to queue
            self.cdr_monitor = CDRMonitor(
                queue=self.cdr_queue,
                max_queue_size=queue_size,
                filter_config=self.cdr_config.get('filter', {})
            )
            
            # Create worker based on mode
            cdr_mode = self.cdr_config.get('mode', 'batch')
            if cdr_mode == 'direct':
                # Use DirectCDRSender for immediate sending without batching
                self.http_worker = DirectCDRSender(
                    queue=self.cdr_queue,
                    api_client=self.api_client,
                    max_concurrent=self.cdr_config.get('max_concurrent', 10),
                    max_retries=self.cdr_config.get('max_retries', 3)
                )
                logger.info("Using direct CDR sending mode")
            else:
                # Use HTTPWorker for batch sending
                self.http_worker = HTTPWorker(
                    queue=self.cdr_queue,
                    api_client=self.api_client,
                    batch_size=self.cdr_config.get('batch_size', 100),
                    batch_timeout=self.cdr_config.get('batch_timeout', 30.0),
                    batch_force_timeout=self.cdr_config.get('batch_force_timeout', 5.0),
                    max_retries=self.cdr_config.get('max_retries', 3)
                )
                logger.info("Using batch CDR sending mode")
        
    async def connect(self) -> bool:
        try:
            logger.info(f"Connecting to Asterisk AMI at {self.host}:{self.port}")
            self.ami_client = panoramisk.Manager(
                host=self.host,
                port=self.port,
                username=self.username,
                secret=self.password,
                ssl=False,
                encoding='utf8'
            )
            
            # Register event handlers
            self.ami_client.register_event('RecordFile', self._handle_record_file)
            self.ami_client.register_event('VoicemailMessage', self._handle_voicemail_message)
            
            # Register MixMonitor event handlers for recording tracking
            self.ami_client.register_event('MixMonitorStart', self._handle_mixmonitor_start)
            self.ami_client.register_event('MixMonitorStop', self._handle_mixmonitor_stop)
            self.ami_client.register_event('MonitorStart', self._handle_mixmonitor_start)  # Fallback for older Asterisk
            self.ami_client.register_event('MonitorStop', self._handle_mixmonitor_stop)   # Fallback for older Asterisk
            
            # Register CDR event handlers if enabled
            if self.cdr_monitor:
                self.ami_client.register_event('Cdr', self.cdr_monitor.handle_cdr_event)
                self.ami_client.register_event('CEL', self.cdr_monitor.handle_cel_event)
            
            # Connect with timeout
            logger.info("Attempting AMI connection...")
            await asyncio.wait_for(self.ami_client.connect(), timeout=10.0)
            self.connected = True
            logger.info("Successfully connected to Asterisk AMI")
            
            # Start CDR monitor and HTTP worker if enabled
            if self.cdr_monitor:
                await self.cdr_monitor.start()
                logger.info("CDR monitoring started")
                
            if self.http_worker:
                await self.http_worker.start()
                logger.info("HTTP worker started for CDR batch processing")
                
            # Start MixMonitor file size monitoring for completed recordings
            check_interval = min(int(os.getenv('RECORDING_CHECK_INTERVAL_SECONDS', '30')), 60)
            await mixmonitor_tracker.start_monitoring(check_interval)
            
            # Start upload checking task
            self.upload_check_task = asyncio.create_task(self._upload_check_loop())
            logger.info("AMI-based recording upload and monitoring enabled")
            
            # Update metrics with connection status
            record_ami_connection_status(True)
            
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"Connection to Asterisk AMI at {self.host}:{self.port} timed out after 10 seconds")
            self.connected = False
            record_ami_connection_status(False)
            return False
        except Exception as e:
            logger.error(f"Failed to connect to Asterisk AMI: {e}")
            self.connected = False
            
            # Update metrics with connection status
            record_ami_connection_status(False)
            
            return False
            
    async def disconnect(self) -> None:
        if self.ami_client and self.connected:
            logger.info("Disconnecting from Asterisk AMI")
            
            # Stop CDR monitor if running
            if self.cdr_monitor:
                await self.cdr_monitor.stop()
                logger.info("CDR monitoring stopped")
                
            # Stop HTTP worker if running
            if self.http_worker:
                await self.http_worker.stop()
                logger.info("HTTP worker stopped")
                
            # Stop MixMonitor monitoring
            await mixmonitor_tracker.stop_monitoring()
            
            # Stop upload checking task
            if self.upload_check_task:
                self.upload_check_task.cancel()
                try:
                    await self.upload_check_task
                except asyncio.CancelledError:
                    pass
            
            # Recording uploader disabled in v0.9.0 - using AMI events instead
            # if self.recording_uploader:
            #     await self.recording_uploader.stop()
            #     logger.info("Recording uploader stopped")
            
            self.ami_client.close()
            self.connected = False
            
            # Update metrics with connection status
            record_ami_connection_status(False)
    
    async def _handle_record_file(self, manager, event) -> None:
        try:
            logger.debug(f"Received RecordFile event: {event}")
            
            # Extract file information
            filename = event.get('Filename')
            if not filename:
                logger.warning("RecordFile event missing filename, ignoring")
                return
                
            # Build the full path to the recording
            file_path = self._find_recording_file(filename)
            if not file_path:
                logger.warning(f"Could not find recording file: {filename}")
                return
                
            # Extract call metadata
            call_metadata = self._extract_call_metadata(event)
            
            # Check if this is a queue call we should process
            if is_queue_call(event, call_metadata, self.queue_whitelist, self.queue_blacklist):
                logger.info(f"Processing queue call recording: {file_path}")
                await self._process_recording(file_path, call_metadata, recording_type="queue")
            elif self.recording_config.get('process_all_calls', False):
                logger.info(f"Processing regular call recording: {file_path}")
                await self._process_recording(file_path, call_metadata, recording_type="call")
            else:
                logger.debug(f"Ignoring non-queue recording: {file_path}")
                
        except Exception as e:
            logger.error(f"Error handling RecordFile event: {e}")
    
    async def _handle_voicemail_message(self, manager, event) -> None:
        try:
            logger.debug(f"Received VoicemailMessage event: {event}")
            
            # Extract voicemail information
            mailbox = event.get('Mailbox')
            msgnum = event.get('MessageNum')
            folder = event.get('Folder', 'INBOX')
            
            if not all([mailbox, msgnum]):
                logger.warning("VoicemailMessage event missing required fields, ignoring")
                return
                
            # Find the voicemail file
            file_path = self._find_voicemail_file(mailbox, msgnum, folder)
            if not file_path:
                logger.warning(f"Could not find voicemail file for mailbox {mailbox}, message {msgnum}")
                return
                
            # Extract voicemail metadata
            voicemail_metadata = self._extract_voicemail_metadata(event)
            
            # Process the voicemail
            if self.voicemail_config.get('enabled', True):
                logger.info(f"Processing voicemail recording: {file_path}")
                await self._process_recording(file_path, voicemail_metadata, recording_type="voicemail")
            else:
                logger.debug(f"Voicemail processing disabled, ignoring: {file_path}")
                
        except Exception as e:
            logger.error(f"Error handling VoicemailMessage event: {e}")
    
    def _find_recording_file(self, filename: str) -> Optional[str]:
        # If filename is an absolute path and exists, return it directly
        if os.path.isabs(filename) and os.path.isfile(filename):
            return filename
            
        # Try to find the file in configured recording paths
        for base_path in self.recording_paths:
            # Try with original filename
            path = os.path.join(base_path, filename)
            if os.path.isfile(path):
                return path
                
            # Try with .wav extension if not present
            if not filename.endswith('.wav'):
                path = os.path.join(base_path, f"{filename}.wav")
                if os.path.isfile(path):
                    return path
        
        return None
        
    def _find_voicemail_file(self, mailbox: str, msgnum: str, folder: str) -> Optional[str]:
        for base_path in self.voicemail_paths:
            # Standard Asterisk voicemail path format
            # /var/spool/asterisk/voicemail/[context]/[mailbox]/[folder]/msg[msgnum].wav
            mailbox_parts = mailbox.split('@')
            
            if len(mailbox_parts) == 2:
                box, context = mailbox_parts
                path = os.path.join(base_path, context, box, folder.lower(), f"msg{msgnum}.wav")
                if os.path.isfile(path):
                    return path
            
            # Try alternate formats if standard format doesn't exist
            # Some installations use different path layouts
            for alt_path in [
                os.path.join(base_path, 'default', mailbox, folder.lower(), f"msg{msgnum}.wav"),
                os.path.join(base_path, mailbox, folder.lower(), f"msg{msgnum}.wav"),
            ]:
                if os.path.isfile(alt_path):
                    return alt_path
        
        return None
        
    def _extract_call_metadata(self, event) -> Dict[str, Any]:
        return {
            'uniqueid': event.get('UniqueID'),
            'channel': event.get('Channel'),
            'caller_id_num': event.get('CallerIDNum'),
            'caller_id_name': event.get('CallerIDName'),
            'connected_line_num': event.get('ConnectedLineNum'),
            'connected_line_name': event.get('ConnectedLineName'),
            'queue': event.get('Queue'),
            'timestamp': datetime.now().isoformat(),
            'direction': self._determine_call_direction(event),
            'duration': event.get('Duration', '0'),
            'recording_type': 'call'
        }
        
    def _extract_voicemail_metadata(self, event) -> Dict[str, Any]:
        return {
            'mailbox': event.get('Mailbox'),
            'caller_id_num': event.get('CallerIDNum', event.get('CallerID', '')),
            'caller_id_name': event.get('CallerIDName', ''),
            'folder': event.get('Folder', 'INBOX'),
            'timestamp': datetime.now().isoformat(),
            'duration': event.get('Duration', '0'),
            'recording_type': 'voicemail'
        }
        
    def _determine_call_direction(self, event) -> str:
        # Logic to determine if call is inbound or outbound
        # This is a simplified version and may need customization for specific setups
        channel = event.get('Channel', '')
        
        if channel.startswith('SIP/') and not channel.startswith('SIP/s'):
            # Typically outbound calls start with trunk name
            return 'outbound'
        else:
            # Most other patterns are inbound
            return 'inbound'
    
    async def _process_recording(self, file_path: str, metadata: Dict[str, Any], recording_type: str) -> None:
        try:
            # Make sure file exists and has content
            if not os.path.isfile(file_path):
                logger.warning(f"Recording file not found: {file_path}")
                record_processed_recording(recording_type, 'file_not_found')
                return
                
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.warning(f"Recording file is empty: {file_path}")
                record_processed_recording(recording_type, 'empty_file')
                return
                
            # Add file information to metadata
            metadata['file_path'] = file_path
            metadata['file_size'] = file_size
            metadata['recording_type'] = recording_type
            
            # Add hostname
            metadata['hostname'] = socket.gethostname()
            
            # Update metrics
            if recording_type == 'queue' and 'queue' in metadata:
                record_queue_recording(metadata['queue'])
            elif recording_type == 'voicemail' and 'mailbox' in metadata:
                record_voicemail_recording(metadata['mailbox'])
            
            # Record the file size in metrics
            record_processed_recording(recording_type, 'processing', file_size)
            
            # Submit recording to the API
            logger.info(f"Sending {recording_type} recording to sentiment API: {file_path}")
            await self.api_client.upload_recording(file_path, metadata)
            
            # Record successful processing in metrics
            record_processed_recording(recording_type, 'success')
            
            logger.info(f"Successfully processed {recording_type} recording: {file_path}")
            
        except Exception as e:
            logger.error(f"Error processing {recording_type} recording {file_path}: {e}")
            
            # Record failure in metrics
            record_processed_recording(recording_type, 'error')
    
    async def _handle_mixmonitor_start(self, manager, event) -> None:
        """Handle MixMonitorStart/MonitorStart AMI events."""
        try:
            await mixmonitor_tracker.handle_mixmonitor_start(event)
        except Exception as e:
            logger.error(f"Error handling MixMonitor start event: {e}", exc_info=True)
    
    async def _handle_mixmonitor_stop(self, manager, event) -> None:
        """Handle MixMonitorStop/MonitorStop AMI events."""
        try:
            # Update the tracker with stop event
            await mixmonitor_tracker.handle_mixmonitor_stop(event)
            
            # Don't trigger immediate upload - let the monitoring loop handle it
            # This prevents uploading files that are still being written
            # The monitoring loop will detect completion based on stable file size
                
        except Exception as e:
            logger.error(f"Error handling MixMonitor stop event: {e}", exc_info=True)
    
    async def _upload_recording_with_metadata(self, filename: str):
        """Upload recording immediately using AMI-tracked metadata."""
        try:
            # Get metadata from tracker
            metadata = mixmonitor_tracker.get_recording_metadata(filename)
            if not metadata:
                logger.warning(f"No AMI metadata found for recording: {filename}")
                return
            
            # Check if already uploaded
            if metadata.get('uploaded', 0) == 1:
                logger.debug(f"Recording already uploaded: {filename}")
                return
            
            # Find the actual file path
            recording_paths = self.recording_config.get('paths', ['/var/spool/asterisk/monitor'])
            file_path = None
            
            for base_path in recording_paths:
                potential_path = os.path.join(base_path, filename)
                if os.path.exists(potential_path):
                    file_path = potential_path
                    break
            
            if not file_path:
                logger.warning(f"Recording file not found in any configured path: {filename}")
                return
            
            # Mark file as existing
            mixmonitor_tracker.mark_file_exists(filename)
            
            # Build upload command with AMI metadata
            success = await self._execute_upload_with_ami_data(file_path, metadata)
            
            if success:
                # Mark as uploaded
                mixmonitor_tracker.mark_uploaded(filename)
                logger.info(f"Successfully uploaded recording with AMI metadata: {filename}")
            
        except Exception as e:
            logger.error(f"Error uploading recording {filename}: {e}", exc_info=True)
    
    async def _execute_upload_with_ami_data(self, file_path: str, ami_metadata: Dict[str, Any]) -> bool:
        """Execute the upload using curl command with AMI-derived metadata.
        
        Returns:
            bool: True if upload successful, False otherwise
        """
        try:
            import subprocess
            
            # Verify file exists and has content
            if not os.path.exists(file_path):
                logger.error(f"Recording file does not exist: {file_path}")
                return False
            
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.error(f"Recording file is empty: {file_path}")
                return False
                
            logger.debug(f"Uploading file: {file_path} ({file_size} bytes)")
            
            # Build curl command with proper metadata
            api_key = os.getenv('API_KEY')
            region = os.getenv('REGION', 'us1')
            ami_host = os.getenv('AMI_HOST', 'localhost')
            
            # Determine API URL based on region
            if region in ['dev', 'ca1']:
                api_url = 'https://api-dev.sipstack.com/v1/mqs/recording'
            elif region == 'us2':
                api_url = 'https://api-us2.sipstack.com/v1/mqs/recording'
            else:
                api_url = 'https://api.sipstack.com/v1/mqs/recording'
            
            # Get version - try local file first, then container path
            version = "0.9.8"  # Default fallback
            version_files = ["VERSION", "/app/VERSION", "connectors/asterisk/VERSION"]
            for vf in version_files:
                if os.path.exists(vf):
                    with open(vf, 'r') as f:
                        version = f.read().strip()
                        break
            
            # Determine content type
            filename = os.path.basename(file_path)
            if filename.lower().endswith('.mp3'):
                content_type = 'audio/mpeg'
            elif filename.lower().endswith('.gsm'):
                content_type = 'audio/wav'  # GSM sent as wav for compatibility
            else:
                content_type = 'audio/wav'
            
            # Escape the file path for shell - wrap in single quotes
            escaped_path = f"'{file_path}'"
            
            # Build curl command as a single shell string (like the working bash script)
            # Note: removed -f flag to see error responses
            curl_cmd = f"""curl -s -w '\\n%{{http_code}}' -X POST \
-H 'Authorization: Bearer {api_key}' \
-H 'User-Agent: SIPSTACK-Connector-Asterisk/{version}' \
-H 'X-Asterisk-Hostname: {ami_host}' \
-F 'recording_id={filename}' \
-F 'src_number={ami_metadata.get("callerid_num", "")}' \
-F 'dst_number={ami_metadata.get("exten", "")}' \
-F 'call_id={ami_metadata.get("uniqueid", "")}' \
-F 'linkedid={ami_metadata.get("linkedid", ami_metadata.get("uniqueid", ""))}' \
-F 'channel_state={ami_metadata.get("channel_state", "")}' \
-F 'language={ami_metadata.get("language", "")}' \
-F 'priority={ami_metadata.get("priority", "")}' \
-F 'audio=@{escaped_path};type={content_type}' \
{api_url}"""
            
            # Execute upload
            logger.info(f"Uploading recording with AMI metadata: {filename} -> {ami_metadata.get('uniqueid')}/{ami_metadata.get('linkedid')}")
            
            # Log the curl command for debugging (without the API key)
            debug_cmd = curl_cmd.replace(api_key, 'sk_[REDACTED]')
            logger.debug(f"Curl command: {debug_cmd}")
            
            # Execute the curl command using shell
            result = subprocess.run(curl_cmd, shell=True, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                http_code = lines[-1] if lines else '000'
                response_body = '\n'.join(lines[:-1]) if len(lines) > 1 else ''
                
                if http_code == '202':
                    logger.info(f"Successfully uploaded recording: {filename}")
                    # Files remain in place - no moving or deletion
                    return True
                    
                else:
                    logger.error(f"Upload failed for {filename}: HTTP {http_code}")
                    logger.error(f"Response body: {response_body}")
                    logger.error(f"Full stdout: {result.stdout}")
                    # Track the failure in SQLite
                    mixmonitor_tracker.mark_upload_failed(
                        filename, 
                        f"HTTP {http_code}: {response_body[:200]}", 
                        int(http_code) if http_code.isdigit() else 0
                    )
                    return False
            else:
                error_msg = f"Curl failed with code {result.returncode}"
                logger.error(f"Curl command failed for {filename} with return code {result.returncode}")
                logger.error(f"STDOUT: {result.stdout}")
                logger.error(f"STDERR: {result.stderr}")
                # Track the failure in SQLite
                mixmonitor_tracker.mark_upload_failed(filename, f"{error_msg}: {result.stderr[:200]}", 0)
                return False
                
        except Exception as e:
            logger.error(f"Error executing upload for {file_path}: {e}", exc_info=True)
            # Track the failure in SQLite
            mixmonitor_tracker.mark_upload_failed(os.path.basename(file_path), str(e), 0)
            return False
    
    async def check_and_upload_completed_recordings(self):
        """Check for completed recordings and upload them."""
        try:
            # Get retry interval from environment (0 = disabled)
            retry_minutes = int(os.getenv('RECORDING_UPLOAD_RETRY_MINUTES', '5'))
            
            completed = mixmonitor_tracker.get_completed_recordings(retry_minutes)
            if not completed:
                return
                
            logger.info(f"Found {len(completed)} recordings ready for upload (retry enabled: {retry_minutes > 0})")
            
            for recording in completed:
                filename = recording['filename']
                file_path = recording['file_path']
                upload_attempts = recording.get('upload_attempts', 0)
                uploaded = recording.get('uploaded', 0)
                
                # Skip if already successfully uploaded
                if uploaded == 1:
                    logger.debug(f"Skipping already uploaded recording: {filename}")
                    continue
                
                if not file_path or not os.path.exists(file_path):
                    logger.warning(f"Completed recording file not found: {file_path}")
                    mixmonitor_tracker.mark_upload_failed(filename, "File not found", 404)
                    continue
                
                if upload_attempts > 0:
                    logger.info(f"Retrying upload for {filename} (attempt #{upload_attempts + 1})")
                
                try:
                    # Upload using existing method with the tracked metadata
                    success = await self._execute_upload_with_ami_data(file_path, recording)
                    
                    if success:
                        # Mark as uploaded only if successful
                        mixmonitor_tracker.mark_uploaded(filename)
                        # Success already logged in _execute_upload_with_ami_data
                    else:
                        # Already logged error in _execute_upload_with_ami_data
                        pass
                    
                except Exception as e:
                    logger.error(f"Failed to upload recording {filename}: {e}")
                    mixmonitor_tracker.mark_upload_failed(filename, str(e), 0)
                    
        except Exception as e:
            logger.error(f"Error checking completed recordings: {e}", exc_info=True)
    
    async def _upload_check_loop(self):
        """Periodic task to check for completed recordings and upload them."""
        # Get check interval from environment, default to 30 seconds, max 60 seconds
        check_interval = min(int(os.getenv('RECORDING_CHECK_INTERVAL_SECONDS', '30')), 60)
        logger.info(f"Recording upload check interval: {check_interval} seconds")
        
        while self.connected:
            try:
                # Check periodically for completed recordings
                await asyncio.sleep(check_interval)
                
                # Check if the monitoring loop flagged that we should check for uploads
                if mixmonitor_tracker.check_upload_needed() or True:  # Always check for now
                    await self.check_and_upload_completed_recordings()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in upload check loop: {e}", exc_info=True)
                await asyncio.sleep(check_interval)
