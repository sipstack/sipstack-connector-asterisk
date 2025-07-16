from typing import Dict, Any, List, Optional

def is_queue_call(event: Dict[str, Any], metadata: Dict[str, Any], 
                 whitelist: List[str] = None, blacklist: List[str] = None) -> bool:
    """
    Determine if an event represents a queue call based on multiple detection methods
    
    Args:
        event: The Asterisk AMI event
        metadata: Call metadata extracted from the event
        whitelist: List of queue names to include (if empty, include all)
        blacklist: List of queue names to exclude
        
    Returns:
        True if the call is a queue call that should be processed
    """
    whitelist = whitelist or []
    blacklist = blacklist or []
    
    # Method 1: Check for Queue variable in event
    queue = event.get('Queue') or metadata.get('queue')
    if queue:
        # If we have a whitelist and the queue isn't in it, skip it
        if whitelist and queue not in whitelist:
            return False
        
        # If the queue is in the blacklist, skip it
        if queue in blacklist:
            return False
            
        return True
    
    # Method 2: Check for Queue variable in channel variables
    # Some Asterisk setups store queue info in variables
    variables = event.get('ChanVariable') or {}
    if isinstance(variables, dict):
        queue_var = variables.get('QUEUE') or variables.get('QUEUENAME')
        if queue_var:
            if whitelist and queue_var not in whitelist:
                return False
            if queue_var in blacklist:
                return False
            return True
    
    # Method 3: Check for queue context in channel
    channel = event.get('Channel', '')
    context = event.get('Context', '')
    
    queue_contexts = ['queue', 'from-queue', 'queue-callback']
    if any(q_ctx in context.lower() for q_ctx in queue_contexts):
        return True
    
    # Method 4: Path-based detection
    filename = event.get('Filename', '')
    if 'queue' in filename.lower():
        return True
    
    # No queue indicators found
    return False

def is_voicemail(event: Dict[str, Any]) -> bool:
    """
    Determine if an event represents a voicemail recording
    
    Args:
        event: The Asterisk AMI event
        
    Returns:
        True if the event is for a voicemail
    """
    # Most reliable way is to check the event name is VoicemailMessage
    if event.get('Event') == 'VoicemailMessage':
        return True
    
    # For RecordFile events, check filename and context for voicemail indicators
    filename = event.get('Filename', '')
    context = event.get('Context', '')
    
    # Check for typical voicemail patterns
    if any(vm in filename.lower() for vm in ['voicemail', 'vm-', 'msg']):
        return True
        
    if any(vm in context.lower() for vm in ['voicemail', 'vm']):
        return True
    
    # No voicemail indicators found
    return False