"""
Python compatibility module for supporting both Python 3.6 and 3.7+
"""
import sys
import asyncio
from typing import Dict, List, Optional, Any

# Check Python version
PY37_PLUS = sys.version_info >= (3, 7)

# Handle dataclasses compatibility
if PY37_PLUS:
    from dataclasses import dataclass, field, asdict
else:
    try:
        # Try to import backport for Python 3.6
        from dataclasses import dataclass, field, asdict
    except ImportError:
        # Fallback to manual implementation
        def dataclass(cls):
            """Simple dataclass decorator for Python 3.6 without backport"""
            original_init = cls.__init__ if hasattr(cls, '__init__') else None
            
            def __init__(self, **kwargs):
                # Set attributes from kwargs
                for key, value in kwargs.items():
                    setattr(self, key, value)
                # Call original init if exists
                if original_init:
                    original_init(self)
            
            cls.__init__ = __init__
            return cls
        
        def field(default=None, default_factory=None):
            """Simple field implementation for Python 3.6"""
            if default_factory is not None:
                return default_factory()
            return default
        
        def asdict(obj):
            """Convert object to dict for Python 3.6"""
            result = {}
            for key in dir(obj):
                if not key.startswith('_'):
                    value = getattr(obj, key)
                    if not callable(value):
                        result[key] = value
            return result


def run_async(coro):
    """
    Run an async coroutine in a way compatible with both Python 3.6 and 3.7+
    
    Args:
        coro: The coroutine to run
    
    Returns:
        The result of the coroutine
    """
    if PY37_PLUS:
        # Python 3.7+: use asyncio.run()
        return asyncio.run(coro)
    else:
        # Python 3.6: use the old pattern
        loop = asyncio.get_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def create_dataclass_compat(name: str, fields: List[tuple], bases=()):
    """
    Create a dataclass-like class compatible with Python 3.6
    
    Args:
        name: Class name
        fields: List of (field_name, field_type, default_value) tuples
        bases: Base classes
    
    Returns:
        A new class
    """
    if PY37_PLUS:
        # Use real dataclasses for Python 3.7+
        from dataclasses import make_dataclass
        field_list = []
        for field_info in fields:
            if len(field_info) == 3:
                fname, ftype, fdefault = field_info
                field_list.append((fname, ftype, field(default=fdefault)))
            else:
                fname, ftype = field_info
                field_list.append((fname, ftype))
        return make_dataclass(name, field_list, bases=bases)
    else:
        # Create a simple class for Python 3.6
        class_dict = {}
        
        def __init__(self, **kwargs):
            for field_info in fields:
                fname = field_info[0]
                if len(field_info) == 3:
                    # Has default value
                    default_val = field_info[2]
                    setattr(self, fname, kwargs.get(fname, default_val))
                else:
                    # Required field
                    if fname not in kwargs:
                        raise TypeError(f"__init__() missing required argument: '{fname}'")
                    setattr(self, fname, kwargs[fname])
        
        def __repr__(self):
            field_strs = []
            for field_info in fields:
                fname = field_info[0]
                value = getattr(self, fname, None)
                field_strs.append(f"{fname}={repr(value)}")
            return f"{name}({', '.join(field_strs)})"
        
        class_dict['__init__'] = __init__
        class_dict['__repr__'] = __repr__
        
        return type(name, bases, class_dict)