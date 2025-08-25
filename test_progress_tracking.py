"""
Test script for Phase 1: Enhanced Progress Tracking
Run this to verify all components work correctly
"""

import requests
import json
import time
from datetime import datetime

TRACKER_URL = "http://localhost:5000"

def test_progress_apis():
    """Test all progress tracking APIs"""
    print("🧪 Testing Phase 1: Enhanced Progress Tracking")
    print("=" * 50)
    
    # Test 1: Update progress
    print("📊 Test 1: Update Job Progress...")
    test_queue_id = 1
    
    progress_data = {
        'stage': 'test_stage',
        'substep': 'test_substep_1',
        'progress_percent': 25.0,
        'status': 'active',
        'metadata': {'test': True, 'timestamp': datetime.now().isoformat()}
    }
    
    try:
        response = requests.post(
            f"{TRACKER_URL}/api/queue/progress/{test_queue_id}",
            json=progress_data,
            timeout=10
        )
        
        if response.status_code == 200:
            print("✅ Progress update successful")
            print(f"   Response: {response.json()}")
        else:
            print(f"❌ Progress update failed: {response.status_code}")
            print(f"   Response: {response.text}")
            
    except Exception as e:
        print(f"❌ Progress update error: {e}")
    
    # Test 2: Get progress
    print("\n📈 Test 2: Get Job Progress...")
    try:
        response = requests.get(
            f"{TRACKER_URL}/api/queue/progress/{test_queue_id}",
            timeout=10
        )
        
        if response.status_code == 200:
            print("✅ Get progress successful")
            data = response.json()
            progress = data.get('progress', {})
            print(f"   Current Stage: {progress.get('current_stage')}")
            print(f"   Overall Progress: {progress.get('overall_progress')}%")
            print(f"   Total Steps: {progress.get('summary', {}).get('total_steps')}")
        else:
            print(f"❌ Get progress failed: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Get progress error: {e}")
    
    # Test 3: Bulk progress
    print("\n📋 Test 3: Bulk Progress...")
    try:
        response = requests.get(
            f"{TRACKER_URL}/api/queue/progress/bulk",
            timeout=10
        )
        
        if response.status_code == 200:
            print("✅ Bulk progress successful")
            data = response.json()
            print(f"   Active jobs: {data.get('active_jobs_count')}")
        else:
            print(f"❌ Bulk progress failed: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Bulk progress error: {e}")
    
    # Test 4: Progress stats
    print("\n📊 Test 4: Progress Statistics...")
    try:
        response = requests.get(
            f"{TRACKER_URL}/api/queue/progress/stats",
            timeout=10
        )
        
        if response.status_code == 200:
            print("✅ Progress stats successful")
            data = response.json()
            stats = data.get('stats', {})
            print(f"   Jobs with progress: {stats.get('jobs_with_progress')}")
            print(f"   Total records: {stats.get('total_records')}")
            print(f"   Average progress: {stats.get('avg_progress')}%")
        else:
            print(f"❌ Progress stats failed: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Progress stats error: {e}")
    
    print("\n🏁 Phase 1 Testing Complete!")

if __name__ == "__main__":
    print("Starting in 3 seconds... (Make sure Tracker is running)")
    time.sleep(3)
    test_progress_apis()