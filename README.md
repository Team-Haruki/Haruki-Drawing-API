uv run granian --interface asgi --host 0.0.0.0 --port 8000 src.core.main:app

自由线程本地运行（Python 3.14t）:
`python -X gil=0 -m granian --interface asgi --host 0.0.0.0 --port 8000 src.core.main:app`

并发拉图测试脚本:
`python scripts/concurrent_fetch_images.py --base-url http://127.0.0.1:8000 --endpoint /api/pjsk/profile/ --payload-file payloads/profile.json --requests 100 --concurrency 16 --output-dir out/profile-load --save-errors`

谱面预览功能来自 https://github.com/Sekai-World 源自 pjsekai.moe

技能覆盖功能来自 https://github.com/xfl03 bilibili@xfl03 (3.3.dev)

Music meta数据来自 3.3.dev
