"""
Direct database connector for reading Asterisk CDR/CEL tables.
Replaces AMI listeners for CDR/CEL data collection.
"""

import os
import json
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
import pymysql
import psycopg2
from psycopg2.extras import RealDictCursor
import sqlite3
from contextlib import contextmanager

logger = logging.getLogger(__name__)

@dataclass
class CallData:
    """Formatted call data matching call_logs table structure"""
    # Required fields (no defaults) must come first
    connector_version: str
    customer_id: int
    tenant: str
    hostname: str
    linkedid: str
    is_complete: bool
    call_time: str
    duration_seconds: int
    call_threads: List[Dict]
    call_threads_count: int  # Changed from call_thread_count to match DB schema
    direction: str  # 'i'=inbound, 'o'=outbound, 'x'=internal
    disposition: str
    
    # Optional fields (with defaults) must come after required fields
    connector: str = "asterisk"
    src_number: Optional[str] = None
    src_extension: Optional[str] = None
    src_name: Optional[str] = None
    src_extension_name: Optional[str] = None
    dst_number: Optional[str] = None
    dst_extension: Optional[str] = None
    dst_name: Optional[str] = None
    dst_extension_name: Optional[str] = None
    recording_files: Optional[List[Dict]] = None
    raw_cdrs: Optional[List[Dict]] = None
    raw_cels: Optional[List[Dict]] = None


class DatabaseConnector:
    """
    Auto-detect and connect to Asterisk's database (MySQL or PostgreSQL).
    Reads CDR/CEL data directly without AMI overhead.
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.db_type = config.get('DB_TYPE', 'mysql').lower()
        self.db_host = config.get('DB_HOST', 'localhost')
        self.db_port = int(config.get('DB_PORT', 3306 if self.db_type == 'mysql' else 5432))
        self.db_name = config.get('DB_NAME', 'asterisk')
        self.db_user = config.get('DB_USER', 'asterisk_reader')
        self.db_password = config.get('DB_PASSWORD', '')
        self.cdr_table = config.get('DB_TABLE_CDR', 'cdr')
        
        # Shipping configuration
        self.shipping_mode = config.get('CALL_SHIPPING_MODE', 'complete').lower()
        if self.shipping_mode not in ['complete', 'progressive']:
            logger.warning(f"Invalid CALL_SHIPPING_MODE '{self.shipping_mode}', defaulting to 'complete'")
            self.shipping_mode = 'complete'
        
        self.long_call_update_interval = int(config.get('LONG_CALL_UPDATE_INTERVAL', '600'))
        
        # Tenant detection configuration
        known_trunks_str = config.get('KNOWN_TRUNKS', '')
        self.known_trunks = [trunk.strip().lower() for trunk in known_trunks_str.split(',') if trunk.strip()]
        
        # Log database configuration on startup
        logger.info("=" * 60)
        logger.info("DATABASE CONNECTOR CONFIGURATION")
        logger.info("=" * 60)
        logger.info(f"CDR Source: {self.db_type} database")
        logger.info(f"  Host: {self.db_host}:{self.db_port}")
        logger.info(f"  Database: {self.db_name}")
        logger.info(f"  User: {self.db_user}")
        logger.info(f"  CDR Table: {self.cdr_table}")
        logger.info(f"📦 Shipping Mode: {self.shipping_mode.upper()}")
        if self.shipping_mode == 'complete' and self.long_call_update_interval > 0:
            logger.info(f"  Long call updates: Every {self.long_call_update_interval}s")
        
        if self.known_trunks:
            logger.info(f"🏢 Tenant Detection: Filtering out {len(self.known_trunks)} known trunks: {', '.join(self.known_trunks)}")
        else:
            logger.info("🏢 Tenant Detection: No known trunks configured (KNOWN_TRUNKS empty)")
        
        # CEL mode configuration (REQUIRED)
        self.cel_mode = config.get('CEL_MODE', '').lower()
        if not self.cel_mode:
            raise ValueError("CEL_MODE is required. Options: db, csv, ami")
        
        if self.cel_mode == 'db':
            self.cel_table = config.get('DB_TABLE_CEL', 'cel')
            logger.info(f"CEL Source: Database table")
            logger.info(f"  CEL Table: {self.cel_table}")
            logger.info(f"  Using same {self.db_type} connection as CDR")
        elif self.cel_mode == 'csv':
            self.cel_csv_path = config.get('CEL_CSV_PATH', '/var/log/asterisk/cel-custom/Master.csv')
            self.cel_csv_poll_interval = int(config.get('CEL_CSV_POLL_INTERVAL', '2'))
            self.cel_csv_last_position = 0
            self.cel_csv_cache = {}  # Cache of linkedid -> events
            self.cel_csv_last_read = 0  # Last file modification time
            self.cel_csv_cache_ttl = 300  # Cache for 5 minutes
            self.cel_csv_max_read_lines = int(config.get('CEL_CSV_MAX_LINES', '50000'))  # Limit lines per read
            logger.info(f"CEL Source: CSV file")
            logger.info(f"  Path: {self.cel_csv_path}")
            logger.info(f"  Poll Interval: {self.cel_csv_poll_interval}s")
            # Check if CSV file exists
            if os.path.exists(self.cel_csv_path):
                logger.info(f"  ✓ CSV file exists")
            else:
                logger.warning(f"  ✗ CSV file not found at {self.cel_csv_path}")
        elif self.cel_mode == 'ami':
            # AMI configuration will be handled by a separate AMI listener for CEL only
            self.ami_host = config.get('AMI_HOST', 'localhost')
            self.ami_port = int(config.get('AMI_PORT', '5038'))
            self.ami_username = config.get('AMI_USERNAME', '')
            self.ami_password = config.get('AMI_PASSWORD', '')
            logger.info(f"CEL Source: AMI events")
            logger.info(f"  AMI Host: {self.ami_host}:{self.ami_port}")
            logger.info(f"  AMI User: {self.ami_username}")
        else:
            raise ValueError(f"Invalid CEL_MODE: {self.cel_mode}. Options: db, csv, ami")
        
        # Tracking database - try /data first, fallback to /tmp
        self.tracker_db = '/data/tracker.db'
        try:
            self._init_tracker_db()
        except (sqlite3.OperationalError, PermissionError) as e:
            logger.warning(f"Cannot create tracker DB at /data: {e}")
            self.tracker_db = '/tmp/tracker.db'
            logger.info(f"Using fallback tracker DB: {self.tracker_db}")
            self._init_tracker_db()
        
        # Perform database health check
        self._health_check()
        
        logger.info("=" * 60)
        logger.info(f"Database connector ready - CDR from {self.db_type} DB, CEL from {self.cel_mode}")
        logger.info("=" * 60)
    
    def _health_check(self):
        """
        Comprehensive database health check on startup.
        Tests connection, table access, and basic functionality.
        Exits with error code 1 if any checks fail.
        """
        logger.info("Performing database health check...")
        
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Test 1: Basic connection
                logger.info("✓ Database connection successful")
                
                # Test 2: CDR table access
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {self.cdr_table} LIMIT 1")
                    cdr_count = cursor.fetchone()
                    if self.db_type == 'mysql':
                        count_value = cdr_count[0] if isinstance(cdr_count, (list, tuple)) else cdr_count['COUNT(*)']
                    else:  # PostgreSQL
                        count_value = cdr_count[0] if isinstance(cdr_count, (list, tuple)) else cdr_count['count']
                    logger.info(f"✓ CDR table '{self.cdr_table}' accessible ({count_value} records)")
                except Exception as e:
                    logger.error(f"✗ CDR table '{self.cdr_table}' access failed: {e}")
                    raise
                
                # Test 3: CDR table structure - check for required columns
                required_cdr_columns = ['linkedid', 'calldate', 'src', 'dst', 'disposition']
                try:
                    if self.db_type == 'mysql':
                        cursor.execute(f"DESCRIBE {self.cdr_table}")
                        columns = [row[0] if isinstance(row, (list, tuple)) else row['Field'] for row in cursor.fetchall()]
                    else:  # PostgreSQL
                        cursor.execute(f"""
                            SELECT column_name 
                            FROM information_schema.columns 
                            WHERE table_name = '{self.cdr_table}'
                        """)
                        columns = [row[0] if isinstance(row, (list, tuple)) else row['column_name'] for row in cursor.fetchall()]
                    
                    missing_columns = [col for col in required_cdr_columns if col not in columns]
                    if missing_columns:
                        logger.error(f"✗ CDR table missing required columns: {missing_columns}")
                        raise ValueError(f"CDR table missing columns: {missing_columns}")
                    
                    logger.info(f"✓ CDR table structure valid (found {len(columns)} columns)")
                except Exception as e:
                    logger.error(f"✗ CDR table structure check failed: {e}")
                    raise
                
                # Test 4: CEL source validation
                if self.cel_mode == 'db':
                    try:
                        cursor.execute(f"SELECT COUNT(*) FROM {self.cel_table} LIMIT 1")
                        cel_count = cursor.fetchone()
                        if self.db_type == 'mysql':
                            count_value = cel_count[0] if isinstance(cel_count, (list, tuple)) else cel_count['COUNT(*)']
                        else:  # PostgreSQL
                            count_value = cel_count[0] if isinstance(cel_count, (list, tuple)) else cel_count['count']
                        logger.info(f"✓ CEL table '{self.cel_table}' accessible ({count_value} records)")
                    except Exception as e:
                        logger.error(f"✗ CEL table '{self.cel_table}' access failed: {e}")
                        raise
                elif self.cel_mode == 'csv':
                    try:
                        if not os.path.exists(self.cel_csv_path):
                            raise FileNotFoundError(f"CEL CSV file not found: {self.cel_csv_path}")
                        
                        # Test CSV file readability
                        with open(self.cel_csv_path, 'r') as f:
                            # Read first few lines to validate format
                            lines = []
                            for _ in range(3):
                                line = f.readline().strip()
                                if line:
                                    lines.append(line)
                                else:
                                    break
                            
                            if not lines:
                                logger.warning("⚠ CEL CSV file is empty (this is normal for new installations)")
                            else:
                                # Basic CSV format validation
                                sample_line = lines[0]
                                field_count = len(sample_line.split(','))
                                if field_count < 10:  # CEL should have many fields
                                    logger.warning(f"⚠ CEL CSV format may be invalid ({field_count} fields)")
                                    logger.debug(f"Sample line: {sample_line[:100]}...")
                                else:
                                    logger.info(f"✓ CEL CSV file readable ({len(lines)} sample lines, {field_count} fields)")
                        
                    except Exception as e:
                        logger.error(f"✗ CEL CSV file check failed: {e}")
                        raise
                elif self.cel_mode == 'ami':
                    logger.info("✓ CEL AMI mode configured (will validate AMI connection separately)")
                
                # Test 5: Basic query functionality
                try:
                    # Test a simple query that the connector will use
                    if self.db_type == 'mysql':
                        test_query = f"""
                            SELECT linkedid, calldate 
                            FROM {self.cdr_table} 
                            WHERE calldate >= %s 
                            ORDER BY calldate DESC 
                            LIMIT 1
                        """
                    else:  # PostgreSQL
                        test_query = f"""
                            SELECT linkedid, calldate 
                            FROM {self.cdr_table} 
                            WHERE calldate >= %s 
                            ORDER BY calldate DESC 
                            LIMIT 1
                        """
                    
                    test_timestamp = datetime.now() - timedelta(days=1)
                    cursor.execute(test_query, (test_timestamp,))
                    result = cursor.fetchone()
                    
                    logger.info("✓ Query functionality test passed")
                    
                    if result:
                        linkedid = result[0] if isinstance(result, (list, tuple)) else result['linkedid']
                        logger.debug(f"Sample recent call found: {linkedid}")
                    else:
                        logger.info("No recent calls found in last 24 hours (this is normal)")
                        
                except Exception as e:
                    logger.error(f"✗ Query functionality test failed: {e}")
                    raise
                
                logger.info("🎉 Database health check completed successfully!")
                
        except Exception as e:
            logger.error("=" * 60)
            logger.error("❌ DATABASE HEALTH CHECK FAILED")
            logger.error("=" * 60)
            logger.error(f"Error: {e}")
            logger.error("Common issues:")
            logger.error("1. Database credentials incorrect (check DB_USER, DB_PASSWORD)")
            logger.error("2. Database host unreachable (check DB_HOST, DB_PORT)")
            logger.error("3. Database/tables don't exist (check DB_NAME, DB_TABLE_CDR)")
            logger.error("4. Insufficient permissions (grant SELECT on tables)")
            logger.error("5. CEL file path incorrect or not mounted (check CEL_CSV_PATH)")
            logger.error("=" * 60)
            logger.error("Connector cannot start without database access. Exiting...")
            raise SystemExit(1)

    def _get_last_cdr_time(self) -> Optional[datetime]:
        """Get the timestamp of the most recent CDR in the database"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                query = f"SELECT MAX(calldate) as last_time FROM {self.cdr_table}"
                cursor.execute(query)
                result = cursor.fetchone()
                if result:
                    last_time = result['last_time'] if isinstance(result, dict) else result[0]
                    if last_time:
                        logger.debug(f"Last CDR time in database: {last_time}")
                        return last_time
        except Exception as e:
            logger.warning(f"Could not get last CDR time: {e}")
        return None
    
    def _get_database_time(self) -> Optional[datetime]:
        """Get current time from database to match timezone"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                if self.db_type == 'mysql':
                    cursor.execute("SELECT NOW() as dbtime")
                else:  # PostgreSQL
                    cursor.execute("SELECT NOW() as dbtime")
                result = cursor.fetchone()
                if result:
                    db_time = result['dbtime'] if isinstance(result, dict) else result[0]
                    logger.debug(f"Database time: {db_time}")
                    return db_time
        except Exception as e:
            logger.warning(f"Could not get database time: {e}")
            return None
    
    def _get_startup_time(self) -> Optional[datetime]:
        """Get the startup time from tracker database"""
        try:
            with sqlite3.connect(self.tracker_db) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT startup_time FROM startup_info WHERE id = 1")
                result = cursor.fetchone()
                if result:
                    return datetime.fromisoformat(result[0])
        except Exception as e:
            logger.debug(f"Could not get startup time: {e}")
        return None
    
    def _init_tracker_db(self):
        """Initialize SQLite database for tracking processed calls"""
        os.makedirs(os.path.dirname(self.tracker_db), exist_ok=True)
        
        is_fresh_start = not os.path.exists(self.tracker_db) or os.path.getsize(self.tracker_db) == 0
        
        with sqlite3.connect(self.tracker_db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_calls (
                    linkedid TEXT PRIMARY KEY,
                    first_seen TEXT,
                    last_updated TEXT,
                    is_complete INTEGER DEFAULT 0,
                    shipped_at TEXT,
                    ship_count INTEGER DEFAULT 0,
                    last_cdr_count INTEGER DEFAULT 0,
                    last_cel_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    last_error TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS shipment_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    linkedid TEXT,
                    shipped_at TEXT,
                    phase TEXT,  -- 'initial', 'update', 'complete'
                    success INTEGER,
                    response_code INTEGER,
                    error_message TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS startup_info (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    startup_time TEXT NOT NULL
                )
            """)
            
            # If fresh start, record startup time
            if is_fresh_start:
                # Get the most recent CDR timestamp to use as starting point
                last_cdr_time = self._get_last_cdr_time()
                if last_cdr_time:
                    startup_time_str = last_cdr_time.isoformat()
                    logger.info("=" * 60)
                    logger.info("FRESH START MODE")
                    logger.info(f"Last CDR in database: {startup_time_str}")
                    logger.info(f"Will only process NEW CDRs created after this time")
                    logger.info("=" * 60)
                else:
                    # No CDRs exist, use current database time
                    db_time = self._get_database_time()
                    startup_time = db_time if db_time else datetime.now()
                    startup_time_str = startup_time.isoformat()
                    logger.info("=" * 60)
                    logger.info("FRESH START MODE - Empty Database")
                    logger.info(f"Starting from: {startup_time_str}")
                    logger.info("=" * 60)
                
                conn.execute("INSERT OR REPLACE INTO startup_info (id, startup_time) VALUES (1, ?)", 
                           (startup_time_str,))
            
            # Clean up old entries (> 24 hours)
            cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
            conn.execute("DELETE FROM processed_calls WHERE last_updated < ?", (cutoff,))
            conn.commit()
    
    @contextmanager
    def get_db_connection(self):
        """Get database connection based on type with improved error handling"""
        conn = None
        try:
            if self.db_type == 'mysql':
                # Only log connection attempt on first connect
                if not hasattr(self, '_connection_attempt_logged'):
                    logger.debug(f"Attempting MySQL/MariaDB connection to {self.db_host}:{self.db_port} as user '{self.db_user}'")
                    self._connection_attempt_logged = True
                
                # Try simplest connection first for MariaDB compatibility
                try:
                    # Most basic connection - works with MariaDB 10.x
                    conn = pymysql.connect(
                        host=self.db_host,
                        port=self.db_port,
                        user=self.db_user,
                        password=self.db_password,
                        database=self.db_name,
                        cursorclass=pymysql.cursors.DictCursor
                    )
                    # If basic connection works, enhance it
                    conn.autocommit = True
                    # Only log connection debug on first connect, not every poll
                    if not hasattr(self, '_connection_logged'):
                        logger.debug("Basic MySQL connection successful")
                        self._connection_logged = True
                except Exception as basic_err:
                    logger.debug(f"Basic connection failed: {basic_err}")
                    # Try with additional parameters
                    conn = pymysql.connect(
                        host=self.db_host,
                        port=self.db_port,
                        user=self.db_user,
                        password=self.db_password,
                        database=self.db_name,
                        cursorclass=pymysql.cursors.DictCursor,
                        autocommit=True,
                        charset='utf8mb4',
                        connect_timeout=10,
                        read_timeout=30,
                        write_timeout=30
                    )
            elif self.db_type == 'postgresql':
                conn = psycopg2.connect(
                    host=self.db_host,
                    port=self.db_port,
                    user=self.db_user,
                    password=self.db_password,
                    database=self.db_name,
                    cursor_factory=RealDictCursor,
                    connect_timeout=10
                )
                conn.autocommit = True
            else:
                raise ValueError(f"Unsupported database type: {self.db_type}")
            
            yield conn
        except Exception as e:
            error_msg = str(e)
            if "Packet sequence number wrong" in error_msg:
                logger.error("Connection failed with packet sequence error.")
                logger.error("Common causes:")
                logger.error("1. Run 'mysqladmin -u root -p flush-hosts' on database server")
                logger.error("2. Check user permissions: GRANT ALL ON %s.* TO '%s'@'172.%%'" % (self.db_name, self.db_user))
                logger.error("3. Verify MariaDB/MySQL is listening on %s:%s" % (self.db_host, self.db_port))
            logger.debug(f"Database connection error: {e}")
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass  # Ignore close errors
    
    def get_failed_calls(self, max_attempts: int = 100) -> List[str]:
        """Get linkedids of calls that failed to ship (retry for up to 48 hours)"""
        try:
            with sqlite3.connect(self.tracker_db) as conn:
                cursor = conn.cursor()
                # Retry with exponential backoff: 5 min, 10 min, 20 min, 40 min, then every hour
                cursor.execute("""
                    SELECT linkedid, error_count
                    FROM processed_calls
                    WHERE shipped_at IS NULL
                    AND error_count > 0
                    AND first_seen > datetime('now', '-48 hours')
                    AND (
                        (error_count = 1 AND last_updated < datetime('now', '-5 minutes')) OR
                        (error_count = 2 AND last_updated < datetime('now', '-10 minutes')) OR
                        (error_count = 3 AND last_updated < datetime('now', '-20 minutes')) OR
                        (error_count = 4 AND last_updated < datetime('now', '-40 minutes')) OR
                        (error_count >= 5 AND last_updated < datetime('now', '-1 hour'))
                    )
                    ORDER BY last_updated ASC
                    LIMIT 5
                """)
                results = cursor.fetchall()
                if results:
                    logger.debug(f"Found {len(results)} calls to retry (oldest error_count: {results[0][1]})")
                return [row[0] for row in results]
        except Exception as e:
            logger.debug(f"Could not get failed calls: {e}")
            return []
    
    def get_updated_calls(self, since: datetime, limit: int = 1000) -> List[str]:
        """Get linkedids of calls that have been updated since timestamp"""
        # Only log on first poll
        if not hasattr(self, '_first_poll_logged'):
            logger.info(f"Starting to monitor for CDRs after {since}")
            self._first_poll_logged = True
            
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Build query based on database type
            if self.db_type == 'mysql':
                query = f"""
                    SELECT DISTINCT linkedid,
                           MAX(calldate) as last_update
                    FROM {self.cdr_table}
                    WHERE calldate > %s
                    GROUP BY linkedid
                    ORDER BY last_update DESC
                    LIMIT %s
                """
                
            else:  # PostgreSQL
                query = f"""
                    SELECT DISTINCT linkedid,
                           MAX(calldate) as last_update
                    FROM {self.cdr_table}
                    WHERE calldate > %s
                    GROUP BY linkedid
                    ORDER BY last_update DESC
                    LIMIT %s
                """
            
            cursor.execute(query, (since, limit))
            results = cursor.fetchall()
            
            # Log results if found
            if results:
                logger.info(f"📞 Found {len(results)} new/updated calls")
                # Log each CDR briefly
                for row in results[:5]:  # Show first 5
                    linkedid = row.get('linkedid') if isinstance(row, dict) else row[0]
                    last_update = row.get('last_update') if isinstance(row, dict) else row[1]
                    # Get CDR details for better logging
                    cursor.execute(f"SELECT src, dst, disposition, duration FROM {self.cdr_table} WHERE linkedid = %s LIMIT 1", (linkedid,))
                    cdr = cursor.fetchone()
                    if cdr:
                        src = cdr.get('src') if isinstance(cdr, dict) else cdr[0]
                        dst = cdr.get('dst') if isinstance(cdr, dict) else cdr[1]
                        disp = cdr.get('disposition') if isinstance(cdr, dict) else cdr[2]
                        dur = cdr.get('duration') if isinstance(cdr, dict) else cdr[3]
                        logger.info(f"  → {src} → {dst} ({disp}, {dur}s) at {last_update}")
            
            linkedids = [row['linkedid'] if isinstance(row, dict) else row[0] for row in results]
            
            # Also check CEL for additional linkedids (only if CEL mode is database)
            if linkedids and self.cel_mode == 'db':
                cel_query = f"""
                    SELECT DISTINCT linkedid
                    FROM {self.cel_table}
                    WHERE eventtime > %s
                    AND linkedid NOT IN ({','.join(['%s'] * len(linkedids))})
                    LIMIT %s
                """
                cursor.execute(cel_query, [since] + linkedids + [limit - len(linkedids)])
                cel_results = cursor.fetchall()
                linkedids.extend([row['linkedid'] for row in cel_results])
            
            return linkedids
    
    def get_call_cdrs(self, linkedid: str) -> List[Dict]:
        """Get all CDR records for a linkedid"""
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            
            query = f"""
                SELECT *
                FROM {self.cdr_table}
                WHERE linkedid = %s
                ORDER BY calldate
            """
            
            cursor.execute(query, (linkedid,))
            return cursor.fetchall()
    
    def get_call_cels(self, linkedid: str) -> List[Dict]:
        """Get all CEL events for a linkedid based on configured mode"""
        if self.cel_mode == 'db':
            return self._get_cel_from_db(linkedid)
        elif self.cel_mode == 'csv':
            return self._get_cel_from_csv(linkedid)
        elif self.cel_mode == 'ami':
            return self._get_cel_from_ami_cache(linkedid)
        else:
            logger.warning(f"Unknown CEL mode: {self.cel_mode}")
            return []
    
    def _get_cel_from_db(self, linkedid: str) -> List[Dict]:
        """Get CEL events from database"""
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            
            query = f"""
                SELECT *
                FROM {self.cel_table}
                WHERE linkedid = %s
                ORDER BY eventtime
            """
            
            cursor.execute(query, (linkedid,))
            return cursor.fetchall()
    
    def _get_cel_from_csv(self, linkedid: str) -> List[Dict]:
        """Get CEL events from CSV file with caching"""
        import csv
        import time
        import re
        
        # Check cache first
        cache_key = linkedid
        if cache_key in self.cel_csv_cache:
            cache_entry = self.cel_csv_cache[cache_key]
            if time.time() - cache_entry['timestamp'] < self.cel_csv_cache_ttl:
                logger.debug(f"Using cached CEL events for {linkedid}")
                return cache_entry['events']
        
        events = []
        
        try:
            if not os.path.exists(self.cel_csv_path):
                logger.warning(f"CEL CSV file not found: {self.cel_csv_path}")
                return []
            
            # Check if file has been modified since last read
            file_stat = os.stat(self.cel_csv_path)
            file_size = file_stat.st_size
            file_mtime = file_stat.st_mtime
            
            # If file hasn't changed and we have full cache, use it
            if file_mtime == self.cel_csv_last_read and self.cel_csv_cache:
                logger.debug(f"CEL CSV unchanged, checking cache for {linkedid}")
                # File unchanged, but this linkedid wasn't in cache
                self.cel_csv_cache[cache_key] = {'events': [], 'timestamp': time.time()}
                return []
            
            logger.debug(f"Reading CEL CSV file (size: {file_size} bytes) for linkedid: {linkedid}")
            
            with open(self.cel_csv_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Read the entire file content
                content = f.read()
                
                # CSV columns match the cel_custom.conf mapping
                fieldnames = ['eventtype', 'eventtime', 'cid_name', 'cid_num', 'cid_ani', 
                             'cid_rdnis', 'cid_dnid', 'exten', 'context', 'channame',
                             'appname', 'appdata', 'amaflags', 'accountcode', 'uniqueid',
                             'linkedid', 'peer', 'userdeftype', 'extra']
                
                # Asterisk cel_custom.conf writes events with quotes that can contain newlines
                # Split by pattern: "eventtype"," where eventtype is one of the known types
                event_types = ['CHAN_START', 'CHAN_END', 'HANGUP', 'ANSWER', 'BRIDGE_ENTER', 
                              'BRIDGE_EXIT', 'APP_START', 'APP_END', 'LINKEDID_END', 'PARK_START', 
                              'PARK_END', 'CONF_ENTER', 'CONF_EXIT', 'USER_DEFINED']
                
                # Create pattern to split on event boundaries
                # Look for pattern like: "EVENTTYPE","timestamp"
                pattern = r'("(?:' + '|'.join(event_types) + r')","[^"]*")'
                
                # Split content into individual events
                # Each match is an event starting with eventtype
                matches = re.findall(pattern, content)
                
                logger.debug(f"CEL CSV: Found {len(matches)} potential events using pattern matching")
                
                if not matches and content:
                    # Fallback: try simple CSV parsing if pattern matching fails
                    logger.debug("Pattern matching failed, trying standard CSV parsing")
                    
                    # Try to detect delimiter
                    delimiter = ','
                    first_line = content.split('\n')[0] if '\n' in content else content[:1000]
                    if first_line.count('|') > first_line.count(','):
                        delimiter = '|'
                    elif first_line.count('\t') > first_line.count(','):
                        delimiter = '\t'
                    
                    # Use StringIO to parse content as CSV
                    from io import StringIO
                    csv_buffer = StringIO(content)
                    reader = csv.DictReader(csv_buffer, fieldnames=fieldnames, delimiter=delimiter)
                    
                    line_count = 0
                    for row in reader:
                        try:
                            line_count += 1
                            if line_count > self.cel_csv_max_read_lines:
                                logger.warning(f"CEL CSV: Reached max lines limit ({self.cel_csv_max_read_lines})")
                                break
                            
                            row_linkedid = row.get('linkedid', '')
                            if row_linkedid == linkedid:
                                events.append(row)
                                if len(events) == 1:
                                    logger.debug(f"First matching CEL event: {row}")
                        except Exception as e:
                            if line_count <= 3:
                                logger.debug(f"Error parsing row {line_count}: {e}")
                            continue
                else:
                    # Parse the matched events
                    event_count = 0
                    for match in matches:
                        try:
                            event_count += 1
                            if event_count > self.cel_csv_max_read_lines:
                                logger.warning(f"CEL CSV: Reached max events limit ({self.cel_csv_max_read_lines})")
                                break
                            
                            # Each match is a full CSV line for one event
                            # Find the rest of the line after this match
                            match_pos = content.find(match)
                            if match_pos == -1:
                                continue
                            
                            # Find the end of this event (next event or end of content)
                            next_match_pos = len(content)
                            for next_event in event_types:
                                next_pattern = f'"{next_event}",'
                                next_pos = content.find(next_pattern, match_pos + len(match))
                                if next_pos != -1 and next_pos < next_match_pos:
                                    next_match_pos = next_pos
                            
                            # Extract full event line
                            event_line = content[match_pos:next_match_pos].rstrip('\r\n,')
                            
                            # Parse as CSV
                            from io import StringIO
                            csv_buffer = StringIO(event_line)
                            reader = csv.DictReader(csv_buffer, fieldnames=fieldnames, delimiter=',')
                            
                            for row in reader:
                                row_linkedid = row.get('linkedid', '')
                                if row_linkedid == linkedid:
                                    events.append(row)
                                    if len(events) == 1:
                                        logger.debug(f"First matching CEL event: {row.get('eventtype')} at {row.get('eventtime')}")
                                break  # Only one row per event
                                
                        except Exception as e:
                            if event_count <= 3:
                                logger.debug(f"Error parsing event {event_count}: {e}")
                            continue
                
                logger.info(f"Scanned {len(matches) if matches else 'file'}, found {len(events)} CEL events for linkedid {linkedid}")
                
                if events:
                    # Log summary
                    event_types_found = set(e.get('eventtype') for e in events)
                    logger.debug(f"CEL event types found: {', '.join(event_types_found)}")
                
                # Update cache
                self.cel_csv_cache[cache_key] = {'events': events, 'timestamp': time.time()}
                self.cel_csv_last_read = file_mtime
                
                # Clean old cache entries
                current_time = time.time()
                expired_keys = [k for k, v in self.cel_csv_cache.items() 
                              if current_time - v['timestamp'] > self.cel_csv_cache_ttl]
                for k in expired_keys:
                    del self.cel_csv_cache[k]
                    
        except Exception as e:
            logger.error(f"Error reading CEL CSV: {e}", exc_info=True)
        
        return events
    
    def _get_cel_from_ami_cache(self, linkedid: str) -> List[Dict]:
        """Get CEL events from AMI cache (populated by separate AMI listener)"""
        # This would query a cache table populated by an AMI CEL listener
        # For now, return empty - the AMI listener would need to be implemented
        logger.debug(f"AMI CEL mode not yet fully implemented for linkedid {linkedid}")
        return []
    
    def is_call_complete(self, linkedid: str, cdrs: List[Dict], cels: List[Dict]) -> bool:
        """
        Determine if a call is complete and ready for final shipping.
        
        Checks:
        1. LINKEDID_END event in CEL (most reliable)
        2. All CDRs have disposition != NULL
        3. No updates in last 60 seconds
        4. HANGUP event exists for primary channel
        """
        # Check for LINKEDID_END in CEL (most definitive)
        if cels:
            has_linkedid_end = any(
                cel.get('eventtype') == 'LINKEDID_END' 
                for cel in cels
            )
            if has_linkedid_end:
                logger.debug(f"Call {linkedid} complete: LINKEDID_END found")
                return True
            
            # Check for HANGUP events
            hangup_count = sum(
                1 for cel in cels 
                if cel.get('eventtype') == 'HANGUP'
            )
            channel_count = len(set(
                cel.get('channame') for cel in cels 
                if cel.get('eventtype') == 'CHAN_START'
            ))
            
            if hangup_count >= channel_count and channel_count > 0:
                logger.debug(f"Call {linkedid} complete: All channels hung up")
                return True
        
        # Check CDR dispositions
        if not cdrs:
            return False
        
        all_disposed = all(
            cdr.get('disposition') and cdr.get('disposition') != 'NULL'
            for cdr in cdrs
        )
        
        if not all_disposed:
            return False
        
        # Check time since last update
        last_update = max(
            cdr.get('calldate', datetime.min) 
            for cdr in cdrs
        )
        
        if isinstance(last_update, str):
            last_update = datetime.fromisoformat(last_update)
        
        seconds_since_update = (datetime.now() - last_update).total_seconds()
        
        if seconds_since_update > 60:
            logger.debug(f"Call {linkedid} complete: No updates for {seconds_since_update}s")
            return True
        
        return False
    
    def determine_direction(self, cdrs: List[Dict], cels: List[Dict]) -> str:
        """
        Determine call direction from CDR and CEL data.
        Returns: 'i' (inbound), 'o' (outbound), or 'x' (internal)
        """
        if not cdrs:
            return 'x'
        
        primary_cdr = cdrs[0]
        
        # Check channel patterns (keep original case for better matching)
        channel = primary_cdr.get('channel', '')
        dstchannel = primary_cdr.get('dstchannel', '')
        src = primary_cdr.get('src', '')
        dst = primary_cdr.get('dst', '')
        
        # Identify trunk channels (external connections)
        # Updated patterns to match actual Asterisk trunk naming
        trunk_patterns = ['trunk', 'sbc-', 'sbc_', 'pstn', 'voip', 'gateway', 'provider', 'DAHDI/', 'IAX2/']
        channel_lower = channel.lower()
        dstchannel_lower = dstchannel.lower()
        
        is_src_trunk = any(pattern.lower() in channel_lower for pattern in trunk_patterns)
        is_dst_trunk = any(pattern.lower() in dstchannel_lower for pattern in trunk_patterns)
        
        # Identify extension channels (internal phones)
        # Extension pattern: SIP/XXX-tenant-uniqueid where XXX is numeric extension
        def is_extension_channel(chan):
            if not chan:
                return False
            chan_lower = chan.lower()
            if 'sip/' in chan_lower or 'pjsip/' in chan_lower:
                # Skip if it's a trunk
                if any(pattern.lower() in chan_lower for pattern in trunk_patterns):
                    return False
                # Check for extension pattern
                parts = chan.split('/')
                if len(parts) > 1:
                    # Extract the part after SIP/ or PJSIP/
                    channel_info = parts[1]
                    # Get extension part (before first dash or entire string if no dash)
                    ext_part = channel_info.split('-')[0] if '-' in channel_info else channel_info
                    # Check if it's a numeric extension (typically 3-4 digits)
                    # Also allow slightly longer for some systems (up to 6 digits)
                    return ext_part.isdigit() and len(ext_part) <= 6
            return False
        
        is_src_extension = is_extension_channel(channel)
        is_dst_extension = is_extension_channel(dstchannel)
        
        # DEBUG: Log detection results
        logger.debug(f"Direction detection: channel={channel[:50] if channel else 'none'}, dstchannel={dstchannel[:50] if dstchannel else 'none'}")
        logger.debug(f"  is_src_trunk={is_src_trunk}, is_dst_trunk={is_dst_trunk}")
        logger.debug(f"  is_src_extension={is_src_extension}, is_dst_extension={is_dst_extension}")
        
        # Direction logic based on trunk/extension combinations
        if is_src_trunk and is_dst_extension:
            return 'i'  # Inbound: trunk → extension
        
        if is_src_extension and is_dst_trunk:
            return 'o'  # Outbound: extension → trunk
        
        if is_src_extension and is_dst_extension:
            return 'x'  # Internal: extension → extension
        
        # Fallback: Check by phone number patterns
        src_is_external = src.isdigit() and len(src) >= 10
        dst_is_external = dst.isdigit() and len(dst) >= 10
        
        if src_is_external and not dst_is_external:
            return 'i'  # External number calling internal
        
        if not src_is_external and dst_is_external:
            return 'o'  # Internal calling external number
        
        # Context-based detection as last resort
        context = primary_cdr.get('dcontext', '') or primary_cdr.get('context', '')
        context_lower = context.lower()
        
        if any(pattern in context_lower for pattern in ['from-trunk', 'from-pstn', 'from-external', 'from-did']):
            return 'i'
        
        if any(pattern in context_lower for pattern in ['from-internal', 'from-inside']):
            if dst_is_external:
                return 'o'
        
        return 'x'  # Default to internal
    
    def extract_numbers_and_extensions(self, cdrs: List[Dict], cels: List[Dict], direction: str) -> Dict:
        """Extract and normalize phone numbers and extensions"""
        result = {
            'src_number': None,
            'src_extension': None,
            'dst_number': None,
            'dst_extension': None
        }
        
        if not cdrs:
            return result
        
        # For ring groups, use the primary CDR but check all for answered
        primary_cdr = cdrs[0]
        answered_cdr = None
        for cdr in cdrs:
            if cdr.get('disposition') == 'ANSWERED':
                answered_cdr = cdr
                break
        
        # Use answered CDR if available, otherwise primary
        working_cdr = answered_cdr if answered_cdr else primary_cdr
        
        # Extract from channels
        channel = working_cdr.get('channel', '')
        dstchannel = working_cdr.get('dstchannel', '')
        src = working_cdr.get('src', '')
        dst = working_cdr.get('dst', '')
        
        # Extract extensions from channel names (format: SIP/ext-tenant-id or PJSIP/ext-tenant-id)
        if 'SIP/' in channel or 'PJSIP/' in channel:
            parts = channel.split('/')
            if len(parts) > 1:
                channel_info = parts[1]
                ext_part = channel_info.split('-')[0] if '-' in channel_info else channel_info
                if ext_part.isdigit() and len(ext_part) <= 6:
                    result['src_extension'] = ext_part
        
        if 'SIP/' in dstchannel or 'PJSIP/' in dstchannel:
            parts = dstchannel.split('/')
            if len(parts) > 1:
                channel_info = parts[1]
                ext_part = channel_info.split('-')[0] if '-' in channel_info else channel_info
                if ext_part.isdigit() and len(ext_part) <= 6:
                    result['dst_extension'] = ext_part
        
        # Direction-specific extraction
        if direction == 'i':  # Inbound
            # Source is external caller number
            # Clean the src first to check if it's a phone number
            src_cleaned = ''.join(c for c in src if c.isdigit()) if src else ''
            if src_cleaned:
                # Keep original format for external numbers
                if len(src_cleaned) >= 10:
                    result['src_number'] = self._normalize_number(src)
                elif len(src_cleaned) < 10 and not result['src_extension']:
                    # Might be an extension calling in
                    result['src_extension'] = src_cleaned
            elif not src and cels:
                # src is empty - try to get caller number from CEL events
                for cel in cels:
                    cid_num = cel.get('cid_num', '')
                    if cid_num and cid_num.isdigit() and len(cid_num) >= 10:
                        result['src_number'] = self._normalize_number(cid_num)
                        break
            
            # Destination: For inbound, we need to find the DID that was dialed
            # First try to extract DID from context
            dcontext = working_cdr.get('dcontext', '')
            did = self._extract_did_from_context(dcontext)
            
            if did:
                result['dst_number'] = did
            elif dst == 's' or dst == 'i' or dst == 't' or dst == 'h':
                # Special Asterisk destinations - DID not found in context
                # Try to get it from CEL events (CHAN_START exten field)
                if cels:
                    for cel in cels:
                        if cel.get('eventtype') == 'CHAN_START' or cel.get('event') == 'CHAN_START':
                            exten = cel.get('exten', '')
                            if exten and exten.isdigit() and len(exten) >= 10:
                                result['dst_number'] = self._normalize_number(exten)
                                break
            elif dst:
                dst_cleaned = ''.join(c for c in dst if c.isdigit())
                if dst_cleaned and len(dst_cleaned) >= 10:
                    # dst is the actual DID
                    result['dst_number'] = self._normalize_number(dst)
                elif dst_cleaned and len(dst_cleaned) < 10:
                    # dst is an extension - but still try to find the DID from CEL
                    if not result['dst_extension']:
                        result['dst_extension'] = dst_cleaned
                    
                    # For inbound calls, even when dst is an extension, 
                    # we should try to find the DID from CEL events
                    if cels and not result['dst_number']:
                        for cel in cels:
                            if cel.get('eventtype') == 'CHAN_START' or cel.get('event') == 'CHAN_START':
                                exten = cel.get('exten', '')
                                # Check if it's a DID (10+ digits)
                                if exten and exten.isdigit() and len(exten) >= 10:
                                    result['dst_number'] = self._normalize_number(exten)
                                    break
        
        elif direction == 'o':  # Outbound
            # Source: Extension making the call (already extracted from channel)
            # Also check if there's a caller ID number set
            # Clean the src first to check if it's a phone number
            src_cleaned = ''.join(c for c in src if c.isdigit()) if src else ''
            if src_cleaned and len(src_cleaned) >= 10:
                result['src_number'] = self._normalize_number(src)
            elif src_cleaned and len(src_cleaned) < 10 and not result['src_extension']:
                result['src_extension'] = src_cleaned
            
            # Destination is external number
            # Clean the dst first to check if it's a phone number
            dst_cleaned = ''.join(c for c in dst if c.isdigit()) if dst else ''
            if dst_cleaned and len(dst_cleaned) >= 10:
                result['dst_number'] = self._normalize_number(dst)
        
        elif direction == 'x':  # Internal
            # Both are extensions
            if not result['src_extension'] and src:
                if src.isdigit() and len(src) <= 4:
                    result['src_extension'] = src
            
            if not result['dst_extension'] and dst:
                # Handle special extensions like *98 (voicemail)
                if dst.startswith('*') or (dst.isdigit() and len(dst) <= 4):
                    result['dst_extension'] = dst
        
        return result
    
    def _normalize_number(self, number: str) -> Optional[str]:
        """Normalize phone number to E.164 format"""
        if not number:
            return None
        
        # Remove common prefixes
        cleaned = number
        for prefix in ['*67', '*82', '9']:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
        
        # Remove non-digits
        cleaned = ''.join(c for c in cleaned if c.isdigit())
        
        # Add country code if missing (assuming US/CA)
        if len(cleaned) == 10:
            cleaned = '1' + cleaned
        
        return cleaned if len(cleaned) >= 10 else None
    
    def _extract_did_from_context(self, context: str) -> Optional[str]:
        """Extract DID from dcontext patterns"""
        if not context:
            return None
        
        # Pattern 1: XXX-NNNNNNNNNN-XXX-NAME-tenant (e.g., 338-6478752300-338-CFLAW-gconnect)
        # The DID is the 10-11 digit number in the middle
        parts = context.split('-')
        for part in parts:
            # Look for 10 or 11 digit numbers
            if part.isdigit() and 10 <= len(part) <= 11:
                return self._normalize_number(part)
        
        # Pattern 2: from-did-direct,NNNNNNNNNN
        if ',' in context:
            parts = context.split(',')
            for part in parts:
                part = part.strip()
                if part.isdigit() and len(part) >= 10:
                    return self._normalize_number(part)
        
        # Pattern 3: Check CEL events for the actual dialed number if context doesn't have it
        # This will be handled by the caller using CEL data
        
        return None
    
    def _extract_tenant_from_channel(self, channel: str) -> Optional[str]:
        """Extract tenant from channel pattern - aggressively scan for tenant names"""
        if not channel:
            return None
        
        # Pattern: SIP/ext-tenant-uniqueid or PJSIP/ext-tenant-uniqueid
        if 'SIP/' in channel or 'PJSIP/' in channel:
            parts = channel.split('/')
            if len(parts) > 1:
                channel_info = parts[1]
                # Split by dash to get components
                components = channel_info.split('-')
                
                # Try different positions where tenant might be
                # Position 1 (second component): SIP/100-tenant-xxx
                if len(components) >= 2:
                    candidate = components[1]
                    if self._is_valid_tenant(candidate):
                        return candidate.lower()
                
                # Position 2 (last non-hex component)
                for component in reversed(components):
                    if self._is_valid_tenant(component):
                        return component.lower()
        
        return None
    
    def _is_valid_tenant(self, candidate: str) -> bool:
        """Check if a string is a valid tenant name"""
        if not candidate or len(candidate) < 2:
            return False
        
        candidate_lower = candidate.lower()
        
        # Skip if numeric
        if candidate_lower.isdigit():
            return False
        
        # Skip if it's a hex ID (all hex chars)
        if all(c in '0123456789abcdef' for c in candidate_lower):
            return False
        
        # Skip known trunk names from configuration
        if candidate_lower in self.known_trunks:
            return False
        
        # Skip common non-tenant patterns
        skip_patterns = ['sip', 'pjsip', 'iax', 'dahdi', 'local', 'from', 'to', 
                        'did', 'direct', 'trunk', 'peer', 'sbc', 'ca1', 'ca2', 
                        'us1', 'us2', 'closed', 'open', 'internal', 'external']
        if candidate_lower in skip_patterns:
            return False
        
        # Accept if reasonable length and contains letters
        if len(candidate) <= 20 and any(c.isalpha() for c in candidate):
            return True
        
        return False
    
    def _extract_tenant_from_context(self, context: str) -> Optional[str]:
        """Extract tenant from context pattern - aggressively scan for tenant names"""
        if not context:
            return None
        
        # Split by common delimiters
        for delimiter in ['-', '_', ',', '/', '@']:
            if delimiter in context:
                parts = context.split(delimiter)
                
                # Check each part from right to left (tenant usually at end)
                for part in reversed(parts):
                    if self._is_valid_tenant(part):
                        return part.lower()
                
                # Special handling for patterns like XXX-NNNNNNNNNN-XXX-NAME-tenant
                if len(parts) >= 5 and delimiter == '-':
                    # Last part is often the tenant
                    if self._is_valid_tenant(parts[-1]):
                        return parts[-1].lower()
                    # Sometimes it's the 4th part (NAME position)
                    if len(parts) >= 4 and self._is_valid_tenant(parts[3]):
                        return parts[3].lower()
        
        # If no delimiter, check the whole context
        if self._is_valid_tenant(context):
            return context.lower()
        
        return None
    
    def extract_names_from_cel(self, cels: List[Dict], numbers: Dict) -> Dict:
        """Extract caller names from CEL events"""
        result = {
            'src_name': None,
            'dst_name': None,
            'src_extension_name': None,
            'dst_extension_name': None
        }
        
        if not cels:
            return result
        
        # Look for names in CEL events
        for cel in cels:
            cid_name = cel.get('cid_name', '').strip()
            cid_num = cel.get('cid_num', '').strip()
            cid_dnid = cel.get('cid_dnid', '').strip()
            channame = cel.get('channame', '').strip()
            
            # Extract extension name from channel (format: "First Last" <ext>)
            if cid_name and cid_name != '':
                # For inbound calls, cid_name is the caller's name (source)
                # cid_num is the caller's number, cid_dnid is the dialed number (destination)
                
                # Clean up tenant-prefixed caller ID names
                # Some tenants prefix the caller ID with their identifier
                cleaned_name = cid_name
                
                # Check for common patterns of tenant prefixes
                # Pattern 1: Long names (>30 chars) with a dash separator near the end
                if len(cid_name) > 30 and '-' in cid_name:
                    # Find the last dash that's not part of a phone number
                    parts = cid_name.rsplit('-', 1)
                    if len(parts) == 2:
                        prefix, suffix = parts
                        # If suffix looks like a name or phone number (not empty), use it
                        if suffix and not suffix.startswith(prefix[:3]):  # Avoid recursive prefixes
                            # If suffix is just a phone number starting with +, skip the name
                            if suffix.strip().startswith('+') and suffix.strip()[1:].isdigit():
                                cleaned_name = ''  # No actual name, just phone number
                            else:
                                cleaned_name = suffix.strip()
                
                # Pattern 2: Check for specific known prefixes (can be expanded)
                # Format: "NNN-NN-Law-Company Name-ACTUAL NAME"
                elif '-' in cid_name:
                    # Check if it matches pattern like "428-24-Law-"
                    import re
                    pattern = r'^\d{3}-\d{2}-[A-Za-z]+-.*?-(.+)$'
                    match = re.match(pattern, cid_name)
                    if match:
                        cleaned_name = match.group(1).strip()
                        # If the extracted part is just a phone number, clear it
                        if cleaned_name.startswith('+') and cleaned_name[1:].replace('-', '').isdigit():
                            cleaned_name = ''
                
                # Check if cid_num matches source - this means cid_name is the source name
                if cid_num and (cid_num == numbers.get('src_number') or 
                               self._normalize_number(cid_num) == numbers.get('src_number')):
                    if not result['src_name'] and cleaned_name:
                        result['src_name'] = cleaned_name
                
                # Note: For inbound calls, we typically don't have a "name" for the destination DID
                # The cid_name in CEL is always the caller's name, not the destination's name
                
                # Check for extension names based on channel
                if 'SIP/' in channame:
                    ext_match = channame.split('SIP/')[1].split('-')[0] if '-' in channame else None
                    if ext_match and ext_match.isdigit():
                        if ext_match == numbers.get('src_extension'):
                            if not result['src_extension_name']:
                                result['src_extension_name'] = cid_name
                        elif ext_match == numbers.get('dst_extension'):
                            if not result['dst_extension_name']:
                                result['dst_extension_name'] = cid_name
        
        return result
    
    def build_call_threads(self, cdrs: List[Dict], cels: List[Dict]) -> List[Dict]:
        """Build comprehensive call threads from CDR and CEL data"""
        threads = []
        
        # Add CDR events
        for cdr in cdrs:
            thread = {
                'time': cdr.get('calldate', '').isoformat() if hasattr(cdr.get('calldate', ''), 'isoformat') else str(cdr.get('calldate', '')),
                'event': 'CDR',
                'src': cdr.get('src', ''),
                'dst': cdr.get('dst', ''),
                'duration': cdr.get('duration', 0),
                'billsec': cdr.get('billsec', 0),
                'disposition': cdr.get('disposition', ''),
                'channel': cdr.get('channel', ''),
                'dstchannel': cdr.get('dstchannel', ''),
                'uniqueid': cdr.get('uniqueid', '')
            }
            threads.append(thread)
        
        # Add significant CEL events
        significant_events = [
            'CHAN_START', 'ANSWER', 'BRIDGE_ENTER', 'BRIDGE_EXIT',
            'BLINDTRANSFER', 'ATTENDEDTRANSFER', 'HANGUP', 'LINKEDID_END'
        ]
        
        for cel in cels:
            if cel.get('eventtype') in significant_events:
                thread = {
                    'time': cel.get('eventtime', '').isoformat() if hasattr(cel.get('eventtime', ''), 'isoformat') else str(cel.get('eventtime', '')),
                    'event': cel.get('eventtype', ''),
                    'channel': cel.get('channame', ''),
                    'exten': cel.get('exten', ''),
                    'context': cel.get('context', ''),
                    'uniqueid': cel.get('uniqueid', '')
                }
                
                # Add peer for bridge events
                if 'BRIDGE' in cel.get('eventtype', ''):
                    thread['peer'] = cel.get('peer', '')
                
                # Add transfer details
                if 'TRANSFER' in cel.get('eventtype', ''):
                    thread['transferee'] = cel.get('extra', '')
                
                threads.append(thread)
        
        # Sort by time
        threads.sort(key=lambda x: x['time'])
        
        return threads
    
    def format_call_data(self, linkedid: str, cdrs: List[Dict], cels: List[Dict], 
                        is_complete: bool, config: Dict) -> CallData:
        """Format call data according to call_logs table structure"""
        
        # DEBUG: Log raw CDR data for troubleshooting
        if cdrs:
            primary = cdrs[0]
            logger.debug(f"DEBUG CDR: linkedid={linkedid}")
            logger.debug(f"  channel={primary.get('channel')}, dstchannel={primary.get('dstchannel')}")
            logger.debug(f"  src={primary.get('src')}, dst={primary.get('dst')}")
            logger.debug(f"  dcontext={primary.get('dcontext')}, context={primary.get('context')}")
            logger.debug(f"  accountcode={primary.get('accountcode')}")
        
        # Determine direction
        direction = self.determine_direction(cdrs, cels)
        logger.debug(f"DEBUG Direction detected: {direction}")
        
        # Extract numbers and extensions
        numbers = self.extract_numbers_and_extensions(cdrs, cels, direction)
        logger.debug(f"DEBUG Numbers extracted: {numbers}")
        
        # Extract names from CEL data
        names = self.extract_names_from_cel(cels, numbers)
        logger.debug(f"DEBUG Names extracted: {names}")
        
        # Combine numbers and names
        call_details = {**numbers, **names}
        
        # Build call threads
        threads = self.build_call_threads(cdrs, cels)
        
        # Get primary CDR for basic info
        primary_cdr = cdrs[0] if cdrs else {}
        
        # Calculate total duration
        duration = max([cdr.get('duration', 0) for cdr in cdrs], default=0)
        
        # Get disposition (prioritize ANSWERED)
        dispositions = [cdr.get('disposition', '') for cdr in cdrs]
        disposition = 'ANSWERED' if 'ANSWERED' in dispositions else (dispositions[0] if dispositions else 'NO ANSWER')
        
        # Get call time
        call_time = primary_cdr.get('calldate', datetime.now())
        if isinstance(call_time, str):
            call_time = datetime.fromisoformat(call_time)
        
        # Extract tenant from various sources - AGGRESSIVE SCANNING
        tenant = None
        
        # 1. Try CDR fields
        for cdr in cdrs:
            # Check all relevant CDR fields - prioritize context fields over channels
            fields_to_check = [
                cdr.get('dcontext', ''),      # Check context first (most reliable for tenant)
                cdr.get('context', ''),
                cdr.get('accountcode', ''),
                cdr.get('userfield', ''),
                cdr.get('peeraccount', ''),
                cdr.get('lastdata', ''),
                cdr.get('channel', ''),       # Check channels last (less reliable)
                cdr.get('dstchannel', '')
            ]
            
            for field in fields_to_check:
                if field and not tenant:
                    # Try channel extraction first
                    if 'SIP/' in field or 'PJSIP/' in field:
                        tenant = self._extract_tenant_from_channel(field)
                    # Otherwise try context extraction
                    if not tenant:
                        tenant = self._extract_tenant_from_context(field)
                
                if tenant:
                    logger.debug(f"Tenant '{tenant}' found in CDR field: {field[:50]}")
                    break
            
            if tenant:
                break
        
        # 2. Try CEL events if no tenant found yet
        if not tenant and cels:
            for cel in cels:
                fields_to_check = [
                    cel.get('context', ''),
                    cel.get('channame', ''),
                    cel.get('appdata', ''),
                    cel.get('peer', ''),
                    cel.get('eventextra', '')
                ]
                
                for field in fields_to_check:
                    if field and not tenant:
                        # Try channel extraction for channel-like fields
                        if 'SIP/' in field or 'PJSIP/' in field:
                            tenant = self._extract_tenant_from_channel(field)
                        # Otherwise try context extraction
                        if not tenant:
                            tenant = self._extract_tenant_from_context(field)
                    
                    if tenant:
                        logger.debug(f"Tenant '{tenant}' found in CEL field: {field[:50]}")
                        break
                
                if tenant:
                    break
        
        # 3. Final fallback to config
        if not tenant:
            tenant = config.get('TENANT', '')
        
        logger.debug(f"DEBUG Tenant extracted: {tenant}")
        
        # Create formatted call data
        call_data = CallData(
            connector_version=config.get('CONNECTOR_VERSION', '2.2.0'),
            customer_id=config.get('CUSTOMER_ID', 0),
            tenant=tenant,
            hostname=config.get('HOSTNAME', os.uname().nodename),
            linkedid=linkedid,
            is_complete=is_complete,
            call_time=call_time.isoformat(),
            duration_seconds=duration,
            call_threads=threads,
            call_threads_count=len(threads),  # Changed to match DB schema
            direction=direction,
            disposition=disposition,
            **call_details
        )
        
        # Add raw data if debugging enabled
        if config.get('INCLUDE_RAW_DATA', False):
            call_data.raw_cdrs = cdrs
            call_data.raw_cels = cels
        
        return call_data
    
    def track_processed_call(self, linkedid: str, is_complete: bool, 
                            cdr_count: int, cel_count: int, shipped: bool = False):
        """Track processed call in local SQLite database"""
        with sqlite3.connect(self.tracker_db) as conn:
            now = datetime.now().isoformat()
            
            # Upsert processed call record
            conn.execute("""
                INSERT INTO processed_calls 
                (linkedid, first_seen, last_updated, is_complete, 
                 last_cdr_count, last_cel_count, shipped_at, ship_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(linkedid) DO UPDATE SET
                    last_updated = ?,
                    is_complete = ?,
                    last_cdr_count = ?,
                    last_cel_count = ?,
                    shipped_at = CASE WHEN ? THEN ? ELSE shipped_at END,
                    ship_count = ship_count + ?
            """, (
                linkedid, now, now, is_complete, cdr_count, cel_count,
                now if shipped else None, 1 if shipped else 0,
                now, is_complete, cdr_count, cel_count,
                shipped, now, 1 if shipped else 0
            ))
            conn.commit()
    
    def get_unprocessed_calls(self, limit: int = 100) -> List[str]:
        """Get calls that haven't been shipped or need updates"""
        with sqlite3.connect(self.tracker_db) as conn:
            cursor = conn.cursor()
            
            # Get calls that need shipping
            cursor.execute("""
                SELECT linkedid 
                FROM processed_calls
                WHERE (shipped_at IS NULL OR is_complete = 0)
                AND error_count < 5
                ORDER BY last_updated DESC
                LIMIT ?
            """, (limit,))
            
            return [row[0] for row in cursor.fetchall()]
    
    def should_ship_call(self, linkedid: str, is_complete: bool, 
                         cdr_count: int, cel_count: int) -> Tuple[bool, str]:
        """
        Determine if call should be shipped and what phase.
        Returns: (should_ship, phase)
        """
        with sqlite3.connect(self.tracker_db) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT is_complete, last_cdr_count, last_cel_count, 
                       ship_count, last_updated
                FROM processed_calls
                WHERE linkedid = ?
            """, (linkedid,))
            
            row = cursor.fetchone()
            
            # In COMPLETE mode, only ship when call is complete (or periodic update for long calls)
            if self.shipping_mode == 'complete':
                # New call that's already complete - ship it
                if not row and is_complete:
                    return True, 'complete'
                
                # New call that's not complete - don't ship yet
                if not row and not is_complete:
                    return False, 'none'
                
                # Existing call
                if row:
                    prev_complete, prev_cdr_count, prev_cel_count, ship_count, last_updated = row
                    
                    # Completed now but wasn't before - ship complete
                    if is_complete and not prev_complete:
                        return True, 'complete'
                    
                    # Already complete and shipped - don't ship again
                    if is_complete and prev_complete and ship_count > 0:
                        return False, 'none'
                    
                    # Long call periodic update (if enabled)
                    if not is_complete and self.long_call_update_interval > 0:
                        last_update_time = datetime.fromisoformat(last_updated)
                        if (datetime.now() - last_update_time).total_seconds() > self.long_call_update_interval:
                            return True, 'update'
                
                return False, 'none'
            
            # PROGRESSIVE mode - original behavior
            else:
                # New call - ship initial
                if not row:
                    return True, 'initial'
                
                prev_complete, prev_cdr_count, prev_cel_count, ship_count, last_updated = row
                
                # Completed now but wasn't before - ship complete
                if is_complete and not prev_complete:
                    return True, 'complete'
                
                # New events added - ship update
                if cdr_count > prev_cdr_count or cel_count > prev_cel_count:
                    return True, 'update'
                
                # Already complete and shipped
                if is_complete and prev_complete and ship_count > 0:
                    return False, 'none'
                
                # Periodic update for long calls (every 60 seconds)
                if not is_complete:
                    last_update_time = datetime.fromisoformat(last_updated)
                    if (datetime.now() - last_update_time).total_seconds() > 60:
                        return True, 'update'
                
                return False, 'none'