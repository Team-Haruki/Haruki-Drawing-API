from pathlib import Path

import pytest

from scripts.sync_card_list_assets import (
    DEFAULT_FONT_FILES,
    AssetPathError,
    build_rsync_command,
    build_rsync_commands,
    extract_card_list_asset_paths,
    split_remote_asset_paths,
)


def _payload() -> dict:
    return {
        "background_img_path": "static_images/bg/card_list.png",
        "term_limited_icon_path": "static_images/card/term_limited.png",
        "fes_limited_icon_path": "static_images/card/fes_limited.png",
        "cards": [
            {
                "skill": {"skill_type_icon_path": "static_images/skill_score_up.png"},
                "special_skill_info": {"skill_type_icon_path": "static_images/skill_score_up.png"},
                "thumbnail_info": [
                    {
                        "card_thumbnail_path": "asset/jp-assets/startapp/thumbnail/chara/res001_no001_normal.png",
                        "frame_img_path": "static_images/card/frame_rarity_4.png",
                        "attr_img_path": "static_images/card/attr_cute.png",
                        "rare_img_path": "static_images/card/rare_star_normal.png",
                        "train_rank_img_path": None,
                    },
                    {
                        "card_thumbnail_path": (
                            "asset/jp-assets/startapp/thumbnail/chara/res001_no001_after_training.png"
                        ),
                        "frame_img_path": "static_images/card/frame_rarity_4.png",
                        "attr_img_path": "static_images/card/attr_cute.png",
                        "rare_img_path": "static_images/card/rare_star_after_training.png",
                        "train_rank_img_path": "static_images/card/train_rank_5.png",
                    },
                ],
            },
            {
                "skill": {"skill_type_icon_path": "static_images/skill_life_recovery.png"},
                "thumbnail_info": [
                    {
                        "card_thumbnail_path": "asset/jp-assets/startapp/thumbnail/chara/res002_no001_normal.png",
                        "frame_img_path": "static_images/card/frame_rarity_birthday.png",
                        "attr_img_path": "static_images/card/attr_happy.png",
                        "rare_img_path": "static_images/card/rare_star_normal.png",
                        "birthday_icon_path": "static_images/card/rare_birthday.png",
                    },
                ],
            },
        ],
    }


def test_extract_card_list_asset_paths_deduplicates_and_includes_fonts():
    paths = extract_card_list_asset_paths(_payload())

    assert paths == [
        "static_images/bg/card_list.png",
        "static_images/card/term_limited.png",
        "static_images/card/fes_limited.png",
        "static_images/skill_score_up.png",
        "asset/jp-assets/startapp/thumbnail/chara/res001_no001_normal.png",
        "static_images/card/frame_rarity_4.png",
        "static_images/card/attr_cute.png",
        "static_images/card/rare_star_normal.png",
        "asset/jp-assets/startapp/thumbnail/chara/res001_no001_after_training.png",
        "static_images/card/rare_star_after_training.png",
        "static_images/card/train_rank_5.png",
        "static_images/skill_life_recovery.png",
        "asset/jp-assets/startapp/thumbnail/chara/res002_no001_normal.png",
        "static_images/card/frame_rarity_birthday.png",
        "static_images/card/attr_happy.png",
        "static_images/card/rare_birthday.png",
        *DEFAULT_FONT_FILES,
    ]


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/evil.png",
        "../evil.png",
        "asset\\jp-assets\\evil.png",
        "C:/evil.png",
        "asset/jp-assets/good.png\nbad.png",
    ],
)
def test_extract_card_list_asset_paths_rejects_unsafe_paths(path):
    payload = _payload()
    payload["cards"][0]["thumbnail_info"][0]["card_thumbnail_path"] = path

    with pytest.raises(AssetPathError):
        extract_card_list_asset_paths(payload)


def test_extract_card_list_asset_paths_can_skip_fonts():
    paths = extract_card_list_asset_paths(_payload(), include_fonts=False)

    assert not any(path in DEFAULT_FONT_FILES for path in paths)


def test_split_remote_asset_paths_routes_game_assets_without_asset_prefix():
    drawing_paths, game_asset_paths = split_remote_asset_paths(
        [
            "static_images/card/frame_rarity_4.png",
            "asset/jp-assets/startapp/thumbnail/chara/res001_no001_normal.png",
            "SourceHanSansSC-Regular.otf",
        ]
    )

    assert drawing_paths == ["static_images/card/frame_rarity_4.png", "SourceHanSansSC-Regular.otf"]
    assert game_asset_paths == ["jp-assets/startapp/thumbnail/chara/res001_no001_normal.png"]


def test_build_rsync_command_uses_files_from_manifest():
    command = build_rsync_command(
        ssh_host="root@100.111.213.59",
        ssh_port=None,
        remote_root="/data/HarukiServices/data/drawing/",
        local_root=Path("data"),
        manifest_path=Path("out/assets.txt"),
        dry_run=True,
    )

    assert command == [
        "rsync",
        "-aR",
        "--files-from",
        "out/assets.txt",
        "-e",
        "ssh -o BatchMode=yes -o ConnectTimeout=15",
        "--dry-run",
        "root@100.111.213.59:/data/HarukiServices/data/drawing/",
        "data",
    ]


def test_build_rsync_command_can_use_custom_ssh_port():
    command = build_rsync_command(
        ssh_host="root@yamamoto.j8.network",
        ssh_port=60022,
        remote_root="/data/HarukiServices/data/drawing",
        local_root=Path("data"),
        manifest_path=Path("out/assets.txt"),
        dry_run=False,
    )

    assert command[5] == "ssh -o BatchMode=yes -o ConnectTimeout=15 -p 60022"
    assert command[-2] == "root@yamamoto.j8.network:/data/HarukiServices/data/drawing/"


def test_build_rsync_commands_splits_roots():
    commands = build_rsync_commands(
        ssh_host="root@100.111.213.59",
        ssh_port=None,
        remote_root="/data/HarukiServices/data/drawing",
        remote_game_assets_root="/data/HarukiServices/data/assets",
        local_root=Path("data"),
        drawing_manifest_path=Path("out/drawing.txt"),
        game_assets_manifest_path=Path("out/game-assets.txt"),
        dry_run=False,
    )

    assert commands == [
        [
            "rsync",
            "-aR",
            "--files-from",
            "out/drawing.txt",
            "-e",
            "ssh -o BatchMode=yes -o ConnectTimeout=15",
            "root@100.111.213.59:/data/HarukiServices/data/drawing/",
            "data",
        ],
        [
            "rsync",
            "-aR",
            "--files-from",
            "out/game-assets.txt",
            "-e",
            "ssh -o BatchMode=yes -o ConnectTimeout=15",
            "root@100.111.213.59:/data/HarukiServices/data/assets/",
            "data/asset",
        ],
    ]
