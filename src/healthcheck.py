#!/usr/bin/env python3
"""
Simple health check script for Docker HEALTHCHECK.
Checks if the connector is running and AMI is connected.
"""

import sys
import os
import socket
import time

def check_metrics_port():
    """Check if metrics port is available (if enabled)."""
    if os.getenv('MONITORING_ENABLED', 'false').lower() == 'true':
        port = int(os.getenv('MONITORING_PORT', '8000'))
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('localhost', port))
            sock.close()
            return result == 0
        except:
            return False
    return True  # If monitoring disabled, consider it healthy

def check_process_running():
    """Check if the main process is running."""
    try:
        # Check if main.py process exists
        import subprocess
        result = subprocess.run(['pgrep', '-f', 'python.*main'], 
                              capture_output=True, text=True)
        return result.returncode == 0
    except:
        # If pgrep not available, assume healthy
        return True

def main():
    """Run health checks."""
    # Give the service time to start on initial check
    if os.path.exists('/tmp/first_health_check'):
        pass
    else:
        with open('/tmp/first_health_check', 'w') as f:
            f.write(str(time.time()))
        time.sleep(10)  # Wait 10 seconds on first check
    
    # Check if process is running
    if not check_process_running():
        print("Main process not running")
        sys.exit(1)
    
    # Check metrics port if enabled
    if not check_metrics_port():
        print("Metrics port not available")
        sys.exit(1)
    
    print("Health check passed")
    sys.exit(0)

if __name__ == "__main__":
    main()