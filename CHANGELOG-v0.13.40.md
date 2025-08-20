# Version 0.13.40 - Call Threads Count Fix

## Release Date
2025-08-20

## Changes

### Fixed
- **Call Threads Count Field**: Fixed field name from `call_thread_count` to `call_threads_count` to match database schema
  - The connector was calculating the count correctly but using the wrong field name
  - This caused `call_threads_count` to always be 0 in the database despite having populated `call_threads` data
  - Updated `CallData` dataclass to use correct field name
  - API controller updated to support both field names for backward compatibility

### Technical Details
- Changed field name in `src/database_connector.py`:
  - `CallData.call_thread_count` → `CallData.call_threads_count`
  - Updated field assignment to use `call_threads_count=len(threads)`
- API-Regional updated to accept both field names for smooth transition

### Impact
- Call logs will now properly show the thread count in `call_threads_count` column
- No action required for existing deployments - the API handles both field names
- Recommended to update connector to ensure proper data tracking

### Testing Recommendations
1. Deploy updated connector
2. Verify new calls have non-zero `call_threads_count` values
3. Confirm `call_threads` array data matches the count
4. Monitor for any API rejection errors