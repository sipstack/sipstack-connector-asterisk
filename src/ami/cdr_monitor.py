"""CDR monitoring for AMI events."""

import asyncio
import logging
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timedelta

from models.cdr import CDR, CEL, CDRBatch
from utils.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class CDRMonitor:
    """Monitor and process CDR/CEL events from Asterisk."""
    
    def __init__(self, 
                 on_batch_ready: Callable[[CDRBatch], None],
                 batch_size: int = 100,
                 batch_timeout: float = 30.0):
        """
        Initialize CDR monitor.
        
        Args:
            on_batch_ready: Callback when batch is ready to send
            batch_size: Max records before triggering batch send
            batch_timeout: Max seconds before triggering batch send
        """
        self.on_batch_ready = on_batch_ready
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        
        self.batch = CDRBatch()
        self.metrics = MetricsCollector()
        self._batch_timer: Optional[asyncio.Task] = None
        self._running = False
        
    async def start(self):
        """Start the CDR monitor."""
        self._running = True
        logger.info("CDR monitor started")
        
    async def stop(self):
        """Stop the CDR monitor and flush batch."""
        self._running = False
        if self._batch_timer:
            self._batch_timer.cancel()
        await self._flush_batch()
        logger.info("CDR monitor stopped")
        
    async def handle_cdr_event(self, manager, event: Dict[str, Any]):
        """Handle CDR event from AMI."""
        try:
            # Skip if monitor is stopped
            if not self._running:
                return
                
            # Create CDR from event
            cdr = CDR.from_ami_event(event)
            
            # Add to batch
            self.batch.add_cdr(cdr)
            self.metrics.increment('cdr_received')
            
            logger.debug(f"CDR added to batch: {cdr.uniqueid} ({cdr.src} -> {cdr.dst})")
            
            # Check if batch is ready
            await self._check_batch()
            
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
            
            # Add to batch
            self.batch.add_cel(cel)
            self.metrics.increment('cel_received')
            
            logger.debug(f"CEL added to batch: {cel.uniqueid} ({cel.eventtype})")
            
            # Check if batch is ready
            await self._check_batch()
            
        except Exception as e:
            logger.error(f"Error handling CEL event: {e}", exc_info=True)
            self.metrics.increment('cel_error')
            
    async def _check_batch(self):
        """Check if batch should be sent."""
        logger.debug(f"Checking batch: size={self.batch.size}, threshold={self.batch_size}")
        if self.batch.size >= self.batch_size:
            logger.info(f"Batch size {self.batch.size} reached threshold {self.batch_size}, flushing")
            await self._flush_batch()
        elif not self._batch_timer:
            # Start batch timer
            logger.debug(f"Starting batch timer for {self.batch_timeout} seconds")
            self._batch_timer = asyncio.create_task(self._batch_timeout())
            
    async def _batch_timeout(self):
        """Wait for batch timeout and flush."""
        try:
            await asyncio.sleep(self.batch_timeout)
            await self._flush_batch()
        except asyncio.CancelledError:
            pass
            
    async def _flush_batch(self):
        """Flush the current batch."""
        if self.batch.size == 0:
            return
            
        # Cancel timer if running
        if self._batch_timer:
            self._batch_timer.cancel()
            self._batch_timer = None
            
        # Get batch data
        batch_data = self.batch
        
        # Create new batch
        self.batch = CDRBatch()
        
        # Send batch
        try:
            logger.info(f"Flushing batch with {batch_data.size} records")
            await self.on_batch_ready(batch_data)
            self.metrics.increment('batch_sent')
            self.metrics.increment('records_sent', batch_data.size)
        except Exception as e:
            logger.error(f"Error sending batch: {e}", exc_info=True)
            self.metrics.increment('batch_error')
            # TODO: Implement retry logic or dead letter queue
            
    def get_stats(self) -> Dict[str, Any]:
        """Get monitor statistics."""
        return {
            'current_batch_size': self.batch.size,
            'metrics': self.metrics.get_all(),
            'running': self._running
        }