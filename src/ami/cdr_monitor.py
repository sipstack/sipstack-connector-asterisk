"""CDR monitoring for AMI events with queue-based processing."""

import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from models.cdr import CDR, CEL
from utils.metrics import MetricsCollector, update_cdr_queue_depth, record_cdr_dropped, record_cdr_filtered

logger = logging.getLogger(__name__)


class CDRMonitor:
    """Monitor and process CDR/CEL events from Asterisk using async queue."""
    
    def __init__(self, 
                 queue: asyncio.Queue,
                 max_queue_size: int = 10000,
                 filter_config: Optional[Dict[str, Any]] = None):
        """
        Initialize CDR monitor.
        
        Args:
            queue: AsyncIO queue to add CDR/CEL records to
            max_queue_size: Maximum queue size before applying backpressure
        """
        self.queue = queue
        self.max_queue_size = max_queue_size
        self.filter_config = filter_config or {}
        
        self.metrics = MetricsCollector()
        self._running = False
        self._dropped_count = 0
        self._filtered_count = 0
        
        # Log filter configuration
        if self.filter_config.get('enabled', False):
            logger.info(f"CDR filtering enabled with config: {self.filter_config}")
        
    async def start(self):
        """Start the CDR monitor."""
        self._running = True
        logger.info("CDR monitor started")
        
    async def stop(self):
        """Stop the CDR monitor."""
        self._running = False
        logger.info(f"CDR monitor stopped (dropped: {self._dropped_count}, filtered: {self._filtered_count})")
        
    async def handle_cdr_event(self, manager, event: Dict[str, Any]):
        """Handle CDR event from AMI."""
        try:
            # Skip if monitor is stopped
            if not self._running:
                return
                
            # Create CDR from event
            cdr = CDR.from_ami_event(event)
            
            # Apply filtering if enabled
            if self.filter_config.get('enabled', False) and self._should_filter_cdr(cdr):
                self._filtered_count += 1
                self.metrics.increment('cdr_filtered')
                record_cdr_filtered()  # Update Prometheus metric
                logger.debug(f"CDR filtered out: {cdr.uniqueid} ({cdr.src} -> {cdr.dst}, disposition: {cdr.disposition})")
                return
            
            # Try to add to queue with non-blocking put
            try:
                self.queue.put_nowait(cdr)
                self.metrics.increment('cdr_received')
                logger.debug(f"CDR added to queue: {cdr.uniqueid} ({cdr.src} -> {cdr.dst})")
                # Update queue depth metric
                update_cdr_queue_depth(self.queue.qsize())
            except asyncio.QueueFull:
                self._dropped_count += 1
                self.metrics.increment('cdr_dropped')
                record_cdr_dropped()  # Update Prometheus metric
                logger.warning(
                    f"Queue full ({self.queue.qsize()}/{self.max_queue_size}), "
                    f"dropping CDR: {cdr.uniqueid}"
                )
            
            # Yield control to prevent event loop blocking
            await asyncio.sleep(0)
            
        except Exception as e:
            logger.error(f"Error handling CDR event: {e}", exc_info=True)
            self.metrics.increment('cdr_error')
            
    async def handle_cel_event(self, manager, event: Dict[str, Any]):
        """Handle CEL event from AMI."""
        try:
            # Skip if monitor is stopped
            if not self._running:
                return
                
            # Create CEL from event
            cel = CEL.from_ami_event(event)
            
            # Try to add to queue with non-blocking put
            try:
                self.queue.put_nowait(cel)
                self.metrics.increment('cel_received')
                logger.debug(f"CEL added to queue: {cel.uniqueid} ({cel.eventtype})")
                # Update queue depth metric
                update_cdr_queue_depth(self.queue.qsize())
            except asyncio.QueueFull:
                self._dropped_count += 1
                self.metrics.increment('cel_dropped')
                record_cdr_dropped()  # Update Prometheus metric
                logger.warning(
                    f"Queue full ({self.queue.qsize()}/{self.max_queue_size}), "
                    f"dropping CEL: {cel.uniqueid}"
                )
            
            # Yield control to prevent event loop blocking
            await asyncio.sleep(0)
            
        except Exception as e:
            logger.error(f"Error handling CEL event: {e}", exc_info=True)
            self.metrics.increment('cel_error')
    
    def _should_filter_cdr(self, cdr: CDR) -> bool:
        """
        Determine if a CDR should be filtered out based on configuration.
        
        Returns:
            True if the CDR should be filtered (not processed), False otherwise
        """
        # Filter queue attempts (dst='s' with NO ANSWER)
        if self.filter_config.get('queue_attempts', True):
            if cdr.dst in self.filter_config.get('exclude_destinations', ['s', 'h']) and cdr.disposition == 'NO ANSWER':
                return True
        
        # Filter zero duration calls (except BUSY/FAILED)
        if self.filter_config.get('zero_duration', True):
            if cdr.duration == 0 and cdr.billsec == 0 and cdr.disposition not in ['BUSY', 'FAILED', 'CONGESTION']:
                return True
        
        # Filter by minimum duration
        min_duration = self.filter_config.get('min_duration', 0)
        if min_duration > 0 and cdr.duration < min_duration:
            return True
        
        # Filter internal only (if enabled, only keep calls where both src and dst are numeric extensions)
        if self.filter_config.get('internal_only', False):
            # Check if both src and dst are numeric (extensions)
            if not (cdr.src.isdigit() and cdr.dst.isdigit()):
                return True
        
        return False
            
    def get_stats(self) -> Dict[str, Any]:
        """Get monitor statistics."""
        stats = {
            'queue_size': self.queue.qsize(),
            'dropped_count': self._dropped_count,
            'filtered_count': self._filtered_count,
            'metrics': self.metrics.get_all(),
            'running': self._running
        }
        
        # Add filter configuration if enabled
        if self.filter_config.get('enabled', False):
            stats['filter_config'] = self.filter_config
            
        return stats