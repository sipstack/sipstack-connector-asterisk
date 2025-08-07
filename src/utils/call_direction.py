"""Enhanced call direction detection with configuration support."""

import logging
from typing import Optional, Dict, Any, Tuple
from utils.config import config_manager
from utils.pattern_matcher import PatternMatcher, NumberAnalyzer, TransferDetector

logger = logging.getLogger(__name__)


class CallDirectionDetector:
    """Enhanced call direction detection with caching and configuration."""
    
    def __init__(self):
        self.config = config_manager.get_config()
        self.pattern_matcher = PatternMatcher(cache_ttl=self.config.cache_ttl_seconds)
        self.number_analyzer = NumberAnalyzer(self.config)
        self.transfer_detector = TransferDetector(self.config)
        
        # Initialize pattern matcher with all context patterns
        self._initialize_patterns()
        
    def _initialize_patterns(self):
        """Initialize pattern matcher with configured patterns."""
        # Base internal contexts
        internal_contexts = [
            'from-internal', 'from-inside', 'from-inside-*',  # Include all from-inside patterns
            'from-internal-xfer', 'from-internal-noxfer',
            'from-internal-xfer-ringing', 'from-extension', 'from-local',
            'from-phone', 'from-phones', 'from-user', 'from-users',
            'ext-local', 'ext-group', 'ext-test', 'internal', 'internal-xfer',
            'default', 'phones', 'users', 'extensions', 'locals',
            'macro-dial', 'macro-dial-one', 'macro-exten-vm',
            'from-queue', 'from-ringgroup', 'followme',
            'app-*', 'timeconditions', 'ivr-*'
        ]
        
        # Base external contexts
        external_contexts = [
            'from-external', 'from-trunk', 'from-pstn', 'from-did',
            'from-outside', 'from-sip-external', 'from-dahdi', 'from-zaptel',
            'from-pri', 'from-e1', 'from-t1', 'from-isdn', 'from-fxo',
            'from-gateway', 'from-provider', 'from-carrier', 'from-telco',
            'from-itsp', 'from-voip', 'incoming', 'inbound', 'ext-did',
            'from-did-direct', 'from-trunk-sip', 'from-trunk-iax',
            'from-trunk-dahdi', 'custom-from-trunk'
        ]
        
        # Base outbound contexts
        outbound_contexts = [
            'from-internal', 'macro-dialout', 'outbound-allroutes',
            'outrt-*', 'outbound', 'dial-out', 'macro-dialout-trunk',
            'macro-dialout-dundi', 'macro-dialout-enum'
        ]
        
        # Add custom patterns from config
        internal_contexts.extend(self.config.custom_internal_contexts)
        external_contexts.extend(self.config.custom_external_contexts)
        outbound_contexts.extend(self.config.custom_outbound_contexts)
        
        # Compile patterns
        self.pattern_matcher.compile_patterns('internal', internal_contexts)
        self.pattern_matcher.compile_patterns('external', external_contexts)
        self.pattern_matcher.compile_patterns('outbound', outbound_contexts)
        
        # Add queue and IVR patterns
        self.pattern_matcher.compile_patterns('queue', self.config.queue_contexts)
        self.pattern_matcher.compile_patterns('ivr', self.config.ivr_contexts)
        
        # Add conference, parking, and voicemail patterns
        self.pattern_matcher.compile_patterns('conference', self.config.conference_contexts)
        self.pattern_matcher.compile_patterns('parking', self.config.parking_contexts)
        self.pattern_matcher.compile_patterns('voicemail', self.config.voicemail_contexts)
        
        logger.info("Call direction patterns initialized")
        
    def detect_direction(self, cdr_data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        Detect call direction with enhanced logic.
        
        Returns:
            Tuple of (direction, metadata)
            direction: 'inbound', 'outbound', 'internal'
            metadata: Additional information about the detection
        """
        channel = cdr_data.get('channel', '')
        context = cdr_data.get('context', '')
        dcontext = cdr_data.get('dcontext', '')
        src = cdr_data.get('src', '')
        dst = cdr_data.get('dst', '')
        lastapp = cdr_data.get('lastapp', '')
        lastdata = cdr_data.get('lastdata', '')
        
        # Normalize numbers before analysis
        src_normalized = self.number_analyzer.normalize_number(src)
        dst_normalized = self.number_analyzer.normalize_number(dst)
        
        metadata = {
            'src_type': self.number_analyzer.get_number_type(src),
            'dst_type': self.number_analyzer.get_number_type(dst),
            'src_normalized': src_normalized,
            'dst_normalized': dst_normalized,
            'transfer_type': self.transfer_detector.detect_transfer_chain(channel, lastapp, lastdata),
            'queue_involved': self.pattern_matcher.match_context(context, 'queue') or 
                            self.pattern_matcher.match_context(dcontext, 'queue'),
            'ivr_involved': self.pattern_matcher.match_context(context, 'ivr') or 
                           self.pattern_matcher.match_context(dcontext, 'ivr'),
            'conference_involved': self.pattern_matcher.match_context(context, 'conference') or 
                                 self.pattern_matcher.match_context(dcontext, 'conference'),
            'parking_involved': self.pattern_matcher.match_context(context, 'parking') or 
                              self.pattern_matcher.match_context(dcontext, 'parking'),
            'voicemail_involved': self.pattern_matcher.match_context(context, 'voicemail') or 
                                self.pattern_matcher.match_context(dcontext, 'voicemail')
        }
        
        # Handle anonymous/private calls
        if metadata['src_type'] == 'anonymous':
            # Anonymous calls are typically inbound
            if self.number_analyzer.is_extension(dst_normalized):
                logger.debug(f"Anonymous call to extension {dst}")
                return 'inbound', metadata
            else:
                # Anonymous to external is unusual, likely forwarded
                logger.debug(f"Anonymous call to external {dst}")
                metadata['likely_forwarded'] = True
                return 'inbound', metadata
        
        # Quick check: Extension to extension is always internal
        if (self.number_analyzer.is_extension(src_normalized) and 
            self.number_analyzer.is_extension(dst_normalized)):
            logger.debug(f"Extension-to-extension call: {src} -> {dst}")
            return 'internal', metadata
            
        # PRIORITY CHECK: dcontext takes precedence over channel analysis
        # If dcontext indicates internal routing, it's definitively outbound or internal
        if self.pattern_matcher.match_context(dcontext, 'internal'):
            logger.debug(f"DContext {dcontext} indicates internal routing - checking destination")
            if self.number_analyzer.is_extension(dst_normalized):
                logger.debug(f"Internal dcontext with extension dst {dst} = INTERNAL")
                return 'internal', metadata
            else:
                logger.debug(f"Internal dcontext with external dst {dst} = OUTBOUND")
                return 'outbound', metadata
        
        # Determine call origin (only if dcontext doesn't give us the answer)
        call_originated_internally = self._is_internal_origin(channel, context, src)
        metadata['originated_internally'] = call_originated_internally
        
        # Handle transfers specially
        if metadata['transfer_type']:
            return self._handle_transfer(call_originated_internally, dst, metadata)
            
        # Special handling for voicemail
        if metadata['voicemail_involved']:
            # If the destination is voicemail (*98, *97, etc) from internal, it's internal
            if call_originated_internally and dst_normalized.startswith('*'):
                return 'internal', metadata
            # If external caller is being sent to voicemail, it's still inbound
            elif not call_originated_internally:
                return 'inbound', metadata
        
        # Standard call direction logic
        if call_originated_internally:
            if self.number_analyzer.is_extension(dst_normalized):
                return 'internal', metadata
            else:
                # Check if it's actually going through an outbound route
                if self._is_outbound_route(dcontext):
                    return 'outbound', metadata
                # International calls are always outbound
                if self.number_analyzer.is_international(dst_normalized):
                    metadata['international'] = True
                    return 'outbound', metadata
                return 'outbound', metadata
        else:
            # External origin (SIP/trunk channels)
            # Note: We already checked for internal dcontext at the top with priority
            # So at this point, we know dcontext is NOT internal
            if self._is_outbound_route(dcontext):
                return 'outbound', metadata
            elif self.number_analyzer.is_extension(dst):
                return 'inbound', metadata
            else:
                # External to external - likely forwarded
                metadata['likely_forwarded'] = True
                return 'inbound', metadata
                
    def _is_internal_origin(self, channel: str, context: str, src: str) -> bool:
        """Determine if call originated internally."""
        # Local channel always internal
        if channel and channel.startswith('Local/'):
            return True
            
        # Check context patterns
        if self.pattern_matcher.match_context(context, 'internal'):
            return True
        elif self.pattern_matcher.match_context(context, 'external'):
            return False
            
        # Fallback to number analysis
        return self.number_analyzer.is_extension(src)
        
    def _is_outbound_route(self, context: str) -> bool:
        """Check if context indicates outbound routing."""
        return self.pattern_matcher.match_context(context, 'outbound')
        
    def _handle_transfer(self, originated_internally: bool, dst: str, 
                        metadata: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Handle transfer scenarios."""
        transfer_type = metadata['transfer_type']
        
        if transfer_type in ['blind_transfer', 'attended_transfer']:
            # Transfers maintain original direction
            if originated_internally:
                if self.number_analyzer.is_extension(dst):
                    return 'internal', metadata
                else:
                    return 'outbound', metadata
            else:
                return 'inbound', metadata
        else:
            # Other transfer types - use standard logic
            if self.number_analyzer.is_extension(dst):
                return 'internal', metadata
            elif originated_internally:
                return 'outbound', metadata
            else:
                return 'inbound', metadata


# Global detector instance
_detector: Optional[CallDirectionDetector] = None


def get_detector() -> CallDirectionDetector:
    """Get or create the global detector instance."""
    global _detector
    if _detector is None:
        _detector = CallDirectionDetector()
    return _detector


def detect_call_direction(cdr_data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Convenience function to detect call direction."""
    detector = get_detector()
    return detector.detect_direction(cdr_data)