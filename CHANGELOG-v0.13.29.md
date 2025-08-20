# Asterisk Connector v0.13.29 - Data Quality Improvements

## Fixed Issues
1. **Direction Detection** - Properly identifies trunk channels (SIP/sbc_ca1-xxx) vs extension channels
2. **Number Extraction** - Correctly captures external phone numbers for inbound/outbound calls
3. **Tenant Extraction** - Extracts tenant from channel patterns (e.g., SIP/300-gconnect-xxx → tenant="gconnect")
4. **DID Extraction** - Handles special Asterisk destinations (s, i, t, h) by extracting DID from context

## Changes Made

### database_connector.py

#### Direction Detection Improvements
- Added support for more trunk patterns: 'sbc_', 'DAHDI/', 'IAX2/'
- Fixed case-sensitive matching issues
- Support for both SIP/ and PJSIP/ channel types
- Allow extensions up to 6 digits (was 4)

#### Number Extraction Improvements
- Better handling of 's', 'i', 't', 'h' special destinations
- Extract DID from context patterns:
  - Pattern 1: XXX-NNNNNNNNNN-XXX-NAME-tenant (e.g., 338-6478752300-338-CFLAW-gconnect)
  - Pattern 2: from-did-direct,NNNNNNNNNN
- Properly extract extensions from SIP/PJSIP channels

#### Tenant Extraction (NEW)
- Added `_extract_tenant_from_channel()` - extracts from SIP/ext-tenant-uniqueid pattern
- Added `_extract_tenant_from_context()` - extracts from context pattern
- Updated `format_call_data()` to use tenant extraction methods
- Fallback to accountcode or config if tenant not found in patterns

## Testing
All improvements tested and verified with test_simple.py:
- ✅ Direction detection (inbound/outbound/internal)
- ✅ Tenant extraction from channels
- ✅ DID extraction from context patterns

## Impact
These fixes ensure that call_logs records will have:
- Correct direction (i/o/x) based on trunk/extension patterns
- Phone numbers properly captured for external calls
- Tenant field populated from channel patterns
- DIDs extracted even when dst='s' (special Asterisk destination)

## Next Steps
- CEL CSV format validation and parsing improvements
- CNAM lookups for external numbers (names)