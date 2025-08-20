# Asterisk Connector v0.13.37 - Aggressive Tenant Detection

## Enhanced
- **Aggressive Tenant Detection**: Completely rewrote tenant extraction to scan ALL available data
  - Scans all CDR fields: channel, dstchannel, context, dcontext, accountcode, userfield, peeraccount, lastdata
  - Scans all CEL fields: context, channame, appdata, peer, eventextra
  - Intelligently extracts tenant from any delimiter-separated pattern (-, _, /, @, ,)
  - Works with patterns like "closed-telair", "338-XXX-XXX-NAME-tenant", "SIP/100-tenant-xxx"

## Added
- **Smart Tenant Validation**: New `_is_valid_tenant()` method
  - Filters out common non-tenant words (sip, trunk, closed, etc.)
  - Validates tenant names are reasonable (2-20 chars, contains letters)
  - Skips numeric and hex ID patterns
  - Returns tenant names in lowercase for consistency

## Technical Details
- Tenant extraction now tries multiple strategies in order:
  1. Scan all CDR fields for tenant patterns
  2. Scan all CEL event fields if CDR doesn't have tenant
  3. Fall back to TENANT environment variable
- Extraction works from right-to-left in delimited strings (tenant usually at end)
- Debug logging shows which field contained the tenant for troubleshooting

## Impact
- Dramatically reduces calls with empty tenant fields
- Works with any Asterisk configuration without requiring specific patterns
- Tenant will be found if it exists ANYWHERE in the call data
- Better multi-tenant data organization and reporting