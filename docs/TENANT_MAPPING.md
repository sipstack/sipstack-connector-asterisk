# Tenant Mapping Configuration Guide

## Overview

The Asterisk connector now supports multiple efficient methods for tenant identification at scale:

1. **DID-to-Tenant Mapping** - Map destination phone numbers to specific tenants
2. **AccountCode Mapping** - Use Asterisk account codes to identify tenants
3. **CEL Correlation** - Match CDR with CEL events for enhanced tenant extraction
4. **Pattern-Based Extraction** - Extract from channel names and contexts (existing method)

## Configuration Methods

### 1. Environment Variables (Recommended for Production)

```bash
# DID to tenant mappings
DID_TENANT_MAP="14164775498:gconnect,18665137797:telair,16478743709:cpapliving"

# AccountCode to tenant mappings  
ACCOUNTCODE_TENANT_MAP="GC:gconnect,TL:telair,CP:cpapliving"

# Known trunk names to filter out (prevent trunk names being used as tenant)
KNOWN_TRUNKS="ca1,ca2,us1,us2,sbc-ca1,sbc-ca2,sbc-us1,sbc-us2"

# Optional: Path to JSON config file
DID_TENANT_CONFIG="/etc/asterisk-connector/tenant_mapping.json"
```

### 2. JSON Configuration File

Create `/etc/asterisk-connector/tenant_mapping.json`:

```json
{
  "did_mappings": {
    "14164775498": "gconnect",
    "18665137797": "telair",
    "16478743709": "cpapliving"
  },
  "accountcode_mappings": {
    "GC": "gconnect",
    "TL": "telair",
    "CP": "cpapliving"
  }
}
```

## How It Works

### Matching Priority

The tenant matcher uses the following priority order:

1. **DID Lookup** - Check if destination number matches a configured DID
2. **AccountCode** - Check if CDR accountcode matches configured mappings
3. **CEL Correlation** - Find related CEL events by LinkedID and extract tenant
4. **Pattern Extraction** - Fallback to regex pattern matching on channels/contexts

### Performance Features

- **In-Memory Caching** - LinkedID-to-tenant mappings cached for 5 minutes
- **Batch Processing** - Process multiple CDRs with CEL correlation in single pass
- **O(1) Lookups** - Hash maps for DID and AccountCode lookups
- **CEL Indexing** - Build LinkedID index for efficient correlation

### CEL Correlation

The system automatically correlates CDR records with CEL (Channel Event Log) events:

- CHAN_START events often contain the most complete tenant information
- cid_dnid field in CEL contains the actual DID for inbound calls
- Early channel events show tenant before trunk manipulation

## Monitoring

The tenant matcher provides statistics:

```python
stats = matcher.get_stats()
# Returns:
{
  'cache_hits': 1523,
  'cache_misses': 234,
  'cel_matches': 189,
  'did_matches': 1245,
  'accountcode_matches': 89,
  'linkedid_matches': 45,
  'cache_hit_rate': 86.67,
  'cache_size': 5000,
  'did_mappings': 25,
  'accountcode_mappings': 8
}
```

## Best Practices

### For High Volume (>100K calls/day)

1. **Use DID Mappings** - Most efficient method, O(1) lookup
2. **Configure AccountCodes** - Set in Asterisk dialplan for customer trunks
3. **Enable Caching** - Default 5-minute TTL handles call bursts
4. **Monitor Cache Size** - Auto-pruning keeps last 10K entries

### AccountCode Configuration in Asterisk

Set accountcode in your Asterisk dialplan:

```asterisk
; For customer GConnect
exten => _X.,1,Set(CDR(accountcode)=GC)
exten => _X.,n,Dial(SIP/${EXTEN}@trunk)

; For customer Telair
exten => _X.,1,Set(CDR(accountcode)=TL)
exten => _X.,n,Dial(SIP/${EXTEN}@trunk)
```

### DID Assignment Best Practices

1. Normalize all DIDs to 10-digit format (remove country code)
2. Include all DIDs assigned to each customer
3. Update mappings when provisioning new numbers
4. Use JSON config file for large deployments (>100 DIDs)

## Troubleshooting

### Enable Debug Logging

```bash
export LOG_LEVEL=DEBUG
```

### Common Issues

1. **Tenant not detected** - Check DID is in mapping, verify AccountCode is set
2. **Wrong tenant** - Check KNOWN_TRUNKS includes all trunk names
3. **Performance issues** - Monitor cache hit rate, should be >80%

## Migration from Legacy

Existing installations will continue to work with pattern-based extraction. The new methods are additional and take priority when configured.

To migrate:
1. Start with DID mappings for most common numbers
2. Add AccountCode mappings for major customers
3. Monitor unmatched calls and add mappings as needed

## Examples

### Small Deployment (<10 customers)

Use environment variables:
```bash
DID_TENANT_MAP="14164775498:customer1,18665137797:customer2"
ACCOUNTCODE_TENANT_MAP="C1:customer1,C2:customer2"
```

### Large Deployment (>50 customers)

Use JSON config file with hundreds of DID mappings and accountcode prefixes.

### Multi-Tenant PBX

Combine all methods:
- DIDs for main numbers
- AccountCodes for trunk identification  
- Extension ranges for internal routing
- Pattern matching for complex scenarios