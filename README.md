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
API_KEY=sk_t2_xxxxx_xxxxx          # Your SIPSTACK API key
AMI_HOST=localhost                 # Use localhost with --network host
AMI_USERNAME=manager-sipstack      # Must match section name in manager.conf
AMI_PASSWORD=your_secure_password  # AMI password
REGION=us1                         # API region (ca1, us1, us2)
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
API_KEY=sk_t2_xxxxx_xxxxx
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
  -e API_KEY="sk_t2_xxxxx_xxxxx" \
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
- 🔐 **Smart Key Authentication** - Secure tier-based API access
- 📦 **Automatic Batching** - Efficiently sends CDRs in batches (100 records or 30 seconds)
- 🌍 **Multi-region Support** - Choose from ca1, us1, us2 regions
- 📊 **Prometheus Metrics** - Built-in monitoring on port 8000
- 🔧 **Zero Dependencies** - No Python or system packages needed on host

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| API_KEY | Yes | - | SIPSTACK API key (sk_t{tier}_{customer}_{token}) |
| AMI_HOST | Yes | - | Asterisk server hostname/IP |
| AMI_USERNAME | Yes | - | AMI username |
| AMI_PASSWORD | Yes | - | AMI password |
| REGION | No | us1 | API region (ca1, us1, us2) |
| AMI_PORT | No | 5038 | AMI port |
| LOG_LEVEL | No | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |
| BATCH_SIZE | No | 100 | Maximum CDRs per batch |
| BATCH_TIMEOUT | No | 30 | Maximum seconds before sending batch |
| MONITORING_ENABLED | No | true | Enable Prometheus metrics |
| MONITORING_PORT | No | 8000 | Metrics port |

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
- Verify API key format: `sk_t{tier}_{customer}_{token}`
- Check region setting matches your API key

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

## License

MIT License - see LICENSE file for details
