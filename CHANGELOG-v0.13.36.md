# Asterisk Connector v0.13.36 - Configurable Call Shipping Strategy

## Added
- **Configurable Call Shipping Mode**: New `CALL_SHIPPING_MODE` configuration option
  - `complete` mode (default): Ships calls only when fully complete (LINKEDID_END event)
  - `progressive` mode: Ships calls multiple times (initial/update/complete) 
  - Complete mode reduces API traffic by ~70% (1 shipment vs 3-4 per call)

- **Long Call Update Interval**: New `LONG_CALL_UPDATE_INTERVAL` configuration
  - In complete mode, send periodic updates for calls longer than this interval
  - Default: 600 seconds (10 minutes)
  - Set to 0 to disable periodic updates for long calls
  - Ensures visibility for unusually long conference calls or support sessions

## Changed
- Default shipping behavior changed from progressive to complete mode
- Removed unused configuration variables that were never implemented:
  - `PROGRESSIVE_SHIPPING` (replaced by `CALL_SHIPPING_MODE`)
  - `SHIP_INCOMPLETE_AFTER` (not implemented)
  - `SHIP_COMPLETE_AFTER` (not implemented)
- Removed unused `enable_progressive` variable from call_processor.py

## Benefits
- **70% Reduction in API Traffic**: Each call shipped once instead of 3-4 times
- **Lower Processing Overhead**: Less CPU/memory usage on both connector and API
- **Configurable Strategy**: Choose based on your needs:
  - Use `complete` mode for efficiency (recommended)
  - Use `progressive` mode for real-time visibility during calls
- **Long Call Safety**: Periodic updates ensure long calls aren't "lost"

## Migration Notes
- The default has changed to `complete` mode
- To keep the old behavior, set `CALL_SHIPPING_MODE=progressive` in your .env
- The old `PROGRESSIVE_SHIPPING` variable is no longer used

## Technical Details
- In complete mode, calls are tracked but not shipped until LINKEDID_END event
- Long calls (> LONG_CALL_UPDATE_INTERVAL) still get periodic updates
- Progressive mode maintains original behavior for backward compatibility