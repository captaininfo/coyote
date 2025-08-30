# app/agent/obs.py
import time, uuid, contextlib
from typing import Optional

def _rid() -> str:
    return uuid.uuid4().hex[:8]

@contextlib.contextmanager
def trace(logger, name: str, **fields):
    rid = _rid()
    fields_str = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.debug("[%s] start %s %s", rid, name, fields_str)
    t0 = time.perf_counter()
    try:
        yield rid
        dt = (time.perf_counter() - t0) * 1000
        logger.debug("[%s] done  %s duration_ms=%.1f", rid, name, dt)
    except Exception as e:
        dt = (time.perf_counter() - t0) * 1000
        logger.exception("[%s] fail  %s duration_ms=%.1f error=%s", rid, name, dt, e)
        raise

def trunc(s: Optional[str], n=400) -> str:
    if not s: return ""
    s = s.replace("\n", " ")
    return (s[:n] + "…") if len(s) > n else s
