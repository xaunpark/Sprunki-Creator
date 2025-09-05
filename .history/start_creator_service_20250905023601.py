#!/usr/bin/env python3
"""
Sprunki Creator Service Launcher
Starts Creator as API-driven service instead of GUI
"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from creator_api_client import main

if __name__ == "__main__":
    print("🤖 Launching Sprunki Creator Service...")
    print("🔗 API-driven video production service")
    print("📡 Will connect to Tracker at http://localhost:5000")
    print("="*60)
    
    main()