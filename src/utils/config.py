"""Configuration management for Asterisk connector."""

import os
import json
import logging
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CallDirectionConfig:
    """Configuration for call direction detection."""
    
    # Extension ranges
    extension_min_length: int = 2
    extension_max_length: int = 7
    
    # International number patterns
    international_prefixes: List[str] = field(default_factory=lambda: ['011', '00', '+'])
    e164_enabled: bool = True
    
    # Custom context patterns
    custom_internal_contexts: List[str] = field(default_factory=list)
    custom_external_contexts: List[str] = field(default_factory=list)
    custom_outbound_contexts: List[str] = field(default_factory=list)
    
    # Performance settings
    enable_pattern_cache: bool = True
    cache_ttl_seconds: int = 3600
    
    # Transfer detection
    detect_transfers: bool = True
    transfer_patterns: List[str] = field(default_factory=lambda: [
        'macro-dialout-trunk-predial-hook',
        'macro-dialout-dundi',
        'macro-dialout-enum',
        'macro-dialout',
        'transferer',
        'transferred'
    ])
    
    # Queue/IVR patterns
    queue_contexts: List[str] = field(default_factory=lambda: [
        'ext-queues',
        'from-queue',
        'queue-',
        'app-queue'
    ])
    ivr_contexts: List[str] = field(default_factory=lambda: [
        'ivr-',
        'ext-ivr',
        'from-ivr',
        'app-ivr'
    ])
    
    # Conference patterns
    conference_contexts: List[str] = field(default_factory=lambda: [
        'app-conference',
        'ext-conference',
        'from-conference',
        'conference-',
        'conf-',
        'meetme-'
    ])
    
    # Parking patterns
    parking_contexts: List[str] = field(default_factory=lambda: [
        'park-',
        'parkedcalls',
        'park-dial',
        'park-orphan',
        'park-return',
        'park-hints'
    ])
    
    # Voicemail patterns
    voicemail_contexts: List[str] = field(default_factory=lambda: [
        'macro-vm',
        'vm-',
        'ext-vm',
        'app-vmmain',
        'app-dialvm',
        'macro-exten-vm'
    ])
    
    @classmethod
    def from_env(cls) -> 'CallDirectionConfig':
        """Load configuration from environment variables."""
        config = cls()
        
        # Extension ranges
        if ext_min := os.getenv('ASTERISK_EXT_MIN_LENGTH'):
            config.extension_min_length = int(ext_min)
        if ext_max := os.getenv('ASTERISK_EXT_MAX_LENGTH'):
            config.extension_max_length = int(ext_max)
            
        # International settings
        if intl_prefixes := os.getenv('ASTERISK_INTL_PREFIXES'):
            config.international_prefixes = intl_prefixes.split(',')
        config.e164_enabled = os.getenv('ASTERISK_E164_ENABLED', 'true').lower() == 'true'
        
        # Custom contexts from JSON env var
        if custom_contexts := os.getenv('ASTERISK_CUSTOM_CONTEXTS'):
            try:
                contexts = json.loads(custom_contexts)
                config.custom_internal_contexts = contexts.get('internal', [])
                config.custom_external_contexts = contexts.get('external', [])
                config.custom_outbound_contexts = contexts.get('outbound', [])
            except json.JSONDecodeError:
                logger.warning("Invalid ASTERISK_CUSTOM_CONTEXTS JSON")
                
        # Performance settings
        config.enable_pattern_cache = os.getenv('ASTERISK_ENABLE_CACHE', 'true').lower() == 'true'
        if cache_ttl := os.getenv('ASTERISK_CACHE_TTL'):
            config.cache_ttl_seconds = int(cache_ttl)
            
        # Transfer detection
        config.detect_transfers = os.getenv('ASTERISK_DETECT_TRANSFERS', 'true').lower() == 'true'
        
        return config
    
    @classmethod
    def from_file(cls, filepath: str) -> 'CallDirectionConfig':
        """Load configuration from JSON file."""
        with open(filepath, 'r') as f:
            data = json.load(f)
        
        config = cls()
        
        # Update with file data
        if 'extension' in data:
            config.extension_min_length = data['extension'].get('min_length', 2)
            config.extension_max_length = data['extension'].get('max_length', 7)
            
        if 'international' in data:
            config.international_prefixes = data['international'].get('prefixes', config.international_prefixes)
            config.e164_enabled = data['international'].get('e164_enabled', True)
            
        if 'contexts' in data:
            config.custom_internal_contexts = data['contexts'].get('internal', [])
            config.custom_external_contexts = data['contexts'].get('external', [])
            config.custom_outbound_contexts = data['contexts'].get('outbound', [])
            
        if 'performance' in data:
            config.enable_pattern_cache = data['performance'].get('enable_cache', True)
            config.cache_ttl_seconds = data['performance'].get('cache_ttl', 3600)
            
        if 'transfer' in data:
            config.detect_transfers = data['transfer'].get('enabled', True)
            config.transfer_patterns = data['transfer'].get('patterns', config.transfer_patterns)
            
        if 'queue_ivr' in data:
            config.queue_contexts = data['queue_ivr'].get('queue_patterns', config.queue_contexts)
            config.ivr_contexts = data['queue_ivr'].get('ivr_patterns', config.ivr_contexts)
            
        return config


class ConfigManager:
    """Manages configuration loading and caching."""
    
    _instance: Optional['ConfigManager'] = None
    _config: Optional[CallDirectionConfig] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def get_config(self) -> CallDirectionConfig:
        """Get the current configuration."""
        if self._config is None:
            self._config = self._load_config()
        return self._config
    
    def reload_config(self):
        """Force reload configuration."""
        self._config = self._load_config()
        logger.info("Configuration reloaded")
    
    def _load_config(self) -> CallDirectionConfig:
        """Load configuration from file or environment."""
        config_file = os.getenv('ASTERISK_CONFIG_FILE')
        
        if config_file and os.path.exists(config_file):
            logger.info(f"Loading configuration from file: {config_file}")
            return CallDirectionConfig.from_file(config_file)
        else:
            logger.info("Loading configuration from environment variables")
            return CallDirectionConfig.from_env()


# Global config manager instance
config_manager = ConfigManager()