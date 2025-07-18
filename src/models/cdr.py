"""CDR and CEL data models."""

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import uuid4

# Use compatibility layer for Python 3.6 support
from utils.compat import dataclass, field, asdict


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
    
    # Multi-tenant fields
    api_key_id: Optional[int] = None  # Will be populated server-side from smart key
    custnum: Optional[int] = None     # Extracted from smart key
    
    # Optional Asterisk fields
    accountcode: Optional[str] = None
    userfield: Optional[str] = None
    sequence: Optional[int] = None
    linkedid: Optional[str] = None
    peeraccount: Optional[str] = None
    
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
    def from_ami_event(cls, event: Dict[str, Any]) -> 'CDR':
        """Create CDR from AMI Cdr event."""
        # Log the entire event for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Raw AMI CDR event: {event}")
        logger.info(f"AMI CDR event fields: {list(event.keys())}")
        
        # Parse the start time
        start_time = datetime.fromisoformat(event.get('StartTime', ''))
        
        # Calculate call type based on source and destination
        call_type = 'internal'
        if event.get('Source', '').startswith('+') or len(event.get('Source', '')) > 6:
            call_type = 'inbound'
        elif event.get('Destination', '').startswith('+') or len(event.get('Destination', '')) > 6:
            call_type = 'outbound'
        
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
        
        return cls(
            calldate=start_time,
            clid=event.get('CallerID', ''),
            src=event.get('Source', ''),
            dst=event.get('Destination', ''),
            dcontext=event.get('DestinationContext', ''),
            channel=event.get('Channel', ''),
            dstchannel=event.get('DestinationChannel', ''),
            lastapp=event.get('LastApplication', ''),
            lastdata=event.get('LastData', ''),
            duration=int(event.get('Duration', 0)),
            billsec=int(event.get('BillableSeconds', 0)),
            disposition=event.get('Disposition', 'NO ANSWER'),
            amaflags=cls._parse_amaflags(event.get('AMAFlags', 3)),
            uniqueid=event.get('UniqueID', ''),
            accountcode=event.get('AccountCode'),
            userfield=event.get('UserField'),
            sequence=int(sequence_val) if sequence_val else None,
            linkedid=linkedid,
            peeraccount=event.get('PeerAccount'),
            call_type=call_type
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
            extra=event.get('Extra')
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