"""Efficient tenant matching service for CDR and CEL correlation at scale."""

import os
import re
import json
import logging
from typing import Optional, Dict, Any, List, Tuple, Set
from datetime import datetime, timedelta
from collections import defaultdict
import hashlib

logger = logging.getLogger(__name__)


class TenantMatcher:
    """
    High-performance tenant matching service that correlates CDR and CEL data
    to extract tenant information at scale.
    """
    
    def __init__(self, cache_ttl_seconds: int = 300):
        """
        Initialize the tenant matcher with caching.
        
        Args:
            cache_ttl_seconds: How long to cache tenant lookups (default 5 minutes)
        """
        # Cache for DID to tenant mappings
        self.did_tenant_cache: Dict[str, Tuple[str, datetime]] = {}
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        
        # Cache for extension to tenant mappings
        self.extension_tenant_cache: Dict[str, str] = {}
        
        # LinkedID to tenant mapping (for call correlation)
        self.linkedid_tenant_map: Dict[str, str] = {}
        
        # Load DID to tenant mappings from environment or config file
        self.did_tenant_map = self._load_did_mappings()
        
        # Load accountcode to tenant mappings
        self.accountcode_tenant_map = self._load_accountcode_mappings()
        
        # Pattern cache for regex compilation
        self._pattern_cache: Dict[str, re.Pattern] = {}
        
        # Statistics for monitoring
        self.stats = {
            'cache_hits': 0,
            'cache_misses': 0,
            'cel_matches': 0,
            'did_matches': 0,
            'accountcode_matches': 0,
            'linkedid_matches': 0
        }
    
    def _load_did_mappings(self) -> Dict[str, str]:
        """
        Load DID to tenant mappings from environment or config file.
        Format: DID_TENANT_MAP="14164775498:gconnect,18665137797:telair,16478743709:cpapliving"
        """
        mappings = {}
        env_mappings = os.environ.get('DID_TENANT_MAP', '')
        
        if env_mappings:
            for mapping in env_mappings.split(','):
                mapping = mapping.strip()
                if ':' in mapping:
                    did, tenant = mapping.split(':', 1)
                    # Store normalized DID (remove country code if present)
                    normalized_did = self._normalize_phone_number(did)
                    if normalized_did:
                        mappings[normalized_did] = tenant
                        # Also store with leading 1 for North American numbers
                        if len(normalized_did) == 10:
                            mappings[f"1{normalized_did}"] = tenant
            
            if mappings:
                logger.info(f"Loaded {len(mappings)} DID to tenant mappings")
        
        # Try loading from a JSON file if it exists
        config_file = os.environ.get('DID_TENANT_CONFIG', '/etc/asterisk-connector/did_tenant_map.json')
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    file_mappings = json.load(f)
                    for did, tenant in file_mappings.items():
                        normalized_did = self._normalize_phone_number(did)
                        if normalized_did:
                            mappings[normalized_did] = tenant
                            if len(normalized_did) == 10:
                                mappings[f"1{normalized_did}"] = tenant
                logger.info(f"Loaded additional {len(file_mappings)} DID mappings from {config_file}")
            except Exception as e:
                logger.error(f"Failed to load DID mappings from {config_file}: {e}")
        
        return mappings
    
    def _load_accountcode_mappings(self) -> Dict[str, str]:
        """
        Load accountcode to tenant mappings.
        Format: ACCOUNTCODE_TENANT_MAP="GC:gconnect,TL:telair,CP:cpapliving"
        """
        mappings = {}
        env_mappings = os.environ.get('ACCOUNTCODE_TENANT_MAP', '')
        
        if env_mappings:
            for mapping in env_mappings.split(','):
                mapping = mapping.strip()
                if ':' in mapping:
                    code, tenant = mapping.split(':', 1)
                    mappings[code.lower()] = tenant
            
            if mappings:
                logger.info(f"Loaded {len(mappings)} accountcode to tenant mappings")
        
        return mappings
    
    def _normalize_phone_number(self, number: str) -> Optional[str]:
        """Normalize phone number for matching."""
        if not number:
            return None
        
        # Remove all non-digits
        digits = re.sub(r'\D', '', number)
        
        # Handle different formats
        if len(digits) == 11 and digits.startswith('1'):
            return digits[1:]  # Remove leading 1 for North American
        elif len(digits) == 10:
            return digits
        elif len(digits) == 7:
            return None  # Local number, can't determine tenant from this
        
        return digits
    
    def _get_or_compile_pattern(self, pattern_str: str) -> re.Pattern:
        """Get compiled regex pattern from cache or compile and cache it."""
        if pattern_str not in self._pattern_cache:
            self._pattern_cache[pattern_str] = re.compile(pattern_str)
        return self._pattern_cache[pattern_str]
    
    def extract_tenant_from_cel(self, cel_data: Dict[str, Any]) -> Optional[str]:
        """
        Extract tenant from CEL data with specific focus on CHAN_START events
        which often contain the most complete tenant information.
        """
        # Priority 1: Check cid_dnid (the actual DID for inbound calls)
        if cel_data.get('cid_dnid'):
            normalized = self._normalize_phone_number(cel_data['cid_dnid'])
            if normalized and normalized in self.did_tenant_map:
                self.stats['did_matches'] += 1
                return self.did_tenant_map[normalized]
        
        # Priority 2: For CHAN_START events, check the channel name
        if cel_data.get('eventtype') == 'CHAN_START' and cel_data.get('channame'):
            # Extract from channel patterns like "SIP/tenant-trunk-xxxxx"
            channel = cel_data['channame']
            # Remove the unique ID suffix
            channel_parts = re.sub(r'-[0-9a-f]{6,}$', '', channel, flags=re.IGNORECASE)
            parts = channel_parts.split('/')
            if len(parts) >= 2:
                # Split the second part by dashes
                subparts = parts[1].split('-')
                # The tenant is often the last meaningful part before the unique ID
                for part in reversed(subparts):
                    if part and not part.isdigit() and len(part) > 2:
                        # Validate it's not a trunk name
                        from utils.tenant_extraction import validate_tenant_name
                        tenant = validate_tenant_name(part)
                        if tenant:
                            return tenant
        
        # Priority 3: Check context field
        if cel_data.get('context'):
            from utils.tenant_extraction import extract_from_context
            tenant = extract_from_context(cel_data['context'])
            if tenant:
                return tenant
        
        # Priority 4: Check appdata for specific patterns
        if cel_data.get('appdata'):
            # Look for tenant in Dial command data
            if 'SIP/' in cel_data['appdata']:
                match = re.search(r'SIP/([a-zA-Z][\w]+)-', cel_data['appdata'])
                if match:
                    from utils.tenant_extraction import validate_tenant_name
                    tenant = validate_tenant_name(match.group(1))
                    if tenant:
                        return tenant
        
        return None
    
    def match_cdr_with_cel(self, cdr: Dict[str, Any], cel_records: List[Dict[str, Any]]) -> Optional[str]:
        """
        Match CDR with related CEL records to extract tenant.
        This is the main entry point for efficient tenant extraction.
        
        Args:
            cdr: CDR record dictionary
            cel_records: List of CEL records for the same time window
        
        Returns:
            Extracted tenant name or None
        """
        linkedid = cdr.get('linkedid') or cdr.get('uniqueid')
        if not linkedid:
            return None
        
        # Check cache first
        if linkedid in self.linkedid_tenant_map:
            self.stats['cache_hits'] += 1
            return self.linkedid_tenant_map[linkedid]
        
        self.stats['cache_misses'] += 1
        
        # Strategy 1: Check DID mapping for destination number
        dst_number = cdr.get('dst') or cdr.get('dst_number')
        if dst_number:
            normalized = self._normalize_phone_number(dst_number)
            if normalized and normalized in self.did_tenant_map:
                tenant = self.did_tenant_map[normalized]
                self.linkedid_tenant_map[linkedid] = tenant
                self.stats['did_matches'] += 1
                return tenant
        
        # Strategy 2: Check accountcode mapping
        accountcode = cdr.get('accountcode')
        if accountcode:
            # Try exact match first
            if accountcode.lower() in self.accountcode_tenant_map:
                tenant = self.accountcode_tenant_map[accountcode.lower()]
                self.linkedid_tenant_map[linkedid] = tenant
                self.stats['accountcode_matches'] += 1
                return tenant
            
            # Try prefix match for accountcodes like "GC-Office"
            for code_prefix, tenant in self.accountcode_tenant_map.items():
                if accountcode.lower().startswith(code_prefix):
                    self.linkedid_tenant_map[linkedid] = tenant
                    self.stats['accountcode_matches'] += 1
                    return tenant
        
        # Strategy 3: Find related CEL records by linkedid
        related_cels = [
            cel for cel in cel_records 
            if cel.get('linkedid') == linkedid or cel.get('uniqueid') == linkedid
        ]
        
        # Sort CEL records by eventtime to get CHAN_START first
        related_cels.sort(key=lambda x: (
            0 if x.get('eventtype') == 'CHAN_START' else 1,
            x.get('eventtime', datetime.min)
        ))
        
        # Try to extract tenant from CEL records
        for cel in related_cels:
            tenant = self.extract_tenant_from_cel(cel)
            if tenant:
                self.linkedid_tenant_map[linkedid] = tenant
                self.stats['cel_matches'] += 1
                return tenant
        
        # Strategy 4: Use existing extraction logic as fallback
        from utils.tenant_extraction import extract_tenant_from_cdr
        tenant = extract_tenant_from_cdr(cdr)
        if tenant:
            self.linkedid_tenant_map[linkedid] = tenant
        
        return tenant
    
    def batch_match(self, cdrs: List[Dict[str, Any]], cels: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        Efficiently match a batch of CDRs with CEL records.
        
        Args:
            cdrs: List of CDR records
            cels: List of CEL records
        
        Returns:
            Dictionary mapping uniqueid to tenant name
        """
        results = {}
        
        # Build linkedid index for CEL records for O(1) lookup
        cel_by_linkedid = defaultdict(list)
        for cel in cels:
            if cel.get('linkedid'):
                cel_by_linkedid[cel['linkedid']].append(cel)
            if cel.get('uniqueid'):
                cel_by_linkedid[cel['uniqueid']].append(cel)
        
        # Process each CDR
        for cdr in cdrs:
            uniqueid = cdr.get('uniqueid')
            if not uniqueid:
                continue
            
            # Get related CEL records efficiently
            linkedid = cdr.get('linkedid') or uniqueid
            related_cels = cel_by_linkedid.get(linkedid, [])
            
            # Try to extract tenant
            tenant = self.match_cdr_with_cel(cdr, related_cels)
            if tenant:
                results[uniqueid] = tenant
        
        return results
    
    def clear_old_cache(self):
        """Clear expired cache entries."""
        now = datetime.now()
        
        # Clear DID cache
        expired_dids = [
            did for did, (_, timestamp) in self.did_tenant_cache.items()
            if now - timestamp > self.cache_ttl
        ]
        for did in expired_dids:
            del self.did_tenant_cache[did]
        
        # Clear linkedid cache if it gets too large (keep last 10000 entries)
        if len(self.linkedid_tenant_map) > 10000:
            # Keep only the most recent half
            keep_count = 5000
            items = list(self.linkedid_tenant_map.items())
            self.linkedid_tenant_map = dict(items[-keep_count:])
        
        if expired_dids:
            logger.debug(f"Cleared {len(expired_dids)} expired cache entries")
    
    def get_stats(self) -> Dict[str, int]:
        """Get matching statistics."""
        total_lookups = self.stats['cache_hits'] + self.stats['cache_misses']
        if total_lookups > 0:
            cache_hit_rate = (self.stats['cache_hits'] / total_lookups) * 100
        else:
            cache_hit_rate = 0
        
        return {
            **self.stats,
            'cache_hit_rate': round(cache_hit_rate, 2),
            'cache_size': len(self.linkedid_tenant_map),
            'did_mappings': len(self.did_tenant_map),
            'accountcode_mappings': len(self.accountcode_tenant_map)
        }


# Global instance for reuse
_tenant_matcher: Optional[TenantMatcher] = None


def get_tenant_matcher() -> TenantMatcher:
    """Get or create the global tenant matcher instance."""
    global _tenant_matcher
    if _tenant_matcher is None:
        _tenant_matcher = TenantMatcher()
    return _tenant_matcher