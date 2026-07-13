# Skia 迁移剩余工作清单

> 2026-07-12 盘点,2026-07-14 更新。迁移本体已完成:**63 用例对拍 63 ok / 0 失败(pillow-only 已归零)**。
> **`use_skia_plot` 是唯一的 Skia 门控,默认开**——`use_skia_card_list` / `skia_card_list_fallback_to_pillow` /
> `use_skia_card_box` 已随手写 IR builder 一起从 settings.py 删除,不要再引用。card/box 与 card/list 现在都画
> 共享 widget 树(无专用 scene builder),Chart raw-N32 单次编码已落地。
> **注意**:「画共享 widget 树」只对 card 系与其余 plot 端点成立——**honor 与 chart 至今仍各自手写 IR**
> (`IRBuilder` 直出,不经 widget 树/IRPainter),别把「无专用 scene builder」当成全局结论,
> 详见 ⚪ 收尾与防漂移 的「honor / chart 仍手写 IR」条。
> 本清单是切换后的收尾与生产化工作,按"挡在生产收益前面 → 端点残余 → 质量项 → 性能 → 收尾"排序。
> 完成一项就地打勾并注日期。相关:[`skia-migration-restart-plan.md`](./skia-migration-restart-plan.md)、
> [`custom-profile-skia-feasibility.md`](./custom-profile-skia-feasibility.md)。

## 🔴 挡在生产收益前面(不做这些,生产永远 fail-open 回退 Pillow)

- [x] **CI wheel 流水线**(2026-07-13,skia-wheels.yml:linux-x86_64 + macos-arm64 矩阵、rust-cache、wheel tag 断言、IR_CAPABILITY 冒烟、artifact 上传)。
- [x] **CI 跑 native 测试**(2026-07-13,quick-check native-tests job:maturin develop + OFL 字体下载缓存 + 全量 pytest;素材类 parity 自动跳过)。
- [x] **Docker 集成**(2026-07-13,docker.yml 先构 wheel → docker/skia-wheels → 镜像条件安装 + 构建期自检;无 wheel 时 fail-open 构建仍绿,双分支本地实测)。
- [x] **IR capability 版本握手**(2026-07-13,native 暴露 IR_CAPABILITY,load_native_renderer 校验不足抛 ImportError 走 fail-open;当前=5,SelfImage 画布快照)。
- [ ] **全关金丝雀 → 生产放量验收**:带扩展镜像先全关(env)跑 48h 证明镜像无害,再开;
      验收含 mysekai 端点 200(drawer.real.py 与镜像 API 配对是 CI 盲区)。
- [ ] **PR #33 合并**(所有者暂缓中;分支每多活一天,main 插队漂移风险多一天)。

## 🟡 端点残余

- [x] **honor 迁移**(2026-07-13,group(mask=) 原语 + src/sekai/honor/skia.py 场景 + 三变体 payload + 路由 skia 先行;四用例对拍 ok——最后一个 Pillow 合成端点清零)。
- [ ] **custom profile**(见可行性文档,渐进 0→2):
  - [ ] Phase 0(S,纯 Python):进程级 TMP metadata/字形 SDF/load_font 缓存——冷 1.7s → ~0.2-0.5s。
  - [ ] Phase 1(S~M):IR 加 Transform(矩阵)节点,合成层搬 Skia(mem 图 + 原生仿射)。
  - [ ] Phase 2(M):SdfQuad 节点(SkSL/像素循环)+ freetype-rs 度量——甩掉 fontTools 的
        **全进程 GIL 重启风险**(实测确认),文字重卡估 10-40×。
  - [ ] 前置:从生产拉 tmp-font-assets/{region} + sprite + 真实卡 payload(本地全缺)。
- [x] mysekai msr_map 多图网格拼接迁 IR(2026-07-13,合并 widget 树 + 双后端 tile 裁剪,Pillow 基线 max_diff=0;drawer.real.py 不进 git,注意与镜像 API 配对)。

## 🟠 生产化质量项(一次性切换时跳过的计划内容)

- [x] **可观测性(阶段 4)**(2026-07-14):`render_stats.py` 按端点计 skia/cache_hit/fallback/disabled/error,
      挂 `GET /render-stats`(含 `font_fallbacks`);`image.response` 日志加 `backend=skia|skia_cache|skia_fallback|pillow`
      (进程内走 contextvar,跨 heavy worker 进程走 `EncodedImagePayload.backend` 带回、父进程 replay);
      `_SkiaPayloadCache` 拆到 `payload_cache.py`,接入 `/cache/stats` 与全局缓存清理。
      **记录点在 `render_canvas_payload` 内部**,所以每个走 widget 树的 drawer 都被计数
      (**例外**:honor 与 chart 手写 IR、不经 `render_canvas_payload`,各自调 `record_render` 记账,
      见 `honor/skia.py:77`、`chart/drawer.py:175`);端点名已全部穿参
      (`src/sekai` 下 51 处调用点无一遗漏,签名里的 `endpoint or "unknown"` 只是兜底)。带 payload 缓存的
      card/box 与 card/list 命中 payload 缓存时不进 `render_canvas_payload`,由 `record_skia_cache_hit` 单独计数;
      honor 也有 payload 缓存,但它手写 IR,命中走自己的 `_record`(→`record_render`),不经过那个 helper。
- [x] **影子层结果缓存推广(阶段 5)——结论:整页 payload 缓存不做**(2026-07-14,所有者确认):
      **调用方 cloud 会先按 payload 查自己的缓存,命中就不会调 drawing**,所以同一个 payload 根本不会
      来第二次——drawing 侧再加一层页面级缓存**永远不可能命中**,而每次 miss 仍会 insert,把共享 LRU 里
      真能命中的条目挤出去,净负收益。profile/vlive_list/chart 的整页缓存(连同为它服务的 `bg_hour` 量化、
      asset signature 扫描、cache key 构造)已全部删除,只保留端点名穿参。
      **删的只是"整页"那层**:vlive/list 的逐条目 composed 缓存(`vlive_list_entry`,跨请求跨用户可命中)保留;
      card/box、card/list、honor 的 Skia payload 缓存(`payload_cache.py`)也仍在,本条未动它们。
      跨请求复用发生在**更下层且是跨用户共享的**:Rust 的 Moka 栅格缓存 + Pillow 的全局 resize 缓存按
      素材路径/尺寸缓存单个图层——这才是 `card_full_thumbnail` 子树化后"CPU 反升"担忧的真正补偿。
      **已核对 cloud 实现**(Haruki-Cloud `internal/pjsk/drawing/`):它的 key 由
      `Version + Endpoint + APIPath + UserID + 净化后的 payload` 组成,而净化规则
      (`cache_rules.go:37-41` `defaultRenderCacheRule.IgnoreFieldNames = {"dt"}`)会**在任意深度剥掉 `dt`**
      (有专门测试 `TestBuildRenderCachePolicyIgnoresRootDT` 钉着),`timezone` 保留;`dt` 本身是 cloud 在
      `request_dt.go:52` 注入的 `time.Now().UnixMilli()`,**每请求都新**。两层存储:远端(磁盘 PNG + SQLite 索引)
      与本地内存(10 分钟);TTL 默认 24h,`card/detail`、`card/list`、`mysekai/fixture-*`、`help/render`、
      `misc/alias-list` 标了 `Infinite`(永不过期)。
      ⇒ **drawing 侧的整页结果缓存本就是死重**:cloud 命中就不会调 drawing,cloud 未命中则 drawing 的
      等价 key 也不会命中。cloud 明确接受陈旧水印(这是它自己的取舍)。
- [x] **修:alias-list 的结果缓存抵消了 cloud 的刻意绕过**(2026-07-14,跨服务 bug):
      cloud **专门让 alias-list 绕过自己的渲染缓存**,注释写得很明白(`client.go:361`:"Alias-list watermarks
      include request DT, so we intentionally bypass the render cache here to avoid serving stale timestamps."),
      但 drawing 侧 `alias_list` 自己还有**内存 + 磁盘 + Skia payload 三层**结果缓存,key 全都不含 `dt`,
      而画布上就有 `add_request_watermark` ⇒ cloud 为保证水印新鲜所做的努力被 drawing 内部整个抵消,
      磁盘那层还跨重启存活。三层缓存已全部删除(实测:dt 差 1 小时的两次请求现在产出不同的图;此前字节完全相同)。
      **教训**:drawing 侧任何带 `add_request_watermark` 的端点都不该做结果缓存——上游 cloud 已经做了缓存决策,
      这里再缓存一次只会偷偷覆盖掉它的意图。
- [x] 头像框 9-slice(2026-07-13):子树化取代 composed 缓存——`PlayerFrameBox` 经 `Painter.paste*` 新增的
      `src_rect` 参数在两后端最终尺寸直绘(700×700 中间合成消失;Skia 侧部件栅格进 Rust Moka 缓存跨请求复用,
      Pillow 侧走全局 resize 缓存),旧 `get_player_frame_image` 删除。
- [x] **阶段 2 剩余安全/性能项**(2026-07-14,除 lifespan 字体自检外全部完成):
  - [x] N3:Rust 图片缓存已替换为 Moka 字节预算目标栅格缓存(2026-07-13，含 single-flight、mtime/size key、Rayon 预热和 stats/clear API)。
  - [x] 字体缺失响亮化(2026-07-14):Rust 解析不到字体时 ERROR 日志(带请求的字体名与试过的路径,按字体去重一次)
        + `AtomicU64` 计数,经 `renderer_cache_stats()` 的 `font_fallback_count`/`font_fallback_fonts` 和每次渲染的
        `native_metrics["font_fallbacks"]` 暴露;父进程在 `_record()` 里聚合进 `/render-stats` 的 `font_fallbacks`
        ——**必须走 payload 聚合**,因为 deck/生日卡在 spawn 出来的 heavy worker 里渲染,子进程的静态计数器父进程读不到。
        仍保留 sans-serif 回退(fail-open 不变)。lifespan 字体自检暂未做。
  - [x] Skia 画布守卫(2026-07-14):**不是**照抄 Pillow 的 `CANVAS_SIZE_LIMIT`——真实 chart payload 已达
        5248×2704=14.2Mpx(Pillow 预算 16.8Mpx 的 85%),照抄只会把 Skia 唯一能渲的大图弹回 Pillow 再 assert → 500。
        改成 DoS 级边界(64 Mpx / 单边 32767),且判定放在**线程池任务内**(`_get_self_size()` 要走整棵树,
        放事件循环上会串行化所有请求)。
  - [x] N6(2026-07-14):Rust cache miss 的字体文件读取已移到锁外。**`IRBuilder._pil_font_cache` 维持 thread-local,
        不要改进程级**——Pillow 用 per-object 临界区保护 FreeTypeFont 状态,no-GIL 下共享字体对象会让全进程的
        `getlength/getbbox` 串行化:实测 8 线程 897ms vs 每线程独立 207ms、16 线程 1785ms vs 381ms(4-5× 吞吐损失,
        且随线程数线性恶化)。已改为 thread-local + 不缓存 fallback 字体(否则一次瞬时缺字体会永久毒化该 key)。
        `painter.get_font` 一直就是 thread-local,是对的。
  - [x] serde_json 解析已移入 py.detach;`mem:*` 传输已零拷贝借用(encoded 走不可变 `bytes`;raw N32 走 PyBuffer)。
        **注意**:`PyBuffer::readonly()` 描述的是 view 而非 exporter——`memoryview(bytearray).toreadonly()` 能骗过它,
        而 Skia 是在 py.detach 下读这块内存的,别的 Python 线程改它就是数据竞争。故对 `bytearray`/`memoryview`
        exporter 强制拷贝,只对真正不可变的 exporter(chart 的 `RasterImage`,Rust 持有且无 mutator)借用。
        另:退化 mem 图(如裁剪夹到 0 宽)只跳过该 Image 节点,**不能**抛 `ValueError` 把整个场景打回 Pillow。

## 🟢 性能后备队(指标不达标时按需提前)

- [x] **原始 asset 路径直传 + draw-time 缩放**(2026-07-13):pristine 图片发安全相对路径,Rust image LRU 解码;
      Skia 单次 draw 融合 resize + composite,生成/修改图自动回退 mem。代表场景 raw mem transport 下降 24%-100%,
      63/63 SBS 通过;当前 wall time 基本中性,收益集中在 FFI 拷贝与瞬时内存。
- [x] **lazy AssetRef 穿透 widget/Canvas**(2026-07-13):`ImageBox`/`ImageBg`/`Painter` 全线接受
      `AssetImageRef | EncodedImageRef | PIL.Image`;Pillow `_impl_paste*` 按需解码(缺文件退占位图,
      带目标尺寸时走全局 resize 缓存),`Canvas.get_img` 绘制前并发预取树内 ref——ImageBox 的显示尺寸
      可布局前自算,预取直接温 resize 缓存条目而非全尺寸解码(696 张 jacket 全尺寸约 1.5GiB 会击穿字节
      预算边解码边驱逐;64×64 条目合计仅约 11MiB),music_list Pillow 回退 `3.4s -> 1.8s` 且无 RSS 峰值;
      `music_list` 双构建(`use_asset_refs` 标志)已删除,同一棵树两后端可绘。进程池分发前在父进程物化
      全部 ref(spawn worker 看不到父进程缓存,像素经 `image_dict` 传递,即重构前行为)。`EncodedImageRef`
      以原始 encoded bytes 直传 Rust(`MemImage::Encoded`),无需 capability bump。
- [x] **Moka 目标栅格缓存 + Rayon 并行预热**(2026-07-13):按 asset identity/source rect/target/sampling 缓存,
      696 项仅约 11.4 MiB;music_list 冷启动串行 raster build `6.36s -> 0.83s`,暴露 stats/clear API 与 native metrics。
- [x] **mtpng PNG 编码替换**(2026-07-13):默认多线程 fast encode,保留 `HARUKI_SKIA_PNG_ENCODER=skia` 回退;
      代表大图 encode 提升 `2.9-5.9x`,最终 63/63 SBS 通过,文件大小变化约 `-2%` 到 `+6%`。
- [x] **Chart 中间 PNG 消除**(2026-07-13):`pjsekai-scores-rs RasterImage` 以只读 N32 buffer 跨扩展借用，
      完整路径只做最终一次编码；PyPI `0.5.0` 正式 wheel 全量验收 `63/63 ok`。
- [ ] Scene.scale 整图 resize → canvas 矩阵直渲染(M,需视觉验收;profile 1.5×/winrate 2× 受益)。
- [x] **`card_full_thumbnail` 子树化**(2026-07-13):`CardFullThumbnailBox(ImageBox)` 经 Painter 原语
      在两后端原生绘制(底图/等级条/框/特训 rank/属性/星级/圆角 clip),profile、card detail/list/box、
      event detail/list、gacha、deck 全部迁移;Pillow 预合成 `get_card_full_thumbnail` 及其 composed/disk
      缓存已删除(Skia 侧靠 Rust 路径栅格缓存,Pillow 回退为逐层小图绘制)。新增公共 Painter 原语
      `push_clip_roundrect/pop_clip`(Pillow=clip 矩形大小的离屏缓冲+alpha 遮罩,Skia=`Group{clip:rrect}`)
      与 `shadow_roundrect`(两端=模糊圆角矩形);clip 局部缓冲修复后 card_box Pillow 全量合成
      `2.2s -> 1.27s`,63/63 SBS 通过。
- [x] **Card List 回归共享 widget 树**(2026-07-14):~~最后一个手写 IR scene builder 退役~~
      (**更正**:退役的是最后一个**与既有 widget 树重复**的 scene builder;honor 与 chart 仍在手写 IR,
      见 ⚪ 收尾与防漂移 的「honor / chart 仍手写 IR」条)——
      `_build_card_list_canvas` 成为唯一布局,Pillow 走 `canvas.get_img()`、Skia 走 IRPainter,与 card/box 同构。
      `skia_renderer/card_render.py` 整个删除,`card_common` 收缩到唯一真正共享的 `rare_count`,
      `scripts/compare_card_render.py`(只为对比两套布局而存在)一并删除。专用开关
      `use_skia_card_list` / `skia_card_list_fallback_to_pillow` 退役,card/list 与其余端点同用 `use_skia_plot`
      和同一套 fail-open 契约。对拍 63/63,且共享树反而更快(skia 0.059s vs 手写 builder 记录的 0.076s)。
      **手写 builder 的代价正是它退役的理由**:两套布局要手工保持同步,而它们已经漂移了。
- [x] Pillow/Skia 混用收敛(2026-07-13,详见 migration.md 审计节 ✅3-6):MySekai tile clip 迁
      `push_clip_roundrect(radius=0)`(后端分支与预栅格化管线删除);头像框 9-slice 子树化(`PlayerFrameBox` +
      `paste* src_rect`);housing base64 改 `EncodedImageRef`、costume 迁 ref + 前景检测 crop 走 `src_rect`;
      Honor 单 pass(IR 新增 `SelfImage` 画布快照节点,IR_CAPABILITY 4→5,中间 PNG 和第二次 render 消除,
      对拍逐位一致)。Card List 回归共享树当时仍留最后评估,已于 2026-07-14 完成(见本节上一条)。
- [x] **其余 builder 迁 `get_asset_image_ref`**(2026-07-13,详见 migration.md ✅7):card/mysekai/gacha/
      score/vlive/misc/stamp/profile/inventory 共 35 处转换;**刻意保留 eager 的位置见 migration.md**
      (喂 `ImageBg(fade>0)` 的背景图、走 PIL 像素 API 的 `_circular_progress_avatar`/`concat_images`/
      mysekai site_image/harvest point/spawn_img)——改了就是 bug。`on_missing="raise"` 语义收窄为
      "缺失/非图片"(不再覆盖"像素截断"),已在 gacha 回退链注明。
- [x] **两处回退路径像素回归修复**(2026-07-13,详见 migration.md ✅8;对拍只比 Pillow↔Skia,均不暴露):
      ①ref paste 重采样从 BICUBIC 悄悄降级为 BILINEAR(新增 `PASTE_RESAMPLE`,resize 缓存 key 补 resample 维度);
      ②`CardFullThumbnailBox` 等级文字锚点差 4px(`ImageDraw` 的 la 锚点 vs `Painter.text` 的基线锚点,
      改用 `_ascender_top_to_painter_y` 按字体度量换算)、圆角内 alpha 被叠层 lerp 拉低产生光晕
      (`paste` → `paste_with_alpha_blend`,与 Skia 的 SrcOver 语义一致)。
      两者均有变异测试验证的回归用例(tests/test_card_thumbnail_box.py、tests/test_image_source.py)。
- [ ] 文本渲染缓存:每 Text 节点重复 measure_str("哇")、Left 对齐白测宽度、每节点新建 Font、
      adaptive 双重布局(S-M)。
- [ ] fs::metadata TTL(S);TriangleBg 按 seed 缓存 raster(M);mem 图 content-hash 跨请求缓存(L)。

## 🔵 新增:对 legacy 的像素基线(补上对拍的系统性盲区)

- [x] **`scripts/skia_legacy_baseline.py`**(2026-07-14):在 main 的 git worktree 里跑同一批 payload 的
      **Pillow** 输出,与当前分支的 **Pillow** 输出逐像素对比。对拍 sweep 只比"当前树的 Pillow ↔ Skia",
      因此**两个后端一起偏离 legacy 的漂移一律照绿**——`CardFullThumbnailBox` 的 4px 文字错位和 alpha 光晕
      就是这么带着 63/63 跑了一整轮的。用法:`uv run python -X gil=0 scripts/skia_legacy_baseline.py --tolerance 2`
      (`--ref` 默认 `main`;全量跑要显式换成能比的基线,理由见下一条)。
      注意 `_diff` 不能用 `ImageChops.difference(...).getbbox()`——getbbox 看 alpha,两张不透明图的差值图
      alpha 恒为 0,再大的 RGB 漂移都会报"无差异"。
- [x] **legacy 漂移分诊完成**(2026-07-14):**没有发现回归**。
      首轮报出的"48/52 全在漂移"是 **harness 自己的 bug**——它连 `--ref HEAD` 对自己都报差异:
      ①`configs.yaml` 的素材路径是相对的 `./data`,而 `data/` 在 .gitignore 里(859MB 未跟踪素材),
      所以一次性 worktree 里**一张素材都没有**,基线全画成缺图占位符;②三角背景用**无种子的全局 random**,
      同一棵树渲两次自己就差 ~12% 像素;③用 env 关进程池无效——`env>yaml` 优先级修复只在本分支上,
      老基线里 yaml 赢,进程池仍开着而 spawn worker 进不了一次性 worktree。
      **教训:差分 harness 必须先做 `--ref HEAD` 自检(max_delta 必须为 0),否则你量的是自己。**
      修好后拿**子树化移植前的 1c9f367** 当基线(它已含 Skia 后端,但卡面缩略图/头像框仍是旧 Pillow
      预合成器 `get_card_full_thumbnail`——这正是要比的那一层;main 不能当基线,本分支还带着 main 没有的功能端点):
      **53 个可比端点中 45 个逐位一致、0 个尺寸变化**;剩下 10 个里,8 个是画卡面缩略图的端点
      (差异精确落在缩略图区域,mean 0.03-2.1,正是"按最终尺寸直绘"取代"128 合成再缩放"的预期效果,
      文字位置与 alpha 已单独对着 legacy 合成器逐项验过),另外 2 个 alias-list **内容零差异**,
      只有播种导致的三角背景不同(RNG 消耗顺序一变三角就全变,属 harness 副作用)。

## ⚪ 收尾与防漂移

- [x] 文档修正(2026-07-13):migration.md 已同步 card/detail、Card Box 验收、Rust typeface/Moka/PNG/Chart raw 状态；
- [x] 修正**其他文档**的历史描述(2026-07-14,已逐条对着当前文件核过):
      ①径向渐变**早已实装**(`ir_builder.radial_gradient:198` → `ir_painter:201` 映射 → `interp.rs:828`
      的 `gradient::shaders::radial_gradient`):gaps.md 已改写为"✅ 真实径向渐变……首轮的『只平涂中心色』
      stub 已替换"(gaps.md §6),migration.md 的 v2 节点清单是**带日期的进度记录**,原文"radial…为后续"
      保留但已就地加注"**均已在后续批次落地**",其待办行也标了"radial / adaptive 文本(已补)"——两处都不再误导;
      ②gaps.md 其余错误项亦已修正:gacha **无**条目级合成缓存、honor 的整图缓存仅 mem 无 disk、
      profile 模块预渲染(`profile/drawer.py:445 _build_cached_profile_module_image`,零调用方)是死代码。
- [x] CLAUDE.md Skia 后端章节(2026-07-14,三份镜像文件同步:env-only 开关、wheel/CI 链路、capability 握手、
      cargo test 链接配方、IR-first 规则,以及本轮踩到的 5 个陷阱——对拍的 legacy 盲区、ImageBg fade 默认值、
      Painter.text 基线锚点、Pillow paste 拖低 dst alpha、resize 缓存按 resample 分键)。
- [ ] 结构性防呆 CI 测试:枚举全部路由,断言每个绘图端点绑定 widget 树/IR 路径(白名单显式豁免)。
- [ ] **honor / chart 仍手写 IR——与 card_render.py 同类的双实现风险**(2026-07-14 记):
      两者都直接用 `IRBuilder` 拼绝对坐标场景,**不经 widget 树、也不经 IRPainter**
      (`src/sekai/honor/skia.py`、`src/sekai/chart/drawer.py:40` 的注释里各自写明),因此也不经
      `render_canvas_payload`,而是自己调 `record_render` 记账。
      **honor 是真风险**:它的 Pillow 基线 `honor/drawer.py:106 _compose_full_honor_image_sync` 是一整个
      独立合成器,画布尺寸/裁剪窗/文字度量在 Python 里被**写了两遍**——正是 card/list 手写 builder
      "两套布局要手工同步、而它们已经漂移了"的翻版(见 🟢 节 Card List 回归共享树)。
      **chart 风险低**:Pillow 侧只是 crate PNG + 通用 `add_request_watermark_to_image`
      (`chart/drawer.py:152 compose_music_chart_image`),重复面仅水印页脚,且 crate 位图两边同源。
      待办:评估把 honor 收进 plot.py widget 树(与 card 系同构、Pillow 走 `Canvas.get_img()`),
      或至少给它加"两后端逐位对拍"的锁测试钉住漂移;chart 维持现状即可。
- [x] 删 GIF/APNG helpers 死代码(2026-07-14,img_utils 全仓零调用方,-285 行)。
- [ ] Pillow 退役决策(D8):全量 Skia 稳定 ≥2 个活动周期、fallback≈0 后再议——删端点级双实现 +
      扩展改启动必需 + 删静默泛型回退,`last-dual-backend` tag + 镜像回滚兜底;"永久保留"亦可接受。

## 已完成(2026-07-12,详见 restart-plan 执行日志)

真实 payload 生成器 + 59 用例对拍 harness 入库;607MB 资产同步;修复 card/list 水印 footer 4px、
winrate 请求原地修改、area_item payload;安全加固(fail-open、mem 图强引用、事件循环卸载、env>yaml);
默认开关翻转;组件库覆盖审计缺口清零(emoji 字形覆盖路由、BlurGlass blur、夜间三角衰减、card 背景 fade)
+ 资产缺失告警;储备原语(separate 渐变、pixelwise 自适应、glass corners/shadow_width、mix 颜色矩阵);
card/box shim-first(手写 builder 与专用门控退役);chart 水印壳一进一出。
