# SIPSTACK Connector for Asterisk

A lightweight Docker container that reads call data directly from Asterisk's database (CDR and CEL tables) and sends it to SIPSTACK's API for real-time call analytics.

## Version: 0.13.39

## Architecture Overview

Database-driven approach for reliable call data collection:
- **Direct Database Reading**: Polls CDR and CEL tables from Asterisk's database
- **Configurable Shipping**: Ships calls on completion (efficient) or progressively (real-time)
- **Recording Detection**: Supports database lookup or file system monitoring
- **Smart Retry Logic**: Failed calls retry with exponential backoff for up to 48 hours
- **Multi-Region Support**: Routes to correct regional API endpoint automatically

## Quick Start

### Prerequisites
- Docker installed
- Asterisk 16+ with database CDR/CEL storage enabled
- PostgreSQL/MySQL database with CDR and CEL tables
- SIPSTACK API key

### 1. Configure Database Access

#### For MariaDB/MySQL:
```sql
-- Create connector user with read access
CREATE USER 'asterisk_reader'@'%' IDENTIFIED BY 'secure_password';
GRANT SELECT ON asterisk.cdr TO 'asterisk_reader'@'%';
GRANT SELECT ON asterisk.cel TO 'asterisk_reader'@'%';
FLUSH PRIVILEGES;

-- Allow Docker network access (adjust IP range as needed)
-- For Docker bridge: 172.17.0.0/16
-- For custom networks: check with 'docker network ls'
```

#### For PostgreSQL:
```sql
-- Create connector user
CREATE USER asterisk_reader WITH PASSWORD 'secure_password';
GRANT SELECT ON TABLE cdr TO asterisk_reader;
GRANT SELECT ON TABLE cel TO asterisk_reader;
```

Update `pg_hba.conf` to allow connections:
```
host asterisk asterisk_reader 172.17.0.0/16 md5
```

### 2. Deploy with Docker Compose

Create `docker-compose.yml`:
```yaml
version: '3.8'
services:
  asterisk-connector:
    image: sipstack/asterisk-connector:latest
    container_name: asterisk-connector
    restart: unless-stopped
    env_file: .env
    volumes:
      # Optional: For recording detection
      - /var/spool/asterisk/monitor:/var/spool/asterisk/monitor:ro
      - /var/log/asterisk:/var/log/asterisk:ro
```

Create `.env` file:
```bash
# API Configuration
API_KEY=sk_your_api_key_here
REGION=us1
CUSTOMER_ID=123

# Database Connection
DB_TYPE=mysql
DB_HOST=172.17.0.1
DB_PORT=3306
DB_NAME=asterisk
DB_USER=asterisk_reader
DB_PASSWORD=secure_password

# CEL Configuration (choose one)
CEL_MODE=db
# OR for CSV: CEL_MODE=csv

# Call Shipping (optional)
CALL_SHIPPING_MODE=complete
LONG_CALL_UPDATE_INTERVAL=600
```

Start the connector:
```bash
docker-compose up -d
```

### 3. Alternative: Docker Run

```bash
docker run -d \
  --name asterisk-connector \
  --restart unless-stopped \
  --env-file .env \
  -v /var/spool/asterisk/monitor:/var/spool/asterisk/monitor:ro \
  -v /var/log/asterisk:/var/log/asterisk:ro \
  sipstack/asterisk-connector:latest
```

### 4. Verify

Check logs:
```bash
# Docker Compose
docker-compose logs -f

# Docker Run
docker logs -f asterisk-connector
```

Look for successful connection and call processing messages.

## Key Features

- **Multiple CEL Modes**: Database (cel_odbc), CSV (cel_custom), or AMI (cel_manager)
- **Recording Detection**: Links recordings automatically via database or file monitoring
- **Call Direction Detection**: Automatically detects inbound/outbound/internal calls
- **Tenant Detection**: Extracts tenant information from channel/context patterns
- **Fresh Start Mode**: Prevents duplicate historical data on restart
- **Efficient Shipping**: Complete mode ships calls once when finished (70% less API traffic)
- **Data Persistence**: SQLite tracking prevents duplicate shipping
- **Monitoring**: Prometheus metrics available on port 8000

## Configuration Options

### Core Settings
```bash
API_KEY=sk_xxxxx                    # Your SIPSTACK API key
REGION=us1                          # us1, us2, ca1, or dev
CUSTOMER_ID=123                     # Your customer ID
TENANT=                             # Optional default tenant
```

### Database Connection
```bash
DB_TYPE=mysql                       # mysql or postgresql
DB_HOST=172.17.0.1                  # Database host (Docker gateway)
DB_PORT=3306                        # 3306 for MySQL, 5432 for PostgreSQL
DB_NAME=asterisk                    # Database name
DB_USER=asterisk_reader             # Database user
DB_PASSWORD=secure_password         # Database password
```

### CEL Configuration
```bash
# Option 1: Database CEL (requires cel_odbc module)
CEL_MODE=db
DB_TABLE_CEL=cel

# Option 2: CSV CEL (requires cel_custom module)
CEL_MODE=csv
CEL_CSV_PATH=/var/log/asterisk/cel-custom/Master.csv

# Option 3: AMI CEL (requires cel_manager module)
CEL_MODE=ami
AMI_HOST=asterisk.local
AMI_PORT=5038
AMI_USERNAME=manager
AMI_PASSWORD=secret
```

### Call Shipping
```bash
CALL_SHIPPING_MODE=complete         # complete or progressive
LONG_CALL_UPDATE_INTERVAL=600       # Update long calls every N seconds
```

### Recording Detection
```bash
RECORDING_ENABLED=true
RECORDING_PATHS=/var/spool/asterisk/monitor,/var/spool/asterisk/recordings
DB_TABLE_RECORDINGS=                # Optional recordings table
```

## Docker Network Notes

The connector needs to reach your database. Common approaches:

**Option A: Use Docker Gateway** (Recommended)
```bash
# Find your Docker gateway
docker network inspect bridge | grep Gateway
# Use that IP in DB_HOST (usually 172.17.0.1)
```

**Option B: Host Network Mode**
```bash
docker run --network host ...
# Then use DB_HOST=localhost
```

**Option C: Docker Desktop**
```bash
# Use DB_HOST=host.docker.internal
```

## Troubleshooting

### Check Database Connection
```bash
# Test from container
docker exec -it asterisk-connector python3 -c "
import pymysql
conn = pymysql.connect(host='172.17.0.1', user='asterisk_reader', password='secure_password', database='asterisk')
print('Database connection successful')
"
```

### Common Issues
- **Connection refused**: Check firewall and database host configuration
- **Access denied**: Verify database user permissions and password
- **No calls found**: Check CDR/CEL tables have recent data
- **Module errors**: Ensure required Asterisk modules are loaded (cel_odbc, cel_custom, or cel_manager)

## Complete Documentation

For detailed setup instructions, troubleshooting, and advanced configuration:
👉 **[Full Documentation](https://github.com/your-org/api-regional/tree/main/connectors/asterisk)**

## Support

For technical support, please visit our documentation or contact support with your customer ID and connector logs.