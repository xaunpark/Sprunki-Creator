# FILE: file_validator.py

"""
File Validation & Quality Control System - SIMPLIFIED VERSION
Validates output files based on basic properties ONLY (existence, size).
REMOVED: FFmpeg dependency, technical analysis, quality scoring, and rule matching.
"""

import os
import time
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from pathlib import Path

class FileQualityValidator:
    """
    [SIMPLIFIED] Validates video files based on basic properties.
    Checks only for existence, readability, and a minimum file size.
    """
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        # [SIMPLIFIED] Đặt ra một ngưỡng kích thước tối thiểu rất cơ bản
        self.min_size_mb_threshold = 0.1  # Tối thiểu 0.1MB để coi là file hợp lệ

    def validate_file(self, file_path: str, expected_type: str = 'video') -> dict:
        """
        [SIMPLIFIED] Basic file validation based on Level 1 checks.
        """
        validation_start = time.time()
        
        result = {
            'file_path': file_path,
            'expected_type': expected_type,
            'validation_time': None,
            'exists': False,
            'readable': False,
            'size_bytes': 0,
            'size_mb': 0.0,
            'validation_errors': [],
            'is_valid': False
        }
        
        try:
            # --- CẤP ĐỘ 1: KIỂM TRA CƠ BẢN (ĐƯỢC GIỮ LẠI) ---
            path_obj = Path(file_path)
            
            result['exists'] = path_obj.exists()
            if not result['exists']:
                result['validation_errors'].append('File does not exist')
                result['is_valid'] = False
                return self._finalize_result(result, validation_start)
            
            result['readable'] = path_obj.is_file() and os.access(file_path, os.R_OK)
            if not result['readable']:
                result['validation_errors'].append('File is not a regular file or is not readable')
                result['is_valid'] = False
                return self._finalize_result(result, validation_start)

            result['size_bytes'] = path_obj.stat().st_size
            result['size_mb'] = round(result['size_bytes'] / (1024 * 1024), 2)

            # --- LOGIC QUYẾT ĐỊNH ĐƠN GIẢN ---
            # File được coi là hợp lệ nếu nó tồn tại, có thể đọc, và lớn hơn ngưỡng tối thiểu.
            if result['size_mb'] < self.min_size_mb_threshold:
                result['validation_errors'].append(f'File is too small ({result["size_mb"]} MB), likely empty or corrupted.')
                result['is_valid'] = False
            else:
                # Nếu tất cả các kiểm tra cơ bản đều qua, file được coi là hợp lệ
                result['is_valid'] = True
            
        except Exception as e:
            result['validation_errors'].append(f'Validation exception: {str(e)}')
            result['is_valid'] = False
        
        finally:
            return self._finalize_result(result, validation_start)

    def _finalize_result(self, result: dict, start_time: float) -> dict:
        """Helper to add final timing information."""
        result['validation_time'] = round(time.time() - start_time, 2)
        # [SIMPLIFIED] Thêm các trường bị thiếu để đảm bảo cấu trúc trả về nhất quán
        result['quality_score'] = 100 if result['is_valid'] else 0
        result['quality_grade'] = 'valid' if result['is_valid'] else 'invalid'
        result['technical_analysis'] = {'available': False, 'reason': 'Simplified validation enabled'}
        return result

    def validate_batch(self, file_paths: List[str], expected_type: str = 'video') -> dict:
        """
        [SIMPLIFIED] Validate multiple files and return a basic batch summary.
        """
        batch_start = time.time()
        
        results = []
        summary = {
            'total_files': len(file_paths),
            'valid_files': 0,
            'invalid_files': 0,
            'total_size_mb': 0.0,
            'validation_time': 0.0,
            'success_rate': 0.0,
            'common_errors': {}
        }
        
        print(f"🔍 [SIMPLIFIED] Validating batch of {len(file_paths)} files...")
        
        for i, file_path in enumerate(file_paths, 1):
            print(f"   📁 {i}/{len(file_paths)}: {os.path.basename(file_path)}")
            
            result = self.validate_file(file_path, expected_type)
            results.append(result)
            
            # Update summary
            if result['is_valid']:
                summary['valid_files'] += 1
            else:
                summary['invalid_files'] += 1
                for error in result.get('validation_errors', []):
                    summary['common_errors'][error] = summary['common_errors'].get(error, 0) + 1
            
            summary['total_size_mb'] += result.get('size_mb', 0)
        
        # Calculate success rate
        if summary['total_files'] > 0:
            summary['success_rate'] = round((summary['valid_files'] / summary['total_files']) * 100, 1)

        summary['validation_time'] = round(time.time() - batch_start, 2)
        
        print(f"✅ Batch validation completed in {summary['validation_time']}s")
        print(f"   📊 Valid: {summary['valid_files']}/{summary['total_files']} (Success rate: {summary['success_rate']}%)")
        
        return {
            'summary': summary,
            'individual_results': results
        }

    # [REMOVED] Hàm generate_validation_report không còn cần thiết vì không có nhiều chi tiết để báo cáo.
    # Tuy nhiên, để tránh làm hỏng các phần khác của project nếu có gọi đến nó,
    # chúng ta sẽ tạo một phiên bản đơn giản.
    def generate_validation_report(self, validation_results: dict, output_path: str = None) -> str:
        """
        [SIMPLIFIED] Generate a basic validation report.
        """
        summary = validation_results.get('summary', {})
        report_lines = [
            "# 📊 SIMPLIFIED FILE VALIDATION REPORT",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 📋 BATCH SUMMARY",
            f"- Total Files: {summary.get('total_files', 0)}",
            f"- Valid Files: {summary.get('valid_files', 0)}",
            f"- Invalid Files: {summary.get('invalid_files', 0)}",
            f"- Success Rate: {summary.get('success_rate', 0.0)}%",
            f"- Total Size: {summary.get('total_size_mb', 0)} MB",
            f"- Validation Time: {summary.get('validation_time', 0)}s",
        ]
        
        report_content = "\n".join(report_lines)
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(report_content)
            print(f"📄 Simplified report saved: {output_path}")
        
        return report_content