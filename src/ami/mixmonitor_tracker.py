"""
MixMonitor tracker for mapping recording files to their CDR data via AMI events.
"""

import asyncio
import glob
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Any, List
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class MixMonitorTracker:
    """Tracks MixMonitor events to map recording files to CDR information."""
    
    def __init__(self, db_path: str = "/tmp/mixmonitor_tracking.db"):
        self.db_path = db_path
        self.active_recordings: Dict[str, Dict[str, Any]] = {}
        self.monitoring_task: Optional[asyncio.Task] = None
        self.monitoring_enabled = False
        self._upload_check_needed = False
        self._init_database()
        
    def _init_database(self):
        """Initialize the SQLite database for tracking recordings."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS recording_metadata (
                        filename TEXT PRIMARY KEY,
                        channel TEXT,
                        uniqueid TEXT,
                        linkedid TEXT,
                        callerid_num TEXT,
                        callerid_name TEXT,
                        context TEXT,
                        exten TEXT,
                        
                        -- Enhanced SIP/Channel Information
                        channel_state TEXT,
                        channel_state_desc TEXT,
                        connected_line_num TEXT,
                        connected_line_name TEXT,
                        language TEXT,
                        account_code TEXT,
                        peer_account TEXT,
                        user_field TEXT,
                        
                        -- Audio/Codec Information
                        format TEXT,
                        read_format TEXT,
                        write_format TEXT,
                        codec TEXT,
                        native_formats TEXT,
                        
                        -- SIP User/Auth Information  
                        sip_from_user TEXT,
                        sip_from_domain TEXT,
                        sip_to_user TEXT,
                        sip_to_domain TEXT,
                        sip_call_id TEXT,
                        sip_user_agent TEXT,
                        sip_contact TEXT,
                        auth_user TEXT,
                        
                        -- Network/Transport Information
                        remote_address TEXT,
                        transport TEXT,
                        local_address TEXT,
                        
                        -- Call Quality Information
                        rtcp_rtt TEXT,
                        rtcp_jitter TEXT,
                        rtcp_packet_loss TEXT,
                        
                        -- Timing Information
                        answer_time TEXT,
                        hangup_cause TEXT,
                        hangup_source TEXT,
                        
                        -- Application/Routing Information
                        priority TEXT,
                        application TEXT,
                        app_data TEXT,
                        
                        started_at TEXT,
                        stopped_at TEXT,
                        file_exists INTEGER DEFAULT 0,
                        uploaded INTEGER DEFAULT 0,
                        file_path TEXT,
                        file_size INTEGER DEFAULT 0,
                        last_size_check TEXT,
                        size_stable_count INTEGER DEFAULT 0,
                        recording_complete INTEGER DEFAULT 0,
                        upload_status INTEGER DEFAULT 0,
                        upload_attempts INTEGER DEFAULT 0,
                        last_upload_attempt TEXT,
                        last_upload_error TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Clean up old entries (older than 24 hours)
                cutoff = datetime.now() - timedelta(hours=24)
                conn.execute(
                    "DELETE FROM recording_metadata WHERE created_at < ?",
                    (cutoff.isoformat(),)
                )
                # Add earliest_upload_time column if it doesn't exist
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(recording_metadata)")
                columns = [row[1] for row in cursor.fetchall()]
                if 'earliest_upload_time' not in columns:
                    conn.execute("ALTER TABLE recording_metadata ADD COLUMN earliest_upload_time TEXT")
                    logger.info("Added earliest_upload_time column to recording_metadata table")
                
                conn.commit()
                logger.info("MixMonitor tracking database initialized")
                
        except Exception as e:
            logger.error(f"Failed to initialize tracking database: {e}")
    
    async def handle_mixmonitor_start(self, event: Dict[str, Any]):
        """Handle MixMonitorStart AMI event with comprehensive data extraction."""
        try:
            # Debug: Log all available fields in the event
            logger.info(f"MixMonitorStart event fields: {list(event.keys())}")
            logger.debug(f"Full MixMonitorStart event: {event}")
            
            channel = event.get('Channel', '')
            uniqueid = event.get('Uniqueid', '')
            linkedid = event.get('Linkedid', uniqueid)  # Use uniqueid as fallback
            # Try multiple possible filename fields from different Asterisk versions
            filename = (
                event.get('Mixmonitor_filename') or 
                event.get('MixMonitor_filename') or 
                event.get('File') or 
                event.get('Filename') or
                event.get('MixMonitorFilename') or
                ''
            )
            
            # If no filename in event, try to discover it using available metadata
            if not filename and uniqueid:
                discovered_filename = await self._discover_recording_file(event)
                if discovered_filename:
                    filename = discovered_filename
                    logger.info(f"Discovered actual recording file: {filename}")
                else:
                    # Fallback to generated filename
                    filename = f"{uniqueid}.wav"
                    logger.info(f"Generated filename from uniqueid: {filename}")
            
            if not filename or not uniqueid:
                logger.warning(f"MixMonitorStart event missing filename or uniqueid: {event}")
                return
                
            # Clean up filename path to just basename
            filename = os.path.basename(filename)
            
            # Extract comprehensive data from AMI event
            metadata = {
                'filename': filename,
                'channel': channel,
                'uniqueid': uniqueid,
                'linkedid': linkedid,
                'started_at': datetime.now().isoformat(),
                
                # Basic call information
                'callerid_num': event.get('CallerIDNum', ''),
                'callerid_name': event.get('CallerIDName', ''),
                'context': event.get('Context', ''),
                'exten': event.get('Exten', ''),
                
                # Enhanced SIP/Channel Information
                'channel_state': event.get('ChannelState', ''),
                'channel_state_desc': event.get('ChannelStateDesc', ''),
                'connected_line_num': event.get('ConnectedLineNum', ''),
                'connected_line_name': event.get('ConnectedLineName', ''),
                'language': event.get('Language', ''),
                'account_code': event.get('AccountCode', ''),
                'peer_account': event.get('PeerAccount', ''),
                'user_field': event.get('UserField', ''),
                
                # Audio/Codec Information
                'format': event.get('Format', ''),
                'read_format': event.get('ReadFormat', ''),
                'write_format': event.get('WriteFormat', ''),
                'codec': event.get('Codec', ''),
                'native_formats': event.get('NativeFormats', ''),
                
                # SIP User/Auth Information
                'sip_from_user': event.get('SIPFromUser', event.get('FromUser', '')),
                'sip_from_domain': event.get('SIPFromDomain', event.get('FromDomain', '')),
                'sip_to_user': event.get('SIPToUser', event.get('ToUser', '')),
                'sip_to_domain': event.get('SIPToDomain', event.get('ToDomain', '')),
                'sip_call_id': event.get('SIPCallID', event.get('CallID', '')),
                'sip_user_agent': event.get('SIPUserAgent', event.get('UserAgent', '')),
                'sip_contact': event.get('SIPContact', event.get('Contact', '')),
                'auth_user': event.get('AuthUser', event.get('Username', '')),
                
                # Network/Transport Information
                'remote_address': event.get('RemoteAddress', event.get('Address', '')),
                'transport': event.get('Transport', ''),
                'local_address': event.get('LocalAddress', ''),
                
                # Call Quality Information (may be available in some events)
                'rtcp_rtt': event.get('RTCPRoundTripTime', event.get('RTT', '')),
                'rtcp_jitter': event.get('RTCPJitter', event.get('Jitter', '')),
                'rtcp_packet_loss': event.get('RTCPPacketLoss', event.get('PacketLoss', '')),
                
                # Timing Information
                'answer_time': event.get('AnswerTime', ''),
                'hangup_cause': event.get('HangupCause', ''),
                'hangup_source': event.get('HangupSource', ''),
                
                # Application/Routing Information
                'priority': event.get('Priority', ''),
                'application': event.get('Application', ''),
                'app_data': event.get('AppData', event.get('ApplicationData', '')),
            }
            
            # If we discovered a file, store the full path and initial size
            if discovered_filename and discovered_filename != f"{uniqueid}.wav":
                # Find the full path again
                for base_path in ['/var/spool/asterisk/monitor', '/var/spool/asterisk/mixmonitor', '/var/spool/asterisk/recordings']:
                    if os.path.exists(base_path):
                        import glob
                        search_pattern = os.path.join(base_path, "**", discovered_filename)
                        matches = glob.glob(search_pattern, recursive=True)
                        if matches:
                            full_path = matches[0]
                            metadata['file_path'] = full_path
                            metadata['file_exists'] = 1
                            try:
                                metadata['file_size'] = os.path.getsize(full_path)
                                metadata['last_size_check'] = datetime.now().isoformat()
                            except OSError:
                                metadata['file_size'] = 0
                            break
            
            # Store in memory for quick access
            self.active_recordings[channel] = metadata
            
            # Store in database
            await self._store_recording_metadata(**metadata)
            
            logger.info(f"MixMonitor started: {filename} -> {uniqueid}/{linkedid}")
            
        except Exception as e:
            logger.error(f"Error handling MixMonitorStart event: {e}", exc_info=True)
    
    async def handle_mixmonitor_stop(self, event: Dict[str, Any]):
        """Handle MixMonitorStop AMI event."""
        try:
            channel = event.get('Channel', '')
            uniqueid = event.get('Uniqueid', '')
            
            if channel in self.active_recordings:
                recording_info = self.active_recordings[channel]
                filename = recording_info['filename']
                
                # Update stop time in database with a delay flag
                # This prevents immediate upload attempts
                stop_time = datetime.now()
                earliest_upload_time = (stop_time + timedelta(seconds=5)).isoformat()
                
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        """UPDATE recording_metadata 
                           SET stopped_at = ?, 
                               earliest_upload_time = ?
                           WHERE filename = ?""",
                        (stop_time.isoformat(), earliest_upload_time, filename)
                    )
                    conn.commit()
                
                logger.info(f"MixMonitor stopped: {filename} (upload eligible after {earliest_upload_time})")
                
                # Remove from active recordings after a delay to allow file processing
                asyncio.create_task(self._cleanup_active_recording(channel, delay=30))
                
        except Exception as e:
            logger.error(f"Error handling MixMonitorStop event: {e}", exc_info=True)
    
    async def _cleanup_active_recording(self, channel: str, delay: int = 30):
        """Remove recording from active list after a delay."""
        await asyncio.sleep(delay)
        if channel in self.active_recordings:
            del self.active_recordings[channel]
    
    async def _store_recording_metadata(self, **kwargs):
        """Store comprehensive recording metadata in database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Get all column names for the recording_metadata table
                cursor = conn.execute("PRAGMA table_info(recording_metadata)")
                columns = [row[1] for row in cursor.fetchall()]
                
                # Build insert statement dynamically based on available columns
                insert_columns = []
                insert_values = []
                
                for column in columns:
                    if column in kwargs:
                        insert_columns.append(column)
                        insert_values.append(kwargs[column])
                
                if insert_columns:
                    placeholders = ', '.join(['?' for _ in insert_columns])
                    columns_str = ', '.join(insert_columns)
                    
                    conn.execute(f"""
                        INSERT OR REPLACE INTO recording_metadata ({columns_str}) 
                        VALUES ({placeholders})
                    """, insert_values)
                    conn.commit()
                
        except Exception as e:
            logger.error(f"Failed to store recording metadata: {e}")
    
    def get_recording_metadata(self, filename: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a recording by filename."""
        try:
            # Clean filename to basename if full path provided
            filename = os.path.basename(filename)
            
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM recording_metadata WHERE filename = ?",
                    (filename,)
                )
                row = cursor.fetchone()
                
                if row:
                    return dict(row)
                return None
                
        except Exception as e:
            logger.error(f"Failed to get recording metadata for {filename}: {e}")
            return None
    
    def mark_file_exists(self, filename: str):
        """Mark that a recording file exists on disk."""
        try:
            filename = os.path.basename(filename)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE recording_metadata SET file_exists = 1 WHERE filename = ?",
                    (filename,)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark file exists for {filename}: {e}")
    
    def mark_uploaded(self, filename: str):
        """Mark that a recording has been uploaded successfully."""
        try:
            filename = os.path.basename(filename)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """UPDATE recording_metadata 
                    SET uploaded = 1, upload_status = 202, 
                        last_upload_attempt = ?
                    WHERE filename = ?""",
                    (datetime.now().isoformat(), filename)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark uploaded for {filename}: {e}")
    
    def mark_upload_failed(self, filename: str, error_msg: str, status_code: int = 0):
        """Mark that a recording upload failed."""
        try:
            filename = os.path.basename(filename)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """UPDATE recording_metadata 
                    SET upload_status = ?, 
                        upload_attempts = upload_attempts + 1,
                        last_upload_attempt = ?,
                        last_upload_error = ?
                    WHERE filename = ?""",
                    (status_code, datetime.now().isoformat(), error_msg, filename)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark upload failed for {filename}: {e}")
    
    def get_pending_uploads(self) -> List[Dict[str, Any]]:
        """Get recordings that exist but haven't been uploaded yet."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM recording_metadata 
                    WHERE file_exists = 1 AND uploaded = 0
                    ORDER BY started_at ASC
                """)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get pending uploads: {e}")
            return []
    
    def cleanup_old_entries(self, hours: int = 24):
        """Clean up old tracking entries."""
        try:
            cutoff = datetime.now() - timedelta(hours=hours)
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM recording_metadata WHERE created_at < ?",
                    (cutoff.isoformat(),)
                )
                deleted = cursor.rowcount
                conn.commit()
                
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old recording entries")
                
        except Exception as e:
            logger.error(f"Failed to cleanup old entries: {e}")
    
    async def _discover_recording_file(self, event: Dict[str, Any]) -> Optional[str]:
        """Discover actual recording file using AMI event metadata."""
        try:
            uniqueid = event.get('Uniqueid', '')
            linkedid = event.get('Linkedid', '')
            callerid_num = event.get('CallerIDNum', '')
            callerid_name = event.get('CallerIDName', '')
            start_time = datetime.now()
            
            # Common recording paths to search
            search_paths = [
                '/var/spool/asterisk/monitor',
                '/var/spool/asterisk/mixmonitor', 
                '/var/spool/asterisk/recordings',
                '/tmp/recordings',
                '/var/recordings'
            ]
            
            # Search patterns based on available metadata
            search_patterns = []
            
            if uniqueid:
                search_patterns.extend([
                    f"*{uniqueid}*",
                    f"*{uniqueid}*.wav",
                    f"*{uniqueid}*.WAV"
                ])
            
            if linkedid and linkedid != uniqueid:
                search_patterns.extend([
                    f"*{linkedid}*",
                    f"*{linkedid}*.wav", 
                    f"*{linkedid}*.WAV"
                ])
            
            if callerid_num:
                search_patterns.extend([
                    f"*{callerid_num}*{uniqueid}*",
                    f"*{callerid_num}*{linkedid}*"
                ])
            
            logger.debug(f"Searching for recording file with patterns: {search_patterns}")
            
            # Search in each path
            for base_path in search_paths:
                if not os.path.exists(base_path):
                    continue
                    
                for pattern in search_patterns:
                    try:
                        # Use glob to find matching files
                        search_pattern = os.path.join(base_path, "**", pattern)
                        matches = glob.glob(search_pattern, recursive=True)
                        
                        # Filter matches by creation time (within last 2 minutes)
                        recent_matches = []
                        cutoff_time = start_time - timedelta(minutes=2)
                        
                        for match in matches:
                            try:
                                file_mtime = datetime.fromtimestamp(os.path.getmtime(match))
                                if file_mtime >= cutoff_time:
                                    recent_matches.append((match, file_mtime))
                            except (OSError, ValueError):
                                continue
                        
                        if recent_matches:
                            # Sort by modification time (newest first)
                            recent_matches.sort(key=lambda x: x[1], reverse=True)
                            found_file = recent_matches[0][0]
                            
                            logger.info(f"Found recording file: {found_file} (modified: {recent_matches[0][1]})")
                            return os.path.basename(found_file)
                            
                    except Exception as e:
                        logger.debug(f"Error searching pattern {pattern} in {base_path}: {e}")
                        continue
            
            # Try a more targeted search in today's date structure
            today = start_time.strftime("%Y/%m/%d")
            date_paths = []
            
            for base_path in search_paths:
                date_paths.extend([
                    os.path.join(base_path, today),
                    os.path.join(base_path, "extensions", today),
                    os.path.join(base_path, start_time.strftime("%Y-%m-%d")),
                ])
            
            for date_path in date_paths:
                if not os.path.exists(date_path):
                    continue
                    
                for pattern in search_patterns:
                    try:
                        search_pattern = os.path.join(date_path, pattern)
                        matches = glob.glob(search_pattern, recursive=False)
                        
                        if matches:
                            # Get the most recently modified file
                            latest_file = max(matches, key=os.path.getmtime)
                            file_mtime = datetime.fromtimestamp(os.path.getmtime(latest_file))
                            
                            # Check if file was created within reasonable time window
                            if file_mtime >= cutoff_time:
                                logger.info(f"Found recording in date path: {latest_file}")
                                return os.path.basename(latest_file)
                                
                    except Exception as e:
                        logger.debug(f"Error searching date pattern {pattern} in {date_path}: {e}")
                        continue
            
            logger.debug(f"No recording file discovered for uniqueid: {uniqueid}")
            return None
            
        except Exception as e:
            logger.error(f"Error in recording file discovery: {e}", exc_info=True)
            return None
    
    async def start_monitoring(self, check_interval: int = 60):
        """Start the file size monitoring task."""
        if self.monitoring_enabled:
            logger.warning("File monitoring already started")
            return
            
        self.monitoring_enabled = True
        self.check_interval = check_interval
        self.monitoring_task = asyncio.create_task(self._monitoring_loop())
        logger.info(f"Recording file size monitoring started ({check_interval}s intervals)")
    
    async def stop_monitoring(self):
        """Stop the file size monitoring task."""
        self.monitoring_enabled = False
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        logger.info("Recording file size monitoring stopped")
    
    async def _monitoring_loop(self):
        """Periodic task to check recording file sizes and detect completion."""
        while self.monitoring_enabled:
            try:
                await self._check_recording_files()
                
                # Signal that we should check for completed recordings
                # This will be picked up by the connector
                self._upload_check_needed = True
                
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval)
    
    async def _check_recording_files(self):
        """Check active recordings for completion based on file size stability."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                # Get recordings that exist but aren't complete or uploaded
                cursor = conn.execute("""
                    SELECT * FROM recording_metadata 
                    WHERE file_exists = 1 
                    AND recording_complete = 0 
                    AND uploaded = 0
                    AND file_path IS NOT NULL
                    ORDER BY started_at ASC
                """)
                
                active_recordings = [dict(row) for row in cursor.fetchall()]
                
            if not active_recordings:
                logger.debug("No active recordings to monitor")
                return
                
            logger.debug(f"Monitoring {len(active_recordings)} active recordings")
            
            for recording in active_recordings:
                await self._check_single_recording(recording)
                
        except Exception as e:
            logger.error(f"Error checking recording files: {e}", exc_info=True)
    
    async def _check_single_recording(self, recording: Dict[str, Any]):
        """Check a single recording for completion."""
        try:
            filename = recording['filename']
            file_path = recording['file_path']
            last_size = recording['file_size'] or 0
            stable_count = recording['size_stable_count'] or 0
            
            # Check if file still exists
            if not os.path.exists(file_path):
                logger.warning(f"Recording file no longer exists: {file_path}")
                self._mark_recording_missing(filename)
                return
            
            # Get current file size
            current_size = os.path.getsize(file_path)
            current_time = datetime.now().isoformat()
            
            logger.debug(f"Size check {filename}: {last_size} -> {current_size} bytes")
            
            # Check if size changed
            if current_size == last_size and last_size > 0:
                # File size is stable
                new_stable_count = stable_count + 1
                logger.debug(f"File size stable for {new_stable_count} checks: {filename}")
                
                # Minimum file size check - WAV headers are typically 44-80 bytes
                # Don't consider files complete if they're just headers
                MIN_RECORDING_SIZE = 1000  # 1KB minimum
                if current_size < MIN_RECORDING_SIZE:
                    logger.debug(f"File too small to be complete: {filename} ({current_size} bytes < {MIN_RECORDING_SIZE})")
                    # Reset stable count for small files to prevent premature upload
                    self._update_size_check(filename, current_size, current_time, 0)
                    return
                
                # Consider recording complete after 2 consecutive stable checks
                # Always require at least 2 checks to avoid race conditions
                required_checks = 2
                if new_stable_count >= required_checks:
                    logger.info(f"Recording complete (size stable): {filename} ({current_size} bytes)")
                    await self._mark_recording_complete(filename, current_size, file_path)
                    return
                else:
                    # Update stable count
                    self._update_size_check(filename, current_size, current_time, new_stable_count)
            else:
                # File size changed - still recording
                if current_size > last_size:
                    logger.debug(f"Recording still growing: {filename} (+{current_size - last_size} bytes)")
                else:
                    logger.debug(f"Recording size changed: {filename} ({current_size} bytes)")
                
                # Reset stable count
                self._update_size_check(filename, current_size, current_time, 0)
                
        except Exception as e:
            logger.error(f"Error checking recording {recording.get('filename')}: {e}", exc_info=True)
    
    def _update_size_check(self, filename: str, size: int, check_time: str, stable_count: int):
        """Update file size check information."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE recording_metadata 
                    SET file_size = ?, last_size_check = ?, size_stable_count = ?
                    WHERE filename = ?
                """, (size, check_time, stable_count, filename))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to update size check for {filename}: {e}")
    
    async def _mark_recording_complete(self, filename: str, final_size: int, file_path: str):
        """Mark recording as complete and signal for upload."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE recording_metadata 
                    SET recording_complete = 1, file_size = ?, stopped_at = ?
                    WHERE filename = ?
                """, (final_size, datetime.now().isoformat(), filename))
                conn.commit()
            
            logger.info(f"Recording completed and ready for upload: {filename} ({final_size} bytes)")
            
            # Note: The connector will check for completed recordings in its monitoring loop
            # and handle the upload process
                
        except Exception as e:
            logger.error(f"Failed to mark recording complete: {filename}: {e}")
    
    def _mark_recording_missing(self, filename: str):
        """Mark recording as missing (file deleted)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE recording_metadata 
                    SET file_exists = 0, stopped_at = ?
                    WHERE filename = ?
                """, (datetime.now().isoformat(), filename))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark recording missing: {filename}: {e}")
    
    def get_completed_recordings(self, retry_minutes: int = 0) -> List[Dict[str, Any]]:
        """Get recordings that are complete but not yet uploaded, including retries.
        
        Args:
            retry_minutes: If > 0, include failed uploads that are ready for retry.
                         If 0, retry is disabled.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                if retry_minutes > 0:
                    # Include recordings that:
                    # 1. Are complete but never uploaded (uploaded = 0)
                    # 2. Failed upload and are ready for retry
                    retry_cutoff = (datetime.now() - timedelta(minutes=retry_minutes)).isoformat()
                    cursor = conn.execute("""
                        SELECT * FROM recording_metadata 
                        WHERE recording_complete = 1 
                        AND (earliest_upload_time IS NULL OR earliest_upload_time <= ?)
                        AND (
                            uploaded = 0 
                            OR (
                                uploaded = 0 
                                AND upload_status != 202 
                                AND (last_upload_attempt IS NULL OR last_upload_attempt < ?)
                            )
                        )
                        ORDER BY upload_attempts ASC, stopped_at ASC
                    """, (datetime.now().isoformat(), retry_cutoff))
                else:
                    # Only get recordings that have never been attempted
                    cursor = conn.execute("""
                        SELECT * FROM recording_metadata 
                        WHERE recording_complete = 1 
                        AND (earliest_upload_time IS NULL OR earliest_upload_time <= ?)
                        AND uploaded = 0
                        AND upload_attempts = 0
                        ORDER BY stopped_at ASC
                    """, (datetime.now().isoformat(),))
                
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get completed recordings: {e}")
            return []
    
    def check_upload_needed(self) -> bool:
        """Check if upload check is needed and reset the flag."""
        if self._upload_check_needed:
            self._upload_check_needed = False
            return True
        return False


# Global instance
mixmonitor_tracker = MixMonitorTracker()