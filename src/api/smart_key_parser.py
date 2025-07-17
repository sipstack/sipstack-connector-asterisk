"""API key parser for standard and legacy key formats."""

import re
from typing import Optional, Dict, Any

# Use compatibility layer for Python 3.6 support
from utils.compat import dataclass


@dataclass
class ParsedApiKey:
    """Parsed API key with extracted metadata."""
    is_valid: bool
    format: str  # 'standard', 'smart', 'legacy', or 'invalid'
    tier: Optional[int] = None
    customer_id: Optional[int] = None
    rate_limit: Optional[int] = None
    queue_delay: Optional[int] = None
    error: Optional[str] = None
    
    @property
    def is_smart_key(self) -> bool:
        """Check if this is a smart key with embedded metadata."""
        return self.format == 'smart'
    
    @property
    def is_standard_key(self) -> bool:
        """Check if this is a standard key format."""
        return self.format == 'standard'
    
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
    """Parser for API keys including standard and legacy formats."""
    
    # Standard key pattern: sk_[32 alphanumeric characters]
    STANDARD_KEY_PATTERN = re.compile(r'^sk_[a-zA-Z0-9]{32}$')
    
    # Smart key patterns (legacy embedded tier keys)
    # New format: sk_t{tier}_{encrypted_customer_id}_{token}
    SMART_KEY_PATTERN = re.compile(r'^sk_t([0-4])_([a-fA-F0-9]{32})_([a-fA-F0-9]{64})$')
    
    # Old format: sk_t{tier}_c{custnum}_{random}
    OLD_SMART_KEY_PATTERN = re.compile(r'^sk_t([0-4])_c(\d+)_([a-zA-Z0-9]{20,})$')
    
    # Legacy key pattern: sk_{random}
    LEGACY_KEY_PATTERN = re.compile(r'^sk_[a-zA-Z0-9]{20,}$')
    
    # Tier limits (for legacy smart keys only)
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
        
        # Try standard key format first (sk_[32 chars])
        if cls.STANDARD_KEY_PATTERN.match(api_key):
            return ParsedApiKey(
                is_valid=True,
                format='standard',
                # No embedded metadata in standard keys - all managed server-side
                tier=None,
                customer_id=None,
                rate_limit=None,
                queue_delay=None
            )
        
        # Try new smart key format (with encrypted customer ID)
        match = cls.SMART_KEY_PATTERN.match(api_key)
        if match:
            tier = int(match.group(1))
            encrypted_customer_id = match.group(2)
            limits = cls.TIER_LIMITS[tier]
            
            return ParsedApiKey(
                is_valid=True,
                format='smart',
                tier=tier,
                customer_id=None,  # Not available in encrypted format
                rate_limit=limits['rate_limit'],
                queue_delay=limits['queue_delay']
            )
        
        # Try old smart key format (with plain customer ID)
        match = cls.OLD_SMART_KEY_PATTERN.match(api_key)
        if match:
            tier = int(match.group(1))
            customer_id = int(match.group(2))
            limits = cls.TIER_LIMITS[tier]
            
            return ParsedApiKey(
                is_valid=True,
                format='smart',
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