"""API-Regional CDR client for data ingestion."""

import asyncio
import aiohttp
import logging
import json
import os
from typing import Optional, Dict, Any, List
from datetime import datetime
import backoff
import asyncio

from models.cdr import CDRBatch
from utils.metrics import MetricsCollector
from .smart_key_parser import SmartKeyParser, ParsedApiKey
from .cdr_mapper import CDRMapper
from __version__ import __version__

logger = logging.getLogger(__name__)


class ApiRegionalCDRClient:
    """Client for sending CDR/CEL data to API-Regional service."""
    
    def __init__(self, 
                 api_base_url: str,
                 api_key: str,
                 timeout: float = 30.0,
                 max_retries: int = 3,
                 host_info: Optional[Dict[str, str]] = None):
        """
        Initialize API-Regional CDR client.
        
        Args:
            api_base_url: API-Regional service base URL (e.g., http://localhost:3000)
            api_key: Smart API key for authentication (format: sk_t{tier}_{customer}_{token})
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
        """
        self.api_base_url = api_base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.host_info = host_info  # Store host information for CDR mapping
        
        # Parse API key for format validation
        self.parsed_key = SmartKeyParser.parse(api_key)
        if self.parsed_key.is_standard_key:
            logger.info("Using standard API key format")
        elif self.parsed_key.is_smart_key:
            logger.info(
                f"Using legacy smart API key - Tier: {self.parsed_key.tier}, "
                f"Customer: {self.parsed_key.customer_id or 'encrypted'}, "
                f"Rate limit: {self.parsed_key.rate_limit}/min"
            )
        else:
            logger.info(f"Using {self.parsed_key.format} API key format")
            if not self.parsed_key.is_valid:
                logger.warning(f"API key validation issue: {self.parsed_key.error}")
        
        self.metrics = MetricsCollector()
        self._session: Optional[aiohttp.ClientSession] = None
        
        # Rate limiting for legacy smart keys only
        self._last_request_time = 0
        self._request_count = 0
        self._rate_window_start = datetime.now()
        
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()
        
    async def start(self):
        """Start the client session with optimized settings for high performance."""
        if not self._session:
            # Use simple session configuration that works
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            
            # Set custom headers including User-Agent
            headers = {
                'User-Agent': f'SIPSTACK-Connector-Asterisk/{__version__}'
            }
            
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers=headers
            )
        logger.info("API-Regional CDR client started")
        
    async def stop(self):
        """Stop the client session."""
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("API-Regional CDR client stopped")
        
    def _get_headers(self) -> Dict[str, str]:
        """Get request headers."""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_key}'
        }
        # User-Agent is already set in session headers
        
        return headers
    
    async def _check_rate_limit(self):
        """Check and enforce rate limits for legacy smart keys only."""
        # Skip rate limiting for standard keys (handled server-side)
        if self.parsed_key.is_standard_key:
            return
            
        # Also skip for other non-smart key formats
        if not self.parsed_key.is_smart_key or not self.parsed_key.rate_limit:
            return
            
        now = datetime.now()
        window_duration = (now - self._rate_window_start).total_seconds()
        
        # Reset window if more than 60 seconds
        if window_duration >= 60:
            self._request_count = 0
            self._rate_window_start = now
            return
            
        # Check if we've exceeded rate limit
        if self._request_count >= self.parsed_key.rate_limit:
            # Calculate wait time
            wait_time = 60 - window_duration
            logger.warning(
                f"Rate limit reached ({self.parsed_key.rate_limit}/min). "
                f"Waiting {wait_time:.1f}s"
            )
            await asyncio.sleep(wait_time)
            # Reset after waiting
            self._request_count = 0
            self._rate_window_start = datetime.now()
            
        self._request_count += 1
        
    @backoff.on_exception(
        backoff.expo,
        (aiohttp.ClientError, asyncio.TimeoutError),
        max_tries=3,
        max_time=60
    )
    async def send_batch(self, batch):
        """
        Send a batch of CDR/CEL records to API-Regional service.
        
        Args:
            batch: CDRBatch or dict containing records to send
        """
        if not self._session:
            raise RuntimeError("Client not started. Call start() first.")
        
        # Handle dict format for direct sending
        if isinstance(batch, dict):
            cdrs = batch.get('cdrs', [])
            cels = batch.get('cels', [])
            batch_size = len(cdrs) + len(cels)
            logger.debug(f"Preparing to send {batch_size} records from dict")
        else:
            batch_size = batch.size
            logger.debug(f"Preparing to send batch with {batch_size} records")
        
        # Check rate limit before sending
        await self._check_rate_limit()
            
        start_time = datetime.now()
        
        try:
            # Combine CDRs and CELs into a single batch request
            records = []
            
            # Handle dict format
            if isinstance(batch, dict):
                cdrs = batch.get('cdrs', [])
                cels = batch.get('cels', [])
                if cdrs:
                    # Already in dict format, just map to MQS
                    records.extend([CDRMapper.to_mqs_format(cdr, self.host_info) for cdr in cdrs])
                    logger.debug(f"Mapped {len(cdrs)} CDRs to MQS format")
                if cels:
                    records.extend(cels)
                    logger.debug(f"Added {len(cels)} CELs")
            else:
                # Original CDRBatch format
                if batch.cdrs:
                    # Map CDRs to MQS format
                    records.extend([CDRMapper.to_mqs_format(cdr, self.host_info) for cdr in batch.cdrs])
                    logger.debug(f"Mapped {len(batch.cdrs)} CDRs to MQS format")
                if batch.cels:
                    # CELs use their original format for now
                    records.extend([cel.to_dict() for cel in batch.cels])
                    logger.debug(f"Added {len(batch.cels)} CELs")
            
            if records:
                logger.info(f"Sending {len(records)} records to {self.api_base_url}/mqs/cdr/batch")
                await self._send_batch_records(records)
                
            # Record metrics
            duration = (datetime.now() - start_time).total_seconds()
            self.metrics.increment('batches_sent')
            self.metrics.increment('records_sent', batch.size)
            self.metrics.record_value('batch_duration', duration)
            
            logger.info(f"Batch sent successfully: {len(batch.cdrs)} CDRs, {len(batch.cels)} CELs in {duration:.2f}s")
            
        except Exception as e:
            self.metrics.increment('batch_errors')
            logger.error(f"Error sending batch: {e}", exc_info=True)
            raise
            
    async def _send_batch_records(self, records: List[Dict[str, Any]]):
        """
        Send a batch of CDR records to the API-Regional service.
        
        Args:
            records: List of CDR record dictionaries
        """
        if not self._session:
            raise RuntimeError("Client not started. Call start() first.")
            
        url = f"{self.api_base_url}/mqs/cdr/batch"
        
        headers = self._get_headers()
        logger.debug(f"Request URL: {url}")
        logger.debug(f"Request headers: {headers}")
        logger.debug(f"Number of records: {len(records)}")
        if records and len(records) > 0:
            logger.debug(f"Sample record: {json.dumps(records[0], default=str)[:500]}")
            # Check payload size
            payload_size = len(json.dumps(records, default=str))
            logger.debug(f"Total payload size: {payload_size} bytes ({payload_size/1024:.1f} KB)")
        
        try:
            logger.debug("Making POST request to API...")
            
            # Add shorter timeout for this specific request
            timeout = aiohttp.ClientTimeout(total=10.0)  # 10 second timeout
            
            # Yield control before making request
            await asyncio.sleep(0)
            
            async with self._session.post(
                url,
                headers=headers,
                json=records,
                timeout=timeout
            ) as response:
                logger.debug(f"Response received - status: {response.status}")
                
                # Yield control after receiving response
                await asyncio.sleep(0)
                
                if response.status == 202:
                    # Handle accepted response
                    result = await response.json()
                    job_ids = result.get('job_ids', [])
                    queued_count = result.get('queued_count', 0)
                    logger.info(f"Batch accepted: {queued_count} records queued, job IDs: {job_ids}")
                    self.metrics.increment('records_queued', queued_count)
                elif response.status in (200, 201, 204):
                    # Success
                    logger.info(f"Batch sent successfully: {len(records)} records")
                    self.metrics.increment('records_sent', len(records))
                else:
                    error_text = await response.text()
                    raise Exception(f"API error {response.status}: {error_text}")
                    
                self.metrics.increment('cdr_inserted', len(records))
                logger.debug(f"Sent {len(records)} CDR records")
                
        except aiohttp.ClientError as e:
            logger.error(f"HTTP client error sending batch: {e}", exc_info=True)
            raise
        except asyncio.TimeoutError as e:
            logger.error(f"Timeout error sending batch: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error sending batch: {e}", exc_info=True)
            raise
            
    async def send_cdr(self, cdr: Dict[str, Any]):
        """
        Send a single CDR record.
        
        Args:
            cdr: CDR dictionary
        """
        url = f"{self.api_base_url}/mqs/cdr"
        
        headers = self._get_headers()
        
        async with self._session.post(
            url,
            headers=headers,
            json=cdr
        ) as response:
            if response.status == 202:
                # Handle accepted response
                result = await response.json()
                logger.info(f"CDR accepted: {result}")
            elif response.status in (200, 201, 204):
                logger.debug("CDR sent successfully")
            else:
                error_text = await response.text()
                raise Exception(f"API error {response.status}: {error_text}")
                
            self.metrics.increment('cdr_sent')
        
    async def test_connection(self) -> bool:
        """
        Test the connection to API-Regional service.
        
        Returns:
            True if connection is successful
        """
        if not self._session:
            logger.error("Connection test failed: Session not initialized")
            return False
            
        try:
            # Try health check endpoint
            url = f"{self.api_base_url}/health"
            logger.info(f"Testing connection to {url}")
            
            # Use consistent headers with the rest of the client
            headers = self._get_headers()
            # Remove Content-Type for GET request
            headers.pop('Content-Type', None)
            
            async with self._session.get(
                url,
                headers=headers
            ) as response:
                logger.info(f"Connection test response: {response.status}")
                # Any non-5xx response means the connection works
                return response.status < 500
                
        except aiohttp.ClientError as e:
            logger.error(f"Connection test failed with client error: {e}")
            return False
        except Exception as e:
            logger.error(f"Connection test failed: {e}", exc_info=True)
            return False
            
    async def upload_recording(self, file_path: str, metadata: Dict[str, Any], endpoint: str = 'recording') -> Dict[str, Any]:
        """
        Upload a recording file to API-Regional service.
        
        Args:
            file_path: Path to the recording file
            metadata: Recording metadata
            endpoint: API endpoint ('recording' or 'queue-recording')
            
        Returns:
            API response data
        """
        if not self._session:
            raise RuntimeError("Client not started. Call start() first.")
            
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Recording file not found: {file_path}")
            
        # Check rate limit before sending
        await self._check_rate_limit()
        
        url = f"{self.api_base_url}/mqs/{endpoint}"
        logger.info(f"Uploading recording to {url}: {file_path}")
        
        try:
            # Prepare multipart form data
            data = aiohttp.FormData()
            
            # Add the audio file
            with open(file_path, 'rb') as f:
                file_content = f.read()
                filename = os.path.basename(file_path)
                data.add_field('file', file_content, filename=filename, content_type='audio/wav')
                
            # Add metadata fields
            # Convert metadata to match API expectations
            if 'caller_id_num' in metadata:
                data.add_field('src_number', str(metadata.get('caller_id_num', '')))
            if 'connected_line_num' in metadata:
                data.add_field('dst_number', str(metadata.get('connected_line_num', '')))
            if 'direction' in metadata:
                data.add_field('direction', str(metadata.get('direction', 'inbound')))
            if 'uniqueid' in metadata:
                data.add_field('call_id', str(metadata.get('uniqueid', '')))
            if 'duration' in metadata:
                data.add_field('duration', str(metadata.get('duration', '0')))
            if 'queue' in metadata:
                data.add_field('queue_name', str(metadata.get('queue', '')))
            if 'timestamp' in metadata:
                data.add_field('start_time', str(metadata.get('timestamp', '')))
                
            # Add customer ID from parsed API key if available (legacy smart keys only)
            if self.parsed_key.is_smart_key and self.parsed_key.customer_id:
                data.add_field('customer_id', self.parsed_key.customer_id)
                
            # Add any additional metadata as JSON
            extra_metadata = {
                k: v for k, v in metadata.items() 
                if k not in ['caller_id_num', 'connected_line_num', 'direction', 
                             'uniqueid', 'duration', 'queue', 'timestamp']
            }
            if extra_metadata:
                data.add_field('metadata', json.dumps(extra_metadata))
            
            # Prepare headers (remove Content-Type as it will be set by FormData)
            headers = {
                'Authorization': f'Bearer {self.api_key}'
            }
            
            # Upload with longer timeout for file uploads
            timeout = aiohttp.ClientTimeout(total=60.0)
            
            async with self._session.post(
                url,
                data=data,
                headers=headers,
                timeout=timeout
            ) as response:
                response_data = await response.json()
                
                if response.status == 202:
                    # Accepted - recording queued for processing
                    logger.info(f"Recording queued for processing: {response_data}")
                    self.metrics.increment('recordings_uploaded')
                    return response_data
                elif response.status in (200, 201):
                    # Success
                    logger.info(f"Recording uploaded successfully: {response_data}")
                    self.metrics.increment('recordings_uploaded')
                    return response_data
                else:
                    error_msg = response_data.get('error', 'Unknown error')
                    raise Exception(f"API error {response.status}: {error_msg}")
                        
        except Exception as e:
            self.metrics.increment('recording_upload_errors')
            logger.error(f"Error uploading recording {file_path}: {e}", exc_info=True)
            raise
            
    def get_stats(self) -> Dict[str, Any]:
        """Get client statistics."""
        return {
            'metrics': self.metrics.get_all(),
            'connected': self._session is not None
        }