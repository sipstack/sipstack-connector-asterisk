# SIPSTACK Connector for Asterisk

A lightweight Docker container that reads call data directly from Asterisk's database (CDR and CEL tables) and sends it to SIPSTACK's API-Regional service for real-time call analytics.

## Version: 0.13.39

## Architecture Overview

This connector uses a **database-driven approach** (v0.13.0+) to collect call data:
- **Direct Database Reading**: Polls CDR and CEL tables directly from Asterisk's database
- **Progressive Call Shipping**: Ships calls in phases (initial → update → complete)
- **Recording Detection**: Supports database table lookup or file system monitoring
- **Smart Retry Logic**: Failed API calls retry with exponential backoff for up to 48 hours
- **Multi-Region Support**: Automatically routes to correct regional API endpoint

## Quick Start

### 1. Prerequisites
- Docker installed
- Asterisk 16+ with database CDR/CEL storage enabled
- PostgreSQL/MySQL database with CDR and CEL tables
- SIPSTACK API key

### 2. Configure Asterisk Database

#### 2.1 Create Database Tables

First, create the CDR and CEL tables in your database (if not already created/in use).

**For PostgreSQL:**
```sql
-- Create CDR table
CREATE TABLE cdr (
    id SERIAL PRIMARY KEY,
    calldate TIMESTAMP NOT NULL DEFAULT NOW(),
    clid VARCHAR(80) NOT NULL DEFAULT '',
    src VARCHAR(80) NOT NULL DEFAULT '',
    dst VARCHAR(80) NOT NULL DEFAULT '',
    dcontext VARCHAR(80) NOT NULL DEFAULT '',
    channel VARCHAR(80) NOT NULL DEFAULT '',
    dstchannel VARCHAR(80) NOT NULL DEFAULT '',
    lastapp VARCHAR(80) NOT NULL DEFAULT '',
    lastdata VARCHAR(80) NOT NULL DEFAULT '',
    duration INTEGER NOT NULL DEFAULT 0,
    billsec INTEGER NOT NULL DEFAULT 0,
    disposition VARCHAR(45) NOT NULL DEFAULT '',
    amaflags INTEGER NOT NULL DEFAULT 0,
    accountcode VARCHAR(20) NOT NULL DEFAULT '',
    uniqueid VARCHAR(150) NOT NULL DEFAULT '',
    userfield VARCHAR(255) NOT NULL DEFAULT '',
    linkedid VARCHAR(150) NOT NULL DEFAULT '',
    sequence INTEGER NOT NULL DEFAULT 0,
    peeraccount VARCHAR(20) NOT NULL DEFAULT ''
);

-- Create CEL table
CREATE TABLE cel (
    id SERIAL PRIMARY KEY,
    eventtype VARCHAR(30) NOT NULL,
    eventtime TIMESTAMP NOT NULL,
    cid_name VARCHAR(80) NOT NULL DEFAULT '',
    cid_num VARCHAR(80) NOT NULL DEFAULT '',
    cid_ani VARCHAR(80) NOT NULL DEFAULT '',
    cid_rdnis VARCHAR(80) NOT NULL DEFAULT '',
    cid_dnid VARCHAR(80) NOT NULL DEFAULT '',
    exten VARCHAR(80) NOT NULL DEFAULT '',
    context VARCHAR(80) NOT NULL DEFAULT '',
    channame VARCHAR(80) NOT NULL DEFAULT '',
    appname VARCHAR(80) NOT NULL DEFAULT '',
    appdata VARCHAR(512) NOT NULL DEFAULT '',
    amaflags INTEGER NOT NULL DEFAULT 0,
    accountcode VARCHAR(20) NOT NULL DEFAULT '',
    uniqueid VARCHAR(150) NOT NULL DEFAULT '',
    linkedid VARCHAR(150) NOT NULL DEFAULT '',
    peer VARCHAR(80) NOT NULL DEFAULT '',
    userdeftype VARCHAR(255) NOT NULL DEFAULT '',
    extra VARCHAR(512) NOT NULL DEFAULT ''
);

-- Create indexes for performance
CREATE INDEX idx_cdr_calldate ON cdr(calldate);
CREATE INDEX idx_cdr_linkedid ON cdr(linkedid);
CREATE INDEX idx_cel_eventtime ON cel(eventtime);
CREATE INDEX idx_cel_linkedid ON cel(linkedid);
```

**For MySQL:**
```sql
-- Create CDR table
CREATE TABLE cdr (
    id INT AUTO_INCREMENT PRIMARY KEY,
    calldate DATETIME NOT NULL,
    clid VARCHAR(80) NOT NULL DEFAULT '',
    src VARCHAR(80) NOT NULL DEFAULT '',
    dst VARCHAR(80) NOT NULL DEFAULT '',
    dcontext VARCHAR(80) NOT NULL DEFAULT '',
    channel VARCHAR(80) NOT NULL DEFAULT '',
    dstchannel VARCHAR(80) NOT NULL DEFAULT '',
    lastapp VARCHAR(80) NOT NULL DEFAULT '',
    lastdata VARCHAR(80) NOT NULL DEFAULT '',
    duration INT NOT NULL DEFAULT 0,
    billsec INT NOT NULL DEFAULT 0,
    disposition VARCHAR(45) NOT NULL DEFAULT '',
    amaflags INT NOT NULL DEFAULT 0,
    accountcode VARCHAR(20) NOT NULL DEFAULT '',
    uniqueid VARCHAR(150) NOT NULL DEFAULT '',
    userfield VARCHAR(255) NOT NULL DEFAULT '',
    linkedid VARCHAR(150) NOT NULL DEFAULT '',
    sequence INT NOT NULL DEFAULT 0,
    peeraccount VARCHAR(20) NOT NULL DEFAULT '',
    INDEX idx_calldate (calldate),
    INDEX idx_linkedid (linkedid)
) ENGINE=InnoDB;

-- Create CEL table
CREATE TABLE cel (
    id INT AUTO_INCREMENT PRIMARY KEY,
    eventtype VARCHAR(30) NOT NULL,
    eventtime DATETIME NOT NULL,
    cid_name VARCHAR(80) NOT NULL DEFAULT '',
    cid_num VARCHAR(80) NOT NULL DEFAULT '',
    cid_ani VARCHAR(80) NOT NULL DEFAULT '',
    cid_rdnis VARCHAR(80) NOT NULL DEFAULT '',
    cid_dnid VARCHAR(80) NOT NULL DEFAULT '',
    exten VARCHAR(80) NOT NULL DEFAULT '',
    context VARCHAR(80) NOT NULL DEFAULT '',
    channame VARCHAR(80) NOT NULL DEFAULT '',
    appname VARCHAR(80) NOT NULL DEFAULT '',
    appdata VARCHAR(512) NOT NULL DEFAULT '',
    amaflags INT NOT NULL DEFAULT 0,
    accountcode VARCHAR(20) NOT NULL DEFAULT '',
    uniqueid VARCHAR(150) NOT NULL DEFAULT '',
    linkedid VARCHAR(150) NOT NULL DEFAULT '',
    peer VARCHAR(80) NOT NULL DEFAULT '',
    userdeftype VARCHAR(255) NOT NULL DEFAULT '',
    extra VARCHAR(512) NOT NULL DEFAULT '',
    INDEX idx_eventtime (eventtime),
    INDEX idx_linkedid (linkedid)
) ENGINE=InnoDB;
```

#### 2.2 Configure ODBC Driver

Install ODBC drivers and configure the connection:

**For PostgreSQL:**
```bash
# Install PostgreSQL ODBC driver
apt-get install odbc-postgresql

# Configure /etc/odbcinst.ini
[PostgreSQL]
Description = PostgreSQL ODBC driver
Driver = /usr/lib/x86_64-linux-gnu/odbc/psqlodbca.so
Setup = /usr/lib/x86_64-linux-gnu/odbc/libodbcpsqlS.so

# Configure /etc/odbc.ini
[asterisk-connector]
Description = PostgreSQL connection for Asterisk
Driver = PostgreSQL
Database = asterisk
Servername = localhost
Port = 5432
Username = asterisk
Password = your_password
```

**For MySQL:**
```bash
# Install MySQL ODBC driver
apt-get install unixodbc unixodbc-dev libmyodbc

# Configure /etc/odbcinst.ini
[MySQL]
Description = MySQL ODBC driver
Driver = /usr/lib/x86_64-linux-gnu/odbc/libmyodbc.so
Setup = /usr/lib/x86_64-linux-gnu/odbc/libodbcmyS.so

# Configure /etc/odbc.ini
[asterisk-connector]
Description = MySQL connection for Asterisk
Driver = MySQL
Server = localhost
Port = 3306
Database = asterisk
Username = asterisk
Password = your_password
```

#### 2.3 Configure Asterisk ODBC Resource

Edit `/etc/asterisk/res_odbc.conf`:

```ini
[asterisk]
enabled => yes
dsn => asterisk-connector
pre-connect => yes
max_connections => 5
username => asterisk
password => your_password
```

#### 2.4 Configure CDR to use ODBC

Edit `/etc/asterisk/cdr.conf`:

```ini
[general]
enable = yes
batch = yes
size = 100
time = 300
scheduleronly = no
safeshutdown = yes
```

Edit `/etc/asterisk/cdr_odbc.conf`:

```ini
[global]
dsn = asterisk
table = cdr
loguniqueid = yes
loguserfield = yes
newcdrcolumns = yes
```

#### 2.5 Configure CEL (Choose based on available modules)

**IMPORTANT**: CEL is REQUIRED for complete call tracking. Without CEL, you lose:
- DNID (actual dialed number) tracking
- Call transfers and threading
- Queue events and IVR navigation
- Recording detection via MixMonitor events
- DTMF digit tracking

Check which CEL modules you have:
```bash
ls /usr/lib*/asterisk/modules/cel_*.so
```

**Option A: If you have cel_odbc.so (Best Performance)**

Edit `/etc/asterisk/cel.conf`:
```ini
[general]
enable = yes
dateformat = %F %T.%3q
events = CHAN_START,CHAN_END,HANGUP,ANSWER,BRIDGE_ENTER,BRIDGE_EXIT,APP_START,APP_END,PARK_START,PARK_END,LINKEDID_END

[odbc]
connection = asterisk
table = cel
```

Set in connector `.env`:
```env
CEL_MODE=db
DB_TABLE_CEL=cel
```

**Option B: If you have cel_custom.so (Universal Compatibility)**

Edit `/etc/asterisk/cel_custom.conf`:
```ini
[mappings]
; CSV format matching database columns
Master.csv => ${CSV_QUOTE(${eventtype})},${CSV_QUOTE(${eventtime})},${CSV_QUOTE(${CALLERID(name)})},${CSV_QUOTE(${CALLERID(num)})},${CSV_QUOTE(${CALLERID(ANI)})},${CSV_QUOTE(${CALLERID(RDNIS)})},${CSV_QUOTE(${CALLERID(DNID)})},${CSV_QUOTE(${CHANNEL(exten)})},${CSV_QUOTE(${CHANNEL(context)})},${CSV_QUOTE(${CHANNEL(channame)})},${CSV_QUOTE(${CHANNEL(appname)})},${CSV_QUOTE(${CHANNEL(appdata)})},${CSV_QUOTE(${CHANNEL(amaflags)})},${CSV_QUOTE(${CHANNEL(accountcode)})},${CSV_QUOTE(${CHANNEL(uniqueid)})},${CSV_QUOTE(${CHANNEL(linkedid)})},${CSV_QUOTE(${BRIDGEPEER})},${CSV_QUOTE(${CHANNEL(userdeftype)})},${CSV_QUOTE(${CHANNEL(extra)})
```

Edit `/etc/asterisk/cel.conf`:
```ini
[general]
enable = yes
dateformat = %F %T.%3q
events = CHAN_START,CHAN_END,HANGUP,ANSWER,BRIDGE_ENTER,BRIDGE_EXIT,APP_START,APP_END,PARK_START,PARK_END,LINKEDID_END
```

Set in connector `.env`:
```env
CEL_MODE=csv
CEL_CSV_PATH=/var/log/asterisk/cel-custom/Master.csv
```

Add Docker volume mount:
```yaml
volumes:
  - /var/log/asterisk:/var/log/asterisk:ro
```

**Option C: If you have cel_manager.so (AMI Fallback)**

Edit `/etc/asterisk/cel.conf`:
```ini
[general]
enable = yes
dateformat = %F %T.%3q
events = CHAN_START,CHAN_END,HANGUP,ANSWER,BRIDGE_ENTER,BRIDGE_EXIT,APP_START,APP_END,PARK_START,PARK_END,LINKEDID_END

[manager]
enabled = yes
```

Set in connector `.env`:
```env
CEL_MODE=ami
AMI_HOST=localhost
AMI_PORT=5038
AMI_USERNAME=manager-sipstack
AMI_PASSWORD=your_secure_password
```

Ensure AMI user has CEL read permission in `/etc/asterisk/manager.conf`:
```ini
[manager-sipstack]
secret = your_secure_password
read = cdr,reporting
```

#### 2.6 Load Required Modules

Edit `/etc/asterisk/modules.conf` to ensure these modules are loaded:

```ini
; Ensure these are loaded (or not set to noload)
load => res_odbc.so
load => cdr_odbc.so
load => cel_odbc.so
```

#### 2.7 Apply Configuration

```bash
# Reload Asterisk modules
asterisk -rx "module reload res_odbc.so"
asterisk -rx "module reload cdr_odbc.so"
asterisk -rx "module reload cel_odbc.so"

# Or restart Asterisk
systemctl restart asterisk

# Verify modules are loaded
asterisk -rx "module show like odbc"

# Test ODBC connection
asterisk -rx "odbc show"

# Verify CDR is working
asterisk -rx "cdr show status"

# Verify CEL is working
asterisk -rx "cel show status"
```

#### 2.8 Test Database Logging

Make a test call and verify records appear in the database:

```sql
-- Check for CDR records
SELECT * FROM cdr ORDER BY calldate DESC LIMIT 5;

-- Check for CEL events
SELECT * FROM cel ORDER BY eventtime DESC LIMIT 10;
```

### 3. Configure Database for Docker Access

#### For MariaDB/MySQL:

**IMPORTANT**: MariaDB/MySQL must be configured to listen on the network interface Docker will use.

1. **Check current bind-address**:
   ```bash
   grep bind-address /etc/mysql/mariadb.conf.d/50-server.cnf
   # or
   grep bind-address /etc/my.cnf
   ```

2. **Update bind-address** to allow Docker connections:
   ```ini
   # In /etc/mysql/mariadb.conf.d/50-server.cnf or /etc/my.cnf
   
   # Option 1: Listen on all interfaces (simplest)
   bind-address = 0.0.0.0
   
   # Option 2: Listen on specific IPs (more secure)
   bind-address = 127.0.0.1,172.17.0.1
   
   # Option 3: Comment out to listen on all (MariaDB default)
   #bind-address = 127.0.0.1
   ```

3. **Restart MariaDB/MySQL**:
   ```bash
   sudo systemctl restart mariadb
   # or
   sudo systemctl restart mysql
   ```

4. **Verify it's listening**:
   ```bash
   sudo netstat -tlnp | grep 3306
   # Should show 0.0.0.0:3306 or 172.17.0.1:3306
   ```

5. **Grant user permissions** for Docker subnet:
   ```sql
   GRANT ALL PRIVILEGES ON pbxlogs.* TO 'asterisk'@'172.%' IDENTIFIED BY 'your_password';
   FLUSH PRIVILEGES;
   ```

#### For PostgreSQL:

1. **Update postgresql.conf**:
   ```ini
   # In /etc/postgresql/*/main/postgresql.conf
   listen_addresses = '*'  # or 'localhost,172.17.0.1'
   ```

2. **Update pg_hba.conf**:
   ```
   # In /etc/postgresql/*/main/pg_hba.conf
   host    asterisk    asterisk    172.17.0.0/16    md5
   ```

3. **Restart PostgreSQL**:
   ```bash
   sudo systemctl restart postgresql
   ```

### 4. Configure Docker Connection

After database is configured, use one of these methods:

#### Option A: Standard Docker Bridge (Recommended)
```env
# In .env file:
DB_HOST=172.17.0.1  # Default Docker bridge gateway
# Find your gateway with: docker network inspect bridge | grep Gateway
```

#### Option B: Host Network Mode
```bash
# In docker run:
docker run --network host ...

# In .env file:
DB_HOST=localhost
```

#### Option C: Use host.docker.internal (Docker Desktop only)
```env
# In .env file:
DB_HOST=host.docker.internal
```

### 4. Deploy

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
# Required - API Configuration
API_KEY=sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  # Your SIPSTACK API key
REGION=us1                                     # API region (ca1, us1, us2, dev)

# Required - Database Connection
DB_TYPE=mysql                                 # Database type: mysql or postgresql
DB_HOST=localhost                             # Database host (use localhost with --network host)
DB_PORT=3306                                  # Database port (3306 for MySQL, 5432 for PostgreSQL)
DB_USER=asterisk                              # Database user
DB_PASSWORD=your_db_password                  # Database password
DB_NAME=asterisk                              # Database name (e.g., pbxlogs)
DB_TABLE_CDR=cdr                              # CDR table name (default: cdr)

# Required - CEL Configuration
CEL_MODE=db                                   # Options: db, csv, ami
DB_TABLE_CEL=cel                              # CEL table name (if CEL_MODE=db)
CEL_CSV_PATH=/var/log/asterisk/cel-custom/Master.csv  # CSV path (if CEL_MODE=csv)

# Optional - Recording Configuration
DB_TABLE_RECORDINGS=                          # Database table for recordings (e.g., recordings)
RECORDING_PATHS=/var/spool/asterisk/monitor   # Recording directories (comma-separated)

# Optional - Processing Configuration
CDR_POLL_INTERVAL=5                           # Database poll interval in seconds
CDR_BATCH_SIZE=100                            # Records per batch
CUSTOMER_ID=1                                 # Your SIPSTACK customer ID
TENANT=                                        # Optional tenant identifier
HOST_HOSTNAME=                                 # Optional hostname identifier
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

## Method B: Docker Run

**Option 1: Using .env file (Recommended)**

Create your `.env` file from the example:
```bash
cp .env.example .env
nano .env  # Edit with your values
```

Then run with environment file:
```bash
# Load .env and run container
source .env
docker run -d \
  --name sipstack-connector \
  --restart unless-stopped \
  --network host \
  --user ${PUID:-1000}:${PGID:-1000} \
  --env-file .env \
  -v /var/spool/asterisk:/var/spool/asterisk:ro \
  -v /var/log/asterisk:/var/log/asterisk:ro \
  -v sipstack-data:/data \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  sipstack/connector-asterisk:latest
```

**Option 2: Direct environment variables**

```bash
docker run -d \
  --name sipstack-connector \
  --restart unless-stopped \
  --network host \
  -e API_KEY="sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -e REGION="us1" \
  -e DB_TYPE="mysql" \
  -e DB_HOST="localhost" \
  -e DB_PORT="3306" \
  -e DB_USER="asterisk" \
  -e DB_PASSWORD="your_db_password" \
  -e DB_NAME="asterisk" \
  -e DB_TABLE_CDR="cdr" \
  -e CEL_MODE="db" \
  -e DB_TABLE_CEL="cel" \
  -e RECORDING_PATHS="/var/spool/asterisk/monitor" \
  -v /var/spool/asterisk:/var/spool/asterisk:ro \
  -v /var/log/asterisk:/var/log/asterisk:ro \
  -v sipstack-data:/data \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  sipstack/connector-asterisk:latest
```

**Notes**: 
- The `/var/log/asterisk` volume mount is required if using `CEL_MODE=csv`
- Set PUID/PGID in your `.env` file to match your asterisk user (run `id asterisk`)

### 4. Verify

```bash
# Docker Compose
docker-compose logs -f

# Docker Run
docker logs -f sipstack-connector
```

## Features

- 🚀 **Database-Driven** - Direct database polling, no AMI overhead
- 📊 **Progressive Shipping** - Ships calls in phases as they progress
- 🔄 **Real-time Processing** - Polls database every 5 seconds
- 🎯 **Complete Call Data** - Combines CDR and CEL for full context
- 📼 **Recording Detection** - Automatic recording detection via CEL events or database table
- 🔐 **Secure API Access** - Standard key authentication with region-based routing
- 📦 **Batch Processing** - Efficient batch uploads to reduce API calls
- 🔗 **LinkedID Support** - Complete call flow tracking
- 🌍 **Multi-region Support** - Choose from ca1, us1, us2, dev regions
- 🔁 **Smart Retry Logic** - Failed API calls retry with exponential backoff for up to 48 hours
- 📊 **Prometheus Metrics** - Built-in monitoring on port 8000
- 🔧 **Zero Dependencies** - No system packages needed on host
- ⚡ **Fresh Start Mode** - Uses database's last CDR timestamp to avoid processing old data

## Recording Detection Strategy

The connector uses a two-pronged approach for recording detection:

### 1. CEL-Based Detection (Primary)
- Monitors CEL events for MixMonitor application (APP_START/APP_END)
- Extracts recording filename from appdata field
- Associates recordings with calls using linkedid

### 2. File System Monitoring (Fallback)
- Watches configured recording directories
- Detects new recordings that weren't caught by CEL
- Uses filename patterns to extract call metadata

## Configuration

### Core Settings

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| API_KEY | Yes | - | SIPSTACK API key |
| REGION | No | us1 | API region (ca1, us1, us2) |
| LOG_LEVEL | No | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |

### Database Connection

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| DB_TYPE | Yes | - | Database type: postgres or mysql |
| DB_HOST | Yes | - | Database hostname/IP |
| DB_PORT | No | 5432/3306 | Database port |
| DB_USER | Yes | - | Database username |
| DB_PASSWORD | Yes | - | Database password |
| DB_NAME | Yes | - | Database name |
| DB_TABLE_CDR | No | cdr | CDR table name |

### CEL Configuration (REQUIRED)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| CEL_MODE | Yes | - | CEL data source: db, csv, or ami |
| DB_TABLE_CEL | If mode=db | cel | CEL table name |
| CEL_CSV_PATH | If mode=csv | /var/log/asterisk/cel-custom/Master.csv | Path to CEL CSV file |
| CEL_CSV_POLL_INTERVAL | If mode=csv | 2 | Seconds between CSV checks |
| AMI_HOST | If mode=ami | - | Asterisk AMI hostname |
| AMI_PORT | If mode=ami | 5038 | AMI port |
| AMI_USERNAME | If mode=ami | - | AMI username |
| AMI_PASSWORD | If mode=ami | - | AMI password |

### Processing Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| POLL_INTERVAL | No | 5 | Database poll interval (seconds) |
| BATCH_SIZE | No | 100 | Records per batch |
| SHIP_INCOMPLETE_AFTER | No | 30 | Ship incomplete calls after (seconds) |
| SHIP_COMPLETE_AFTER | No | 5 | Ship complete calls after (seconds) |
| MAX_RECORDS_PER_POLL | No | 1000 | Maximum records per poll cycle |

### Recording Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| DB_TABLE_RECORDINGS | No | - | Database table for recordings (e.g., recordings) |
| RECORDING_PATHS | No | /var/spool/asterisk/monitor | Recording directories (comma-separated) |
| RECORDING_STABILITY_CHECK | No | 2 | Seconds to wait for file stability |
| RECORDING_BATCH_SIZE | No | 10 | Recordings per upload batch |
| RECORDING_FILE_EXTENSIONS | No | wav,mp3,gsm,ogg | File extensions to process |

### Call Direction Detection

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| ASTERISK_EXT_MIN_LENGTH | No | 2 | Minimum extension length |
| ASTERISK_EXT_MAX_LENGTH | No | 7 | Maximum extension length |
| ASTERISK_INTL_PREFIXES | No | 011,00,+ | International dialing prefixes |
| ASTERISK_E164_ENABLED | No | true | Enable E.164 format detection |

## Data Persistence

The connector maintains state using an internal SQLite database at `/data/tracker.db`:
- Tracks processed CDRs to avoid duplicates
- Stores call state for progressive shipping
- Records startup time for fresh start behavior

To persist state across container restarts:
```yaml
volumes:
  - sipstack-data:/data  # Named volume (recommended)
  # OR
  - ./_data:/data       # Local directory mount
```

## Fresh Start Behavior

When the connector starts with no existing state:
- Queries the database for the most recent CDR timestamp
- Uses this timestamp as the starting point for processing
- Only processes CDRs created after this point
- Ignores historical CDRs to avoid processing old data

This ensures clean deployments don't flood the system with historical calls and properly handles timezone differences between the connector and database.

## Monitoring

Access metrics at `http://localhost:8000/metrics`:

- `database_connection_status` - Database connection state
- `cdrs_processed_total` - Total CDRs processed
- `cels_processed_total` - Total CEL events processed
- `calls_shipped_total` - Calls successfully shipped
- `recordings_detected_total` - Recordings detected
- `api_request_duration_seconds` - API response times

## Troubleshooting

### Check Logs
```bash
# Docker Compose
docker-compose logs -f sipstack-connector

# Docker Run
docker logs -f sipstack-connector
```

### Test Database Connection
```bash
# Test from within container
docker exec -it sipstack-connector python -c "
from database_connector import DatabaseConnector
import os
config = {
    'DB_TYPE': os.getenv('DB_TYPE'),
    'DB_HOST': os.getenv('DB_HOST'),
    'DB_PORT': os.getenv('DB_PORT'),
    'DB_USER': os.getenv('DB_USER'),
    'DB_PASSWORD': os.getenv('DB_PASSWORD'),
    'DB_NAME': os.getenv('DB_NAME'),
}
db = DatabaseConnector(config)
print('Database connection successful!' if db.test_connection() else 'Connection failed')
"

# Test MySQL connection from host system
mysql -h 172.17.0.1 -u asterisk -p asterisk -e "SELECT COUNT(*) FROM cdr LIMIT 1;"

# Test from inside Docker container
docker run --rm -it mysql:8.0 mysql -h 172.17.0.1 -u asterisk -p asterisk
```

### Common Issues

**Cannot connect to database**
- Verify database credentials and network connectivity
- Ensure CDR and CEL tables exist
- Check firewall rules if database is remote

**MySQL/MariaDB "Packet sequence number wrong" Error**

This error means the database is not accepting connections from Docker's IP. You need BOTH:
1. MariaDB/MySQL listening on the Docker network interface
2. User permissions for the Docker subnet

**Quick Fix**:

1. **First, check if MariaDB is listening on the right interface**:
   ```bash
   sudo netstat -tlnp | grep 3306
   # If it shows 127.0.0.1:3306, it's ONLY listening on localhost
   # If it shows 0.0.0.0:3306, it's listening on all interfaces ✓
   ```

2. **If only on localhost, update bind-address (choose the most secure option)**:
   ```bash
   # Find config file:
   find /etc -name "*.cnf" | xargs grep -l bind-address 2>/dev/null
   
   # Edit the file (usually /etc/my.cnf or /etc/mysql/mariadb.conf.d/50-server.cnf):
   sudo nano /etc/mysql/mariadb.conf.d/50-server.cnf
   ```
   
   **Option A: Multiple specific IPs (Most Secure - MariaDB 10.3+)**:
   ```ini
   # Listen only on localhost and Docker bridge
   bind-address = 127.0.0.1,172.17.0.1
   ```
   
   **Option B: Bind to Docker bridge IP only**:
   ```ini
   # If you don't need local connections
   bind-address = 172.17.0.1
   ```
   
   **Option C: Use Unix socket for local, TCP for Docker**:
   ```ini
   # Comment out bind-address entirely
   #bind-address = 127.0.0.1
   # MariaDB will listen on all IPs, but use firewall rules:
   ```
   Then add firewall rules:
   ```bash
   # Allow only Docker subnet and localhost
   sudo iptables -A INPUT -p tcp --dport 3306 -s 172.16.0.0/12 -j ACCEPT
   sudo iptables -A INPUT -p tcp --dport 3306 -s 127.0.0.1 -j ACCEPT
   sudo iptables -A INPUT -p tcp --dport 3306 -j DROP
   ```
   
   **Option D: All interfaces (Least Secure)**:
   ```ini
   bind-address = 0.0.0.0
   ```
   
   ```bash
   # Restart MariaDB after changes:
   sudo systemctl restart mariadb
   ```

3. **Then check MySQL host access permissions**:
   ```sql
   -- Connect to MySQL as root and check user permissions
   SELECT User, Host FROM mysql.user WHERE User = 'asterisk';
   
   -- Grant access from Docker subnet (common subnet: 172.17.0.0/16)
   GRANT ALL PRIVILEGES ON asterisk.* TO 'asterisk'@'172.17.%' IDENTIFIED BY 'your_password';
   GRANT ALL PRIVILEGES ON asterisk.* TO 'asterisk'@'172.%.%.%' IDENTIFIED BY 'your_password';
   FLUSH PRIVILEGES;
   ```

2. **Verify Docker network connectivity**:
   ```bash
   # Test connection from within container
   docker exec -it sipstack-connector mysql -h 172.17.0.1 -u asterisk -p asterisk
   
   # Check Docker bridge network
   docker network ls
   docker network inspect bridge
   ```

3. **Check MySQL bind address**:
   ```ini
   # In /etc/mysql/mysql.conf.d/mysqld.cnf
   bind-address = 0.0.0.0  # Allow connections from any IP
   # OR specific to Docker bridge
   bind-address = 172.17.0.1
   ```

4. **Restart MySQL after configuration changes**:
   ```bash
   sudo systemctl restart mysql
   ```

5. **Use host networking as fallback**:
   ```bash
   # Add --network host to docker run command
   docker run -d --name sipstack-connector --network host ...
   ```

6. **Alternative: Use localhost with port mapping**:
   ```env
   # In .env file
   DB_HOST=host.docker.internal  # For Docker Desktop
   # OR
   DB_HOST=172.17.0.1            # For Linux Docker
   ```

**No CDRs being processed**
- Verify CDR and CEL are being written to database
- Check table names match configuration
- Review logs for specific errors

**Recordings not detected**
- Ensure CEL events include APP_START/APP_END for MixMonitor
- Verify recording paths are accessible
- Check file permissions on recording directories

**High CPU usage**
- Increase POLL_INTERVAL to reduce polling frequency
- Adjust BATCH_SIZE for optimal performance
- Monitor database query performance

## Migration from AMI Connector

If migrating from the AMI-based connector:

1. **Database Setup**: Ensure Asterisk is writing CDR/CEL to database
2. **Stop AMI Connector**: `docker stop sipstack-connector`
3. **Update Configuration**: Switch from AMI settings to database settings
4. **Fresh Start**: The new connector will only process new CDRs
5. **Deploy**: Start the database connector

## Performance Tuning

### Database Optimization
```sql
-- Add indexes for better performance
CREATE INDEX idx_cdr_calldate ON cdr(calldate);
CREATE INDEX idx_cdr_linkedid ON cdr(linkedid);
CREATE INDEX idx_cel_eventtime ON cel(eventtime);
CREATE INDEX idx_cel_linkedid ON cel(linkedid);
```

### Connector Tuning
```env
# For high-volume systems
POLL_INTERVAL=2              # More frequent polling
BATCH_SIZE=500              # Larger batches
MAX_RECORDS_PER_POLL=5000   # Process more per cycle

# For low-volume systems
POLL_INTERVAL=30            # Less frequent polling
BATCH_SIZE=50               # Smaller batches
MAX_RECORDS_PER_POLL=500    # Process fewer per cycle
```

## Support

- Issues: https://github.com/sipstack/sipstack-connector-asterisk/issues
- Documentation: https://docs.sipstack.com
- API Reference: https://api.sipstack.com/docs

## License

MIT License - see LICENSE file for details
