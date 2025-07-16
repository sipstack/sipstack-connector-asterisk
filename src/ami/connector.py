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
        
        # Initialize CDR monitor if enabled
        self.cdr_monitor = None
        if self.cdr_config.get('enabled', False):
            self.cdr_monitor = CDRMonitor(
                on_batch_ready=self._send_cdr_batch,
                batch_size=self.cdr_config.get('batch_size', 100),
                batch_timeout=self.cdr_config.get('batch_timeout', 30.0)
            )
        
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
            
            # Register CDR event handlers if enabled
            if self.cdr_monitor:
                self.ami_client.register_event('Cdr', self.cdr_monitor.handle_cdr_event)
                self.ami_client.register_event('CEL', self.cdr_monitor.handle_cel_event)
            
            # Connect with timeout
            logger.info("Attempting AMI connection...")
            await asyncio.wait_for(self.ami_client.connect(), timeout=10.0)
            self.connected = True
            logger.info("Successfully connected to Asterisk AMI")
            
            # Start CDR monitor if enabled
            if self.cdr_monitor:
                await self.cdr_monitor.start()
                logger.info("CDR monitoring started")
            
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
            
            self.ami_client.close()
            self.connected = False
            
            # Update metrics with connection status
            record_ami_connection_status(False)
    
    async def _handle_record_file(self, event) -> None:
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
    
    async def _handle_voicemail_message(self, event) -> None:
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
    
    async def _send_cdr_batch(self, batch) -> None:
        """Send CDR batch to API client."""
        logger.debug(f"_send_cdr_batch called with batch size: {batch.size}")
        try:
            if hasattr(self.api_client, 'send_cdr_batch'):
                logger.debug("API client has send_cdr_batch method, calling it")
                await self.api_client.send_cdr_batch(batch)
                logger.info(f"Successfully sent CDR batch of {batch.size} records")
            else:
                logger.warning("API client does not support CDR batch sending")
        except Exception as e:
            logger.error(f"Error sending CDR batch: {e}", exc_info=True)