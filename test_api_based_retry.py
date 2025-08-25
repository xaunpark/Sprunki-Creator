"""
Test API-based Smart Retry Manager - PHASE 2
"""

from smart_retry_manager import SmartRetryManager
import time

def test_local_error_classification():
    """Test local error classification without database"""
    print("🧪 Testing Local Error Classification...")
    
    # Create retry manager without API client
    retry_manager = SmartRetryManager()
    
    test_errors = [
        "Browser connection timeout after 30 seconds",
        "OBS WebSocket connection failed", 
        "Element not found: play button",
        "Unknown critical system failure"
    ]
    
    for i, error in enumerate(test_errors, 1):
        classification = retry_manager.classify_error_local(error)
        print(f"✅ Test {i}: {error[:40]}...")
        print(f"   Category: {classification['category']}")
        print(f"   Severity: {classification['severity']}")
        print(f"   Strategy: {classification['retry_strategy']}")
        print(f"   Max Retries: {classification['max_retries']}")
        print()

def test_retry_decision():
    """Test retry decision logic"""
    print("🔄 Testing Retry Decision Logic...")
    
    retry_manager = SmartRetryManager()
    
    # Test different scenarios
    test_cases = [
        {
            'queue_id': 1,
            'error': 'Browser connection timeout',
            'context': {'attempt': 1, 'operation': 'browser_setup'}
        },
        {
            'queue_id': 2, 
            'error': 'OBS recording failed to start',
            'context': {'attempt': 3, 'operation': 'obs_setup'}  # High attempt
        },
        {
            'queue_id': 3,
            'error': 'Disk space insufficient',
            'context': {'attempt': 1, 'operation': 'file_write'}
        }
    ]
    
    for i, case in enumerate(test_cases, 1):
        decision = retry_manager.should_retry(
            case['queue_id'], case['error'], case['context']
        )
        
        print(f"📋 Case {i}: {case['error']}")
        print(f"   Should retry: {decision['should_retry']}")
        print(f"   Reason: {decision['reason']}")
        if decision['should_retry']:
            print(f"   Strategy: {decision['retry_strategy']}")
            print(f"   Delay: {decision.get('retry_delay', 0)}s")
            print(f"   Recovery: {decision.get('recovery_action', 'None')}")
        print()

if __name__ == "__main__":
    print("🧪 Testing API-based Smart Retry Manager")
    print("=" * 50)
    
    test_local_error_classification()
    test_retry_decision()
    
    print("🏁 API-based Retry Manager Test Complete!")