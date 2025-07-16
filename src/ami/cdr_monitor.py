"""CDR monitoring for AMI events with queue-based processing."""

import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from models.cdr import CDR, CEL
from utils.metrics import MetricsCollector, update_cdr_queue_depth, record_cdr_dropped

logger = logging.getLogger(__name__)


class CDRMonitor:
    """Monitor and process CDR/CEL events from Asterisk using async queue."""
    
    def __init__(self, 
                 queue: asyncio.Queue,
                 max_queue_size: int = 10000):
        """
        Initialize CDR monitor.
        
        Args:
            queue: AsyncIO queue to add CDR/CEL records to
            max_queue_size: Maximum queue size before applying backpressure
        """
        self.queue = queue
        self.max_queue_size = max_queue_size
        
        self.metrics = MetricsCollector()
        self._running = False
        self._dropped_count = 0
        
    async def start(self):
        """Start the CDR monitor."""
        self._running = True
        logger.info("CDR monitor started")
        
    async def stop(self):
        """Stop the CDR monitor."""
        self._running = False
        logger.info(f"CDR monitor stopped (dropped {self._dropped_count} records due to full queue)")
        
    async def handle_cdr_event(self, manager, event: Dict[str, Any]):
        """Handle CDR event from AMI."""
        try:
            # Skip if monitor is stopped
            if not self._running:
                return
                
            # Create CDR from event
            cdr = CDR.from_ami_event(event)
            
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
            
    def get_stats(self) -> Dict[str, Any]:
        """Get monitor statistics."""
        return {
            'queue_size': self.queue.qsize(),
            'dropped_count': self._dropped_count,
            'metrics': self.metrics.get_all(),
            'running': self._running
        }