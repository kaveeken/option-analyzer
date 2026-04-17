#!/usr/bin/env python3
"""
Monitor IServer authentication status by polling every 5 minutes.
"""
import time
from datetime import datetime

import requests

# Configuration
# URL = "https://localhost:5000/v1/api/iserver/auth/status"
# INTERVAL_SECONDS = 300  # 5 minutes
URL = "https://localhost:5000/v1/api/tickle"
INTERVAL_SECONDS = 60  # 1 minute

def check_auth_status():
    """Query the auth status endpoint and print the result."""
    try:
        # Disable SSL verification for localhost
        response = requests.get(URL, verify=False, timeout=10)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"[{timestamp}] Status: {response.status_code}")
        print(f"Response: {response.text}")
        print("-" * 60)

    except requests.exceptions.RequestException as e:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Error: {e}")
        print("-" * 60)

if __name__ == "__main__":
    print(f"Starting auth status monitor (querying every {INTERVAL_SECONDS} seconds)...")
    print(f"URL: {URL}")
    print("=" * 60)

    # Disable SSL warnings for localhost
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    while True:
        check_auth_status()
        time.sleep(INTERVAL_SECONDS)
