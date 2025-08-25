"""
Performance Analytics Collector - PHASE 4
Collects and analyzes performance metrics for bottleneck detection
"""

import time
import psutil
import threading
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from collections import deque, defaultdict
from dataclasses import dataclass, field
import statistics

@dataclass
class PerformanceMetric:
    """Single performance measurement"""
    timestamp: float
    metric_type: str
    value: float
    context: Dict[str, Any] = field(default_factory=dict)

class PerformanceAnalyzer:
    """Analyzes system and application performance"""
    
    def __init__(self, history_minutes: int = 60):
        self.history_minutes = history_minutes
        self.max_samples = history_minutes * 12  # 5-second intervals
        
        # Metric storage
        self.metrics = defaultdict(lambda: deque(maxlen=self.max_samples))
        self.bottlenecks = deque(maxlen=100)  # Recent bottlenecks
        
        # Performance thresholds
        self.thresholds = {
            'cpu_percent': {'warning': 80, 'critical': 95},
            'memory_percent': {'warning': 85, 'critical': 95}, 
            'disk_usage_percent': {'warning': 90, 'critical': 98},
            'job_duration_minutes': {'warning': 15, 'critical': 30},
            'retry_rate_percent': {'warning': 20, 'critical': 40},
            'file_validation_rate': {'warning': 70, 'critical': 50}
        }
        
        # Analysis state
        self.analysis_running = False
        self.analysis_thread = None
        
        # Job tracking
        self.active_jobs = {}  # queue_id -> job_info
        self.completed_jobs = deque(maxlen=100)
        
        print("📊 Performance Analyzer initialized")
    
    def start_monitoring(self):
        """Start continuous performance monitoring"""
        if self.analysis_running:
            return
        
        self.analysis_running = True
        self.analysis_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.analysis_thread.start()
        
        print("📈 Performance monitoring started")
    
    def stop_monitoring(self):
        """Stop performance monitoring"""
        self.analysis_running = False
        if self.analysis_thread:
            self.analysis_thread.join(timeout=5)
        
        print("📈 Performance monitoring stopped")
    
    def record_job_start(self, queue_id: int, job_info: Dict[str, Any]):
        """Record job start for performance tracking"""
        self.active_jobs[queue_id] = {
            **job_info,
            'start_time': time.time(),
            'start_timestamp': datetime.now().isoformat(),
            'progress_updates': 0,
            'retry_count': 0,
            'last_progress_time': time.time()
        }
        
        self._record_metric('active_jobs', len(self.active_jobs))
    
    def record_job_progress(self, queue_id: int, stage: str, progress_percent: float):
        """Record job progress update"""
        if queue_id in self.active_jobs:
            job = self.active_jobs[queue_id]
            job['progress_updates'] += 1
            job['current_stage'] = stage
            job['current_progress'] = progress_percent
            job['last_progress_time'] = time.time()
            
            # Calculate progress rate
            elapsed = time.time() - job['start_time']
            if elapsed > 0:
                progress_rate = progress_percent / elapsed
                self._record_metric('progress_rate', progress_rate, {
                    'queue_id': queue_id, 
                    'stage': stage
                })
    
    def record_job_retry(self, queue_id: int, retry_info: Dict[str, Any]):
        """Record job retry event"""
        if queue_id in self.active_jobs:
            self.active_jobs[queue_id]['retry_count'] += 1
        
        self._record_metric('job_retries', 1, retry_info)
    
    def record_job_completion(self, queue_id: int, success: bool, final_info: Dict[str, Any]):
        """Record job completion for performance analysis"""
        if queue_id not in self.active_jobs:
            return
        
        job = self.active_jobs.pop(queue_id)
        
        # Calculate job metrics
        duration = time.time() - job['start_time']
        duration_minutes = duration / 60
        
        completion_data = {
            **job,
            **final_info,
            'end_time': time.time(),
            'end_timestamp': datetime.now().isoformat(),
            'duration_seconds': duration,
            'duration_minutes': duration_minutes,
            'success': success,
            'progress_updates_count': job.get('progress_updates', 0),
            'retry_count': job.get('retry_count', 0)
        }
        
        self.completed_jobs.append(completion_data)
        
        # Record performance metrics
        self._record_metric('job_duration_minutes', duration_minutes, {'success': success})
        self._record_metric('active_jobs', len(self.active_jobs))
        
        if job.get('retry_count', 0) > 0:
            self._record_metric('jobs_with_retries', 1)
        
        # Check for bottlenecks
        self._check_job_bottlenecks(completion_data)
    
    def _monitoring_loop(self):
        """Main monitoring loop"""
        while self.analysis_running:
            try:
                # System metrics
                self._collect_system_metrics()
                
                # Application metrics
                self._collect_application_metrics()
                
                # Bottleneck detection
                self._detect_bottlenecks()
                
                time.sleep(5)  # 5-second intervals
                
            except Exception as e:
                print(f"❌ Monitoring loop error: {e}")
                time.sleep(10)  # Longer delay on error
    
    def _collect_system_metrics(self):
        """Collect system performance metrics"""
        try:
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=1)
            self._record_metric('cpu_percent', cpu_percent)
            
            # Memory usage
            memory = psutil.virtual_memory()
            self._record_metric('memory_percent', memory.percent)
            self._record_metric('memory_available_mb', memory.available / (1024*1024))
            
            # Disk usage
            disk = psutil.disk_usage('/')
            disk_percent = (disk.used / disk.total) * 100
            self._record_metric('disk_usage_percent', disk_percent)
            self._record_metric('disk_free_gb', disk.free / (1024*1024*1024))
            
            # Network (if available)
            try:
                net = psutil.net_io_counters()
                self._record_metric('network_bytes_sent', net.bytes_sent)
                self._record_metric('network_bytes_recv', net.bytes_recv)
            except:
                pass
                
        except Exception as e:
            print(f"❌ System metrics collection error: {e}")
    
    def _collect_application_metrics(self):
        """Collect application-specific metrics"""
        try:
            # Job metrics
            self._record_metric('active_jobs_count', len(self.active_jobs))
            
            # Calculate recent success rate
            if len(self.completed_jobs) >= 5:
                recent_jobs = list(self.completed_jobs)[-20:]  # Last 20 jobs
                success_count = sum(1 for job in recent_jobs if job.get('success', False))
                success_rate = (success_count / len(recent_jobs)) * 100
                self._record_metric('success_rate_percent', success_rate)
            
            # Calculate retry rate
            if len(self.completed_jobs) >= 5:
                recent_jobs = list(self.completed_jobs)[-20:]
                jobs_with_retries = sum(1 for job in recent_jobs if job.get('retry_count', 0) > 0)
                retry_rate = (jobs_with_retries / len(recent_jobs)) * 100
                self._record_metric('retry_rate_percent', retry_rate)
            
            # Average job duration
            if len(self.completed_jobs) >= 3:
                recent_durations = [job.get('duration_minutes', 0) for job in list(self.completed_jobs)[-10:]]
                avg_duration = statistics.mean(recent_durations)
                self._record_metric('avg_job_duration_minutes', avg_duration)
                
        except Exception as e:
            print(f"❌ Application metrics collection error: {e}")
    
    def _record_metric(self, metric_type: str, value: float, context: Dict[str, Any] = None):
        """Record a performance metric"""
        metric = PerformanceMetric(
            timestamp=time.time(),
            metric_type=metric_type,
            value=value,
            context=context or {}
        )
        
        self.metrics[metric_type].append(metric)
    
    def _detect_bottlenecks(self):
        """Detect performance bottlenecks"""
        try:
            current_time = time.time()
            
            # Check each metric against thresholds
            for metric_type, threshold_config in self.thresholds.items():
                if metric_type not in self.metrics:
                    continue
                
                recent_metrics = [m for m in self.metrics[metric_type] 
                                if current_time - m.timestamp <= 300]  # Last 5 minutes
                
                if len(recent_metrics) < 3:
                    continue
                
                # Calculate average for recent period
                avg_value = statistics.mean([m.value for m in recent_metrics])
                
                # Check thresholds
                severity = None
                if avg_value >= threshold_config['critical']:
                    severity = 'critical'
                elif avg_value >= threshold_config['warning']:
                    severity = 'warning'
                
                if severity:
                    bottleneck = {
                        'timestamp': datetime.now().isoformat(),
                        'metric_type': metric_type,
                        'severity': severity,
                        'current_value': avg_value,
                        'threshold': threshold_config[severity],
                        'samples_count': len(recent_metrics),
                        'trend': self._calculate_trend(recent_metrics)
                    }
                    
                    # Avoid duplicate bottlenecks
                    if not self._is_duplicate_bottleneck(bottleneck):
                        self.bottlenecks.append(bottleneck)
                        print(f"🚨 {severity.upper()} bottleneck detected: {metric_type} = {avg_value:.1f}")
                        
        except Exception as e:
            print(f"❌ Bottleneck detection error: {e}")
    
    def _calculate_trend(self, metrics: List[PerformanceMetric]) -> str:
        """Calculate trend direction for metrics"""
        if len(metrics) < 2:
            return 'stable'
        
        try:
            values = [m.value for m in metrics]
            first_half = statistics.mean(values[:len(values)//2])
            second_half = statistics.mean(values[len(values)//2:])
            
            diff_percent = ((second_half - first_half) / first_half) * 100
            
            if diff_percent > 10:
                return 'increasing'
            elif diff_percent < -10:
                return 'decreasing'
            else:
                return 'stable'
                
        except:
            return 'unknown'
    
    def _is_duplicate_bottleneck(self, new_bottleneck: Dict[str, Any]) -> bool:
        """Check if bottleneck is duplicate of recent one"""
        if not self.bottlenecks:
            return False
        
        recent_bottleneck = self.bottlenecks[-1]
        
        # Same metric within 5 minutes
        time_diff = datetime.now() - datetime.fromisoformat(recent_bottleneck['timestamp'].replace('Z', '+00:00'))
        
        return (recent_bottleneck['metric_type'] == new_bottleneck['metric_type'] and
                time_diff.total_seconds() < 300)
    
    def _check_job_bottlenecks(self, job_data: Dict[str, Any]):
        """Check for job-specific bottlenecks"""
        try:
            duration_minutes = job_data.get('duration_minutes', 0)
            retry_count = job_data.get('retry_count', 0)
            
            # Long-running job
            if duration_minutes > 20:
                bottleneck = {
                    'timestamp': datetime.now().isoformat(),
                    'metric_type': 'job_duration',
                    'severity': 'warning' if duration_minutes < 30 else 'critical',
                    'current_value': duration_minutes,
                    'threshold': 15,
                    'job_id': job_data.get('queue_id'),
                    'job_mode': job_data.get('mode', 'unknown')
                }
                self.bottlenecks.append(bottleneck)
            
            # High retry job
            if retry_count > 2:
                bottleneck = {
                    'timestamp': datetime.now().isoformat(),
                    'metric_type': 'job_retries',
                    'severity': 'warning' if retry_count < 5 else 'critical',
                    'current_value': retry_count,
                    'threshold': 2,
                    'job_id': job_data.get('queue_id')
                }
                self.bottlenecks.append(bottleneck)
                
        except Exception as e:
            print(f"❌ Job bottleneck check error: {e}")
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary"""
        try:
            current_time = time.time()
            
            summary = {
                'timestamp': datetime.now().isoformat(),
                'monitoring_active': self.analysis_running,
                'active_jobs': len(self.active_jobs),
                'completed_jobs_count': len(self.completed_jobs),
                'recent_bottlenecks': len([b for b in self.bottlenecks 
                                         if current_time - datetime.fromisoformat(b['timestamp'].replace('Z', '+00:00')).timestamp() <= 3600]),
                'system_health': {},
                'application_health': {},
                'trends': {},
                'recommendations': []
            }
            
            # System health
            for metric_type in ['cpu_percent', 'memory_percent', 'disk_usage_percent']:
                if metric_type in self.metrics and self.metrics[metric_type]:
                    latest = self.metrics[metric_type][-1]
                    threshold = self.thresholds.get(metric_type, {})
                    
                    health_status = 'good'
                    if latest.value >= threshold.get('critical', 100):
                        health_status = 'critical'
                    elif latest.value >= threshold.get('warning', 90):
                        health_status = 'warning'
                    
                    summary['system_health'][metric_type] = {
                        'current_value': latest.value,
                        'status': health_status,
                        'timestamp': latest.timestamp
                    }
            
            # Application health
            if len(self.completed_jobs) >= 5:
                recent_jobs = list(self.completed_jobs)[-20:]
                success_count = sum(1 for job in recent_jobs if job.get('success', False))
                success_rate = (success_count / len(recent_jobs)) * 100
                
                retry_jobs = sum(1 for job in recent_jobs if job.get('retry_count', 0) > 0)
                retry_rate = (retry_jobs / len(recent_jobs)) * 100
                
                avg_duration = statistics.mean([job.get('duration_minutes', 0) for job in recent_jobs])
                
                summary['application_health'] = {
                    'success_rate': success_rate,
                    'retry_rate': retry_rate,
                    'avg_duration_minutes': avg_duration,
                    'sample_size': len(recent_jobs)
                }
            
            # Generate recommendations
            summary['recommendations'] = self._generate_recommendations(summary)
            
            return summary
            
        except Exception as e:
            return {
                'error': f'Performance summary error: {str(e)}',
                'timestamp': datetime.now().isoformat()
            }
    
    def _generate_recommendations(self, summary: Dict[str, Any]) -> List[str]:
        """Generate performance improvement recommendations"""
        recommendations = []
        
        try:
            # System recommendations
            system_health = summary.get('system_health', {})
            
            if system_health.get('cpu_percent', {}).get('current_value', 0) > 80:
                recommendations.append("🔧 High CPU usage detected - consider reducing concurrent jobs")
            
            if system_health.get('memory_percent', {}).get('current_value', 0) > 85:
                recommendations.append("💾 High memory usage - enable memory cleanup between jobs")
            
            if system_health.get('disk_usage_percent', {}).get('current_value', 0) > 90:
                recommendations.append("💿 Low disk space - enable automatic file cleanup")
            
            # Application recommendations
            app_health = summary.get('application_health', {})
            
            if app_health.get('success_rate', 100) < 80:
                recommendations.append("⚠️ Low success rate - investigate common failure patterns")
            
            if app_health.get('retry_rate', 0) > 30:
                recommendations.append("🔄 High retry rate - check network and browser stability")
            
            if app_health.get('avg_duration_minutes', 0) > 15:
                recommendations.append("⏱️ Long job duration - optimize processing workflow")
            
            # Bottleneck recommendations
            recent_bottlenecks = [b for b in self.bottlenecks 
                                if (time.time() - datetime.fromisoformat(b['timestamp'].replace('Z', '+00:00')).timestamp()) <= 1800]
            
            if len(recent_bottlenecks) > 5:
                recommendations.append("🚨 Multiple bottlenecks detected - consider system scaling")
            
            if not recommendations:
                recommendations.append("✅ System performance is healthy")
                
        except Exception as e:
            recommendations.append(f"❌ Recommendation generation error: {str(e)}")
        
        return recommendations

# Global instance
performance_analyzer = PerformanceAnalyzer()