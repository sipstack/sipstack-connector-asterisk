"""Direct CDR sender that sends events immediately without batching."""

import asyncio
import logging
from typing import Optional
from datetime import datetime

from models.cdr import CDR, CEL
from utils.metrics import MetricsCollector, update_http_worker_status

logger = logging.getLogger(__name__)


class DirectCDRSender:
    """Send CDR/CEL events directly to API without batching."""
    
    def __init__(self,
                 queue: asyncio.Queue,
                 api_client,
                 max_concurrent: int = 10,
                 max_retries: int = 3):
        """
        Initialize direct CDR sender.
        
        Args:
            queue: AsyncIO queue to read CDR/CEL records from
            api_client: API client for sending records
            max_concurrent: Maximum concurrent API requests
            max_retries: Maximum retry attempts for failed sends
        """
        self.queue = queue
        self.api_client = api_client
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        
        self.metrics = MetricsCollector()
        self._running = False
        self._worker_task = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        
    async def start(self):
        """Start the direct sender."""
        if self._running:
            logger.warning("Direct sender already running")
            return
            
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        update_http_worker_status(True)
        logger.info(f"Direct CDR sender started (max {self.max_concurrent} concurrent requests)")
        
    async def stop(self):
        """Stop the direct sender."""
        logger.info("Stopping direct CDR sender...")
        self._running = False
        
        # Cancel worker task
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        
        update_http_worker_status(False)        
        logger.info("Direct CDR sender stopped")
        
    async def _worker_loop(self):
        """Main worker loop that processes items from queue."""
        logger.info("Direct sender loop started")
        
        while self._running:
            try:
                # Wait for item with short timeout
                try:
                    item = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=0.1
                    )
                    
                    # Send immediately in background task
                    asyncio.create_task(self._send_item(item))
                    
                    # Yield control
                    await asyncio.sleep(0)
                    
                except asyncio.TimeoutError:
                    # Just yield and continue
                    await asyncio.sleep(0)
                    
            except asyncio.CancelledError:
                logger.info("Worker loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in worker loop: {e}", exc_info=True)
                self.metrics.increment('worker_errors')
                await asyncio.sleep(0.1)  # Brief pause on errors
                
    async def _send_item(self, item):
        """Send a single CDR or CEL item."""
        async with self._semaphore:  # Limit concurrent requests
            try:
                # Determine type and convert to dict
                if hasattr(item, 'calldate'):  # It's a CDR
                    record_type = "CDR"
                    record_dict = item.to_dict()
                else:  # It's a CEL
                    record_type = "CEL"
                    record_dict = item.to_dict()
                
                # Send with retry logic
                for attempt in range(self.max_retries):
                    try:
                        logger.debug(f"Sending {record_type} (attempt {attempt + 1})")
                        
                        # Send single record as a batch of 1
                        await self.api_client.send_cdr_batch({
                            'cdrs': [record_dict] if record_type == "CDR" else [],
                            'cels': [record_dict] if record_type == "CEL" else []
                        })
                        
                        # Success
                        self.metrics.increment(f'{record_type.lower()}s_sent')
                        logger.debug(f"{record_type} sent successfully")
                        break
                        
                    except Exception as e:
                        if attempt < self.max_retries - 1:
                            wait_time = 2 ** attempt  # Exponential backoff
                            logger.warning(
                                f"{record_type} send failed (attempt {attempt + 1}), "
                                f"retrying in {wait_time}s: {e}"
                            )
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"Failed to send {record_type} after {self.max_retries} attempts: {e}")
                            self.metrics.increment(f'{record_type.lower()}_send_errors')
                            
            except Exception as e:
                logger.error(f"Unexpected error sending record: {e}", exc_info=True)
                self.metrics.increment('send_errors')