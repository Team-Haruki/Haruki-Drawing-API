"""Safe, reusable Render-IR subtrees.

``build_canvas_ir`` intentionally returns a live :class:`IRBuilder` plus an independent
``mem:`` image map.  That is convenient for one-off callers, but it is not sufficient for
composing several lowered canvases:

* every IRPainter starts its memory keys at ``m0``;
* ``IRBuilder.splice_root_children`` does not merge memory payloads;
* font aliases are scene-global and a conflicting alias must not be overwritten silently.

``NativeSubtree`` packages those pieces and splices them atomically.  All checks and deep
copies happen before the parent builder or caller-owned memory sink is mutated.  Memory
references are namespaced recursively across the complete node structure, including masks
and SDF fields, rather than only rewriting Image ``path`` fields.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any

from .canvas import build_canvas_ir
from .ir_builder import IRBuilder

_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_KNOWN_FONT_KEYS = frozenset({"dir", "default", "bold", "heavy", "emoji", "extra"})


class NativeSubtreeError(ValueError):
    """The subtree cannot be safely merged into the requested parent scene."""


def _rewrite_mem_references(value: Any, key_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        if not value.startswith("mem:"):
            return value
        source_key = value.removeprefix("mem:")
        target_key = key_map.get(source_key)
        if target_key is None:
            raise NativeSubtreeError(f"subtree references missing memory image {source_key!r}")
        return f"mem:{target_key}"
    if isinstance(value, list):
        return [_rewrite_mem_references(item, key_map) for item in value]
    if isinstance(value, tuple):
        return tuple(_rewrite_mem_references(item, key_map) for item in value)
    if isinstance(value, dict):
        rewritten: dict[Any, Any] = {}
        for key, item in value.items():
            new_key = _rewrite_mem_references(key, key_map)
            if new_key in rewritten:
                raise NativeSubtreeError(f"memory reference rewriting creates duplicate node key {new_key!r}")
            rewritten[new_key] = _rewrite_mem_references(item, key_map)
        return rewritten
    return deepcopy(value)


def _validate_font_merge(parent_fonts: dict[str, Any], subtree_fonts: dict[str, Any]) -> None:
    unknown = set(subtree_fonts).difference(_KNOWN_FONT_KEYS)
    if unknown:
        raise NativeSubtreeError(f"subtree contains unsupported font-map keys: {sorted(unknown)}")

    for key in ("dir", "default", "bold", "heavy", "emoji"):
        parent_value = parent_fonts.get(key)
        subtree_value = subtree_fonts.get(key)
        if parent_value is not None and subtree_value is not None and parent_value != subtree_value:
            raise NativeSubtreeError(f"font role {key!r} conflicts: parent={parent_value!r}, subtree={subtree_value!r}")

    parent_extra = parent_fonts.get("extra", {})
    subtree_extra = subtree_fonts.get("extra", {})
    if not isinstance(parent_extra, dict) or not isinstance(subtree_extra, dict):
        raise NativeSubtreeError("font-map 'extra' entries must be mappings")
    for name, path in subtree_extra.items():
        existing = parent_extra.get(name)
        if existing is not None and existing != path:
            raise NativeSubtreeError(f"extra font {name!r} conflicts: parent={existing!r}, subtree={path!r}")


@dataclass(frozen=True, slots=True)
class NativeSubtree:
    """A lowered canvas that can be safely embedded in another Render-IR scene.

    ``nodes`` includes the lowered scene background, when present, as its first node so the
    original background-before-content order is preserved.  Callers must splice into a
    same-sized scene or an isolated subscene matching :attr:`size`; nodes such as ``ImageBg``
    and ``TriangleBg`` intentionally use the active canvas dimensions.
    """

    size: tuple[int, int]
    nodes: tuple[dict[str, Any], ...]
    fonts: dict[str, Any]
    mem_images: dict[str, Any]
    assets_base_dir: str

    def __post_init__(self) -> None:
        width, height = int(self.size[0]), int(self.size[1])
        if width <= 0 or height <= 0:
            raise NativeSubtreeError(f"subtree size must be positive, got {width}x{height}")
        object.__setattr__(self, "size", (width, height))
        object.__setattr__(self, "nodes", tuple(deepcopy(list(self.nodes))))
        object.__setattr__(self, "fonts", deepcopy(dict(self.fonts)))
        object.__setattr__(self, "mem_images", deepcopy(dict(self.mem_images)))
        object.__setattr__(self, "assets_base_dir", str(self.assets_base_dir))

    @property
    def asset_backed(self) -> bool:
        return not self.mem_images

    @classmethod
    def from_canvas(
        cls,
        canvas: Any,
        *,
        require_asset_backed: bool = False,
        bg_hour: float | None = None,
        export_format: str | None = None,
        renderer_options: dict[str, Any] | None = None,
    ) -> NativeSubtree:
        """Lower ``canvas`` through IRPainter and capture a detached subtree."""

        options = dict(renderer_options or {})
        if bg_hour is not None:
            options["bg_hour"] = bg_hour
        if export_format is not None:
            options["export_format"] = export_format
        builder, mem_images = build_canvas_ir(canvas, **options)
        scene = builder.build()
        root = scene.get("root")
        if not isinstance(root, dict) or not isinstance(root.get("children"), list):
            raise NativeSubtreeError("lowered canvas has an invalid root")
        canvas_spec = scene.get("canvas")
        if not isinstance(canvas_spec, dict):
            raise NativeSubtreeError("lowered canvas has no canvas dimensions")

        nodes: list[dict[str, Any]] = []
        background = scene.get("background")
        if background is not None:
            if not isinstance(background, dict):
                raise NativeSubtreeError("lowered canvas has an invalid background node")
            nodes.append(background)
        nodes.extend(root["children"])
        subtree = cls(
            size=(int(canvas_spec.get("width", 0)), int(canvas_spec.get("height", 0))),
            nodes=tuple(nodes),
            fonts=scene.get("fonts") or {},
            mem_images=mem_images,
            assets_base_dir=str(scene.get("assets_base_dir") or ""),
        )
        if require_asset_backed and not subtree.asset_backed:
            raise NativeSubtreeError(
                f"asset-backed subtree required, but lowering produced {len(subtree.mem_images)} memory image(s)"
            )
        return subtree

    def splice_into(
        self,
        parent: IRBuilder,
        mem_sink: dict[str, Any],
        *,
        namespace: str,
        require_asset_backed: bool = False,
    ) -> dict[str, str]:
        """Atomically append this subtree and merge its namespaced memory images.

        Returns ``{source_key: namespaced_key}``.  A namespace is single-use for a memory-
        carrying subtree: an existing sink key is a hard collision even when its payload happens
        to compare equal.
        """

        if not isinstance(parent, IRBuilder):
            raise TypeError("parent must be an IRBuilder")
        if not isinstance(mem_sink, dict):
            raise TypeError("mem_sink must be a dict")
        if not isinstance(namespace, str) or not _NAMESPACE_RE.fullmatch(namespace):
            raise NativeSubtreeError("namespace must contain only ASCII letters, digits, dot, underscore, or hyphen")
        if require_asset_backed and self.mem_images:
            raise NativeSubtreeError(
                f"asset-backed subtree required, but subtree carries {len(self.mem_images)} memory image(s)"
            )

        parent_scene = parent.build()
        parent_assets = str(parent_scene.get("assets_base_dir") or "")
        if parent_assets != self.assets_base_dir:
            raise NativeSubtreeError(
                f"asset root conflicts: parent={parent_assets!r}, subtree={self.assets_base_dir!r}"
            )
        parent_fonts = parent_scene.get("fonts")
        if not isinstance(parent_fonts, dict):
            raise NativeSubtreeError("parent builder has an invalid font map")
        _validate_font_merge(parent_fonts, self.fonts)

        key_map = {str(source_key): f"{namespace}:{source_key}" for source_key in self.mem_images}
        collisions = sorted(target_key for target_key in key_map.values() if target_key in mem_sink)
        if collisions:
            raise NativeSubtreeError(f"memory namespace collision: {collisions}")

        # Deep-copy and recursively validate/rewrite every node before mutating either target.
        rewritten_nodes = tuple(_rewrite_mem_references(node, key_map) for node in self.nodes)
        namespaced_mem = {key_map[str(key)]: deepcopy(payload) for key, payload in self.mem_images.items()}

        prepared = IRBuilder(
            self.size[0],
            self.size[1],
            assets_base_dir=self.assets_base_dir,
            font_dir=str(self.fonts.get("dir") or ""),
            default_font=str(self.fonts.get("default") or ""),
            bold_font=str(self.fonts.get("bold") or ""),
            heavy_font=self.fonts.get("heavy"),
            emoji_font=self.fonts.get("emoji"),
            extra_fonts=deepcopy(self.fonts.get("extra")) if self.fonts.get("extra") else None,
        )
        prepared._root_children.extend(rewritten_nodes)

        # All operations below are deterministic built-in dict/list updates after complete
        # preflight. IRBuilder performs no validation or callbacks while splicing.
        mem_sink.update(namespaced_mem)
        parent.splice_root_children(prepared)
        return key_map


def lower_canvas_subtree(
    canvas: Any,
    *,
    require_asset_backed: bool = False,
    bg_hour: float | None = None,
    export_format: str | None = None,
    renderer_options: dict[str, Any] | None = None,
) -> NativeSubtree:
    """Functional spelling of :meth:`NativeSubtree.from_canvas`."""

    return NativeSubtree.from_canvas(
        canvas,
        require_asset_backed=require_asset_backed,
        bg_hour=bg_hour,
        export_format=export_format,
        renderer_options=renderer_options,
    )


__all__ = [
    "NativeSubtree",
    "NativeSubtreeError",
    "lower_canvas_subtree",
]
