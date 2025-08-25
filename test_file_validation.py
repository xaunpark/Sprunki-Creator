"""
Test File Validation System - PHASE 3
"""

import os
import tempfile
from file_validator import FileQualityValidator

def create_test_video_file(filename: str, size_mb: float = 5.0) -> str:
    """Create a dummy video file for testing"""
    temp_dir = tempfile.gettempdir()
    test_file = os.path.join(temp_dir, filename)
    
    # Create a dummy file with specified size
    with open(test_file, 'wb') as f:
        f.write(b'0' * int(size_mb * 1024 * 1024))
    
    return test_file

def test_file_validator():
    """Test file validation functionality"""
    print("🧪 Testing File Quality Validator")
    print("=" * 50)
    
    validator = FileQualityValidator()
    
    # Test 1: Basic validation rules
    print("📋 Test 1: Validation Rules")
    rules = validator._get_validation_rules()
    print(f"   Video rules: {len(rules['video'])} criteria")
    print(f"   Min size: {rules['video']['min_size_mb']} MB")
    print(f"   Max size: {rules['video']['max_size_mb']} MB")
    
    # Test 2: Quality thresholds
    print("\n🎯 Test 2: Quality Thresholds")
    thresholds = validator._get_quality_thresholds()
    for grade, info in thresholds.items():
        print(f"   {grade.title()}: {info['min_score']}+ points")
    
    # Test 3: Create and validate test files
    print("\n📁 Test 3: File Validation")
    
    test_files = [
        ('good_video.mp4', 10.0),
        ('small_video.mp4', 0.5),   # Too small
        ('large_video.mp4', 600.0), # Too large
        ('wrong_ext.avi', 5.0)
    ]
    
    created_files = []
    
    for filename, size_mb in test_files:
        print(f"\n   📄 Creating test file: {filename} ({size_mb} MB)")
        test_file = create_test_video_file(filename, size_mb)
        created_files.append(test_file)
        
        # Validate file
        result = validator.validate_file(test_file, 'video')
        
        print(f"   Status: {'✅ VALID' if result['is_valid'] else '❌ INVALID'}")
        print(f"   Quality Score: {result['quality_score']}/100")
        print(f"   Quality Grade: {result['quality_grade'].title()}")
        print(f"   Errors: {len(result['validation_errors'])}")
        print(f"   Warnings: {len(result['validation_warnings'])}")
        
        if result['validation_errors']:
            for error in result['validation_errors'][:2]:
                print(f"     - {error}")
    
    # Test 4: Batch validation
    print(f"\n📦 Test 4: Batch Validation ({len(created_files)} files)")
    
    batch_result = validator.validate_batch(created_files, 'video')
    summary = batch_result['summary']
    
    print(f"   Total Files: {summary['total_files']}")
    print(f"   Valid Files: {summary['valid_files']}")
    print(f"   Success Rate: {summary.get('average_quality_score', 0):.1f}%")
    print(f"   Validation Time: {summary['validation_time']}s")
    
    # Test 5: Generate report
    print(f"\n📄 Test 5: Validation Report")
    
    report_path = os.path.join(tempfile.gettempdir(), 'validation_report.md')
    report = validator.generate_validation_report(batch_result, report_path)
    
    print(f"   Report generated: {report_path}")
    print(f"   Report size: {len(report)} characters")
    
    # Cleanup
    print(f"\n🧹 Cleaning up test files...")
    for test_file in created_files:
        try:
            os.remove(test_file)
            print(f"   Removed: {os.path.basename(test_file)}")
        except:
            pass
    
    try:
        os.remove(report_path)
        print(f"   Removed: validation_report.md")
    except:
        pass
    
    print("\n✅ File validation testing completed!")

if __name__ == "__main__":
    test_file_validator()