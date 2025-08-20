"""
Asterisk AMI Connector v2.0.0
Now only handles recordings/voicemails via AMI.
CDR/CEL data is read directly from database by call_processor.py
"""

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
from .mixmonitor_tracker import mixmonitor_tracker
from recording_uploader import RecordingUploader

logger = logging.getLogger(__name__)

class AmiConnectorV2:
    """
    Streamlined AMI connector that only handles recordings and voicemails.
    CDR/CEL processing moved to database_connector.py for better performance.
    """
    
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        api_client,
        recording_config: Dict[str, Any],
        voicemail_config: Dict[str, Any]
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.api_client = api_client
        self.recording_config = recording_config
        self.voicemail_config = voicemail_config
        self.ami_client = None
        self.connected = False
        self.queue_whitelist = recording_config.get('queue_whitelist', [])
        self.queue_blacklist = recording_config.get('queue_blacklist', [])
        self.recording_paths = recording_config.get('paths', ['/var/spool/asterisk/monitor'])
        self.voicemail_paths = voicemail_config.get('paths', ['/var/spool/asterisk/voicemail'])
        
        # Recording tracking
        self.upload_check_task = None
        
        logger.info("AMI Connector v2.0.0 initialized - Recording/Voicemail only mode")
        logger.info("CDR/CEL processing handled by database connector")
        
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
            
            # Register ONLY recording and voicemail event handlers
            # NO CDR/CEL listeners - handled by database connector
            
            # Recording events
            self.ami_client.register_event('RecordFile', self._handle_record_file)
            self.ami_client.register_event('MixMonitorStart', self._handle_mixmonitor_start)
            self.ami_client.register_event('MixMonitorStop', self._handle_mixmonitor_stop)
            self.ami_client.register_event('MonitorStart', self._handle_mixmonitor_start)  # Fallback
            self.ami_client.register_event('MonitorStop', self._handle_mixmonitor_stop)   # Fallback
            
            # Voicemail events
            self.ami_client.register_event('VoicemailMessage', self._handle_voicemail_message)
            
            # Connect with timeout
            logger.info("Attempting AMI connection...")
            await asyncio.wait_for(self.ami_client.connect(), timeout=10.0)
            self.connected = True
            logger.info("Successfully connected to Asterisk AMI (Recording/Voicemail mode)")
            
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
            record_ami_connection_status(False)
            return False
            
    async def disconnect(self) -> None:
        if self.ami_client and self.connected:
            logger.info("Disconnecting from Asterisk AMI")
            
            # Stop MixMonitor monitoring
            await mixmonitor_tracker.stop_monitoring()
            
            # Stop upload checking task
            if self.upload_check_task:
                self.upload_check_task.cancel()
                try:
                    await self.upload_check_task
                except asyncio.CancelledError:
                    pass
            
            self.ami_client.close()
            self.connected = False
            
            # Update metrics with connection status
            record_ami_connection_status(False)
    
    async def _handle_record_file(self, manager, event) -> None:
        """Handle RecordFile AMI event"""
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
        """Handle VoicemailMessage AMI event"""
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
        """Find recording file in configured paths"""
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
        """Find voicemail file in configured paths"""
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
            for alt_path in [
                os.path.join(base_path, 'default', mailbox, folder.lower(), f"msg{msgnum}.wav"),
                os.path.join(base_path, mailbox, folder.lower(), f"msg{msgnum}.wav"),
            ]:
                if os.path.isfile(alt_path):
                    return alt_path
        
        return None
        
    def _extract_call_metadata(self, event) -> Dict[str, Any]:
        """Extract metadata from RecordFile event"""
        return {
            'uniqueid': event.get('UniqueID'),
            'linkedid': event.get('LinkedID') or event.get('UniqueID'),
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
        """Extract metadata from VoicemailMessage event"""
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
        """Simple direction detection from event"""
        channel = event.get('Channel', '')
        
        if channel.startswith('SIP/') and not channel.startswith('SIP/s'):
            # Typically outbound calls start with trunk name
            return 'outbound'
        else:
            # Most other patterns are inbound
            return 'inbound'
    
    async def _process_recording(self, file_path: str, metadata: Dict[str, Any], recording_type: str) -> None:
        """Process and upload recording"""
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
            logger.info(f"Sending {recording_type} recording to API: {file_path}")
            await self.api_client.upload_recording(file_path, metadata)
            
            # Record successful processing in metrics
            record_processed_recording(recording_type, 'success')
            
            logger.info(f"Successfully processed {recording_type} recording: {file_path}")
            
        except Exception as e:
            logger.error(f"Error processing {recording_type} recording {file_path}: {e}")
            record_processed_recording(recording_type, 'error')
    
    async def _handle_mixmonitor_start(self, manager, event) -> None:
        """Handle MixMonitorStart/MonitorStart AMI events"""
        try:
            await mixmonitor_tracker.handle_mixmonitor_start(event)
        except Exception as e:
            logger.error(f"Error handling MixMonitor start event: {e}", exc_info=True)
    
    async def _handle_mixmonitor_stop(self, manager, event) -> None:
        """Handle MixMonitorStop/MonitorStop AMI events"""
        try:
            await mixmonitor_tracker.handle_mixmonitor_stop(event)
        except Exception as e:
            logger.error(f"Error handling MixMonitor stop event: {e}", exc_info=True)
    
    async def _upload_check_loop(self):
        """Periodic task to check for completed recordings and upload them"""
        check_interval = min(int(os.getenv('RECORDING_CHECK_INTERVAL_SECONDS', '30')), 60)
        logger.info(f"Recording upload check interval: {check_interval} seconds")
        
        while self.connected:
            try:
                await asyncio.sleep(check_interval)
                
                # Check if the monitoring loop flagged that we should check for uploads
                if mixmonitor_tracker.check_upload_needed():
                    await self.check_and_upload_completed_recordings()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in upload check loop: {e}", exc_info=True)
                await asyncio.sleep(check_interval)
    
    async def check_and_upload_completed_recordings(self):
        """Check for completed recordings and upload them"""
        try:
            # Get retry interval from environment (0 = disabled)
            retry_minutes = int(os.getenv('RECORDING_UPLOAD_RETRY_MINUTES', '5'))
            
            completed = mixmonitor_tracker.get_completed_recordings(retry_minutes)
            if not completed:
                return
                
            logger.info(f"Found {len(completed)} recordings ready for upload")
            
            for recording in completed:
                filename = recording['filename']
                file_path = recording['file_path']
                
                if not file_path or not os.path.exists(file_path):
                    logger.warning(f"Completed recording file not found: {file_path}")
                    mixmonitor_tracker.mark_upload_failed(filename, "File not found", 404)
                    continue
                
                try:
                    # Upload using the tracked metadata
                    metadata = {
                        'uniqueid': recording.get('uniqueid'),
                        'linkedid': recording.get('linkedid'),
                        'caller_id_num': recording.get('callerid_num'),
                        'exten': recording.get('exten'),
                        'context': recording.get('context'),
                        'channel_state': recording.get('channel_state'),
                        'recording_type': 'call'
                    }
                    
                    await self.api_client.upload_recording(file_path, metadata)
                    
                    # Mark as uploaded
                    mixmonitor_tracker.mark_uploaded(filename)
                    logger.info(f"Successfully uploaded recording: {filename}")
                    
                except Exception as e:
                    logger.error(f"Failed to upload recording {filename}: {e}")
                    mixmonitor_tracker.mark_upload_failed(filename, str(e), 0)
                    
        except Exception as e:
            logger.error(f"Error checking completed recordings: {e}", exc_info=True)