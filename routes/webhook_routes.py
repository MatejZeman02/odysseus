"""Backward-compat shim — canonical location is routes/webhook/webhook_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that legacy imports and monkeypatches continue to operate on that same object.
"""

import sys as _sys

from routes.webhook import webhook_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
