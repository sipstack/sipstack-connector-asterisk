"""Tenant extraction utilities for CDR and CEL data."""

import os
import re
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Parse known trunks from environment variable
KNOWN_TRUNKS_ENV = os.environ.get('KNOWN_TRUNKS', '')
KNOWN_TRUNKS: List[str] = []

if KNOWN_TRUNKS_ENV:
    KNOWN_TRUNKS = [trunk.strip().lower() for trunk in KNOWN_TRUNKS_ENV.split(',') if trunk.strip()]
    if KNOWN_TRUNKS:
        logger.info(f"Configured known trunks for tenant extraction filtering: {', '.join(KNOWN_TRUNKS)}")


def is_known_trunk(name: str) -> bool:
    """Check if a name is a known trunk."""
    if not name or not KNOWN_TRUNKS:
        return False
    lower_name = name.lower()
    # Only exact matches for trunk names
    return lower_name in KNOWN_TRUNKS


def validate_tenant_name(tenant: Optional[str]) -> Optional[str]:
    """Validate if extracted tenant name is valid (not a known trunk)."""
    if not tenant:
        return None
    if is_known_trunk(tenant):
        logger.debug(f"Filtered out known trunk from tenant extraction: {tenant}")
        return None
    return tenant


def extract_from_dcontext(dcontext: str) -> Optional[str]:
    """
    Extract tenant from dcontext patterns.
    
    Examples:
    - "300-14164775498-300-GC-Office-gconnect" → "gconnect"
    - "from-outside-14164775481-tl-allhours-cpapliving" → "cpapliving"
    - "ext-14164775498-telair" → "telair"
    """
    if not dcontext or not isinstance(dcontext, str):
        return None
    
    # Pattern 1: extension-DID-extension-description-tenant
    # "300-14164775498-300-GC-Office-gconnect" → "gconnect"
    match = re.match(r'^\d+-\d{10,11}-\d+-[\w-]+-([\w]+)$', dcontext)
    if match:
        return validate_tenant_name(match.group(1))
    
    # Pattern 2: from-outside-DID-description-tenant
    # "from-outside-14164775481-tl-allhours-cpapliving" → "cpapliving"
    match = re.match(r'^from-outside-\d{10,11}-[\w-]+-([\w]+)$', dcontext)
    if match:
        return validate_tenant_name(match.group(1))
    
    # Pattern 3: ext-DID-tenant
    # "ext-14164775498-telair" → "telair"
    match = re.match(r'^ext-\d{10,11}-([\w]+)$', dcontext)
    if match:
        return validate_tenant_name(match.group(1))
    
    # Pattern 4: from-did-direct-DID-tenant
    # "from-did-direct-14164775498-telair" → "telair"
    match = re.match(r'^from-did-direct-\d{10,11}-([\w]+)$', dcontext)
    if match:
        return validate_tenant_name(match.group(1))
    
    # Pattern 5: from-internal-tenant, from-inside-tenant, from-inside-redir-tenant
    # "from-internal-telair" → "telair"
    # "from-inside-gconnect" → "gconnect"
    # "from-inside-redir-cpapliving" → "cpapliving"
    match = re.match(r'^from-(?:internal|inside|inside-redir|inside-restricted-redir)-([\w]+)$', dcontext)
    if match:
        return validate_tenant_name(match.group(1))
    
    # Pattern 6: local-extensions-tenant
    # "local-extensions-gconnect" → "gconnect"
    match = re.match(r'^local-extensions-([\w]+)$', dcontext)
    if match:
        return validate_tenant_name(match.group(1))
    
    # Pattern 7: outgoing-tenant
    # "outgoing-centrecourt" → "centrecourt"
    match = re.match(r'^outgoing-([\w]+)$', dcontext)
    if match:
        return validate_tenant_name(match.group(1))
    
    return None


def extract_from_context(context: str) -> Optional[str]:
    """
    Extract tenant from context patterns.
    
    Examples:
    - "ext-queues-cpapliving" → "cpapliving"
    - "from-internal-cpapliving" → "cpapliving"
    """
    if not context or not isinstance(context, str):
        return None
    
    # Pattern 1: ext-queues-tenant
    match = re.match(r'^ext-queues-([\w]+)$', context)
    if match:
        return validate_tenant_name(match.group(1))
    
    # Pattern 2: from-internal-tenant
    match = re.match(r'^from-internal-([\w]+)$', context)
    if match:
        return validate_tenant_name(match.group(1))
    
    # Pattern 3: ivr-tenant
    match = re.match(r'^ivr-([\w]+)$', context)
    if match:
        return validate_tenant_name(match.group(1))
    
    # Pattern 4: from-did-direct-tenant
    match = re.match(r'^from-did-direct-([\w]+)$', context)
    if match:
        return validate_tenant_name(match.group(1))
    
    # Pattern 5: macro-tenant-specific patterns
    match = re.match(r'^macro-dial-([\w]+)$', context)
    if match:
        return validate_tenant_name(match.group(1))
    
    return None


def extract_from_channel(channel: str) -> Optional[str]:
    """
    Extract tenant from channel/channame patterns.
    
    Examples:
    - "SIP/sbc-ca2-telair-00000123" → "telair"
    - "SIP/101-gconnect-00000456" → "gconnect"
    - "PJSIP/202-telair-00000789" → "telair"
    """
    if not channel or not isinstance(channel, str):
        return None
    
    # Remove unique ID pattern at the end (6+ hex characters)
    unique_id_pattern = r'-([0-9a-f]{6,})$'
    channel_without_id = re.sub(unique_id_pattern, '', channel, flags=re.IGNORECASE)
    
    # Split the channel into parts
    parts = re.split(r'[/\-]', channel_without_id)
    
    # Build list of parts that aren't known trunks
    filtered_parts = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if not part:
            i += 1
            continue
        
        # Check if this part (alone or combined with following parts) is a known trunk
        found_trunk = False
        for j in range(i + 1, len(parts) + 1):
            combined = '-'.join(parts[i:j])
            if is_known_trunk(combined):
                logger.debug(f"Filtering known trunk '{combined}' from channel: {channel}")
                i = j  # Skip past the trunk parts
                found_trunk = True
                break
        
        if not found_trunk:
            filtered_parts.append(part)
            i += 1
    
    # Get the last non-empty part that looks like a tenant name
    for part in reversed(filtered_parts):
        # Skip protocol names and numeric extensions
        if part.upper() in ['SIP', 'PJSIP', 'IAX2', 'DAHDI', 'LOCAL']:
            continue
        if part.isdigit():
            continue
        # Check if it's a valid tenant name (alphanumeric starting with letter)
        if re.match(r'^[a-zA-Z][a-zA-Z0-9]*$', part):
            # Skip common identifiers that aren't tenants
            skip_patterns = ['sbc', 'trunk', 'peer', 'server', 'gw', 'gateway', 'pstn']
            if not any(pattern in part.lower() for pattern in skip_patterns):
                return validate_tenant_name(part)
    
    # Fallback patterns for specific formats
    # Pattern 1: Protocol/extension-tenant-uniqueid
    match = re.match(r'^(?:SIP|PJSIP|IAX2)/\d+-([\w]+)-[0-9a-f]+$', channel, re.IGNORECASE)
    if match:
        return validate_tenant_name(match.group(1))
    
    # Pattern 2: Local/extension@context-tenant
    match = re.match(r'^Local/\d+@[\w-]+-([\w]+)$', channel, re.IGNORECASE)
    if match:
        return validate_tenant_name(match.group(1))
    
    return None


def extract_from_extra_field(extra: str) -> Optional[str]:
    """
    Extract tenant from extra field (CEL specific).
    The extra field might contain JSON or structured data.
    """
    if not extra or not isinstance(extra, str):
        return None
    
    # Try to parse as JSON first
    try:
        import json
        parsed = json.loads(extra)
        if isinstance(parsed, dict) and 'tenant' in parsed:
            return validate_tenant_name(parsed.get('tenant'))
    except (json.JSONDecodeError, ValueError):
        pass  # Not JSON, try other patterns
    
    # Look for tenant= pattern
    match = re.search(r'tenant=([\w]+)', extra)
    if match:
        return validate_tenant_name(match.group(1))
    
    return None


def extract_tenant_from_cdr(cdr_data: Dict[str, Any]) -> Optional[str]:
    """
    Extract tenant name from CDR data.
    
    Priority order:
    1. Check dcontext (most reliable for tenant info)
    2. Check dstchannel
    3. Check context
    4. Check channel
    5. Check custom_vars if present
    """
    try:
        # Priority 1: Check dcontext (most reliable for tenant info)
        if 'dcontext' in cdr_data:
            tenant = extract_from_dcontext(cdr_data['dcontext'])
            if tenant:
                return tenant
        
        # Priority 2: Check dstchannel
        if 'dstchannel' in cdr_data:
            tenant = extract_from_channel(cdr_data['dstchannel'])
            if tenant:
                return tenant
        
        # Priority 3: Check context
        if 'context' in cdr_data:
            tenant = extract_from_context(cdr_data['context'])
            if tenant:
                return tenant
        
        # Priority 4: Check channel
        if 'channel' in cdr_data:
            tenant = extract_from_channel(cdr_data['channel'])
            if tenant:
                return tenant
        
        # Priority 5: Check custom_vars if present
        custom_vars = cdr_data.get('custom_vars')
        if custom_vars and isinstance(custom_vars, dict):
            tenant = custom_vars.get('tenant')
            if tenant:
                return validate_tenant_name(tenant)
        
        return None
    except Exception as e:
        logger.error(f"Error extracting tenant from CDR: {e}")
        return None


def extract_tenant_from_cel(cel_data: Dict[str, Any]) -> Optional[str]:
    """
    Extract tenant name from CEL data.
    
    Priority order:
    1. Check context
    2. Check channame
    3. Check peer
    4. Check extra field
    """
    try:
        # Priority 1: Check context
        if 'context' in cel_data:
            tenant = extract_from_context(cel_data['context'])
            if tenant:
                return tenant
        
        # Priority 2: Check channame
        if 'channame' in cel_data:
            tenant = extract_from_channel(cel_data['channame'])
            if tenant:
                return tenant
        
        # Priority 3: Check peer
        if 'peer' in cel_data:
            tenant = extract_from_channel(cel_data['peer'])
            if tenant:
                return tenant
        
        # Priority 4: Check extra field
        if 'extra' in cel_data:
            tenant = extract_from_extra_field(cel_data['extra'])
            if tenant:
                return tenant
        
        return None
    except Exception as e:
        logger.error(f"Error extracting tenant from CEL: {e}")
        return None


def merge_tenant_info(cdr_tenant: Optional[str], cel_tenant: Optional[str]) -> Optional[str]:
    """
    Merge tenant information from both CDR and CEL data.
    CEL data might have more accurate tenant info for certain call flows.
    """
    # If both are the same, return it
    if cdr_tenant == cel_tenant:
        return cdr_tenant
    
    # Prefer CEL tenant if CDR tenant is null
    if not cdr_tenant and cel_tenant:
        return cel_tenant
    
    # Prefer CDR tenant if CEL tenant is null
    if cdr_tenant and not cel_tenant:
        return cdr_tenant
    
    # If both exist but differ, log warning and prefer CDR
    if cdr_tenant and cel_tenant and cdr_tenant != cel_tenant:
        logger.warning(f"Tenant mismatch between CDR and CEL - CDR: {cdr_tenant}, CEL: {cel_tenant}")
    
    return cdr_tenant