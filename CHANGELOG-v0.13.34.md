# Asterisk Connector v0.13.34 - Caller ID Name Fix

## Fixed
- **Caller ID Name Assignment**: Fixed incorrect assignment of caller ID names for inbound calls
  - Was incorrectly putting caller name in `dst_name` field
  - Now correctly assigns to `src_name` for inbound calls
  
- **Tenant-Prefixed Caller ID Names**: Added logic to clean up tenant-prefixed caller ID names
  - Detects and removes tenant-specific prefixes from caller names
  - Extracts actual caller name from prefixed strings
  - Handles cases where suffix is just a phone number by clearing the name

## Technical Details
- Pattern detection for names >30 characters with dash separators
- Regex pattern matching for "NNN-NN-Word-Company-ACTUALNAME" format
- Preserves original logic for extension name extraction
- Only processes when `cid_num` matches the source number

## Examples
### Before:
```
Inbound call:
  src_name: (empty)
  dst_name: "[tenant-prefix]-[caller name]"
```

### After:
```
Inbound call:
  src_name: "[caller name]"
  dst_name: (empty)
```