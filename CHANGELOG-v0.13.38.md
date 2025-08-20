# Asterisk Connector v0.13.38 - KNOWN_TRUNKS Filtering

## Enhanced
- **KNOWN_TRUNKS Configuration**: Tenant detection now respects `KNOWN_TRUNKS` environment variable
  - Filters out trunk names from tenant detection (e.g., sbc-ca1, sbc-ca2, trunk1)
  - Prevents false positive tenant detection from trunk identifiers
  - Configurable via comma-separated list in .env file

## Added
- **Startup Logging**: Shows configured known trunks at startup
  - Displays count and list of filtered trunk names
  - Helps troubleshoot tenant detection issues

## Configuration Example
```bash
# Filter out these trunk names from tenant detection
KNOWN_TRUNKS=sbc-ca1,sbc-ca2,trunk1,pstn-gateway
```

## Impact
- More accurate tenant detection by excluding known infrastructure names
- Reduces false positives where trunk names were incorrectly identified as tenants
- Better data quality for multi-tenant environments with named trunks