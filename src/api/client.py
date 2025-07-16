import asyncio
import logging
import os
import time
from typing import Dict, Any, Optional

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.metrics import record_api_request, record_api_error

logger = logging.getLogger(__name__)

class SentimentApiClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int = 30,
        retry_attempts: int = 3
    ):
        self.base_url = base_url
        self.token = token
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.upload_endpoint = f"{self.base_url.rstrip('/')}/sentiment"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "Asterisk-Sentiment-Connector/1.0"
        }
        self.session = None
        
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp client session"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self.session
        
    async def close(self) -> None:
        """Close the client session"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))
    )
    def _validate_file_path(self, file_path: str) -> None:
        """
        Validate file path to prevent path traversal and other security issues
        
        Args:
            file_path: The file path to validate
            
        Raises:
            ValueError: If the file path is invalid or insecure
        """
        # Check if path is absolute
        if not os.path.isabs(file_path):
            raise ValueError(f"File path must be absolute: {file_path}")
            
        # Check if file exists
        if not os.path.isfile(file_path):
            raise ValueError(f"File does not exist: {file_path}")
            
        # Check file size (limit to 50MB)
        max_size = 50 * 1024 * 1024  # 50MB
        file_size = os.path.getsize(file_path)
        if file_size > max_size:
            raise ValueError(f"File size exceeds maximum allowed (50MB): {file_size}")
            
        # Check file extension
        allowed_extensions = ['.wav', '.mp3', '.alaw', '.ulaw', '.sln', '.gsm']
        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext not in allowed_extensions:
            raise ValueError(f"File type not allowed: {file_ext}. Allowed types: {', '.join(allowed_extensions)}")
    
    def _sanitize_metadata(self, metadata: Dict[str, Any]) -> Dict[str, str]:
        """
        Sanitize and validate metadata to prevent injection attacks
        
        Args:
            metadata: The metadata dictionary to sanitize
            
        Returns:
            Sanitized metadata dictionary with string values
        """
        sanitized = {}
        
        # List of allowed metadata fields
        allowed_fields = [
            'uniqueid', 'channel', 'caller_id_num', 'caller_id_name',
            'connected_line_num', 'connected_line_name', 'queue',
            'timestamp', 'direction', 'duration', 'recording_type',
            'mailbox', 'folder', 'file_path', 'file_size', 'hostname'
        ]
        
        # Sanitize each field
        for key, value in metadata.items():
            # Check if field is allowed
            if key not in allowed_fields:
                logger.warning(f"Skipping disallowed metadata field: {key}")
                continue
                
            # Convert value to string and limit length
            if value is not None:
                str_value = str(value)
                # Limit string length to 256 chars
                if len(str_value) > 256:
                    str_value = str_value[:256]
                sanitized[key] = str_value
        
        return sanitized
            
    async def upload_recording(self, file_path: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Upload a recording file to the sentiment analysis API"""
        session = await self._get_session()
        start_time = time.time()
        endpoint = "sentiment"
        recording_type = metadata.get('recording_type', 'unknown')
        
        try:
            # Validate file path
            self._validate_file_path(file_path)
            
            # Sanitize metadata
            sanitized_metadata = self._sanitize_metadata(metadata)
            
            # Prepare form data with file and metadata
            form_data = aiohttp.FormData()
            
            # Add file
            with open(file_path, 'rb') as file:
                form_data.add_field(
                    'file',
                    file,
                    filename=os.path.basename(file_path),
                    content_type='audio/wav'
                )
            
            # Add metadata fields
            for key, value in sanitized_metadata.items():
                form_data.add_field(key, value)
            
            # Make the request
            async with session.post(self.upload_endpoint, data=form_data) as response:
                duration = time.time() - start_time
                
                if response.status == 200 or response.status == 201:
                    result = await response.json()
                    logger.info(f"Successfully uploaded recording {file_path}")
                    
                    # Record metrics for successful request
                    record_api_request(endpoint, response.status, duration)
                    
                    return result
                else:
                    error_text = await response.text()
                    logger.error(f"API error: HTTP {response.status} - {error_text}")
                    
                    # Record metrics for failed request
                    record_api_request(endpoint, response.status, duration)
                    record_api_error(f"http_{response.status}")
                    
                    raise ApiError(f"API returned error: HTTP {response.status} - {error_text}")
        
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            duration = time.time() - start_time
            logger.error(f"Network error uploading {file_path}: {e}")
            
            # Record metrics for network error
            record_api_request(endpoint, "network_error", duration)
            record_api_error("network_error")
            
            raise
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Unexpected error uploading {file_path}: {e}")
            
            # Record metrics for unexpected error
            record_api_request(endpoint, "error", duration)
            record_api_error("unexpected_error")
            
            raise ApiError(f"Failed to upload recording: {e}")
    
    async def test_connectivity(self) -> bool:
        """Test connectivity to the API endpoint"""
        session = await self._get_session()
        
        try:
            # Most APIs provide a health or status endpoint
            test_url = f"{self.base_url.rstrip('/')}/health"
            
            async with session.get(test_url) as response:
                if response.status == 200:
                    logger.info("API connectivity test successful")
                    return True
                else:
                    logger.warning(f"API connectivity test failed: HTTP {response.status}")
                    return False
        
        except Exception as e:
            logger.error(f"API connectivity test failed: {e}")
            return False


class ApiError(Exception):
    """Exception raised for API-related errors"""
    pass