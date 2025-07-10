from functools import singledispatch

from pydantic import BaseModel


@singledispatch
def format_object_for_export(_object, keys_to_remove: set[str]):
    """
    Recursively remove keys from dictionaries on a nested object of pydantic models, dicts, and lists.
    Pydantic models are converted to dictionaries before processing.

    Args:
        _object: Pydantic model or dict
        keys_to_remove: set of keys to remove from the object

    Returns:
        cleaned_object
    """
    return _object


@format_object_for_export.register
def _(_object: dict, keys_to_remove: set[str]):
    return {
        k: format_object_for_export(v, keys_to_remove)
        for k, v in _object.items()
        if k not in keys_to_remove
    }


@format_object_for_export.register
def _(_object: BaseModel, keys_to_remove: set[str]):
    return format_object_for_export(_object.dict(), keys_to_remove)


@format_object_for_export.register
def _(_object: list, keys_to_remove: set[str]):
    return [format_object_for_export(item, keys_to_remove) for item in _object]
