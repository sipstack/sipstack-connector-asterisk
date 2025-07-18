"""Unified API client supporting CDR ingestion."""

import asyncio
import logging
from typing import Dict, Any, Optional

from .cdr_client import ApiRegionalCDRClient
from models.cdr import CDRBatch

logger = logging.getLogger(__name__)


class UnifiedApiClient:
    """Unified client for API-Regional CDR ingestion."""
    
    def __init__(self,
                 sentiment_config: Optional[Dict[str, Any]] = None,
                 cdr_config: Optional[Dict[str, Any]] = None):
        """
        Initialize unified API client.
        
        Args:
            sentiment_config: Legacy parameter, ignored
            cdr_config: Configuration for CDR/API-Regional service
        """
        # Initialize CDR client if configured
        self.cdr_client = None
        if cdr_config and cdr_config.get('enabled', False):
            # Add host information to CDR client
            host_info = cdr_config.get('host_info', {})
            
            self.cdr_client = ApiRegionalCDRClient(
                api_base_url=cdr_config['api_base_url'],
                api_key=cdr_config['api_key'],
                timeout=cdr_config.get('timeout', 30.0),
                max_retries=cdr_config.get('max_retries', 3),
                host_info=host_info
            )
            
    async def start(self):
        """Start all configured clients."""
        if self.cdr_client:
            await self.cdr_client.start()
            logger.info("CDR client started")
            
    async def close(self):
        """Close all configured clients."""
        if self.cdr_client:
            await self.cdr_client.stop()
            
    async def upload_recording(self, file_path: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Upload recording to API-Regional service.
        
        Args:
            file_path: Path to recording file
            metadata: Recording metadata
            
        Returns:
            Response from API
        """
        if not self.cdr_client:
            logger.warning("CDR client not configured, skipping recording upload")
            return {}
            
        # All recordings go to the same endpoint
        endpoint = 'recording'
            
        return await self.cdr_client.upload_recording(file_path, metadata, endpoint)
        
    async def send_cdr_batch(self, batch: CDRBatch):
        """
        Send CDR batch to API-Regional service.
        
        Args:
            batch: CDRBatch to send
        """
        if not self.cdr_client:
            logger.warning("CDR client not configured, skipping batch")
            return
            
        await self.cdr_client.send_batch(batch)
        
    async def test_connectivity(self) -> Dict[str, bool]:
        """
        Test connectivity for all configured services.
        
        Returns:
            Dictionary with service connectivity status
        """
        results = {}
        
        if self.cdr_client:
            results['cdr'] = await self.cdr_client.test_connection()
            
        return results