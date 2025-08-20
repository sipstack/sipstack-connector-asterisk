# Asterisk Connector v0.13.39 - Fix Tenant Detection Priority

## Fixed
- **Tenant Detection Field Priority**: Fixed incorrect tenant extraction from channel names instead of context
  - Now prioritizes context fields (dcontext, context) over channel fields
  - Prevents false extraction like "sbc" from "SIP/sbc-ca2-xxx" channels
  - Ensures "closed-telair" context correctly extracts "telair" tenant

## Changed
- **Field Processing Order**: Reordered tenant detection to check fields by reliability:
  1. dcontext (most reliable)
  2. context
  3. accountcode, userfield, peeraccount, lastdata
  4. channel, dstchannel (least reliable due to infrastructure naming)

## Impact
- Calls with context "closed-telair" will now correctly show tenant "telair"
- Infrastructure channel names (like sbc-ca2) won't override context-based tenant detection
- More accurate tenant assignment for multi-tenant environments