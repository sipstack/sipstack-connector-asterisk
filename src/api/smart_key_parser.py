"""Smart API key parser for extracting embedded metadata."""

import re
from typing import Optional, Dict, Any

# Use compatibility layer for Python 3.6 support
from utils.compat import dataclass


@dataclass
class ParsedApiKey:
    """Parsed API key with extracted metadata."""
    is_valid: bool
    format: str  # 'smart_v1', 'legacy', or 'invalid'
    tier: Optional[int] = None
    customer_id: Optional[int] = None
    rate_limit: Optional[int] = None
    queue_delay: Optional[int] = None
    error: Optional[str] = None
    
    @property
    def is_smart_key(self) -> bool:
        """Check if this is a smart key with embedded metadata."""
        return self.format == 'smart_v1'
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'is_valid': self.is_valid,
            'format': self.format,
            'tier': self.tier,
            'customer_id': self.customer_id,
            'rate_limit': self.rate_limit,
            'queue_delay': self.queue_delay,
            'error': self.error
        }


class SmartKeyParser:
    """Parser for smart API keys with embedded metadata."""
    
    # Smart key pattern: sk_t{tier}_c{custnum}_{random}
    SMART_KEY_PATTERN = re.compile(r'^sk_t([0-4])_c(\d+)_([a-zA-Z0-9]{20,})$')
    
    # Legacy key pattern: sk_{random}
    LEGACY_KEY_PATTERN = re.compile(r'^sk_[a-zA-Z0-9]{20,}$')
    
    # Tier limits
    TIER_LIMITS = {
        0: {'rate_limit': 10, 'queue_delay': 60},    # Free
        1: {'rate_limit': 60, 'queue_delay': 20},    # Starter
        2: {'rate_limit': 300, 'queue_delay': 10},   # Pro
        3: {'rate_limit': 1200, 'queue_delay': 5},   # Enterprise
        4: {'rate_limit': 3600, 'queue_delay': 2},   # Custom
    }
    
    @classmethod
    def parse(cls, api_key: str) -> ParsedApiKey:
        """
        Parse an API key and extract embedded metadata.
        
        Args:
            api_key: The API key to parse
            
        Returns:
            ParsedApiKey with extracted metadata
        """
        if not api_key:
            return ParsedApiKey(
                is_valid=False,
                format='invalid',
                error='Empty API key'
            )
        
        # Try smart key format
        match = cls.SMART_KEY_PATTERN.match(api_key)
        if match:
            tier = int(match.group(1))
            customer_id = int(match.group(2))
            limits = cls.TIER_LIMITS[tier]
            
            return ParsedApiKey(
                is_valid=True,
                format='smart_v1',
                tier=tier,
                customer_id=customer_id,
                rate_limit=limits['rate_limit'],
                queue_delay=limits['queue_delay']
            )
        
        # Try legacy format
        if cls.LEGACY_KEY_PATTERN.match(api_key):
            return ParsedApiKey(
                is_valid=True,
                format='legacy',
                error='Legacy key format - database lookup required'
            )
        
        # Invalid format
        return ParsedApiKey(
            is_valid=False,
            format='invalid',
            error='Invalid API key format'
        )
    
    @classmethod
    def extract_tier(cls, api_key: str) -> Optional[int]:
        """
        Quick extraction of tier from smart key.
        
        Args:
            api_key: The API key
            
        Returns:
            Tier (0-4) or None if not a smart key
        """
        parsed = cls.parse(api_key)
        return parsed.tier if parsed.is_smart_key else None
    
    @classmethod
    def extract_customer_id(cls, api_key: str) -> Optional[int]:
        """
        Quick extraction of customer ID from smart key.
        
        Args:
            api_key: The API key
            
        Returns:
            Customer ID or None if not a smart key
        """
        parsed = cls.parse(api_key)
        return parsed.customer_id if parsed.is_smart_key else None
    
    @classmethod
    def get_rate_limit(cls, api_key: str) -> Optional[int]:
        """
        Get rate limit from API key without DB lookup.
        
        Args:
            api_key: The API key
            
        Returns:
            Rate limit (requests/minute) or None
        """
        parsed = cls.parse(api_key)
        return parsed.rate_limit if parsed.is_smart_key else None
    
    @classmethod
    def validate_format(cls, api_key: str) -> bool:
        """
        Validate API key format.
        
        Args:
            api_key: The API key
            
        Returns:
            True if valid format (smart or legacy)
        """
        parsed = cls.parse(api_key)
        return parsed.is_valid