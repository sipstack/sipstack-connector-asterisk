# Changelog

All notable changes to the SIPSTACK Asterisk Connector will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.12.3] - 2025-08-14

### Fixed
- Fixed timestamp serialization to always include timezone marker ('Z' for UTC)
- Ensures timestamps are properly interpreted as UTC by the API

### Added
- Added TZ=UTC environment variable to .env.example for consistent timezone handling

## [0.12.2] - 2025-08-14

### Fixed
- Fixed missing `started_at` field in CDR data sent to API (was only sending `calldate`)
- Fixed timezone handling: now treats timestamps without timezone as UTC instead of local time
- When StartTime is missing from AMI events, now extracts Unix timestamp from uniqueid/linkedid
- Added `src_number` and `dst_number` fields to match API expectations

### Changed
- Improved timestamp extraction from Asterisk uniqueid format (hostname-unixtime.sequence)
- Enhanced timezone handling to prevent 4-hour EDT offset issues

## [0.12.1] - 2025-08-14

### Changed
- Updated endpoint path to connector-first organization: `/v1/mqs/connectors/asterisk/cdr`
- Maintains backward compatibility through API-side redirects

## [0.12.0] - 2025-08-14

### Changed
- **BREAKING:** Migrated from `/v1/mqs/cdr/batch` to `/v1/mqs/cdr/asterisk` endpoint
- **BREAKING:** Architecture change from database-trigger enrichment to API-level enrichment
- Enhanced call enrichment now happens at API ingestion time instead of database trigger
- Improved extension detection using CEL data analysis
- Better destination resolution for calls with 's', 't', 'h', 'i' destinations

### Added
- Connector-specific processing optimized for Asterisk CDR+CEL format
- Enhanced call thread normalization with participant names and extensions
- Improved CNAM and location lookups at ingestion time
- Better error handling and logging for enrichment failures

### Fixed
- Extension names now properly extracted from CEL events
- Destination number extraction from context patterns improved
- Call direction detection enhanced for internal/external calls

### Technical Notes  
- Requires API-Regional v1.5.0+ with new `/v1/mqs/connectors/asterisk/cdr` endpoint
- Database trigger `aggregate_cdr_to_call_logs` must be disabled
- Raw CDR/CEL data still stored for debugging purposes
- No data migration required - new processing applies to incoming calls only

## [0.11.1] - 2025-08-13

### Fixed
- Improved extension detection accuracy
- Enhanced location data handling
- Fixed duplicate extension display issues

## [0.11.0] - 2025-08-12

### Added
- Enhanced SIP Sight analytics integration
- Improved call quality metrics collection
- Better error reporting for failed uploads

### Changed
- Optimized batch processing performance
- Enhanced retry logic for API calls

## [0.10.x] - Previous Versions

See git history for detailed changes in earlier versions.

---

### Migration Guide: 0.11.x → 0.12.0

**Before upgrading:**
1. Ensure API-Regional is updated to v1.5.0+
2. Apply migration `069_disable_cdr_aggregation_trigger.sql` to disable database trigger
3. Verify the new `/v1/mqs/connectors/asterisk/cdr` endpoint is available

**After upgrading:**
1. Monitor connector logs for successful connections to new endpoint `/v1/mqs/connectors/asterisk/cdr`
2. Verify call enrichment is working properly in call_logs table
3. Check that extension names and CNAM data are being populated correctly

**Rollback procedure:**
1. Revert connector to v0.11.x
2. Re-enable database trigger: `ALTER TABLE data.call_detail_records ENABLE TRIGGER aggregate_cdr_to_call_logs;`
3. Restart API-Regional service