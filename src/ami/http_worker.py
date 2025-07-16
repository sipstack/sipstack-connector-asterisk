"""HTTP Worker for processing CDR batches from queue."""

import asyncio
import logging
from typing import Optional, Callable
from datetime import datetime

from models.cdr import CDRBatch
from utils.metrics import MetricsCollector, update_http_worker_status, record_cdr_batch_duration, update_cdr_queue_depth

logger = logging.getLogger(__name__)


class HTTPWorker:
    """Worker that processes CDR batches from queue and sends to API."""
    
    def __init__(self,
                 queue: asyncio.Queue,
                 api_client,
                 batch_size: int = 100,
                 batch_timeout: float = 30.0,
                 max_retries: int = 3):
        """
        Initialize HTTP worker.
        
        Args:
            queue: AsyncIO queue to consume CDRs from
            api_client: API client for sending batches
            batch_size: Maximum batch size before sending
            batch_timeout: Maximum time to wait before sending partial batch
            max_retries: Maximum retry attempts for failed batches
        """
        self.queue = queue
        self.api_client = api_client
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.max_retries = max_retries
        
        self.metrics = MetricsCollector()
        self._running = False
        self._worker_task = None
        self._current_batch = CDRBatch()
        self._batch_timer = None
        
    async def start(self):
        """Start the HTTP worker."""
        if self._running:
            logger.warning("HTTP worker already running")
            return
            
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        update_http_worker_status(True)
        logger.info("HTTP worker started")
        
    async def stop(self):
        """Stop the HTTP worker and flush pending batch."""
        logger.info("Stopping HTTP worker...")
        self._running = False
        
        # Cancel batch timer
        if self._batch_timer:
            self._batch_timer.cancel()
            
        # Flush current batch
        if self._current_batch.size > 0:
            await self._send_batch(self._current_batch)
            
        # Cancel worker task
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        
        update_http_worker_status(False)        
        logger.info("HTTP worker stopped")
        
    async def _worker_loop(self):
        """Main worker loop that processes items from queue."""
        logger.info("HTTP worker loop started")
        
        while self._running:
            try:
                # Wait for item with timeout to check batch timeout
                timeout = 1.0  # Check every second
                
                try:
                    item = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=timeout
                    )
                    
                    # Add to current batch
                    if hasattr(item, 'calldate'):  # It's a CDR
                        self._current_batch.add_cdr(item)
                        self.metrics.increment('cdrs_queued')
                    else:  # It's a CEL
                        self._current_batch.add_cel(item)
                        self.metrics.increment('cels_queued')
                    
                    # Check if batch is ready
                    await self._check_batch()
                    
                    # Update queue depth metric after consuming
                    update_cdr_queue_depth(self.queue.qsize())
                    
                    # Yield control to prevent blocking
                    await asyncio.sleep(0)
                    
                except asyncio.TimeoutError:
                    # Check if we need to flush due to timeout
                    await self._check_timeout()
                    
            except asyncio.CancelledError:
                logger.info("Worker loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in worker loop: {e}", exc_info=True)
                self.metrics.increment('worker_errors')
                await asyncio.sleep(1)  # Prevent tight loop on errors
                
    async def _check_batch(self):
        """Check if batch should be sent."""
        if self._current_batch.size >= self.batch_size:
            logger.info(f"Batch size {self._current_batch.size} reached threshold")
            await self._flush_batch()
        elif not self._batch_timer:
            # Start batch timer
            self._batch_timer = asyncio.create_task(self._batch_timeout_handler())
            
    async def _check_timeout(self):
        """Check if batch timeout has been reached."""
        if self._batch_timer and self._current_batch.size > 0:
            # Timer is handled separately, just log queue depth
            queue_size = self.queue.qsize()
            if queue_size > 100:
                logger.warning(f"Queue depth high: {queue_size} items")
                self.metrics.record_value('queue_depth', queue_size)
                
    async def _batch_timeout_handler(self):
        """Handle batch timeout."""
        try:
            await asyncio.sleep(self.batch_timeout)
            if self._current_batch.size > 0:
                logger.info(f"Batch timeout reached with {self._current_batch.size} records")
                await self._flush_batch()
        except asyncio.CancelledError:
            pass
            
    async def _flush_batch(self):
        """Flush current batch to API."""
        if self._current_batch.size == 0:
            return
            
        # Cancel timer
        if self._batch_timer:
            self._batch_timer.cancel()
            self._batch_timer = None
            
        # Get batch and create new one
        batch_to_send = self._current_batch
        self._current_batch = CDRBatch()
        
        # Send with retry logic
        await self._send_batch(batch_to_send)
        
    async def _send_batch(self, batch: CDRBatch):
        """Send batch with retry logic."""
        retry_count = 0
        backoff_seconds = 1.0
        
        while retry_count < self.max_retries:
            try:
                logger.info(f"Sending batch of {batch.size} records (attempt {retry_count + 1})")
                
                # Record start time
                start_time = datetime.now()
                
                # Send batch
                await self.api_client.send_cdr_batch(batch)
                
                # Record metrics
                duration = (datetime.now() - start_time).total_seconds()
                self.metrics.increment('batches_sent')
                self.metrics.increment('records_sent', batch.size)
                self.metrics.record_value('batch_send_duration', duration)
                record_cdr_batch_duration(duration)  # Prometheus metric
                
                logger.info(f"Batch sent successfully in {duration:.2f}s")
                return
                
            except Exception as e:
                retry_count += 1
                self.metrics.increment('batch_send_errors')
                
                if retry_count >= self.max_retries:
                    logger.error(
                        f"Failed to send batch after {self.max_retries} attempts: {e}",
                        exc_info=True
                    )
                    self.metrics.increment('batches_failed')
                    # TODO: Implement dead letter queue
                    break
                else:
                    logger.warning(
                        f"Batch send failed (attempt {retry_count}), "
                        f"retrying in {backoff_seconds}s: {e}"
                    )
                    await asyncio.sleep(backoff_seconds)
                    backoff_seconds *= 2  # Exponential backoff
                    
    def get_stats(self) -> dict:
        """Get worker statistics."""
        return {
            'running': self._running,
            'current_batch_size': self._current_batch.size,
            'queue_depth': self.queue.qsize(),
            'metrics': self.metrics.get_all()
        }