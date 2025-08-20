"""Maps Asterisk CDR format to MQS expected format."""

from typing import Dict, Any, Optional
from models.cdr import CDR


class CDRMapper:
    """Maps between Asterisk CDR format and MQS API format."""
    
    @staticmethod
    def parse_caller_name(clid: str) -> Optional[str]:
        """
        Parse caller name from CLID format.
        
        Examples:
        - "John Doe" <4165551234> -> John Doe
        - "314-RE-24-Trimaxx Rlty-" <4163170972> -> 314-RE-24-Trimaxx Rlty
        - <4165551234> -> None
        - 4165551234 -> None
        
        Args:
            clid: Caller ID string in Asterisk format
            
        Returns:
            Extracted caller name or None if not found
        """
        if not clid:
            return None
            
        # Look for pattern: "Name" <number> or Name <number>
        # Match everything before the opening angle bracket
        angle_bracket_pos = clid.find('<')
        if angle_bracket_pos > 0:
            name = clid[:angle_bracket_pos].strip()
            # Remove surrounding quotes if present
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1]
            # Remove trailing dash if present
            if name.endswith('-'):
                name = name[:-1].strip()
            # Return name if it's not empty
            return name if name else None
        
        return None
    
    @staticmethod
    def to_mqs_format(cdr: CDR, host_info: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Convert Asterisk CDR to MQS expected format.
        
        Args:
            cdr: Asterisk CDR object
            host_info: Optional host/server information
            
        Returns:
            Dictionary in MQS expected format
        """
        # Parse caller name from CLID
        caller_name = CDRMapper.parse_caller_name(cdr.clid)
        
        # Map to MQS expected minimal fields
        mqs_cdr = {
            'src': cdr.src,
            'dst': cdr.dst,
            'src_number': cdr.src,  # API expects src_number
            'dst_number': cdr.dst,  # API expects dst_number
            'call_id': cdr.uniqueid,  # Use uniqueid as call_id
            'call_type': cdr.call_type or 'internal',
            'direction': cdr.call_type or 'internal',  # Send direction field for API consistency
            'duration': cdr.duration,
            
            # Additional useful fields that MQS might accept
            'billsec': cdr.billsec,
            'disposition': cdr.disposition,
            'calldate': cdr.calldate.isoformat() if cdr.calldate.tzinfo else cdr.calldate.isoformat() + 'Z',
            'started_at': cdr.calldate.isoformat() if cdr.calldate.tzinfo else cdr.calldate.isoformat() + 'Z',  # API expects started_at, not calldate
            'channel': cdr.channel,
            'dstchannel': cdr.dstchannel,
            'lastapp': cdr.lastapp,
            'accountcode': cdr.accountcode,
            'uniqueid': cdr.uniqueid,
            'linkedid': cdr.linkedid,
            'sequence': cdr.sequence,
            'context': cdr.context,    # Include source context
            'dcontext': cdr.dcontext,  # Include destination context
            
            # Include parsed caller name for display
            'src_name': caller_name,
            # Keep full CLID for backwards compatibility
            'clid': cdr.clid,
            
            # Include tenant if extracted
            'tenant': cdr.tenant,
        }
        
        # Add host information if provided
        if host_info:
            mqs_cdr.update({
                'host_id': host_info.get('host_id'),
                'host_name': host_info.get('host_name'),
                'host_ip': host_info.get('host_ip'),
            })
        
        # Add queue information if available
        if cdr.queue_name:
            mqs_cdr['queue_name'] = cdr.queue_name
            
        # Add agent information if available
        if cdr.agent_id:
            mqs_cdr['agent_id'] = cdr.agent_id
            
        # Remove None values
        return {k: v for k, v in mqs_cdr.items() if v is not None}
    
    @staticmethod
    def batch_to_mqs_format(cdrs: list, host_info: Optional[Dict[str, str]] = None) -> list:
        """
        Convert a batch of CDRs to MQS format.
        
        Args:
            cdrs: List of CDR objects
            host_info: Optional host/server information
            
        Returns:
            List of dictionaries in MQS format
        """
        return [CDRMapper.to_mqs_format(cdr, host_info) for cdr in cdrs]