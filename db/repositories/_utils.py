"""Shared utility functions for repository modules."""

from typing import Any


def set_nested_value(data: dict, parts: list, val: Any) -> dict:
    """Recursively set a nested value in a dictionary"""
    if len(parts) == 1:
        data[parts[0]] = val
        return data

    # Ensure the parent key exists
    if parts[0] not in data:
        data[parts[0]] = {}
    elif not isinstance(data[parts[0]], dict):
        data[parts[0]] = {}

    # Recurse
    data[parts[0]] = set_nested_value(data[parts[0]], parts[1:], val)
    return data
