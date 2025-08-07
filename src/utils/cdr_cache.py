"""CDR cache for matching recordings with their associated CDRs."""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from collections import OrderedDict
import threading

logger = logging.getLogger(__name__)


class CDRCache:
    """
    Thread-safe cache for recent CDRs to enable matching with recordings.
    Uses a time-based sliding window to keep memory usage bounded.
    """
    
    def __init__(self, ttl_minutes: int = 30, max_size: int = 10000):
        """
        Initialize CDR cache.
        
        Args:
            ttl_minutes: Time to live for cached CDRs in minutes
            max_size: Maximum number of CDRs to cache
        """
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_size = max_size
        
        # Use OrderedDict to maintain insertion order for efficient cleanup
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        
        logger.info(f"CDR cache initialized with TTL={ttl_minutes}min, max_size={max_size}")
    
    def add_cdr(self, cdr_data: Dict[str, Any]) -> None:
        """
        Add a CDR to the cache.
        
        Args:
            cdr_data: CDR data including uniqueid, linkedid, calldate, and direction info
        """
        uniqueid = cdr_data.get('uniqueid')
        if not uniqueid:
            logger.warning("CDR missing uniqueid, skipping cache")
            return
            
        with self._lock:
            # Clean up old entries
            self._cleanup()
            
            # Add CDR with timestamp
            cache_entry = {
                'cdr': cdr_data,
                'cached_at': datetime.now()
            }
            
            # Remove if already exists (to update position)
            if uniqueid in self._cache:
                del self._cache[uniqueid]
                
            # Add to end (most recent)
            self._cache[uniqueid] = cache_entry
            
            # Enforce max size
            while len(self._cache) > self.max_size:
                # Remove oldest (first) item
                self._cache.popitem(last=False)
                
            logger.debug(f"Cached CDR: uniqueid={uniqueid}, linkedid={cdr_data.get('linkedid')}, "
                        f"direction={cdr_data.get('call_type')}, cache_size={len(self._cache)}")
    
    def find_by_linkedid(self, linkedid: str) -> Optional[Dict[str, Any]]:
        """
        Find CDR by linkedid.
        
        Args:
            linkedid: LinkedID to search for
            
        Returns:
            Matching CDR data or None
        """
        if not linkedid:
            return None
            
        with self._lock:
            for cache_entry in self._cache.values():
                cdr = cache_entry['cdr']
                if cdr.get('linkedid') == linkedid:
                    logger.debug(f"Found CDR by linkedid: {linkedid}")
                    return cdr
                    
        return None
    
    def find_by_uniqueid(self, uniqueid: str) -> Optional[Dict[str, Any]]:
        """
        Find CDR by uniqueid.
        
        Args:
            uniqueid: UniqueID to search for
            
        Returns:
            Matching CDR data or None
        """
        if not uniqueid:
            return None
            
        with self._lock:
            cache_entry = self._cache.get(uniqueid)
            if cache_entry:
                logger.debug(f"Found CDR by uniqueid: {uniqueid}")
                return cache_entry['cdr']
                
        return None
    
    def find_by_phone_numbers(self, src: str = None, dst: str = None, 
                            time_window_minutes: int = 5) -> List[Dict[str, Any]]:
        """
        Find CDRs by phone numbers within a time window.
        
        Args:
            src: Source phone number
            dst: Destination phone number
            time_window_minutes: Time window to search within
            
        Returns:
            List of matching CDRs
        """
        matches = []
        now = datetime.now()
        window = timedelta(minutes=time_window_minutes)
        
        with self._lock:
            for cache_entry in self._cache.values():
                cdr = cache_entry['cdr']
                cached_at = cache_entry['cached_at']
                
                # Check if within time window
                if now - cached_at > window:
                    continue
                    
                # Check phone number matches
                if src and cdr.get('src') == src:
                    matches.append(cdr)
                elif dst and cdr.get('dst') == dst:
                    matches.append(cdr)
                elif src and dst:
                    # Check both numbers
                    if (cdr.get('src') == src and cdr.get('dst') == dst) or \
                       (cdr.get('src') == dst and cdr.get('dst') == src):
                        matches.append(cdr)
                        
        logger.debug(f"Found {len(matches)} CDRs by phone numbers: src={src}, dst={dst}")
        return matches
    
    def get_direction_for_recording(self, metadata: Dict[str, Any]) -> Optional[str]:
        """
        Get direction for a recording by matching with cached CDRs.
        
        Args:
            metadata: Recording metadata that may contain uniqueid, linkedid, or phone numbers
            
        Returns:
            Direction string (inbound/outbound/internal) or None
        """
        # Try to match by linkedid first (most accurate)
        linkedid = metadata.get('linkedid')
        if linkedid:
            cdr = self.find_by_linkedid(linkedid)
            if cdr:
                direction = cdr.get('call_type') or cdr.get('direction')
                logger.info(f"Found direction by linkedid: {linkedid} -> {direction}")
                return direction
        
        # Try to match by uniqueid
        uniqueid = metadata.get('uniqueid')
        if uniqueid:
            cdr = self.find_by_uniqueid(uniqueid)
            if cdr:
                direction = cdr.get('call_type') or cdr.get('direction')
                logger.info(f"Found direction by uniqueid: {uniqueid} -> {direction}")
                return direction
        
        # Try to match by phone numbers
        src = metadata.get('src_number') or metadata.get('caller_id_num')
        dst = metadata.get('dst_number') or metadata.get('connected_line_num')
        
        if src or dst:
            matching_cdrs = self.find_by_phone_numbers(src, dst)
            if matching_cdrs:
                # Use the most recent match
                cdr = matching_cdrs[0]
                direction = cdr.get('call_type') or cdr.get('direction')
                logger.info(f"Found direction by phone numbers: src={src}, dst={dst} -> {direction}")
                return direction
        
        logger.debug(f"No matching CDR found for recording metadata: {metadata}")
        return None
    
    def _cleanup(self) -> None:
        """Remove expired entries from cache. Must be called with lock held."""
        now = datetime.now()
        expired_keys = []
        
        for uniqueid, cache_entry in self._cache.items():
            if now - cache_entry['cached_at'] > self.ttl:
                expired_keys.append(uniqueid)
            else:
                # Since OrderedDict maintains order, once we hit a non-expired entry,
                # all subsequent entries are also non-expired
                break
                
        for key in expired_keys:
            del self._cache[key]
            
        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired CDRs")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            return {
                'size': len(self._cache),
                'max_size': self.max_size,
                'ttl_minutes': self.ttl.total_seconds() / 60
            }


# Global CDR cache instance
_cdr_cache: Optional[CDRCache] = None


def get_cdr_cache() -> CDRCache:
    """Get or create the global CDR cache instance."""
    global _cdr_cache
    if _cdr_cache is None:
        _cdr_cache = CDRCache()
    return _cdr_cache