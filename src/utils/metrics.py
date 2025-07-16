import logging
from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = logging.getLogger(__name__)

# Define metrics
recordings_processed = Counter(
    'asterisk_sentiment_recordings_processed_total',
    'Total number of recordings processed',
    ['recording_type', 'status']
)

recording_size = Histogram(
    'asterisk_sentiment_recording_size_bytes',
    'Size of processed recordings in bytes',
    ['recording_type'],
    buckets=(0, 1024, 10*1024, 100*1024, 1024*1024, 10*1024*1024)
)

api_request_duration = Histogram(
    'asterisk_sentiment_api_request_duration_seconds',
    'Duration of API requests',
    ['endpoint', 'status'],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
)

ami_connection_status = Gauge(
    'asterisk_sentiment_ami_connection_status',
    'AMI connection status (1=connected, 0=disconnected)'
)

queue_recordings = Counter(
    'asterisk_sentiment_queue_recordings_total',
    'Number of queue recordings processed',
    ['queue_name']
)

voicemail_recordings = Counter(
    'asterisk_sentiment_voicemail_recordings_total',
    'Number of voicemail recordings processed',
    ['mailbox']
)

# CDR Queue metrics
cdr_queue_depth = Gauge(
    'asterisk_cdr_queue_depth',
    'Current number of CDRs in processing queue'
)

cdr_queue_dropped = Counter(
    'asterisk_cdr_queue_dropped_total',
    'Total number of CDRs dropped due to full queue'
)

cdr_batch_processing_duration = Histogram(
    'asterisk_cdr_batch_processing_duration_seconds',
    'Time taken to process and send CDR batches',
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
)

cdr_http_worker_status = Gauge(
    'asterisk_cdr_http_worker_status',
    'HTTP worker status (1=running, 0=stopped)'
)

api_errors = Counter(
    'asterisk_sentiment_api_errors_total',
    'Number of API errors encountered',
    ['error_type']
)

def initialize_metrics_server(port=8000):
    """
    Initialize and start the Prometheus metrics server
    
    Args:
        port: Port to expose metrics on
    """
    try:
        logger.info(f"Starting Prometheus metrics server on port {port}")
        start_http_server(port)
        logger.info(f"Metrics server started successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to start metrics server: {e}")
        return False

def record_processed_recording(recording_type, status, size=None):
    """
    Record a processed recording in metrics
    
    Args:
        recording_type: Type of recording (call, queue, voicemail)
        status: Processing status (success, error)
        size: Size of recording in bytes
    """
    recordings_processed.labels(recording_type=recording_type, status=status).inc()
    
    if size is not None:
        recording_size.labels(recording_type=recording_type).observe(size)

def record_queue_recording(queue_name):
    """
    Record a processed queue recording
    
    Args:
        queue_name: Name of the queue
    """
    queue_recordings.labels(queue_name=queue_name).inc()

def record_voicemail_recording(mailbox):
    """
    Record a processed voicemail recording
    
    Args:
        mailbox: Mailbox identifier
    """
    voicemail_recordings.labels(mailbox=mailbox).inc()

def record_api_request(endpoint, status, duration):
    """
    Record an API request duration
    
    Args:
        endpoint: API endpoint
        status: HTTP status code or error
        duration: Request duration in seconds
    """
    api_request_duration.labels(endpoint=endpoint, status=status).observe(duration)

def record_ami_connection_status(connected):
    """
    Update AMI connection status
    
    Args:
        connected: Boolean indicating if connected
    """
    ami_connection_status.set(1 if connected else 0)

def record_api_error(error_type):
    """
    Record an API error
    
    Args:
        error_type: Type of error encountered
    """
    api_errors.labels(error_type=error_type).inc()

def update_cdr_queue_depth(depth):
    """
    Update CDR queue depth metric
    
    Args:
        depth: Current queue depth
    """
    cdr_queue_depth.set(depth)

def record_cdr_dropped():
    """Record a CDR dropped due to full queue"""
    cdr_queue_dropped.inc()

def record_cdr_batch_duration(duration):
    """
    Record CDR batch processing duration
    
    Args:
        duration: Processing duration in seconds
    """
    cdr_batch_processing_duration.observe(duration)

def update_http_worker_status(running):
    """
    Update HTTP worker status
    
    Args:
        running: Boolean indicating if worker is running
    """
    cdr_http_worker_status.set(1 if running else 0)


class MetricsCollector:
    """Simple metrics collector for tracking events."""
    
    def __init__(self):
        self._counters = {}
    
    def increment(self, metric_name, value=1):
        """Increment a metric counter."""
        if metric_name not in self._counters:
            self._counters[metric_name] = 0
        self._counters[metric_name] += value
        logger.debug(f"Metric {metric_name} incremented by {value} (total: {self._counters[metric_name]})")
    
    def record_value(self, metric_name, value):
        """Record a metric value."""
        self._counters[f"{metric_name}_value"] = value
        logger.debug(f"Metric {metric_name} recorded value: {value}")
    
    def get_all(self):
        """Get all metrics."""
        return self._counters.copy()