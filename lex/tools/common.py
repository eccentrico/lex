"""Shared JSON envelope for all finance tool handlers."""
import json


def _ok(data) -> str:
    return json.dumps({"success": True, "data": data}, default=str)


def _err(msg: str) -> str:
    return json.dumps({"success": False, "error": str(msg)})
