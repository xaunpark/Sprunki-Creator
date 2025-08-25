"""
File Validation & Quality Control System - PHASE 3
Validates output files and measures quality metrics
"""

import os
import json
import time
import hashlib
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from pathlib import Path
import subprocess

class FileQualityValidator:
    """Validates video files and measures quality metrics"""
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.validation_rules = self._get_validation_rules()
        self.quality_thresholds = self._get_quality_thresholds()
        
    def _get_validation_rules(self) -> dict:
        """Get validation rules for different file types"""
        return {
            'video': {
                'required_extensions': ['.mp4', '.mkv', '.avi'],
                'min_size_mb': 1.0,      # Minimum 1MB
                'max_size_mb': 500.0,    # Maximum 500MB
                'min_duration_sec': 5.0, # Minimum 5 seconds
                'max_duration_sec': 300.0, # Maximum 5 minutes
                'required_codecs': ['h264', 'hevc'],
                'min_resolution': (640, 480),   # 480p minimum
                'max_resolution': (1920, 1080), # 1080p maximum
                'min_bitrate_kbps': 500,        # 500 kbps minimum
                'max_bitrate_kbps': 10000       # 10 Mbps maximum
            },
            'audio': {
                'required_sample_rates': [44100, 48000],
                'min_bitrate_kbps': 64,
                'max_bitrate_kbps': 320,
                'required_channels': [1, 2]  # Mono or Stereo
            }
        }
    
    def _get_quality_thresholds(self) -> dict:
        """Get quality scoring thresholds"""
        return {
            'excellent': {'min_score': 90, 'color': 'green'},
            'good': {'min_score': 75, 'color': 'blue'}, 
            'acceptable': {'min_score': 60, 'color': 'orange'},
            'poor': {'min_score': 40, 'color': 'red'},
            'unacceptable': {'min_score': 0, 'color': 'darkred'}
        }
    
    def validate_file(self, file_path: str, expected_type: str = 'video') -> dict:
        """Comprehensive file validation"""
        validation_start = time.time()
        
        result = {
            'file_path': file_path,
            'expected_type': expected_type,
            'validation_time': None,
            'exists': False,
            'readable': False,
            'size_bytes': 0,
            'size_mb': 0.0,
            'basic_checks': {},
            'technical_analysis': {},
            'quality_metrics': {},
            'quality_score': 0,
            'quality_grade': 'unacceptable',
            'validation_errors': [],
            'validation_warnings': [],
            'is_valid': False
        }
        
        try:
            # STEP 1: Basic file existence and accessibility
            basic_result = self._validate_basic_properties(file_path)
            result.update(basic_result)
            
            if not result['exists']:
                result['validation_errors'].append('File does not exist')
                return result
            
            if not result['readable']:
                result['validation_errors'].append('File is not readable')
                return result
            
            # STEP 2: Technical analysis (metadata, format, etc.)
            if expected_type == 'video':
                tech_result = self._analyze_video_technical(file_path)
                result['technical_analysis'] = tech_result
                
                # STEP 3: Quality metrics
                quality_result = self._measure_video_quality(file_path, tech_result)
                result['quality_metrics'] = quality_result
                
                # STEP 4: Validation against rules
                validation_result = self._validate_against_rules(result, expected_type)
                result.update(validation_result)
            
            # STEP 5: Calculate final quality score
            result['quality_score'] = self._calculate_quality_score(result)
            result['quality_grade'] = self._get_quality_grade(result['quality_score'])
            
            # STEP 6: Determine overall validity
            result['is_valid'] = len(result['validation_errors']) == 0 and result['quality_score'] >= 40
            
        except Exception as e:
            result['validation_errors'].append(f'Validation exception: {str(e)}')
            result['is_valid'] = False
        
        finally:
            result['validation_time'] = round(time.time() - validation_start, 2)
        
        return result
    
    def _validate_basic_properties(self, file_path: str) -> dict:
        """Validate basic file properties"""
        try:
            path_obj = Path(file_path)
            
            exists = path_obj.exists()
            readable = path_obj.is_file() and os.access(file_path, os.R_OK) if exists else False
            
            size_bytes = path_obj.stat().st_size if exists else 0
            size_mb = round(size_bytes / (1024 * 1024), 2)
            
            return {
                'exists': exists,
                'readable': readable,
                'size_bytes': size_bytes,
                'size_mb': size_mb,
                'basic_checks': {
                    'file_extension': path_obj.suffix.lower(),
                    'filename': path_obj.name,
                    'parent_directory': str(path_obj.parent),
                    'created_time': datetime.fromtimestamp(path_obj.stat().st_ctime).isoformat() if exists else None,
                    'modified_time': datetime.fromtimestamp(path_obj.stat().st_mtime).isoformat() if exists else None
                }
            }
            
        except Exception as e:
            return {
                'exists': False,
                'readable': False, 
                'size_bytes': 0,
                'size_mb': 0.0,
                'basic_checks': {'error': str(e)}
            }
    
    def _analyze_video_technical(self, file_path: str) -> dict:
        """Analyze video technical properties using ffprobe"""
        try:
            # Use ffprobe to get detailed video information
            cmd = [
                'ffprobe', '-v', 'quiet', '-print_format', 'json', 
                '-show_format', '-show_streams', file_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                return {'error': f'ffprobe failed: {result.stderr}', 'available': False}
            
            data = json.loads(result.stdout)
            
            # Extract video stream info
            video_stream = None
            audio_stream = None
            
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video' and not video_stream:
                    video_stream = stream
                elif stream.get('codec_type') == 'audio' and not audio_stream:
                    audio_stream = stream
            
            format_info = data.get('format', {})
            
            analysis = {
                'available': True,
                'container_format': format_info.get('format_name', 'unknown'),
                'duration_seconds': float(format_info.get('duration', 0)),
                'bitrate_kbps': int(float(format_info.get('bit_rate', 0)) / 1000) if format_info.get('bit_rate') else 0,
                'file_size_bytes': int(format_info.get('size', 0))
            }
            
            if video_stream:
                analysis.update({
                    'video_codec': video_stream.get('codec_name', 'unknown'),
                    'video_resolution': f"{video_stream.get('width', 0)}x{video_stream.get('height', 0)}",
                    'video_width': int(video_stream.get('width', 0)),
                    'video_height': int(video_stream.get('height', 0)),
                    'video_fps': self._parse_fps(video_stream.get('r_frame_rate', '0/1')),
                    'video_bitrate_kbps': int(float(video_stream.get('bit_rate', 0)) / 1000) if video_stream.get('bit_rate') else 0,
                    'pixel_format': video_stream.get('pix_fmt', 'unknown')
                })
            
            if audio_stream:
                analysis.update({
                    'audio_codec': audio_stream.get('codec_name', 'unknown'),
                    'audio_sample_rate': int(audio_stream.get('sample_rate', 0)),
                    'audio_channels': int(audio_stream.get('channels', 0)),
                    'audio_bitrate_kbps': int(float(audio_stream.get('bit_rate', 0)) / 1000) if audio_stream.get('bit_rate') else 0
                })
            
            return analysis
            
        except subprocess.TimeoutExpired:
            return {'error': 'ffprobe timeout', 'available': False}
        except FileNotFoundError:
            return {'error': 'ffprobe not found - install ffmpeg', 'available': False}
        except Exception as e:
            return {'error': f'Technical analysis failed: {str(e)}', 'available': False}
    
    def _parse_fps(self, fps_string: str) -> float:
        """Parse FPS from ffprobe format (e.g., '30/1' -> 30.0)"""
        try:
            if '/' in fps_string:
                num, den = fps_string.split('/')
                return round(float(num) / float(den), 2)
            return float(fps_string)
        except:
            return 0.0
    
    def _measure_video_quality(self, file_path: str, technical_data: dict) -> dict:
        """Measure video quality metrics"""
        quality_metrics = {
            'resolution_score': 0,
            'duration_score': 0,
            'bitrate_score': 0,
            'format_score': 0,
            'audio_score': 0,
            'filesize_efficiency': 0,
            'technical_compliance': 0
        }
        
        if not technical_data.get('available'):
            return quality_metrics
        
        try:
            # Resolution score (0-25 points)
            width = technical_data.get('video_width', 0)
            height = technical_data.get('video_height', 0)
            
            if width >= 1920 and height >= 1080:  # 1080p+
                quality_metrics['resolution_score'] = 25
            elif width >= 1280 and height >= 720:  # 720p
                quality_metrics['resolution_score'] = 20
            elif width >= 854 and height >= 480:   # 480p
                quality_metrics['resolution_score'] = 15
            elif width >= 640 and height >= 360:   # 360p
                quality_metrics['resolution_score'] = 10
            else:
                quality_metrics['resolution_score'] = 5
            
            # Duration score (0-20 points)
            duration = technical_data.get('duration_seconds', 0)
            if 10 <= duration <= 30:      # Optimal duration for individual videos
                quality_metrics['duration_score'] = 20
            elif 5 <= duration <= 60:     # Acceptable duration
                quality_metrics['duration_score'] = 15
            elif duration > 60:           # Long but acceptable
                quality_metrics['duration_score'] = 10
            elif duration >= 3:           # Very short but usable
                quality_metrics['duration_score'] = 5
            else:
                quality_metrics['duration_score'] = 0
            
            # Bitrate score (0-20 points)
            bitrate = technical_data.get('bitrate_kbps', 0)
            if 2000 <= bitrate <= 8000:   # Good quality range
                quality_metrics['bitrate_score'] = 20
            elif 1000 <= bitrate <= 2000: # Acceptable
                quality_metrics['bitrate_score'] = 15
            elif 500 <= bitrate <= 1000:  # Low but watchable
                quality_metrics['bitrate_score'] = 10
            elif bitrate >= 200:          # Very low
                quality_metrics['bitrate_score'] = 5
            else:
                quality_metrics['bitrate_score'] = 0
            
            # Format/Codec score (0-15 points)
            codec = technical_data.get('video_codec', '').lower()
            container = technical_data.get('container_format', '').lower()
            
            format_score = 0
            if 'h264' in codec or 'avc' in codec:
                format_score += 8  # Good compatibility
            elif 'h265' in codec or 'hevc' in codec:
                format_score += 10 # Excellent compression
            elif codec in ['vp9', 'av1']:
                format_score += 9  # Modern codecs
            else:
                format_score += 5  # Other codecs
            
            if 'mp4' in container:
                format_score += 5  # Excellent compatibility
            elif container in ['mkv', 'webm']:
                format_score += 4  # Good compatibility
            else:
                format_score += 2  # Other containers
            
            quality_metrics['format_score'] = min(format_score, 15)
            
            # Audio score (0-10 points)
            audio_channels = technical_data.get('audio_channels', 0)
            audio_bitrate = technical_data.get('audio_bitrate_kbps', 0)
            
            audio_score = 0
            if audio_channels >= 2:       # Stereo+
                audio_score += 5
            elif audio_channels == 1:     # Mono
                audio_score += 3
            
            if 128 <= audio_bitrate <= 256:  # Good quality
                audio_score += 5
            elif 64 <= audio_bitrate < 128:  # Acceptable
                audio_score += 3
            elif audio_bitrate > 0:          # Some audio
                audio_score += 1
            
            quality_metrics['audio_score'] = audio_score
            
            # File size efficiency (0-10 points)
            file_size_mb = technical_data.get('file_size_bytes', 0) / (1024 * 1024)
            duration_min = duration / 60
            
            if duration_min > 0:
                mb_per_minute = file_size_mb / duration_min
                
                if 5 <= mb_per_minute <= 15:      # Efficient compression
                    quality_metrics['filesize_efficiency'] = 10
                elif 2 <= mb_per_minute <= 25:    # Reasonable
                    quality_metrics['filesize_efficiency'] = 7
                elif mb_per_minute <= 50:         # Large but acceptable
                    quality_metrics['filesize_efficiency'] = 5
                else:
                    quality_metrics['filesize_efficiency'] = 2
            
        except Exception as e:
            print(f"❌ Quality measurement error: {e}")
        
        return quality_metrics
    
    def _validate_against_rules(self, file_result: dict, expected_type: str) -> dict:
        """Validate file against defined rules"""
        rules = self.validation_rules.get(expected_type, {})
        errors = []
        warnings = []
        
        try:
            # File size validation
            size_mb = file_result.get('size_mb', 0)
            min_size = rules.get('min_size_mb', 0)
            max_size = rules.get('max_size_mb', float('inf'))
            
            if size_mb < min_size:
                errors.append(f'File too small: {size_mb}MB < {min_size}MB minimum')
            elif size_mb > max_size:
                errors.append(f'File too large: {size_mb}MB > {max_size}MB maximum')
            
            # Extension validation
            extension = file_result.get('basic_checks', {}).get('file_extension', '')
            required_extensions = rules.get('required_extensions', [])
            
            if required_extensions and extension not in required_extensions:
                warnings.append(f'Unexpected file extension: {extension}, expected one of {required_extensions}')
            
            # Technical validation for video
            if expected_type == 'video':
                tech_data = file_result.get('technical_analysis', {})
                
                # Duration validation
                duration = tech_data.get('duration_seconds', 0)
                min_duration = rules.get('min_duration_sec', 0)
                max_duration = rules.get('max_duration_sec', float('inf'))
                
                if duration < min_duration:
                    errors.append(f'Video too short: {duration}s < {min_duration}s minimum')
                elif duration > max_duration:
                    warnings.append(f'Video very long: {duration}s > {max_duration}s recommended maximum')
                
                # Resolution validation
                width = tech_data.get('video_width', 0)
                height = tech_data.get('video_height', 0)
                min_res = rules.get('min_resolution', (0, 0))
                max_res = rules.get('max_resolution', (float('inf'), float('inf')))
                
                if width < min_res[0] or height < min_res[1]:
                    errors.append(f'Resolution too low: {width}x{height} < {min_res[0]}x{min_res[1]} minimum')
                elif width > max_res[0] or height > max_res[1]:
                    warnings.append(f'Resolution very high: {width}x{height} > {max_res[0]}x{max_res[1]} recommended')
                
                # Bitrate validation
                bitrate = tech_data.get('bitrate_kbps', 0)
                min_bitrate = rules.get('min_bitrate_kbps', 0)
                max_bitrate = rules.get('max_bitrate_kbps', float('inf'))
                
                if bitrate < min_bitrate:
                    warnings.append(f'Bitrate low: {bitrate} kbps < {min_bitrate} kbps recommended')
                elif bitrate > max_bitrate:
                    warnings.append(f'Bitrate very high: {bitrate} kbps > {max_bitrate} kbps recommended')
                
        except Exception as e:
            errors.append(f'Rule validation error: {str(e)}')
        
        return {
            'validation_errors': errors,
            'validation_warnings': warnings
        }
    
    def _calculate_quality_score(self, file_result: dict) -> int:
        """Calculate overall quality score (0-100)"""
        try:
            quality_metrics = file_result.get('quality_metrics', {})
            
            # Sum all quality metric scores
            total_score = sum(quality_metrics.values())
            
            # Apply penalties for errors
            error_count = len(file_result.get('validation_errors', []))
            warning_count = len(file_result.get('validation_warnings', []))
            
            # Penalty system
            penalty = (error_count * 15) + (warning_count * 5)
            
            final_score = max(0, total_score - penalty)
            
            return min(100, int(final_score))
            
        except Exception:
            return 0
    
    def _get_quality_grade(self, score: int) -> str:
        """Convert quality score to grade"""
        for grade, threshold in self.quality_thresholds.items():
            if score >= threshold['min_score']:
                return grade
        return 'unacceptable'
    
    def validate_batch(self, file_paths: List[str], expected_type: str = 'video') -> dict:
        """Validate multiple files and return batch summary"""
        batch_start = time.time()
        
        results = []
        summary = {
            'total_files': len(file_paths),
            'valid_files': 0,
            'invalid_files': 0,
            'total_size_mb': 0.0,
            'average_quality_score': 0.0,
            'quality_distribution': {},
            'common_errors': {},
            'validation_time': 0.0
        }
        
        print(f"🔍 Validating batch of {len(file_paths)} files...")
        
        for i, file_path in enumerate(file_paths, 1):
            print(f"   📁 {i}/{len(file_paths)}: {os.path.basename(file_path)}")
            
            result = self.validate_file(file_path, expected_type)
            results.append(result)
            
            # Update summary
            if result['is_valid']:
                summary['valid_files'] += 1
            else:
                summary['invalid_files'] += 1
            
            summary['total_size_mb'] += result.get('size_mb', 0)
            
            # Track quality distribution
            grade = result.get('quality_grade', 'unacceptable')
            summary['quality_distribution'][grade] = summary['quality_distribution'].get(grade, 0) + 1
            
            # Track common errors
            for error in result.get('validation_errors', []):
                summary['common_errors'][error] = summary['common_errors'].get(error, 0) + 1
        
        # Calculate averages
        if results:
            total_score = sum(r.get('quality_score', 0) for r in results)
            summary['average_quality_score'] = round(total_score / len(results), 1)
        
        summary['validation_time'] = round(time.time() - batch_start, 2)
        
        print(f"✅ Batch validation completed in {summary['validation_time']}s")
        print(f"   📊 Valid: {summary['valid_files']}/{summary['total_files']}")
        print(f"   ⭐ Average quality: {summary['average_quality_score']}/100")
        
        return {
            'summary': summary,
            'individual_results': results
        }

    def generate_validation_report(self, validation_results: dict, output_path: str = None) -> str:
        """Generate detailed validation report"""
        try:
            summary = validation_results.get('summary', {})
            results = validation_results.get('individual_results', [])
            
            report_lines = [
                "# 📊 FILE VALIDATION REPORT",
                f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "",
                "## 📋 BATCH SUMMARY",
                f"- Total Files: {summary.get('total_files', 0)}",
                f"- Valid Files: {summary.get('valid_files', 0)}",
                f"- Invalid Files: {summary.get('invalid_files', 0)}",
                f"- Success Rate: {round((summary.get('valid_files', 0) / max(summary.get('total_files', 1), 1)) * 100, 1)}%",
                f"- Total Size: {summary.get('total_size_mb', 0)} MB",
                f"- Average Quality Score: {summary.get('average_quality_score', 0)}/100",
                f"- Validation Time: {summary.get('validation_time', 0)}s",
                "",
                "## 🎯 QUALITY DISTRIBUTION"
            ]
            
            # Quality distribution
            for grade, count in summary.get('quality_distribution', {}).items():
                percentage = round((count / max(summary.get('total_files', 1), 1)) * 100, 1)
                report_lines.append(f"- {grade.title()}: {count} files ({percentage}%)")
            
            # Common errors
            common_errors = summary.get('common_errors', {})
            if common_errors:
                report_lines.extend([
                    "",
                    "## ⚠️ COMMON ISSUES"
                ])
                
                for error, count in sorted(common_errors.items(), key=lambda x: x[1], reverse=True)[:5]:
                    report_lines.append(f"- {error}: {count} occurrences")
            
            # Individual file details
            if results:
                report_lines.extend([
                    "",
                    "## 📁 INDIVIDUAL FILE RESULTS",
                    ""
                ])
                
                for result in results:
                    filename = os.path.basename(result.get('file_path', 'unknown'))
                    status = "✅ VALID" if result.get('is_valid') else "❌ INVALID"
                    score = result.get('quality_score', 0)
                    grade = result.get('quality_grade', 'unknown').title()
                    size_mb = result.get('size_mb', 0)
                    
                    report_lines.extend([
                        f"### {filename}",
                        f"- Status: {status}",
                        f"- Quality: {score}/100 ({grade})",
                        f"- Size: {size_mb} MB",
                        f"- Validation Time: {result.get('validation_time', 0)}s"
                    ])
                    
                    # Technical details if available
                    tech = result.get('technical_analysis', {})
                    if tech.get('available'):
                        report_lines.extend([
                            f"- Resolution: {tech.get('video_resolution', 'N/A')}",
                            f"- Duration: {tech.get('duration_seconds', 0):.1f}s",
                            f"- Bitrate: {tech.get('bitrate_kbps', 0)} kbps",
                            f"- Codec: {tech.get('video_codec', 'N/A')}"
                        ])
                    
                    # Errors and warnings
                    errors = result.get('validation_errors', [])
                    warnings = result.get('validation_warnings', [])
                    
                    if errors:
                        report_lines.append("- Errors:")
                        for error in errors:
                            report_lines.append(f"  - {error}")
                    
                    if warnings:
                        report_lines.append("- Warnings:")
                        for warning in warnings:
                            report_lines.append(f"  - {warning}")
                    
                    report_lines.append("")
            
            report_content = "\n".join(report_lines)
            
            # Save to file if path provided
            if output_path:
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(report_content)
                print(f"📄 Validation report saved: {output_path}")
            
            return report_content
            
        except Exception as e:
            error_msg = f"Error generating validation report: {str(e)}"
            print(f"❌ {error_msg}")
            return error_msg