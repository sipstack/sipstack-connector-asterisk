"""
Recording linker for database-driven connector.
Links recordings to calls based on call_id and filesystem scanning.
"""

import os
import logging
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class RecordingLinker:
    """
    Links recording files to calls based on call_id patterns.
    Scans recording directories to find matching files.
    """
    
    def __init__(self, recording_paths: str):
        """
        Initialize recording linker.
        
        Args:
            recording_paths: Comma-separated list of recording directories
        """
        self.recording_paths = [
            Path(path.strip()) 
            for path in recording_paths.split(',') 
            if path.strip()
        ]
        
        logger.info(f"Recording linker initialized with paths: {self.recording_paths}")
    
    def find_recordings_for_call(self, linkedid: str, call_threads: List[Dict]) -> List[Dict]:
        """
        Find recording files associated with a call.
        
        Args:
            linkedid: Call's linkedid
            call_threads: List of call thread events
            
        Returns:
            List of recording file dictionaries
        """
        recordings = []
        
        # Extract uniqueids from call threads for file matching
        uniqueids = set()
        for thread in call_threads:
            if thread.get('uniqueid'):
                uniqueids.add(thread['uniqueid'])
        
        # Add linkedid as potential filename component
        uniqueids.add(linkedid)
        
        # Scan recording directories
        for recording_path in self.recording_paths:
            if not recording_path.exists():
                logger.debug(f"Recording path does not exist: {recording_path}")
                continue
                
            try:
                # Search for files containing any of our uniqueids
                for uniqueid in uniqueids:
                    # Common Asterisk recording patterns
                    patterns = [
                        f"*{uniqueid}*",
                        f"*{linkedid}*"
                    ]
                    
                    for pattern in patterns:
                        for file_path in recording_path.glob(f"**/{pattern}"):
                            if file_path.is_file() and file_path.suffix.lower() in ['.wav', '.mp3', '.gsm', '.ulaw', '.alaw']:
                                recording_info = {
                                    'file_path': str(file_path),
                                    'file_name': file_path.name,
                                    'file_size': file_path.stat().st_size,
                                    'created_at': file_path.stat().st_mtime,
                                    'uniqueid': uniqueid,
                                    'linkedid': linkedid
                                }
                                recordings.append(recording_info)
                                logger.debug(f"Found recording: {file_path}")
                                
            except Exception as e:
                logger.warning(f"Error scanning recording path {recording_path}: {e}")
        
        if recordings:
            logger.info(f"Found {len(recordings)} recordings for linkedid {linkedid}")
        else:
            logger.debug(f"No recordings found for linkedid {linkedid}")
            
        return recordings