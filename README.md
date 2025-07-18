# SIPSTACK Connector for Asterisk

A lightweight Docker container that monitors Asterisk CDR events via AMI and sends them to SIPSTACK's API-Regional service for real-time call analytics.

## Quick Start

### 1. Prerequisites
- Docker installed
- Asterisk 16+ with AMI enabled (Asterisk 13+ for LinkedID support)
- SIPSTACK API key

### 2. Configure Asterisk

#### 2.1 Enable AMI Manager

Edit `/etc/asterisk/manager.conf`:

```ini
[general]
enabled = yes
port = 5038
bindaddr = 0.0.0.0

[manager-sipstack]
secret = your_secure_password
deny = 0.0.0.0/0.0.0.0
permit = 127.0.0.1/255.255.255.0
permit = 192.168.0.0/255.255.0.0
permit = 10.0.0.0/255.0.0.0
permit = 172.16.0.0/12
read = system,call,log,verbose,agent,user,dtmf,reporting,cdr,dialplan
write = system,call,agent,user,command,reporting,originate
```

#### 2.2 Enable CDR Manager with LinkedID Support

Edit `/etc/asterisk/cdr_manager.conf`:

```ini
[general]
enabled = yes

; LinkedID and Sequence support for call flow tracking
[mappings]
linkedid => LinkedID
sequence => Sequence
```

#### 2.3 Reload Configuration

```bash
# Reload AMI configuration
asterisk -rx "manager reload"

# Reload CDR manager module
asterisk -rx "module unload cdr_manager.so"
asterisk -rx "module load cdr_manager.so"

# Verify CDR manager status
asterisk -rx "cdr show status"
```

#### 2.4 Verify Setup

Use our visual status checker to verify your configuration:

```bash
# Download status checker
curl -O https://raw.githubusercontent.com/sipstack/sipstack-connector-asterisk/main/status.sh
chmod +x status.sh

# Run comprehensive system check
./status.sh
```

The status checker will verify:
- ✅ Asterisk service is running
- ✅ AMI is properly configured and listening
- ✅ CDR manager is enabled and active (not suspended)
- ✅ Docker is installed and running
- ✅ Network connectivity for AMI and Docker Hub
- 🧪 Test CDR submission and AMI connections

**Example output:**
```
╔════════════════════════════════════════════════════════════════╗
║ SIPSTACK Asterisk Connector - System Status Check            ║
╚════════════════════════════════════════════════════════════════╝

▶ Asterisk Service
──────────────────────────────────────────────────────
  ✓ Asterisk binary found
    → /usr/sbin/asterisk
  ✓ Asterisk process is running
    → PID: 1234
  ℹ Asterisk version
    → Asterisk 18.15.0

▶ CDR Configuration
──────────────────────────────────────────────────────
  ✓ CDR logging is enabled
  ✓ CDR manager is active
  ✓ cdr_manager.conf found
```

### 3. Deploy

Choose your preferred deployment method:

## Method A: Docker Compose (Recommended)

**Step 1:** Download configuration files
```bash
curl -O https://raw.githubusercontent.com/sipstack/sipstack-connector-asterisk/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/sipstack/sipstack-connector-asterisk/main/.env.example
```

**Step 2:** Configure environment
```bash
cp .env.example .env
nano .env  # Edit with your values
```

Edit `.env` with your settings:
```env
# Optional: Set to match your asterisk user (run 'id asterisk')
PUID=1000
PGID=1000

# Required
API_KEY=sk_1234567890abcdef1234567890abcdef  # Your SIPSTACK API key (standard format)
AMI_HOST=localhost                             # Use localhost with --network host
AMI_USERNAME=manager-sipstack                  # Must match section name in manager.conf
AMI_PASSWORD=your_secure_password              # AMI password
REGION=us1                                     # API region (ca1, us1, us2)
```

**Step 3:** Deploy
```bash
# Start the connector
docker-compose up -d

# View logs
docker-compose logs -f

# Check status
docker-compose ps
```

**Note:** If you need to access recording files, set PUID/PGID in your `.env` file to match your asterisk user's UID/GID (run `id asterisk` to find these values).

## Method B: Docker Run (No Compose Required)

**Option 1:** With environment file
```bash
# Create .env file
cat > .env << 'EOF'
# Optional: Set to match your asterisk user
PUID=1000
PGID=1000

API_KEY=sk_1234567890abcdef1234567890abcdef
AMI_HOST=localhost
AMI_USERNAME=manager-sipstack
AMI_PASSWORD=your_secure_password
REGION=us1
EOF

# Load .env and run container with host networking
source .env
docker run -d \
  --name sipstack-connector \
  --restart unless-stopped \
  --network host \
  --user ${PUID:-1000}:${PGID:-1000} \
  --env-file .env \
  -v /var/spool/asterisk:/var/spool/asterisk:ro \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  sipstack/connector-asterisk:latest
```

**Option 2:** Direct environment variables
```bash
# Set user to match your asterisk user (e.g., 1001:1001)
docker run -d \
  --name sipstack-connector \
  --restart unless-stopped \
  --network host \
  --user 1001:1001 \
  -e API_KEY="sk_1234567890abcdef1234567890abcdef" \
  -e AMI_HOST="localhost" \
  -e AMI_USERNAME="manager-sipstack" \
  -e AMI_PASSWORD="your_secure_password" \
  -e REGION="us1" \
  -v /var/spool/asterisk:/var/spool/asterisk:ro \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  sipstack/connector-asterisk:latest
```

### 4. Verify

```bash
# Docker Compose
docker-compose logs -f

# Docker Run
docker logs -f sipstack-connector
```

## Features

- 🚀 **Easy Deployment** - Single Docker container, no installation required
- 🔄 **Real-time CDR Monitoring** - Streams CDR events as they happen
- 🔐 **Standard Key Authentication** - Secure API access with server-managed features
- 📦 **Flexible Processing Modes** - Choose between batch or direct sending
- 🔗 **LinkedID & Sequence Support** - Complete call flow tracking with proper event ordering
- 🎯 **Minimal CDR Filtering** - Keep maximum data for analytics
- 🌍 **Multi-region Support** - Choose from ca1, us1, us2 regions
- 📊 **Prometheus Metrics** - Built-in monitoring on port 8000
- 🔧 **Zero Dependencies** - No Python or system packages needed on host
- 🔓 **Simple Permission Handling** - Just set PUID/PGID to match your asterisk user
- ⚡ **Optimized Defaults** - Batch size 200, timeout 30s, 1GB memory for production

## Recording Support

The connector can automatically monitor and upload call recordings from your Asterisk system. When enabled, it watches specified directories for new recording files and uploads them to SIPSTACK for transcription and analysis.

### Enabling Recording Upload

**1. Find your asterisk user's UID/GID:**
```bash
id asterisk
# Example output: uid=1001(asterisk) gid=1001(asterisk)
```

**2. Update your `.env` file with the UID/GID:**
```env
# Set to match your asterisk user (from step 1)
PUID=1001
PGID=1001

# Enable recording watcher
RECORDING_WATCHER_ENABLED=true
RECORDING_WATCH_PATHS=/var/spool/asterisk/monitor
RECORDING_FILE_EXTENSIONS=wav,mp3,gsm
RECORDING_DELETE_AFTER_UPLOAD=false
```

**3. Restart the connector:**
```bash
docker-compose down
docker-compose up -d
```

The connector will now:
- Monitor the specified directories for new recordings
- Wait for files to finish writing before processing
- Upload recordings to SIPSTACK with metadata
- Optionally delete files after successful upload

### Permission Handling

The connector runs as the user specified by PUID/PGID to match your asterisk user's permissions:

- **Set PUID/PGID** in your `.env` file to match your asterisk user
- **No manual permission changes** required on your Asterisk directories  
- **Works with any UID/GID** - just run `id asterisk` to find the values
- **Docker Compose** uses the `user:` directive to run as the specified user
- **Docker Run** requires `--user UID:GID` flag

Simply set these two values and the connector will run with the correct permissions!

### Recording File Naming

For optimal recording-to-CDR linking, the connector automatically extracts metadata from your recording filenames. While the connector is flexible and will process any recording file, following these naming conventions ensures the best integration:

#### Recommended Naming Pattern

Include the Asterisk **UniqueID** in your recording filename. The UniqueID is the primary key that links recordings to CDRs.

**UniqueID Format**: `{timestamp}.{sequence}` (e.g., `1702391234.12345`)

**Example Recording Filenames**:
- `1702391234.12345.wav` - Simple UniqueID format
- `out-555-1234-555-5678-1702391234.12345.wav` - With caller/callee info
- `queue-sales-1702391234.12345.wav` - Queue recording
- `20231212-143015-1702391234.12345.wav` - With human-readable timestamp

#### What the Connector Extracts

1. **UniqueID** (Primary) - Extracted using pattern `\d{10,}\.\d+`
   - This becomes the `call_id` field for joining with CDRs
   - Essential for linking recordings to their corresponding CDR

2. **Direction** - From prefixes like `in-`, `out-`, `inbound-`, `outbound-`

3. **Queue Name** - From `queue-{name}-` prefix or `/queues/{name}/` in path

4. **Timestamp** - From patterns like `YYYYMMDD-HHMMSS` or `YYYY-MM-DD-HH-MM-SS`

#### Asterisk Configuration Example

To include the UniqueID in your recordings, use Asterisk's `${UNIQUEID}` variable:

```asterisk
; In extensions.conf
exten => s,n,MixMonitor(/var/spool/asterisk/monitor/${STRFTIME(${EPOCH},,%Y%m%d-%H%M%S)}-${CALLERID(num)}-${EXTEN}-${UNIQUEID}.wav)

; For queue recordings in queues.conf
monitor-format = wav
monitor-type = MixMonitor
setinterfacevar = yes
; Then in extensions.conf for queue member recordings:
exten => _X.,n,Set(MONITOR_FILENAME=queue-${QUEUENAME}-${UNIQUEID})
```

#### Fallback Behavior

If no UniqueID is found in the filename, the connector will:
- Use the filename itself as the `recording_id`
- Still upload the recording successfully
- You can manually link recordings to CDRs later using other metadata

### Recording Filters

You can filter which recordings to process:

```env
# Only process files matching these patterns
RECORDING_INCLUDE_PATTERNS=queue-,out-

# Exclude files matching these patterns  
RECORDING_EXCLUDE_PATTERNS=test,temp

# Only process recordings at least 5 seconds long
RECORDING_MIN_DURATION=5

# Only process recordings from the last 12 hours
RECORDING_MAX_AGE_HOURS=12
```

## Configuration

### Environment Variables

#### Core Settings

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| API_KEY | Yes | - | SIPSTACK API key (sk_1234567890abcdef1234567890abcdef) |
| AMI_HOST | Yes | - | Asterisk server hostname/IP |
| AMI_USERNAME | Yes | - | AMI username |
| AMI_PASSWORD | Yes | - | AMI password |
| REGION | No | us1 | API region (ca1, us1, us2) |
| AMI_PORT | No | 5038 | AMI port |
| LOG_LEVEL | No | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |

**Note:** Set PUID/PGID in your `.env` file and Docker Compose will use them via the `user:` directive to run the container as your asterisk user.

#### CDR Processing

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| CDR_MODE | No | batch | Processing mode: 'batch' or 'direct' |
| CDR_BATCH_SIZE | No | 200 | Maximum CDRs per batch (batch mode) |
| CDR_BATCH_TIMEOUT | No | 30 | Seconds before sending partial batch |
| CDR_BATCH_FORCE_TIMEOUT | No | 5 | Force flush interval to prevent blocking |
| CDR_MAX_CONCURRENT | No | 10 | Max concurrent API requests (direct mode) |

#### CDR Filtering (Minimal Filtering for Maximum Analytics)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| CDR_FILTER_ENABLED | No | true | Enable CDR filtering |
| CDR_FILTER_QUEUE_ATTEMPTS | No | false | Filter failed queue attempts (keep for analytics) |
| CDR_FILTER_ZERO_DURATION | No | false | Filter zero duration calls (keep for analytics) |
| CDR_FILTER_INTERNAL_ONLY | No | false | Only keep internal extension calls |
| CDR_FILTER_MIN_DURATION | No | 0 | Minimum call duration in seconds |
| CDR_FILTER_EXCLUDE_DST | No | h | Only exclude hangup handlers |

**New Default Strategy**: With `linkedid` support, we now keep almost all CDRs for complete call flow analytics. The only excluded destination is 'h' (hangup handlers) which provides no analytical value. All queue attempts, zero-duration calls, and 's' destinations are preserved to enable:
- Queue performance analytics
- Call abandonment tracking  
- Complete call journey reconstruction
- Transfer success rates

#### Monitoring

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| MONITORING_ENABLED | No | true | Enable Prometheus metrics |
| MONITORING_PORT | No | 8000 | Metrics port |

#### Recording Watcher

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| RECORDING_WATCHER_ENABLED | No | false | Enable monitoring of recording directories |
| RECORDING_WATCH_PATHS | No | /var/spool/asterisk/monitor | Comma-separated list of directories to watch |
| RECORDING_FILE_EXTENSIONS | No | .wav,.mp3,.gsm | Comma-separated list of file extensions to process |
| RECORDING_MIN_FILE_SIZE | No | 1024 | Minimum file size in bytes (filters empty files) |
| RECORDING_STABILIZATION_TIME | No | 2.0 | Time to wait for file to finish writing (seconds) |
| RECORDING_PROCESS_EXISTING | No | false | Process existing files on startup |
| RECORDING_DELETE_AFTER_UPLOAD | No | false | Delete recording files after successful upload |

#### Recording Filters

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| RECORDING_INCLUDE_PATTERNS | No | - | Comma-separated patterns that must be in filename |
| RECORDING_EXCLUDE_PATTERNS | No | - | Comma-separated patterns to exclude from processing |
| RECORDING_MIN_DURATION | No | 0 | Minimum recording duration in seconds |
| RECORDING_MAX_AGE_HOURS | No | 24 | Maximum age of recordings to process (hours) |

## Monitoring

Access metrics at `http://localhost:8000/metrics`:

- `ami_connection_status` - AMI connection state
- `cdrs_processed_total` - Total CDRs processed
- `cdrs_sent_total` - CDRs successfully sent
- `cdrs_failed_total` - Failed CDR transmissions
- `api_request_duration_seconds` - API response times

## System Status Checker

Before troubleshooting, run our comprehensive status checker to identify issues:

```bash
# Download and run status checker
curl -O https://raw.githubusercontent.com/sipstack/sipstack-connector-asterisk/main/status.sh
chmod +x status.sh
./status.sh
```

**What it checks:**
- 🔍 **Asterisk Service**: Binary location, process status, version info
- 🔌 **AMI Configuration**: Port listening, manager.conf settings, user permissions
- 📊 **CDR Setup**: CDR logging status, cdr_manager module, configuration files
- 🐳 **Docker Environment**: Installation, daemon status, existing containers
- 🌐 **Network Connectivity**: AMI port access, internet connection, Docker Hub
- 🧪 **Live Testing**: CDR submission, AMI connections, module status

**Key Asterisk Commands (included in status checker):**
```bash
# Check CDR status
asterisk -rx "cdr show status"

# Reload CDR manager if suspended
asterisk -rx "module unload cdr_manager.so"
asterisk -rx "module load cdr_manager.so"

# Force CDR batch submission
asterisk -rx "cdr submit"

# Check AMI connections
asterisk -rx "manager show connected"

# Verify AMI settings
asterisk -rx "manager show settings"
```

The status checker provides **visual feedback** with color-coded results and actionable recommendations to fix any issues found.

## Troubleshooting

### Check Logs
```bash
# Docker Compose
docker-compose logs -f sipstack-connector

# Docker Run
docker logs -f sipstack-connector
```

### Stop/Start Container
```bash
# Docker Compose
docker-compose down
docker-compose up -d

# Docker Run
docker stop sipstack-connector
docker rm sipstack-connector
# Then run the docker run command again
```

### Test AMI Connection
```bash
# Docker Compose
docker-compose exec sipstack-connector telnet $AMI_HOST 5038

# Docker Run
docker exec -it sipstack-connector telnet $AMI_HOST 5038
```

### Container Management
```bash
# Check container status
docker ps | grep sipstack-connector

# Get container info
docker inspect sipstack-connector

# View metrics (if monitoring enabled)
curl http://localhost:8000/metrics
```

### Asterisk Configuration Issues

**CDR Manager Suspended**
```bash
# Check CDR status
asterisk -rx "cdr show status"

# If cdr_manager shows as (suspended), reload it:
asterisk -rx "module unload cdr_manager.so"
asterisk -rx "module load cdr_manager.so"

# Verify it's running
asterisk -rx "module show like cdr_manager"
```

**Force CDR Processing**
```bash
# Force immediate CDR batch processing
asterisk -rx "cdr submit"

# Check current batch status
asterisk -rx "cdr show status"
```

**AMI Connection Testing**
```bash
# Test AMI from command line
telnet localhost 5038

# Should show: Asterisk Call Manager/X.X.X
# Login with: Action: Login
#             Username: manager-sipstack
#             Secret: your_password
```

**Check AMI Users**
```bash
# Show active AMI connections
asterisk -rx "manager show connected"

# Show AMI configuration
asterisk -rx "manager show settings"
```

### Common Issues

**Cannot connect to AMI**
- Verify AMI is enabled in `manager.conf`
- Check AMI user permissions include `cdr` read access
- Ensure Docker uses `--network host` for localhost access

**API authentication failed**
- Verify API key format: `sk_1234567890abcdef1234567890abcdef` (35 chars total)
- Check region setting matches your account
- Ensure API key is active and not expired

**Recording access issues**
- Verify PUID/PGID in your `.env` match your asterisk user: `id asterisk`
- Check container is running as correct user: `docker exec sipstack-connector id`
- If using `RECORDING_DELETE_AFTER_UPLOAD=true`, ensure the volume is mounted with `:rw` instead of `:ro`
- For docker run, use `--user UID:GID` to set the user directly

## Advanced Usage

### Custom Network
If Asterisk is also in Docker:

```yaml
networks:
  default:
    external:
      name: asterisk-network
```

### Resource Limits
Default resource limits in `docker-compose.yml`:

```yaml
deploy:
  resources:
    limits:
      cpus: '1.0'
      memory: 1G
    reservations:
      cpus: '0.2'
      memory: 256M
```

These defaults are optimized for production workloads. Adjust based on your call volume.

### Multiple Instances
Monitor multiple Asterisk servers:

```bash
# Copy compose file for each instance
cp docker-compose.yml docker-compose-server1.yml
cp docker-compose.yml docker-compose-server2.yml

# Run with different project names
docker-compose -p server1 -f docker-compose-server1.yml up -d
docker-compose -p server2 -f docker-compose-server2.yml up -d
```

## Support

- Issues: https://github.com/sipstack/sipstack-connector-asterisk/issues
- Documentation: https://docs.sipstack.com
- API Reference: https://api.sipstack.com/docs

## CDR Filtering

The connector now uses minimal CDR filtering to maximize analytics value while leveraging `linkedid` for call flow reconstruction.

### New Filtering Philosophy

With `linkedid` support in the database, we've shifted from aggressive filtering to minimal filtering:
- **Previous approach**: Filter 80-90% of CDRs to save storage
- **New approach**: Keep nearly all CDRs for complete analytics

### What We Filter

Only truly useless records are filtered:
- **Hangup handlers** (`dst='h'`): Provide no analytical value
- Everything else is kept for analytics, including:
  - Queue attempts (`dst='s'`) - for queue analytics
  - Zero-duration calls - for routing analysis
  - Failed attempts - for performance metrics

### Filter Configuration

Enable filtering by setting `CDR_FILTER_ENABLED=true` in your `.env` file.

**Default filter rules (when enabled):**
- ✅ Filters failed queue attempts (`dst='s'` with `NO ANSWER` and `duration=0`)
- ✅ Filters zero-duration calls (except `BUSY`/`FAILED`/`CONGESTION`)
- ✅ **Smart destination filtering**: Excludes `s` and `h` ONLY if not answered and no duration
- ❌ Keeps ALL answered calls (including those with `dst='s'`)
- ❌ Keeps ALL calls with duration > 0 (even if `dst='s'`)
- ❌ Keeps all failed/busy calls with duration

### Smart 's' Destination Handling

The filter intelligently handles queue/IVR destinations:
- `dst='s', disposition='NO ANSWER', duration=0` → **Filtered** (failed queue attempt)
- `dst='s', disposition='ANSWERED', duration=45` → **Kept** (successful queue/IVR call)
- `dst='s', disposition='BUSY', duration=3` → **Kept** (meaningful interaction)

This ensures you don't lose legitimate calls that went through your IVR or queue system.

### Example: Call Flow Reconstruction with LinkedID

With minimal filtering, `linkedid`, and `sequence`, you can reconstruct complete call flows with proper event ordering:

**LinkedID**: Groups all CDRs belonging to the same call together
**Sequence**: Provides the exact order of events within the call

**Example Call Journey (linkedid: asterisk-1234567890.1):**
```
Sequence | Source       | Destination | Disposition  | Duration | Description
---------|--------------|-------------|--------------|----------|-------------
3790912  | +1234567890  | s          | ANSWERED     | 5        | IVR entry
3790913  | s            | 100        | NO ANSWER    | 0        | Queue attempt
3790914  | 100          | 101        | NO ANSWER    | 10       | Agent 1 ring
3790915  | 100          | 102        | ANSWERED     | 120      | Agent 2 answered
```

**Analytics Query Examples:**
```sql
-- Find primary (final) CDR for each call
SELECT * FROM call_detail_records 
WHERE linkedid = 'asterisk-1234567890.1'
ORDER BY sequence DESC 
LIMIT 1;

-- Get complete call flow in chronological order
SELECT 
  sequence,
  src AS source,
  dst AS destination,
  disposition,
  duration,
  billsec,
  lastapp,
  started_at
FROM call_detail_records
WHERE linkedid = 'asterisk-1234567890.1'
ORDER BY sequence ASC;

-- Analyze queue performance
SELECT 
  linkedid,
  COUNT(*) as queue_attempts,
  MAX(CASE WHEN disposition = 'ANSWERED' THEN 1 ELSE 0 END) as answered,
  SUM(CASE WHEN dst LIKE '1__' AND disposition = 'NO ANSWER' THEN duration ELSE 0 END) as total_ring_time
FROM call_detail_records
WHERE lastapp = 'Queue'
GROUP BY linkedid;
```

**Important**: The `sequence` field is an incrementing counter from Asterisk that ensures proper ordering even when multiple CDRs have the same timestamp.

### Advanced Filtering Options

```env
# Filter by minimum duration (seconds)
CDR_FILTER_MIN_DURATION=60  # Only keep calls > 60 seconds

# Keep only internal extension-to-extension calls
CDR_FILTER_INTERNAL_ONLY=true

# Custom destination exclusions
CDR_FILTER_EXCLUDE_DST=s,h,conference,ivr-timeout
```

### Monitoring Filter Performance

Track filtering effectiveness via Prometheus metrics:
- `asterisk_cdr_filtered_total` - Total CDRs filtered out
- `asterisk_cdr_queue_depth` - Current processing queue size

Check filter stats in logs:
```bash
docker logs sipstack-connector | grep "filtered\|CDR monitor stopped"
```

## License

MIT License - see LICENSE file for details