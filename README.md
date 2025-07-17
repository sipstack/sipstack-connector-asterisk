# SIPSTACK Connector for Asterisk

A lightweight Docker container that monitors Asterisk CDR events via AMI and sends them to SIPSTACK's API-Regional service for real-time call analytics.

## Quick Start

### 1. Prerequisites
- Docker installed
- Asterisk 16+ with AMI enabled
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

#### 2.2 Enable CDR Manager

Edit `/etc/asterisk/cdr_manager.conf`:

```ini
[general]
enabled = yes
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

## Method B: Docker Run (No Compose Required)

**Option 1:** With environment file
```bash
# Create .env file
cat > .env << 'EOF'
API_KEY=sk_1234567890abcdef1234567890abcdef
AMI_HOST=localhost
AMI_USERNAME=manager-sipstack
AMI_PASSWORD=your_secure_password
REGION=us1
EOF

# Run container with host networking
docker run -d \
  --name sipstack-connector \
  --restart unless-stopped \
  --network host \
  --env-file .env \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  sipstack/connector-asterisk:latest
```

**Option 2:** Direct environment variables
```bash
docker run -d \
  --name sipstack-connector \
  --restart unless-stopped \
  --network host \
  -e API_KEY="sk_1234567890abcdef1234567890abcdef" \
  -e AMI_HOST="localhost" \
  -e AMI_USERNAME="manager-sipstack" \
  -e AMI_PASSWORD="your_secure_password" \
  -e REGION="us1" \
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
- 🎯 **Smart CDR Filtering** - Reduce storage by 80-90% by filtering noise
- 🌍 **Multi-region Support** - Choose from ca1, us1, us2 regions
- 📊 **Prometheus Metrics** - Built-in monitoring on port 8000
- 🔧 **Zero Dependencies** - No Python or system packages needed on host

## Recording Support

The connector can automatically monitor and upload call recordings from your Asterisk system. When enabled, it watches specified directories for new recording files and uploads them to SIPSTACK for transcription and analysis.

### Enabling Recording Upload

**1. Update your `.env` file:**
```env
# Enable recording watcher
RECORDING_WATCHER_ENABLED=true
RECORDING_WATCH_PATHS=/var/spool/asterisk/monitor
RECORDING_FILE_EXTENSIONS=.wav,.mp3,.gsm
RECORDING_DELETE_AFTER_UPLOAD=false
```

**2. Update `docker-compose.yml` to mount the recording directory:**
```yaml
services:
  sipstack-connector:
    # ... existing configuration ...
    volumes:
      # Mount Asterisk spool directory for recording access
      - /var/spool/asterisk:/var/spool/asterisk:ro
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

#### CDR Processing

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| CDR_MODE | No | batch | Processing mode: 'batch' or 'direct' |
| CDR_BATCH_SIZE | No | 100 | Maximum CDRs per batch (batch mode) |
| CDR_BATCH_TIMEOUT | No | 30 | Seconds before sending partial batch |
| CDR_BATCH_FORCE_TIMEOUT | No | 5 | Force flush interval to prevent blocking |
| CDR_MAX_CONCURRENT | No | 10 | Max concurrent API requests (direct mode) |

#### CDR Filtering (Optional - Reduces Storage by ~80-90%)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| CDR_FILTER_ENABLED | No | false | Enable CDR filtering |
| CDR_FILTER_QUEUE_ATTEMPTS | No | true | Filter failed queue attempts (dst='s' with NO ANSWER) |
| CDR_FILTER_ZERO_DURATION | No | true | Filter zero duration calls (except BUSY/FAILED) |
| CDR_FILTER_INTERNAL_ONLY | No | false | Only keep internal extension calls |
| CDR_FILTER_MIN_DURATION | No | 0 | Minimum call duration in seconds |
| CDR_FILTER_EXCLUDE_DST | No | s,h | Comma-separated destinations to exclude |

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
Adjust in `docker-compose.yml`:

```yaml
deploy:
  resources:
    limits:
      cpus: '1.0'
      memory: 1G
```

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

The connector includes smart CDR filtering to reduce storage requirements by 80-90% while preserving meaningful call data.

### Why Filter CDRs?

Analysis of typical Asterisk CDR data shows that the majority of records are noise:
- **Queue distribution attempts**: ~85% of CDRs are failed queue attempts with destination 's'
- **Zero-duration calls**: Internal routing that never connected
- **System destinations**: Calls to 'h' (hangup) and other system contexts

These records provide little value for analytics but consume significant storage.

### Filter Configuration

Enable filtering by setting `CDR_FILTER_ENABLED=true` in your `.env` file.

**Default filter rules (when enabled):**
- ✅ Filters queue distribution attempts (`dst='s'` with `NO ANSWER`)
- ✅ Filters zero-duration calls (except `BUSY`/`FAILED`/`CONGESTION`)
- ✅ Excludes system destinations (`s`, `h`)
- ❌ Keeps all answered calls
- ❌ Keeps all failed/busy calls
- ❌ Keeps calls with duration > 0

### Example: Before and After Filtering

**Before filtering (100 CDRs):**
```
85 queue attempts (dst='s', NO ANSWER, duration=0)
5 zero-duration internal calls
7 actual customer calls (ANSWERED)
3 failed customer calls (BUSY/FAILED)
```

**After filtering (10 CDRs):**
```
7 actual customer calls (ANSWERED)
3 failed customer calls (BUSY/FAILED)
```

**Result**: 90% storage reduction, 100% meaningful data retained.

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