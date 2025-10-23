"""
Pure Python client for interacting with the Jimeng (即梦) generation APIs.

This package rewrites the TypeScript implementation in Python so that
Python applications can talk to the remote service directly without
spawning the Node.js gateway.
"""

from .jimeng_service import JimengAPIService
from .service import JimengClient

__all__ = ["JimengClient", "JimengAPIService"]
