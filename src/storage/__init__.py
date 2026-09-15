"""Object-storage layer: one async protocol (`ObjectStore`) and its value types.

Importing this package never imports `opendal`; the real backend lives in `src.storage.opendal_store`
and imports the wheel lazily inside its factory.
"""

from src.storage.protocols import (
    ObjectRef,
    ObjectStat,
    ObjectStore,
    StorageError,
    StorageNotFound,
    StorageTooLarge,
    StorageUnavailable,
    StorageWriteFailed,
    validate_object_key,
)

__all__ = [
    "ObjectRef",
    "ObjectStat",
    "ObjectStore",
    "StorageError",
    "StorageNotFound",
    "StorageTooLarge",
    "StorageUnavailable",
    "StorageWriteFailed",
    "validate_object_key",
]
