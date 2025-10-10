"""
Debug utilities for inspecting LangChain chains
"""
import logging
from typing import Any, Dict

log = logging.getLogger("coyote.agent")

class DebugRunnable:
    """Wrapper that logs inputs/outputs of any runnable"""
    
    def __init__(self, name: str):
        self.name = name
    
    def __call__(self, inputs: Any) -> Any:
        log.info("="*70)
        log.info(f"🔍 DEBUG [{self.name}] INPUTS:")
        log.info("="*70)
        
        if isinstance(inputs, dict):
            for key, value in inputs.items():
                value_str = str(value)
                log.info(f"  {key}: {value_str[:500]}")
                if len(value_str) > 500:
                    log.info(f"    ... [+{len(value_str)-500} more chars]")
        else:
            log.info(f"  Type: {type(inputs)}")
            log.info(f"  Value: {str(inputs)[:500]}")
        
        log.info("="*70)
        return inputs