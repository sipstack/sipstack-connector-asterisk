# Asterisk Connector v0.13.35 - Inbound Call DID Extraction

## Fixed
- **Missing DID for Inbound Calls**: Fixed missing `dst_number` (DID) for inbound calls
  - Now extracts DID from CEL CHAN_START events when not in dcontext
  - Handles cases where CDR dst field is an extension (e.g., "308")
  - Always attempts to find the actual dialed DID for inbound calls

- **Missing Caller Number**: Fixed missing `src_number` for some inbound calls
  - Now extracts caller number from CEL `cid_num` field when CDR src is empty
  - Ensures all inbound calls have proper caller identification

## Technical Details
- Enhanced DID extraction logic for inbound calls:
  1. First attempts to extract from dcontext
  2. Falls back to CEL CHAN_START event's exten field
  3. Works even when dst is an extension number
  
- Added CEL-based caller number extraction as fallback
- Maintains backward compatibility with existing logic

## Impact
- Inbound calls will now properly show both:
  - The DID that was dialed (dst_number)
  - The extension that answered (dst_extension)
- Improves data completeness for call reporting and analytics