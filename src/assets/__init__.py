"""Asset mirror layer (read side of C3/E5).

`keymap` is the single place in this repo that turns a logical `asset/...` key into a bucket object key;
`version` supplies the manifest-version path segment. Importing this package never imports `opendal`.
"""

from src.assets.keymap import DEFAULT_VERSION, AssetKeyMap, MappedAsset, sanitize_version
from src.assets.version import FileVersion, ManifestVersionSource, StaticVersion, build_version_source

__all__ = [
    "DEFAULT_VERSION",
    "AssetKeyMap",
    "FileVersion",
    "ManifestVersionSource",
    "MappedAsset",
    "StaticVersion",
    "build_version_source",
    "sanitize_version",
]
