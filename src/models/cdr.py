"""CDR and CEL data models."""

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import uuid4
import logging

# Use compatibility layer for Python 3.6 support
from utils.compat import dataclass, field, asdict

logger = logging.getLogger(__name__)

# Import enhanced call direction detector if available
try:
    from utils.call_direction import detect_call_direction
    USE_ENHANCED_DETECTOR = True
except ImportError:
    logger.warning("Enhanced call direction detector not available, using legacy detection")
    USE_ENHANCED_DETECTOR = False


@dataclass
class CDR:
    """Call Detail Record model."""
    
    # Required fields from Asterisk
    calldate: datetime
    clid: str
    src: str
    dst: str
    dcontext: str
    channel: str
    dstchannel: str
    lastapp: str
    lastdata: str
    duration: int
    billsec: int
    disposition: str
    amaflags: int
    uniqueid: str
    
    # Context field - important for call direction
    context: Optional[str] = None
    
    # Multi-tenant fields
    api_key_id: Optional[int] = None  # Will be populated server-side from smart key
    custnum: Optional[int] = None     # Extracted from smart key
    tenant: Optional[str] = None      # Extracted from channel/context patterns
    
    # Optional Asterisk fields
    accountcode: Optional[str] = None
    userfield: Optional[str] = None
    sequence: Optional[int] = None
    linkedid: Optional[str] = None
    peeraccount: Optional[str] = None
    
    # Enhanced SIP/Channel Information
    channel_state: Optional[str] = None
    channel_state_desc: Optional[str] = None
    connected_line_num: Optional[str] = None
    connected_line_name: Optional[str] = None
    language: Optional[str] = None
    
    # Audio/Codec Information
    format: Optional[str] = None
    read_format: Optional[str] = None
    write_format: Optional[str] = None
    codec: Optional[str] = None
    native_formats: Optional[str] = None
    
    # SIP User/Auth Information
    sip_from_user: Optional[str] = None
    sip_from_domain: Optional[str] = None
    sip_to_user: Optional[str] = None
    sip_to_domain: Optional[str] = None
    sip_call_id: Optional[str] = None
    sip_user_agent: Optional[str] = None
    sip_contact: Optional[str] = None
    auth_user: Optional[str] = None
    
    # Network/Transport Information
    remote_address: Optional[str] = None
    transport: Optional[str] = None
    local_address: Optional[str] = None
    
    # Call Quality Information
    rtcp_rtt: Optional[str] = None
    rtcp_jitter: Optional[str] = None
    rtcp_packet_loss: Optional[str] = None
    
    # Enhanced Hangup Information
    hangup_cause: Optional[str] = None
    hangup_source: Optional[str] = None
    answer_time: Optional[str] = None
    
    # Custom fields
    recording_file: Optional[str] = None
    recording_s3_url: Optional[str] = None
    call_type: Optional[str] = None  # inbound/outbound/internal
    queue_name: Optional[str] = None
    agent_id: Optional[str] = None
    
    # AI/ML fields
    risk_score: Optional[float] = None
    risk_factors: Optional[Dict[str, Any]] = None
    fraud_detected: Optional[bool] = None
    sentiment_score: Optional[float] = None
    sentiment_label: Optional[str] = None
    
    # Generated fields
    id: Optional[str] = field(default_factory=lambda: str(uuid4()))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API submission."""
        data = asdict(self)
        # Convert datetime to ISO format
        data['calldate'] = self.calldate.isoformat()
        # Remove None values
        return {k: v for k, v in data.items() if v is not None}
    
    @staticmethod
    def _parse_amaflags(amaflags_value) -> int:
        """Parse AMAFlags value which can be string or int."""
        if isinstance(amaflags_value, int):
            return amaflags_value
        
        # Handle string values
        amaflags_map = {
            'OMIT': 1,
            'BILLING': 2, 
            'DOCUMENTATION': 3,
            'DEFAULT': 3
        }
        
        if isinstance(amaflags_value, str):
            return amaflags_map.get(amaflags_value.upper(), 3)
        
        # Try to convert to int, fallback to 3
        try:
            return int(amaflags_value)
        except (ValueError, TypeError):
            return 3

    @classmethod
    def _determine_call_type(cls, channel: str, context: str, dcontext: str, src: str, dst: str, 
                           lastapp: Optional[str] = None, lastdata: Optional[str] = None) -> str:
        """Determine call type based on comprehensive analysis of channel, contexts and number patterns.
        
        Logic Priority:
        1. Check if call originated internally (Local/ channel or internal context)
        2. For internal origin: determine if calling internal extension or external number
        3. For external channels: check contexts to determine direction
        4. Fallback to number pattern analysis
        """
        # Normalize inputs
        channel = channel or ''
        src_context = context.lower() if context else ''
        dst_context = dcontext.lower() if dcontext else ''
        
        # Import logging at the top
        import logging
        logger = logging.getLogger(__name__)
        
        # PRIORITY 1: Check dcontext first - it definitively tells us where the call is going
        # If dcontext starts with internal patterns, it's either outbound or internal
        if dst_context and any(pattern in dst_context for pattern in ['from-internal', 'from-inside', 'from-phone', 'from-extension', 'from-local']):
            logger.debug(f"DContext {dst_context} indicates internal routing - priority check")
            # Check if destination is internal extension
            if dst and dst.isdigit() and len(dst) <= 7:
                logger.debug(f"Determined as INTERNAL: internal dcontext with extension dst {dst}")
                return 'internal'
            else:
                logger.debug(f"Determined as OUTBOUND: internal dcontext with external dst {dst}")
                return 'outbound'
        
        # Quick check: If both src and dst are internal extensions, it's always internal
        # This catches extension-to-extension calls regardless of channel/context
        if (src and src.isdigit() and len(src) <= 7 and 
            dst and dst.isdigit() and len(dst) <= 7):
            logger.debug(f"Determined as INTERNAL: both src {src} and dst {dst} are internal extensions")
            return 'internal'
        
        # Define comprehensive context patterns
        internal_contexts = [
            'from-internal',
            'from-inside',         # Custom internal context pattern
            'from-inside-redir',   # Redirected internal calls
            'from-internal-xfer',
            'from-internal-noxfer',
            'from-internal-xfer-ringing',
            'from-extension',
            'from-local',
            'from-phone',
            'from-phones',
            'from-user',
            'from-users',
            'ext-local',
            'ext-group',
            'ext-test',
            'internal',
            'internal-xfer',
            'default',
            'phones',
            'users',
            'extensions',
            'locals',
            'macro-dial',
            'macro-dial-one',
            'macro-exten-vm',
            'from-queue',
            'from-ringgroup',
            'followme',
            'app-',  # FreePBX app contexts
            'timeconditions',
            'ivr-'  # IVR contexts
        ]
        
        external_contexts = [
            'from-external',
            'from-trunk',
            'from-pstn',
            'from-did',
            'from-outside',
            'from-sip-external',
            'from-dahdi',
            'from-zaptel',
            'from-pri',
            'from-e1',
            'from-t1',
            'from-isdn',
            'from-fxo',
            'from-gateway',
            'from-provider',
            'from-carrier',
            'from-telco',
            'from-itsp',
            'from-voip',
            'incoming',
            'inbound',
            'ext-did',
            'from-did-direct',
            'from-trunk-sip',
            'from-trunk-iax',
            'from-trunk-dahdi',
            'custom-from-trunk'
        ]
        
        # Helper functions
        def is_internal_context(ctx):
            return any(pattern in ctx for pattern in internal_contexts)
        
        def is_external_context(ctx):
            return any(pattern in ctx for pattern in external_contexts)
        
        def is_internal_number(num):
            # Internal extensions are typically 2-7 digits
            # Also check for special feature codes starting with *
            if not num:
                # Empty numbers should not default to internal
                return False
            if num.startswith('*'):
                return True
            if num.isdigit() and 2 <= len(num) <= 7:
                return True
            return False
        
        # STEP 1: Check if call originated internally
        call_originated_internally = False
        
        # Check channel type
        if channel.startswith('Local/'):
            # Local channel always means internal origin
            call_originated_internally = True
        elif channel.startswith(('SIP/', 'PJSIP/', 'IAX2/', 'DAHDI/', 'SCCP/', 'Skinny/')):
            # These channels can be internal phones OR external trunks
            # Need to check the source context
            if is_internal_context(src_context):
                call_originated_internally = True
            elif is_external_context(src_context):
                call_originated_internally = False
            else:
                # No clear context - check if source is internal extension
                call_originated_internally = is_internal_number(src)
        
        # Log the analysis for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Call direction analysis: channel={channel}, context={src_context}, "
                    f"dcontext={dst_context}, src={src}, dst={dst}, "
                    f"originated_internally={call_originated_internally}")
        
        # STEP 2: If call originated internally, determine if it's internal or outbound
        if call_originated_internally:
            # Check destination
            if is_internal_number(dst):
                # Internal extension calling another internal extension
                logger.debug(f"Determined as INTERNAL: internal origin calling internal dst {dst}")
                return 'internal'
            else:
                # Internal extension calling external number
                logger.debug(f"Determined as OUTBOUND: internal origin calling external dst {dst}")
                return 'outbound'
        
        # STEP 3: Call originated externally - determine if it's truly inbound
        # or possibly an outbound leg of a call
        
        # Note: We already checked for internal dcontext at the top with priority
        # So at this point, we know dcontext is NOT internal
        
        # Check destination context for specific outbound patterns
        outbound_dst_contexts = [
            'macro-dialout',
            'outbound-allroutes',
            'outrt-',  # Outbound routes in FreePBX
            'outbound',
            'dial-out'
        ]
        
        if any(pattern in dst_context for pattern in outbound_dst_contexts):
            # This is likely an outbound call leg
            logger.debug(f"Determined as OUTBOUND: external channel but dst context {dst_context} indicates outbound")
            return 'outbound'
        
        # Check if destination is internal extension
        if is_internal_number(dst):
            # External calling internal = inbound
            logger.debug(f"Determined as INBOUND: external origin calling internal dst {dst}")
            return 'inbound'
        
        # Both numbers are external
        # This could be:
        # 1. Forwarded call (inbound)
        # 2. Outbound call through trunk (outbound)
        # 3. Transfer scenario
        
        # Check contexts for clues
        if is_external_context(src_context) and not is_internal_context(dst_context):
            # External to external through PBX = likely inbound (forwarded)
            logger.debug(f"Determined as INBOUND: external to external, likely forwarded")
            return 'inbound'
        
        # Default case
        logger.debug(f"Determined as INBOUND: default case - unable to determine definitively")
        return 'inbound'

    @classmethod
    def from_ami_event(cls, event: Dict[str, Any]) -> 'CDR':
        """Create CDR from AMI Cdr event."""
        # Log the entire event for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Raw AMI CDR event: {event}")
        logger.info(f"AMI CDR event fields: {list(event.keys())}")
        
        # Parse the start time
        start_time_str = event.get('StartTime', '')
        if start_time_str:
            # Parse the time, ensuring we handle timezone properly
            # If no timezone info, assume UTC (not local time)
            start_time = datetime.fromisoformat(start_time_str)
            if start_time.tzinfo is None:
                # No timezone, treat as UTC
                from datetime import timezone
                start_time = start_time.replace(tzinfo=timezone.utc)
        else:
            # No StartTime provided, extract from uniqueid/linkedid
            # Format: hostname-unixtime.sequence (e.g., 0242036ff24c-1755204113.5195625)
            uniqueid = event.get('UniqueID', '') or event.get('LinkedID', '')
            if uniqueid and '-' in uniqueid and '.' in uniqueid:
                try:
                    # Extract Unix timestamp from uniqueid
                    parts = uniqueid.split('-')
                    timestamp_part = parts[-1].split('.')[0]
                    unix_timestamp = int(timestamp_part)
                    # Convert Unix timestamp to datetime (Unix timestamps are UTC)
                    from datetime import timezone
                    start_time = datetime.fromtimestamp(unix_timestamp, tz=timezone.utc)
                    logger.debug(f"Extracted timestamp from uniqueid: {uniqueid} -> {start_time}")
                except (ValueError, IndexError) as e:
                    logger.warning(f"Failed to extract timestamp from uniqueid {uniqueid}: {e}")
                    # Fallback to current time in UTC
                    from datetime import timezone
                    start_time = datetime.now(timezone.utc)
            else:
                # Fallback to current time in UTC
                from datetime import timezone
                start_time = datetime.now(timezone.utc)
        
        # Extract tenant information using enhanced matcher
        try:
            # First try the enhanced tenant matcher if available
            from services.tenant_matcher import get_tenant_matcher
            matcher = get_tenant_matcher()
            
            # Convert AMI event to CDR format for matching
            cdr_dict = {
                'dst': event.get('Destination', ''),
                'dst_number': event.get('Destination', ''),
                'accountcode': event.get('AccountCode', ''),
                'linkedid': event.get('LinkedID', event.get('UniqueID', '')),
                'uniqueid': event.get('UniqueID', ''),
                'channel': event.get('Channel', ''),
                'dcontext': event.get('DestinationContext', ''),
                'context': event.get('Context', ''),
                'dstchannel': event.get('DestinationChannel', '')
            }
            
            # Note: In production, you would pass related CEL records here
            # For now, use empty list and rely on DID/accountcode matching
            tenant = matcher.match_cdr_with_cel(cdr_dict, [])
            
            if tenant:
                logger.debug(f"Extracted tenant '{tenant}' using enhanced matcher")
            else:
                # Fallback to traditional extraction
                from utils.tenant_extraction import extract_tenant_from_cdr
                tenant = extract_tenant_from_cdr(event)
                if tenant:
                    logger.debug(f"Extracted tenant '{tenant}' using fallback extraction")
        except ImportError as e:
            logger.warning(f"Enhanced tenant matcher not available: {e}")
            # Try fallback extraction
            try:
                from utils.tenant_extraction import extract_tenant_from_cdr
                tenant = extract_tenant_from_cdr(event)
            except ImportError:
                logger.warning("Tenant extraction module not available")
                tenant = None
        except Exception as e:
            logger.error(f"Error extracting tenant: {e}")
            tenant = None
        
        # Calculate call type
        if USE_ENHANCED_DETECTOR:
            # Use enhanced detector with full CDR data
            cdr_data = {
                'channel': event.get('Channel', ''),
                'context': event.get('Context', ''),
                'dcontext': event.get('DestinationContext', ''),
                'src': event.get('Source', ''),
                'dst': event.get('Destination', ''),
                'lastapp': event.get('LastApplication', ''),
                'lastdata': event.get('LastData', '')
            }
            call_type, call_metadata = detect_call_direction(cdr_data)
            logger.debug(f"Enhanced detection: {call_type}, metadata: {call_metadata}")
        else:
            # Fallback to legacy detection
            call_type = cls._determine_call_type(
                event.get('Channel', ''),
                event.get('Context', ''),
                event.get('DestinationContext', ''),
                event.get('Source', ''),
                event.get('Destination', ''),
                event.get('LastApplication', ''),
                event.get('LastData', '')
            )
        
        # Check for linkedid in various possible field names
        # Also check for malformed field names with extra spaces/characters
        linkedid = None
        sequence_val = None
        
        # Look for LinkedID and Sequence fields even if they have extra characters
        for key in event.keys():
            if 'LinkedID' in key:
                linkedid = event.get(key)
                logger.info(f"Found LinkedID in field '{key}' with value: {linkedid}")
            elif 'Sequence' in key and 'Sequence' != key:  # Avoid the normal field
                sequence_val = event.get(key)
                logger.info(f"Found Sequence in field '{key}' with value: {sequence_val}")
        
        # Fallback to normal field names if not found
        if not linkedid:
            linkedid = event.get('LinkedID') or event.get('linkedid')
        if not sequence_val:
            sequence_val = event.get('Sequence')
            if sequence_val:
                logger.info(f"Found Sequence with value: {sequence_val}")
        
        # Handle CallerID which might come as an array or string
        caller_id = event.get('CallerID', '')
        if isinstance(caller_id, list):
            # Take the first non-empty value if it's an array
            caller_id = next((cid for cid in caller_id if cid), '')
        
        return cls(
            # Required fields
            calldate=start_time,
            clid=caller_id,
            src=event.get('Source', ''),
            dst=event.get('Destination', ''),
            dcontext=event.get('DestinationContext', ''),
            context=event.get('Context', ''),  # Add source context
            channel=event.get('Channel', ''),
            dstchannel=event.get('DestinationChannel', ''),
            lastapp=event.get('LastApplication', ''),
            lastdata=event.get('LastData', ''),
            duration=int(event.get('Duration', 0)),
            billsec=int(event.get('BillableSeconds', 0)),
            disposition=event.get('Disposition', 'NO ANSWER'),
            amaflags=cls._parse_amaflags(event.get('AMAFlags', 3)),
            uniqueid=event.get('UniqueID', ''),
            
            # Optional Asterisk fields
            accountcode=event.get('AccountCode'),
            userfield=event.get('UserField'),
            sequence=int(sequence_val) if sequence_val else None,
            linkedid=linkedid,
            peeraccount=event.get('PeerAccount'),
            call_type=call_type,
            tenant=tenant,
            
            # Enhanced SIP/Channel Information
            channel_state=event.get('ChannelState'),
            channel_state_desc=event.get('ChannelStateDesc'),
            connected_line_num=event.get('ConnectedLineNum'),
            connected_line_name=event.get('ConnectedLineName'),
            language=event.get('Language'),
            
            # Audio/Codec Information
            format=event.get('Format'),
            read_format=event.get('ReadFormat'),
            write_format=event.get('WriteFormat'),
            codec=event.get('Codec'),
            native_formats=event.get('NativeFormats'),
            
            # SIP User/Auth Information
            sip_from_user=event.get('SIPFromUser', event.get('FromUser')),
            sip_from_domain=event.get('SIPFromDomain', event.get('FromDomain')),
            sip_to_user=event.get('SIPToUser', event.get('ToUser')),
            sip_to_domain=event.get('SIPToDomain', event.get('ToDomain')),
            sip_call_id=event.get('SIPCallID', event.get('CallID')),
            sip_user_agent=event.get('SIPUserAgent', event.get('UserAgent')),
            sip_contact=event.get('SIPContact', event.get('Contact')),
            auth_user=event.get('AuthUser', event.get('Username')),
            
            # Network/Transport Information
            remote_address=event.get('RemoteAddress', event.get('Address')),
            transport=event.get('Transport'),
            local_address=event.get('LocalAddress'),
            
            # Call Quality Information
            rtcp_rtt=event.get('RTCPRoundTripTime', event.get('RTT')),
            rtcp_jitter=event.get('RTCPJitter', event.get('Jitter')),
            rtcp_packet_loss=event.get('RTCPPacketLoss', event.get('PacketLoss')),
            
            # Enhanced Hangup Information
            hangup_cause=event.get('HangupCause'),
            hangup_source=event.get('HangupSource'),
            answer_time=event.get('AnswerTime')
        )


@dataclass
class CEL:
    """Channel Event Logging model."""
    
    # Required fields
    eventtime: datetime
    eventtype: str
    cid_name: str
    cid_num: str
    cid_ani: str
    cid_rdnis: str
    cid_dnid: str
    exten: str
    context: str
    channame: str
    appname: str
    appdata: str
    accountcode: str
    uniqueid: str
    linkedid: str
    peer: str
    userfield: str
    extra: Optional[str] = None
    
    # Multi-tenant fields
    api_key_id: Optional[int] = None  # Will be populated server-side from smart key
    custnum: Optional[int] = None     # Extracted from smart key
    tenant: Optional[str] = None      # Extracted from channel/context patterns
    
    # Generated fields
    id: Optional[str] = field(default_factory=lambda: str(uuid4()))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API submission."""
        data = asdict(self)
        data['eventtime'] = self.eventtime.isoformat()
        return {k: v for k, v in data.items() if v is not None}
    
    @classmethod
    def from_ami_event(cls, event: Dict[str, Any]) -> 'CEL':
        """Create CEL from AMI CEL event."""
        event_time = datetime.fromisoformat(event.get('EventTime', ''))
        
        # Extract tenant information
        try:
            from utils.tenant_extraction import extract_tenant_from_cel
            tenant = extract_tenant_from_cel(event)
            if tenant:
                logger.debug(f"Extracted tenant '{tenant}' from CEL")
        except ImportError:
            logger.warning("Tenant extraction module not available")
            tenant = None
        except Exception as e:
            logger.error(f"Error extracting tenant: {e}")
            tenant = None
        
        return cls(
            eventtime=event_time,
            eventtype=event.get('EventName', ''),
            cid_name=event.get('CallerIDName', ''),
            cid_num=event.get('CallerIDNum', ''),
            cid_ani=event.get('CallerIDani', ''),
            cid_rdnis=event.get('CallerIDrdnis', ''),
            cid_dnid=event.get('CallerIDdnid', ''),
            exten=event.get('Exten', ''),
            context=event.get('Context', ''),
            channame=event.get('Channel', ''),
            appname=event.get('Application', ''),
            appdata=event.get('AppData', ''),
            accountcode=event.get('AccountCode', ''),
            uniqueid=event.get('UniqueID', ''),
            linkedid=event.get('LinkedID', ''),
            peer=event.get('Peer', ''),
            userfield=event.get('UserField', ''),
            extra=event.get('Extra'),
            tenant=tenant
        )


@dataclass
class CDRBatch:
    """Batch of CDRs for efficient submission."""
    
    cdrs: List[CDR] = field(default_factory=list)
    cels: List[CEL] = field(default_factory=list)
    
    def add_cdr(self, cdr: CDR) -> None:
        """Add CDR to batch."""
        self.cdrs.append(cdr)
    
    def add_cel(self, cel: CEL) -> None:
        """Add CEL to batch."""
        self.cels.append(cel)
    
    def clear(self) -> None:
        """Clear the batch."""
        self.cdrs.clear()
        self.cels.clear()
    
    @property
    def size(self) -> int:
        """Total number of records in batch."""
        return len(self.cdrs) + len(self.cels)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert batch to dictionary for API submission."""
        return {
            'cdrs': [cdr.to_dict() for cdr in self.cdrs],
            'cels': [cel.to_dict() for cel in self.cels]
        }