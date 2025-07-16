"""Maps Asterisk CDR format to MQS expected format."""

from typing import Dict, Any, Optional
from models.cdr import CDR


class CDRMapper:
    """Maps between Asterisk CDR format and MQS API format."""
    
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
        # Map to MQS expected minimal fields
        mqs_cdr = {
            'src': cdr.src,
            'dst': cdr.dst,
            'call_id': cdr.uniqueid,  # Use uniqueid as call_id
            'call_type': cdr.call_type or 'internal',
            'duration': cdr.duration,
            
            # Additional useful fields that MQS might accept
            'billsec': cdr.billsec,
            'disposition': cdr.disposition,
            'calldate': cdr.calldate.isoformat(),
            'channel': cdr.channel,
            'dstchannel': cdr.dstchannel,
            'lastapp': cdr.lastapp,
            'accountcode': cdr.accountcode,
            'uniqueid': cdr.uniqueid,
            'linkedid': cdr.linkedid,
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