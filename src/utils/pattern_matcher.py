"""Optimized pattern matching with caching for call direction detection."""

import re
import time
import logging
from typing import Dict, List, Set, Optional, Tuple
from functools import lru_cache

logger = logging.getLogger(__name__)


class PatternMatcher:
    """Efficient pattern matching with caching and pre-compilation."""
    
    def __init__(self, cache_ttl: int = 3600):
        self.cache_ttl = cache_ttl
        self._pattern_cache: Dict[str, Tuple[re.Pattern, float]] = {}
        self._exact_match_sets: Dict[str, Set[str]] = {}
        self._compiled_patterns: Dict[str, List[re.Pattern]] = {}
        
    def compile_patterns(self, pattern_type: str, patterns: List[str]):
        """Pre-compile regex patterns for efficient matching."""
        compiled = []
        exact_matches = set()
        
        for pattern in patterns:
            if '*' in pattern or '[' in pattern or '(' in pattern:
                # Convert simple wildcards to regex
                regex_pattern = pattern.replace('*', '.*')
                regex_pattern = f'^{regex_pattern}'
                try:
                    compiled.append(re.compile(regex_pattern, re.IGNORECASE))
                except re.error:
                    logger.warning(f"Invalid regex pattern: {pattern}")
            else:
                # Exact match - use set for O(1) lookup
                exact_matches.add(pattern.lower())
                
        self._compiled_patterns[pattern_type] = compiled
        self._exact_match_sets[pattern_type] = exact_matches
        logger.debug(f"Compiled {len(compiled)} regex and {len(exact_matches)} exact patterns for {pattern_type}")
        
    @lru_cache(maxsize=1024)
    def match_context(self, context: str, pattern_type: str) -> bool:
        """Check if context matches any pattern of given type."""
        if not context:
            return False
            
        context_lower = context.lower()
        
        # First check exact matches (O(1))
        if pattern_type in self._exact_match_sets:
            if context_lower in self._exact_match_sets[pattern_type]:
                return True
                
        # Then check regex patterns
        if pattern_type in self._compiled_patterns:
            for pattern in self._compiled_patterns[pattern_type]:
                if pattern.search(context_lower):
                    return True
                    
        return False
    
    def match_any(self, value: str, patterns: List[str]) -> bool:
        """Check if value matches any of the patterns."""
        if not value:
            return False
            
        value_lower = value.lower()
        
        for pattern in patterns:
            if pattern in value_lower:
                return True
                
        return False
    
    def clear_cache(self):
        """Clear all caches."""
        self.match_context.cache_clear()
        self._pattern_cache.clear()
        logger.debug("Pattern matcher cache cleared")


class NumberAnalyzer:
    """Analyze phone numbers for type detection."""
    
    def __init__(self, config):
        self.config = config
        self._e164_pattern = re.compile(r'^\+\d{10,15}$')
        self._intl_prefix_pattern = self._build_intl_pattern()
        # Pattern to clean formatted numbers
        self._cleanup_pattern = re.compile(r'[^0-9+*#]')
        
    def _build_intl_pattern(self) -> Optional[re.Pattern]:
        """Build regex pattern for international prefixes."""
        if not self.config.international_prefixes:
            return None
            
        prefixes = '|'.join(re.escape(p) for p in self.config.international_prefixes)
        return re.compile(f'^({prefixes})\\d+')
    
    def normalize_number(self, number: str) -> str:
        """Normalize phone number by removing formatting characters."""
        if not number:
            return ''
        
        # Preserve leading + for international
        has_plus = number.startswith('+')
        
        # Remove all non-digit characters except * and #
        normalized = self._cleanup_pattern.sub('', number)
        
        # Restore + if it was there
        if has_plus and not normalized.startswith('+'):
            normalized = '+' + normalized
            
        return normalized
    
    def is_extension(self, number: str) -> bool:
        """Check if number is an internal extension."""
        if not number:
            # Empty numbers should NOT default to internal
            # This could be anonymous calls or missing data
            return False
            
        # Normalize the number first
        normalized = self.normalize_number(number)
        if not normalized:
            return False
            
        # Feature codes
        if normalized.startswith('*'):
            return True
            
        # Check length (digits only, excluding + prefix)
        digits_only = normalized.lstrip('+')
        if digits_only.isdigit():
            length = len(digits_only)
            return self.config.extension_min_length <= length <= self.config.extension_max_length
            
        return False
    
    def is_international(self, number: str) -> bool:
        """Check if number is international format."""
        if not number:
            return False
            
        # E.164 format
        if self.config.e164_enabled and self._e164_pattern.match(number):
            return True
            
        # International prefixes
        if self._intl_prefix_pattern and self._intl_prefix_pattern.match(number):
            return True
            
        return False
    
    def get_number_type(self, number: str) -> str:
        """Determine number type: extension, local, long_distance, international, anonymous."""
        if not number:
            return 'anonymous'
            
        # Check for anonymous/private patterns
        if number.lower() in ['anonymous', 'private', 'restricted', 'unavailable', 'unknown']:
            return 'anonymous'
            
        # Normalize before checking
        normalized = self.normalize_number(number)
        
        if self.is_extension(normalized):
            return 'extension'
        elif self.is_international(normalized):
            return 'international'
        elif len(normalized) == 10 or (len(normalized) == 11 and normalized.startswith('1')):
            return 'long_distance'
        elif len(normalized) == 7:
            return 'local'
        else:
            return 'unknown'


class TransferDetector:
    """Detect call transfers and forwarding."""
    
    def __init__(self, config):
        self.config = config
        self.transfer_patterns = config.transfer_patterns
        
    def is_transfer_context(self, context: str) -> bool:
        """Check if context indicates a transfer."""
        if not self.config.detect_transfers or not context:
            return False
            
        context_lower = context.lower()
        return any(pattern in context_lower for pattern in self.transfer_patterns)
    
    def detect_transfer_chain(self, channel: str, lastapp: str, lastdata: str) -> Optional[str]:
        """Detect transfer type from CDR fields."""
        if not self.config.detect_transfers:
            return None
            
        # Blind transfer detection
        if lastapp and lastapp.lower() in ['transfer', 'blindxfer', 'atxfer']:
            return 'blind_transfer'
            
        # Attended transfer
        if lastapp and 'attended' in lastapp.lower():
            return 'attended_transfer'
            
        # Channel-based detection
        if channel and 'masq' in channel.lower():
            return 'masqueraded'
            
        # Transfer context in lastdata
        if lastdata and any(pattern in lastdata.lower() for pattern in ['transfer', 'xfer']):
            return 'transfer'
            
        return None