from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys
import tempfile
from typing import Any

DEFAULT_SSH_HOST = "root@100.111.213.59"
DEFAULT_REMOTE_ROOT = "/data/HarukiServices/data/drawing"
DEFAULT_REMOTE_GAME_ASSETS_ROOT = "/data/HarukiServices/data/assets"
DEFAULT_LOCAL_ROOT = "data"
DEFAULT_FONT_FILES = (
    "SourceHanSansSC-Regular.otf",
    "SourceHanSansSC-Bold.otf",
    "SourceHanSansSC-Heavy.otf",
    "TwitterColorEmoji-SVGinOT.ttf",
)

TOP_LEVEL_ASSET_FIELDS = (
    "background_img_path",
    "term_limited_icon_path",
    "fes_limited_icon_path",
)
THUMBNAIL_ASSET_FIELDS = (
    "card_thumbnail_path",
    "frame_img_path",
    "attr_img_path",
    "rare_img_path",
    "train_rank_img_path",
    "birthday_icon_path",
)


class AssetPathError(ValueError):
    pass


def _as_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be an object")
    return value


def normalize_asset_path(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AssetPathError(f"{field} must be a string asset path")
    path = value.strip()
    if not path:
        return None
    if "\\" in path:
        raise AssetPathError(f"{field} must use forward slash asset paths: {path!r}")
    if "\n" in path or "\r" in path:
        raise AssetPathError(f"{field} must not contain newlines: {path!r}")

    posix = PurePosixPath(path)
    windows = PureWindowsPath(path)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise AssetPathError(f"{field} must be relative to the asset root: {path!r}")
    if ".." in posix.parts:
        raise AssetPathError(f"{field} must not contain '..': {path!r}")
    if posix == PurePosixPath("."):
        return None
    return posix.as_posix()


def _append_path(paths: list[str], value: Any, *, field: str) -> None:
    path = normalize_asset_path(value, field=field)
    if path is not None:
        paths.append(path)


def extract_card_list_asset_paths(payload: dict[str, Any], *, include_fonts: bool = True) -> list[str]:
    paths: list[str] = []

    for field in TOP_LEVEL_ASSET_FIELDS:
        _append_path(paths, payload.get(field), field=field)

    cards = payload.get("cards", [])
    if not isinstance(cards, list):
        raise TypeError("cards must be a list")

    for card_index, raw_card in enumerate(cards):
        card = _as_mapping(raw_card, field=f"cards[{card_index}]")
        for skill_field in ("skill", "special_skill_info"):
            skill = card.get(skill_field)
            if skill is None:
                continue
            skill_map = _as_mapping(skill, field=f"cards[{card_index}].{skill_field}")
            _append_path(
                paths,
                skill_map.get("skill_type_icon_path"),
                field=f"cards[{card_index}].{skill_field}.skill_type_icon_path",
            )

        thumbnails = card.get("thumbnail_info", [])
        if thumbnails is None:
            continue
        if not isinstance(thumbnails, list):
            raise TypeError(f"cards[{card_index}].thumbnail_info must be a list")
        for thumb_index, raw_thumb in enumerate(thumbnails):
            thumb = _as_mapping(raw_thumb, field=f"cards[{card_index}].thumbnail_info[{thumb_index}]")
            for field in THUMBNAIL_ASSET_FIELDS:
                _append_path(
                    paths,
                    thumb.get(field),
                    field=f"cards[{card_index}].thumbnail_info[{thumb_index}].{field}",
                )

    if include_fonts:
        paths.extend(DEFAULT_FONT_FILES)

    return list(dict.fromkeys(paths))


def load_payload(path: Path) -> dict[str, Any]:
    with path.open("rb") as fp:
        payload = json.load(fp)
    return _as_mapping(payload, field=str(path))


def write_manifest(paths: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{asset_path}\n" for asset_path in paths), encoding="utf-8")


def split_remote_asset_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    drawing_paths: list[str] = []
    game_asset_paths: list[str] = []
    for path in paths:
        if path.startswith("asset/"):
            game_asset_paths.append(path.removeprefix("asset/"))
        else:
            drawing_paths.append(path)
    return drawing_paths, game_asset_paths


def build_rsync_command(
    *,
    ssh_host: str,
    ssh_port: int | None,
    remote_root: str,
    local_root: Path,
    manifest_path: Path,
    dry_run: bool,
) -> list[str]:
    remote_root = remote_root.rstrip("/")
    ssh_command = "ssh -o BatchMode=yes -o ConnectTimeout=15"
    if ssh_port is not None:
        ssh_command = f"{ssh_command} -p {ssh_port}"
    command = [
        "rsync",
        "-aR",
        "--files-from",
        str(manifest_path),
        "-e",
        ssh_command,
    ]
    if dry_run:
        command.append("--dry-run")
    command.extend([f"{ssh_host}:{remote_root}/", str(local_root)])
    return command


def build_rsync_commands(
    *,
    ssh_host: str,
    ssh_port: int | None,
    remote_root: str,
    remote_game_assets_root: str,
    local_root: Path,
    drawing_manifest_path: Path | None,
    game_assets_manifest_path: Path | None,
    dry_run: bool,
) -> list[list[str]]:
    commands: list[list[str]] = []
    if drawing_manifest_path is not None:
        commands.append(
            build_rsync_command(
                ssh_host=ssh_host,
                ssh_port=ssh_port,
                remote_root=remote_root,
                local_root=local_root,
                manifest_path=drawing_manifest_path,
                dry_run=dry_run,
            )
        )
    if game_assets_manifest_path is not None:
        commands.append(
            build_rsync_command(
                ssh_host=ssh_host,
                ssh_port=ssh_port,
                remote_root=remote_game_assets_root,
                local_root=local_root / "asset",
                manifest_path=game_assets_manifest_path,
                dry_run=dry_run,
            )
        )
    return commands


def run_rsync_commands(commands: list[list[str]]) -> int:
    for command in commands:
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            return result.returncode
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync the minimal asset set needed by a /api/pjsk/card/list JSON payload.",
    )
    parser.add_argument("--payload-file", required=True, type=Path, help="CardListRequest JSON payload file.")
    parser.add_argument("--ssh-host", default=DEFAULT_SSH_HOST, help="SSH host used by rsync.")
    parser.add_argument("--ssh-port", type=int, help="Optional SSH port used by rsync.")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT, help="Remote Drawing API data root.")
    parser.add_argument(
        "--remote-game-assets-root",
        default=DEFAULT_REMOTE_GAME_ASSETS_ROOT,
        help="Remote root for asset/{region}-assets game assets.",
    )
    parser.add_argument("--local-root", default=Path(DEFAULT_LOCAL_ROOT), type=Path, help="Local asset root.")
    parser.add_argument("--manifest-out", type=Path, help="Write the collected original asset path list.")
    parser.add_argument("--list-only", action="store_true", help="Only print the required asset paths.")
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run to rsync.")
    parser.add_argument(
        "--include-fonts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include configured font files from the asset root.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = load_payload(args.payload_file)
    paths = extract_card_list_asset_paths(payload, include_fonts=args.include_fonts)

    if args.list_only:
        sys.stdout.write("".join(f"{asset_path}\n" for asset_path in paths))
        return 0

    args.local_root.mkdir(parents=True, exist_ok=True)
    if args.manifest_out:
        write_manifest(paths, args.manifest_out)

    drawing_paths, game_asset_paths = split_remote_asset_paths(paths)
    if not drawing_paths and not game_asset_paths:
        return 0

    temp_paths: list[Path] = []
    try:
        drawing_manifest_path = None
        if drawing_paths:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fp:
                drawing_manifest_path = Path(fp.name)
                temp_paths.append(drawing_manifest_path)
                fp.write("".join(f"{asset_path}\n" for asset_path in drawing_paths))

        game_assets_manifest_path = None
        if game_asset_paths:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fp:
                game_assets_manifest_path = Path(fp.name)
                temp_paths.append(game_assets_manifest_path)
                fp.write("".join(f"{asset_path}\n" for asset_path in game_asset_paths))

        commands = build_rsync_commands(
            ssh_host=args.ssh_host,
            ssh_port=args.ssh_port,
            remote_root=args.remote_root,
            remote_game_assets_root=args.remote_game_assets_root,
            local_root=args.local_root,
            drawing_manifest_path=drawing_manifest_path,
            game_assets_manifest_path=game_assets_manifest_path,
            dry_run=args.dry_run,
        )
        return run_rsync_commands(commands)
    finally:
        for path in temp_paths:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
