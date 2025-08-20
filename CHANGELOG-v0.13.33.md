# Asterisk Connector v0.13.33

## Fixed CEL CSV Parsing Issue

### Problem
The CEL CSV file from Asterisk's cel_custom.conf was being written as one massive line without proper newlines between records. This occurred because Asterisk writes CSV with quoted fields that can contain newlines, causing the entire file to appear as a single CSV line. The connector was only reading 1 line and finding 0 CEL events.

### Solution
Implemented a new parsing strategy for CEL CSV files:

1. **Pattern Matching**: First attempts to find CEL events using regex patterns that match known event types (CHAN_START, ANSWER, HANGUP, etc.)
2. **Event Extraction**: Extracts individual events by finding boundaries between event types
3. **Fallback Parsing**: If pattern matching fails, falls back to standard CSV parsing with delimiter detection
4. **Proper Handling**: Correctly handles Asterisk's CSV format where quoted fields contain embedded newlines

### Technical Details
- Reads entire file content instead of line-by-line to handle the single-line format
- Uses regex pattern matching to identify event boundaries: `"EVENTTYPE","timestamp"`
- Supports all standard CEL event types from cel.conf
- Maintains existing caching mechanism for performance
- Preserves the 50,000 event limit to prevent memory issues with large files

### Files Modified
- `src/database_connector.py`: Updated `_get_cel_from_csv()` method with new parsing logic

### Testing Recommendations
1. Verify CEL events are properly extracted from Master.csv
2. Check that call_threads array is populated with CEL event data
3. Confirm extension names and other CEL metadata are captured
4. Monitor performance with large CEL CSV files