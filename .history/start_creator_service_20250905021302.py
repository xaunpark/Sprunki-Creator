#!/usr/bin/env python3
"""
Sprunki Creator Service Launcher
Starts Creator as API-driven service instead of GUI
Supports --quick-test flag for rapid pipeline validation.
"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from creator_api_client import main

if __name__ == "__main__":
    # Kiểm tra xem cờ --quick-test có tồn tại trong các tham số dòng lệnh không
    is_quick_test_mode = "--quick-test" in sys.argv
    
    print("🤖 Launching Sprunki Creator Service...")
    print("🔗 API-driven video production service")
    print("📡 Will connect to Tracker at http://localhost:5000")
    
    if is_quick_test_mode:
        print("\n" + "⚡" * 60)
        print("⚡ QUICK TEST MODE ACTIVATED ⚡")
        print("⚡ All jobs in the queue will run in a shortened format.")
        print("⚡ (2 icons + 1 remix with reduced listen times)")
        print("⚡" * 60 + "\n")
    
    print("="*60)
    
    # Truyền cờ is_quick_test_mode vào hàm main
    main(quick_test_mode=is_quick_test_mode)