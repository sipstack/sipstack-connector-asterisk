# Asterisk Connector v0.13.31 - Trunk Pattern Fix

## Fixed
- **Trunk Detection**: Added `sbc-` pattern to recognize trunks like `SIP/sbc-ca2-xxx`
  - Was only looking for `sbc_` (underscore)
  - Now recognizes both `sbc-` and `sbc_` patterns

## Working Correctly
Based on debug logs from v0.13.30-debug, the connector is now successfully:
- ✅ Detecting call direction (inbound/outbound/internal)
- ✅ Extracting phone numbers with country codes
- ✅ Extracting extensions from channel names
- ✅ Extracting tenant from channel patterns (e.g., `gconnect` from `SIP/302-gconnect-xxx`)
- ✅ Processing and shipping calls to API

## Example Working Output
```
Call: 2898579600 → 6474534224
Direction: o (outbound)
Source: 12898579600 (number), 302 (extension)
Destination: 16474534224 (number)
Tenant: gconnect
```

## Note on CEL
- If using CEL_MODE=csv, the CSV might need different parsing
- Recommend using CEL_MODE=db for better reliability