"""Unified API client supporting both sentiment and CDR."""

import asyncio
import logging
from typing import Dict, Any, Optional

from .client import SentimentApiClient
from .cdr_client import ApiRegionalCDRClient
from models.cdr import CDRBatch

logger = logging.getLogger(__name__)


class UnifiedApiClient:
    """Unified client for sentiment API and API-Regional CDR."""
    
    def __init__(self,
                 sentiment_config: Optional[Dict[str, Any]] = None,
                 cdr_config: Optional[Dict[str, Any]] = None):
        """
        Initialize unified API client.
        
        Args:
            sentiment_config: Configuration for sentiment API
            cdr_config: Configuration for CDR/API-Regional service
        """
        # Initialize sentiment client if configured
        self.sentiment_client = None
        if sentiment_config and sentiment_config.get('enabled', False):
            self.sentiment_client = SentimentApiClient(
                base_url=sentiment_config['base_url'],
                token=sentiment_config['token'],
                timeout=sentiment_config.get('timeout', 30),
                retry_attempts=sentiment_config.get('retry_attempts', 3)
            )
            
        # Initialize CDR client if configured
        self.cdr_client = None
        if cdr_config and cdr_config.get('enabled', False):
            self.cdr_client = ApiRegionalCDRClient(
                api_base_url=cdr_config['api_base_url'],
                api_key=cdr_config['api_key'],
                timeout=cdr_config.get('timeout', 30.0),
                max_retries=cdr_config.get('max_retries', 3)
            )
            
    async def start(self):
        """Start all configured clients."""
        if self.cdr_client:
            await self.cdr_client.start()
            logger.info("CDR client started")
            
    async def close(self):
        """Close all configured clients."""
        if self.sentiment_client:
            await self.sentiment_client.close()
            
        if self.cdr_client:
            await self.cdr_client.stop()
            
    async def upload_recording(self, file_path: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Upload recording to sentiment API (backward compatibility).
        
        Args:
            file_path: Path to recording file
            metadata: Recording metadata
            
        Returns:
            API response
        """
        if not self.sentiment_client:
            logger.warning("Sentiment API not configured, skipping upload")
            return {}
            
        return await self.sentiment_client.upload_recording(file_path, metadata)
        
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
        
        if self.sentiment_client:
            results['sentiment'] = await self.sentiment_client.test_connectivity()
            
        if self.cdr_client:
            results['cdr'] = await self.cdr_client.test_connection()
            
        return results