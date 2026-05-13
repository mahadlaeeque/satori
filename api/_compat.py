"""
FastAPI ↔ legacy Flask compatibility shim
==========================================
Lets us lift route function bodies from the old app.py into FastAPI handlers
with minimal edits. Three patterns are emulated:

  * request.args.get(name, default[, type=int])
  * request.get_json()
  * return jsonify(...)
  * return jsonify(...), 500
"""

from typing import Any, Dict, Optional
from fastapi import Request
from fastapi.responses import JSONResponse


class _FlaskArgs:
    """Mimics werkzeug.MultiDict subset used by route bodies."""
    def __init__(self, qp): self._qp = qp
    def get(self, key, default=None, type=None):
        v = self._qp.get(key, default)
        if type and v is not None:
            try: return type(v)
            except Exception: return default
        return v


class FlaskReq:
    """Minimal Flask request shim. Wraps a FastAPI/Starlette Request."""
    def __init__(self, fastapi_request: Request, json_body: Optional[Dict[str, Any]] = None):
        self.args = _FlaskArgs(fastapi_request.query_params)
        self._json = json_body or {}

    def get_json(self, force: bool = False, silent: bool = False):
        return self._json


def jsonify(*args, **kwargs):
    """Flask's jsonify, simplified — returns a plain dict for FastAPI to serialise."""
    if kwargs and not args:
        return dict(kwargs)
    if len(args) == 1:
        return args[0]
    return dict(args)


async def adapt_body(request: Request) -> Dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        return {}


def to_response(value):
    """Convert a legacy `(body, status)` tuple or bare body into a FastAPI response."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], int):
        body, status = value
        return JSONResponse(content=body, status_code=status)
    return value
