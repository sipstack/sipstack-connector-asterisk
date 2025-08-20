"""API-Regional CDR client for data ingestion."""

import asyncio
import aiohttp
import logging
import json
import os
import io
from typing import Optional, Dict, Any, List, BinaryIO
from datetime import datetime
import backoff
import asyncio

from models.cdr import CDRBatch
from utils.metrics import MetricsCollector
from .smart_key_parser import SmartKeyParser, ParsedApiKey
from .cdr_mapper import CDRMapper
from __version__ import __version__

logger = logging.getLogger(__name__)


class ChunkedFileReader(io.IOBase):
    """A file reader that reads files in chunks to avoid loading entire file into memory."""
    
    def __init__(self, file_path: str, chunk_size: int = 65536):  # 64KB chunks
        self.file_path = file_path
        self.chunk_size = chunk_size
        self.file_handle: Optional[BinaryIO] = None
        self.file_size = os.path.getsize(file_path)
        self.bytes_read = 0
        
    def __enter__(self):
        self.file_handle = open(self.file_path, 'rb')
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.file_handle:
            self.file_handle.close()
            
    def read(self, size: int = -1) -> bytes:
        """Read and return up to size bytes, or if size is -1, until EOF."""
        if not self.file_handle:
            raise ValueError("File not opened. Use with statement.")
            
        if size == -1:
            size = self.chunk_size
            
        data = self.file_handle.read(size)
        self.bytes_read += len(data)
        
        if len(data) > 0:
            logger.debug(f"Read chunk: {len(data)} bytes, total read: {self.bytes_read}/{self.file_size}")
            
        return data
        
    def close(self):
        """Close the file handle."""
        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None


class ApiRegionalCDRClient:
    """Client for sending CDR/CEL data to API-Regional service."""
    
    def __init__(self, 
                 api_base_url: str,
                 api_key: str,
                 timeout: float = 30.0,
                 max_retries: int = 3,
                 host_info: Optional[Dict[str, str]] = None,
                 max_memory_file_size: int = 10 * 1024 * 1024,  # 10MB default
                 max_concurrent_uploads: int = 10):
        """
        Initialize API-Regional CDR client.
        
        Args:
            api_base_url: API-Regional service base URL (e.g., http://localhost:3000)
            api_key: Smart API key for authentication (format: sk_t{tier}_{customer}_{token})
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
            host_info: Optional host information dictionary
            max_memory_file_size: Maximum file size to load into memory (bytes). Larger files use streaming.
            max_concurrent_uploads: Maximum number of concurrent file uploads
        """
        self.api_base_url = api_base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.host_info = host_info  # Store host information for CDR mapping
        self.max_memory_file_size = max_memory_file_size
        self.max_concurrent_uploads = max_concurrent_uploads
        self._upload_semaphore = asyncio.Semaphore(max_concurrent_uploads)
        
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
            
            # Configure connector with proper limits for file uploads
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=30,
                force_close=True,  # Force close connections to avoid reuse issues
                enable_cleanup_closed=True
            )
            
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers=headers,
                read_bufsize=65536  # Increase read buffer size to 64KB
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
        
        # Add hostname header if available
        if self.host_info and self.host_info.get('hostname'):
            headers['X-Asterisk-Hostname'] = self.host_info['hostname']
        
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
            # Check if we should use new format (when we have CELs) or legacy format
            has_cels = (isinstance(batch, dict) and batch.get('cels')) or (hasattr(batch, 'cels') and batch.cels)
            
            if has_cels:
                # Use new format when we have CELs
                batch_data = {'cdrs': [], 'cels': []}
                
                # Handle dict format
                if isinstance(batch, dict):
                    cdrs = batch.get('cdrs', [])
                    cels = batch.get('cels', [])
                    if cdrs:
                        batch_data['cdrs'] = [CDRMapper.to_mqs_format(cdr, self.host_info) for cdr in cdrs]
                        logger.debug(f"Mapped {len(cdrs)} CDRs to MQS format")
                    if cels:
                        batch_data['cels'] = cels
                        logger.debug(f"Added {len(cels)} CELs")
                else:
                    # Original CDRBatch format
                    if batch.cdrs:
                        batch_data['cdrs'] = [CDRMapper.to_mqs_format(cdr, self.host_info) for cdr in batch.cdrs]
                        logger.debug(f"Mapped {len(batch.cdrs)} CDRs to MQS format")
                    if batch.cels:
                        batch_data['cels'] = [cel.to_dict() for cel in batch.cels]
                        logger.debug(f"Added {len(batch.cels)} CELs")
                
                total_records = len(batch_data['cdrs']) + len(batch_data['cels'])
                if total_records > 0:
                    logger.info(f"Sending {total_records} records (new format) to {self.api_base_url}/mqs/connectors/asterisk/cdr")
                    await self._send_batch_records(batch_data)
            else:
                # Use legacy format for CDR-only batches
                records = []
                
                # Handle dict format
                if isinstance(batch, dict):
                    cdrs = batch.get('cdrs', [])
                    if cdrs:
                        records = [CDRMapper.to_mqs_format(cdr, self.host_info) for cdr in cdrs]
                        logger.debug(f"Mapped {len(cdrs)} CDRs to MQS format")
                else:
                    # Original CDRBatch format
                    if batch.cdrs:
                        records = [CDRMapper.to_mqs_format(cdr, self.host_info) for cdr in batch.cdrs]
                        logger.debug(f"Mapped {len(batch.cdrs)} CDRs to MQS format")
                
                if records:
                    logger.info(f"Sending {len(records)} records (legacy format) to {self.api_base_url}/mqs/connectors/asterisk/cdr")
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
            
    async def _send_batch_records(self, batch_data):
        """
        Send a batch of CDR/CEL records to the API-Regional service.
        
        Args:
            batch_data: Either a list (legacy format) or dict with 'cdrs' and 'cels' arrays
        """
        if not self._session:
            raise RuntimeError("Client not started. Call start() first.")
            
        url = f"{self.api_base_url}/mqs/connectors/asterisk/cdr"
        
        headers = self._get_headers()
        logger.debug(f"Request URL: {url}")
        logger.debug(f"Request headers: {headers}")
        
        # Handle both formats
        if isinstance(batch_data, list):
            # Legacy format
            total_records = len(batch_data)
            logger.debug(f"Number of records: {total_records} (legacy format)")
            if batch_data and len(batch_data) > 0:
                logger.debug(f"Sample record: {json.dumps(batch_data[0], default=str)[:500]}")
        else:
            # New format
            total_records = len(batch_data.get('cdrs', [])) + len(batch_data.get('cels', []))
            logger.debug(f"Number of records: {total_records} (CDRs: {len(batch_data.get('cdrs', []))}, CELs: {len(batch_data.get('cels', []))})")
            
            if batch_data.get('cdrs') and len(batch_data['cdrs']) > 0:
                logger.debug(f"Sample CDR: {json.dumps(batch_data['cdrs'][0], default=str)[:500]}")
            if batch_data.get('cels') and len(batch_data['cels']) > 0:
                logger.debug(f"Sample CEL: {json.dumps(batch_data['cels'][0], default=str)[:500]}")
            
        # Check payload size
        payload_size = len(json.dumps(batch_data, default=str))
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
                json=batch_data,
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
                    logger.info(f"Batch sent successfully: {total_records} records")
                    self.metrics.increment('records_sent', total_records)
                else:
                    error_text = await response.text()
                    raise Exception(f"API error {response.status}: {error_text}")
                    
                self.metrics.increment('cdr_inserted', total_records)
                logger.debug(f"Sent {total_records} CDR/CEL records")
                
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
        
        # Use semaphore to limit concurrent uploads
        async with self._upload_semaphore:
            logger.debug(f"Acquired upload slot for {file_path}")
            
            # Check rate limit before sending
            await self._check_rate_limit()
            
            url = f"{self.api_base_url}/mqs/{endpoint}"
            logger.info(f"Uploading recording to {url}: {file_path}")
            
            try:
                # Prepare multipart form data
                data = aiohttp.FormData()
                
                # Get filename for metadata
                filename = os.path.basename(file_path)
                
                # Add metadata fields FIRST (before file)
                # Convert metadata to match API expectations
                # REQUIRED: recording_id - use uniqueid or filename
                recording_id = metadata.get('uniqueid', metadata.get('file_name', filename))
                data.add_field('recording_id', str(recording_id))
                
                # Add all available metadata fields for the API to process
                # Source number - prefer explicit src_number, fallback to caller_id_num
                if 'src_number' in metadata:
                    data.add_field('src_number', str(metadata.get('src_number', '')))
                elif 'caller_id_num' in metadata:
                    data.add_field('src_number', str(metadata.get('caller_id_num', '')))
                    
                # Destination number - prefer explicit dst_number, fallback to connected_line_num
                if 'dst_number' in metadata:
                    data.add_field('dst_number', str(metadata.get('dst_number', '')))
                elif 'connected_line_num' in metadata:
                    data.add_field('dst_number', str(metadata.get('connected_line_num', '')))
                
                # Call identification
                if 'uniqueid' in metadata:
                    data.add_field('call_id', str(metadata.get('uniqueid', '')))
                    data.add_field('uniqueid', str(metadata.get('uniqueid', '')))  # Send both
                if 'linkedid' in metadata:
                    data.add_field('linkedid', str(metadata.get('linkedid', '')))
                    
                # Call details
                if 'direction' in metadata:
                    data.add_field('direction', str(metadata.get('direction', 'inbound')))
                if 'duration' in metadata:
                    data.add_field('duration', str(metadata.get('duration', '0')))
                if 'queue' in metadata:
                    data.add_field('queue_name', str(metadata.get('queue', '')))
                    
                # Timestamps
                if 'recording_timestamp' in metadata:
                    data.add_field('start_time', str(metadata.get('recording_timestamp', '')))
                    data.add_field('calldate', str(metadata.get('recording_timestamp', '')))  # Alternative field name
                elif 'timestamp' in metadata:
                    data.add_field('start_time', str(metadata.get('timestamp', '')))
                    
                # Tenant information
                if 'tenant_name' in metadata:
                    data.add_field('tenant_name', str(metadata.get('tenant_name', '')))
                if 'tenant_id' in metadata:
                    data.add_field('tenant_id', str(metadata.get('tenant_id', '')))
                    
                # Additional phone numbers if found
                if 'phone_numbers' in metadata and isinstance(metadata['phone_numbers'], list):
                    for i, phone in enumerate(metadata['phone_numbers'][:3]):  # Limit to first 3
                        data.add_field(f'phone_{i+1}', str(phone))
                        
                # Session/Extension information
                if 'session_ids' in metadata and isinstance(metadata['session_ids'], list):
                    data.add_field('session_id', str(metadata['session_ids'][0]) if metadata['session_ids'] else '')
                if 'extension' in metadata:
                    data.add_field('extension', str(metadata.get('extension', '')))
                    
                # Add customer ID from parsed API key if available (legacy smart keys only)
                if self.parsed_key.is_smart_key and self.parsed_key.customer_id:
                    data.add_field('customer_id', self.parsed_key.customer_id)
                    
                # File details
                data.add_field('file_name', str(metadata.get('file_name', filename)))
                data.add_field('file_size', str(metadata.get('file_size', 0)))
                
                # Original file path from Asterisk
                if 'recording_path' in metadata:
                    data.add_field('original_file_path', str(metadata.get('recording_path', '')))
                elif 'file_path' in metadata:
                    data.add_field('original_file_path', str(metadata.get('file_path', '')))
                
                # Recording type
                if 'recording_type' in metadata:
                    data.add_field('recording_type', str(metadata.get('recording_type', '')))
                    
                # Agent/User information
                if 'agent_id' in metadata:
                    data.add_field('agent_id', str(metadata.get('agent_id', '')))
                    
                # Extensions found
                if 'extensions' in metadata and isinstance(metadata['extensions'], list):
                    data.add_field('extensions', ','.join(str(e) for e in metadata['extensions']))
                    
                # Add any additional metadata as JSON
                extra_metadata = {
                    k: v for k, v in metadata.items() 
                    if k not in ['caller_id_num', 'connected_line_num', 'direction', 
                                 'uniqueid', 'duration', 'queue', 'timestamp', 'recording_timestamp',
                                 'tenant_name', 'tenant_id', 'phone_numbers', 'session_ids',
                                 'file_name', 'file_path', 'file_size', 'source', 'extensions',
                                 'recording_type', 'agent_id', 'linkedid', 'call_id']
                }
                if extra_metadata:
                    data.add_field('metadata', json.dumps(extra_metadata))
                
                # Log the fields being sent
                src_num = metadata.get('src_number') or metadata.get('caller_id_num')
                dst_num = metadata.get('dst_number') or metadata.get('connected_line_num')
                logger.debug(f"Recording upload fields: recording_id={recording_id}, call_id={metadata.get('uniqueid')}, "
                            f"src_number={src_num}, dst_number={dst_num}")
                
                # Add the audio file LAST (after all other fields)
                # Get file size for logging
                file_size = os.path.getsize(file_path)
                
                # Determine content type based on file extension
                ext = os.path.splitext(filename)[1].lower()
                content_type = 'audio/wav'  # default
                if ext in ['.mp3']:
                    content_type = 'audio/mpeg'
                elif ext in ['.gsm']:
                    content_type = 'audio/gsm'
                
                logger.debug(f"File size: {file_size} bytes, content_type: {content_type}")
                
                # Always read entire file into memory - this is what works correctly
                # The chunked streaming was causing truncation issues
                logger.debug(f"Reading file into memory: {file_path} ({file_size} bytes)")
                
                # Check if file is too large to prevent memory issues
                if file_size > self.max_memory_file_size:
                    logger.warning(f"Large file detected: {file_size} bytes (limit: {self.max_memory_file_size} bytes)")
                    # Still proceed but log warning
                
                with open(file_path, 'rb') as f:
                    file_content = f.read()
                
                logger.debug(f"File read complete. Actual size: {len(file_content)} bytes")
                
                # Add the file content directly
                data.add_field('audio', 
                              file_content,
                              filename=filename, 
                              content_type=content_type)
                
                # Prepare headers (remove Content-Type as it will be set by FormData)
                headers = {
                    'Authorization': f'Bearer {self.api_key}'
                }
                
                # Add hostname header if available
                if self.host_info and self.host_info.get('hostname'):
                    headers['X-Asterisk-Hostname'] = self.host_info['hostname']
                
                # Upload with longer timeout for file uploads
                timeout = aiohttp.ClientTimeout(total=60.0)
                
                # Log the actual data being sent
                logger.debug(f"Uploading file: {filename} with size: {file_size} bytes")
                
                async with self._session.post(
                    url,
                    data=data,
                    headers=headers,
                    timeout=timeout
                ) as response:
                    # Try to get JSON response, but handle non-JSON errors
                    try:
                        response_data = await response.json()
                    except:
                        # If response is not JSON, get text
                        error_text = await response.text()
                        logger.error(f"Non-JSON response (status {response.status}): {error_text}")
                        if response.status == 400:
                            raise Exception(f"API error 400: Bad request - {error_text}")
                        else:
                            raise Exception(f"API error {response.status}: {error_text}")
                    
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
                        error_msg = response_data.get('error', response_data.get('message', 'Unknown error'))
                        error_details = response_data.get('details', response_data.get('code', ''))
                        full_error = f"{error_msg}"
                        if error_details:
                            full_error += f" - {error_details}"
                        logger.error(f"Recording upload failed with status {response.status}: {response_data}")
                        raise Exception(f"API error {response.status}: {full_error}")
                            
            except Exception as e:
                self.metrics.increment('recording_upload_errors')
                logger.error(f"Error uploading recording {file_path}: {e}", exc_info=True)
                raise
            finally:
                # Cleanup not needed anymore since we're reading directly into memory
                pass
            
    def get_stats(self) -> Dict[str, Any]:
        """Get client statistics."""
        return {
            'metrics': self.metrics.get_all(),
            'connected': self._session is not None
        }