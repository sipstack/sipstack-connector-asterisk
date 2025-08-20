# Changelog for v0.13.41

## Fixed

### Number Extraction for Formatted Phone Numbers
- Fixed issue where phone numbers with formatting (e.g., "1-888-887-7686") were not being extracted properly
- The connector now correctly cleans numbers before checking if they're valid phone numbers
- Affects both inbound and outbound call processing
- Resolves issue where `src_number` and `dst_number` were showing as `None` in logs

### Call Processing
- Numbers are now properly normalized regardless of formatting (hyphens, spaces, etc.)
- Extension detection remains unaffected
- Improved handling of various phone number formats in CDR data

## Technical Details
- Modified `extract_numbers_and_extensions()` method to clean numbers before validation
- Added digit extraction logic for both source and destination numbers
- Ensures consistent phone number handling across all call directions (inbound/outbound/internal)

## Impact
- Fixes missing phone numbers in call_logs table (e.g., id=5306)
- Ensures proper number extraction for outbound calls from extensions
- Improves data quality for call analytics and reporting