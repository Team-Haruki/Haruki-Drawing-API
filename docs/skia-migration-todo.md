# Skia 迁移剩余工作清单

> 2026-07-12 盘点,2026-07-18 更新。迁移本体已完成:**63 用例对拍 63 ok / 0 失败(pillow-only 已归零)**。
> **`use_skia_plot` 是唯一的 Skia 门控,默认开**——`use_skia_card_list` / `skia_card_list_fallback_to_pillow` /
> `use_skia_card_box` 已随手写 IR builder 一起从 settings.py 删除,不要再引用。card/box 与 card/list 现在都画
> 共享 widget 树(无专用 scene builder),Chart raw-N32 单次编码已落地。
> **注意**:**所有端点的布局都已收敛到共享 widget 树**(honor 于 2026-07-14 收尾)。`honor/skia.py` 与
> `chart/drawer.py` 仍直接用 `IRBuilder`,但包的是 **widget 树表达不了的栅格页脚外壳**(水印条带 =
> `SelfImage` 采样已渲染画布),不是第二套布局;详见 ⚪ 收尾与防漂移 的对应条目。
> 本清单是切换后的收尾与生产化工作,按"挡在生产收益前面 → 端点残余 → 质量项 → 性能 → 收尾"排序。
> 完成一项就地打勾并注日期。相关:[`skia-migration-restart-plan.md`](./skia-migration-restart-plan.md)、
> [`custom-profile-skia-feasibility.md`](./custom-profile-skia-feasibility.md)。

## 🔴 挡在生产收益前面(不做这些,生产永远 fail-open 回退 Pillow)

- [x] **CI wheel 流水线**(2026-07-13,skia-wheels.yml:linux-x86_64 + macos-arm64 矩阵、rust-cache、wheel tag 断言、IR_CAPABILITY 冒烟、artifact 上传)。
- [x] **CI 跑 native 测试**(2026-07-13,quick-check native-tests job:maturin develop + OFL 字体下载缓存 + 全量 pytest;素材类 parity 自动跳过)。
- [x] **Docker 集成**(2026-07-13,docker.yml 先构 wheel → docker/skia-wheels → 镜像条件安装 + 构建期自检;无 wheel 时 fail-open 构建仍绿,双分支本地实测)。
- [x] **IR capability 版本握手**(2026-07-13,native 暴露 IR_CAPABILITY,load_native_renderer 校验不足抛 ImportError 走 fail-open;
      **当前=10**,`Image.blur_sigma`(8=`Transform`+`catmull_rom`,9=`SdfQuad`+A8 raw buffer)——旧 wheel 会
      serde-skip 未知字段并静默画错;例如 capability 6 wheel 会丢 `TriangleBg.tris`,画出**一个三角形都没有的背景**。
      这正是握手要挡的那种事)。
- [x] **mysekai 真实实现 × 真实 HTTP**(2026-07-15,盲区已关):把 `drawer.real.py` 换到 `drawer.py` 的位置、
      起 granian、打**真实路由**,8 条 mysekai 路由 **9/9 返回 200**、图片可解码、`Content-Length` 正确。
      这以前是**零覆盖**:仓库里的 `drawer.py` 是抛 `NotImplementedError` 的 stub,CI 跑 stub,对拍则绕过路由
      按路径直接加载 real——所以"真实实现配不配得上当前 API 表面"从来没有任何东西验过。
      **它进不了 CI**(`drawer.real.py` 不在仓库里),所以必须是**发布前清单上的一条手工项**。
- [ ] **全关金丝雀 → 生产放量验收**:带扩展镜像先全关(env)跑 48h 证明镜像无害,再开。
      部署时三条必查,漏了都是**静默**出错(不报警、不 500,只是悄悄不对):
      ① **wheel 必须是 capability 10** —— 旧 wheel 握手失败会 fail-open 回 Pillow,服务正常、图也对,
         只是白白慢 3.6 倍;唯一的信号是 `/render-stats` 里 `fallback` 计数飙升。
      ② **`drawer.real.py` 必须挂上**(bind-mount 或改名),否则 8 条 mysekai 路由全 500。
      ③ **内存限额必须真的落在绘图服务上** —— `deploy` 块此前一直挂在 screenshot-service 上(已修,`6c4e138`);
         没有限额时容器的 `memory.max` 读作 `"max"`,`read_cgroup_memory()` 返回 `None`,
         `readiness_unhealthy_cgroup_percent` 那道门**永远不会触发**。
- [ ] **PR #33 合并**(所有者暂缓中;分支每多活一天,main 插队漂移风险多一天)。

## 🔴 已修:每个图片响应都在按"行"切块(2026-07-14)

**全服务 17 个绘图路由的响应出口只有一个** —— `src/core/utils.py` 的 `encoded_image_payload_to_response`
/ `image_to_response`。它们原本返回 `StreamingResponse(io.BytesIO(image_bytes))`。

这里没有任何东西可以"流":字节早就整块在内存里了,包成 `BytesIO` 一点内存都省不下。它真正干的事是
**把一个同步可迭代对象交给 Starlette**,而 Starlette 会用 `iterate_in_threadpool()` 逐项去取 ——
**`BytesIO` 的迭代协议是按行(`readline`)**,于是二进制 PNG 在每一个 `0x0A` 字节处被切开:

- 实测平均 **384 字节一块**。一张 870 KB 的 deck 图 = **~2,262 块**;7.3 MB 的 card/box = **~19,000 块**。
- 每一块都是一次 **anyio 线程池往返 + 一条独立的 ASGI body 消息**,还丢掉了 `Content-Length`(退化成 chunked)。

**代价(deck/recommend,24 请求 @ 并发 8):**

| | 修复前 | 修复后 |
|---|---|---|
| 单发(暖) | 0.43s | **0.07s** |
| p50 | 10.24s | **0.22s** |
| 墙钟 | 30.8s | **0.97s** |
| 吞吐 | 0.78 req/s | **24.8 req/s**(**32×**) |

**它为什么能藏这么久:功能上完全正确** —— 客户端每个字节都收到了,图也没错。而且服务端日志看不见它:
`request.end elapsed=0.116s` 是在端点**返回 Response 对象**时记的,body 的实际发送发生在计时区间之外。
所以日志说 0.12 秒,客户端等 10 秒,两边都"没说谎"。CPU 全程 **5% 空闲**。

**回归锁**:`tests/test_image_response.py` —— 断言的是 **ASGI body 消息数 == 1**(不是字节内容,
因为坏版本的字节内容也是对的;只有消息数会露馅)。已做变异验证:把 `StreamingResponse` 塞回去,3/3 立刻红。

> **教训**:我最初把这 10 秒解释成"`deck_recommend` 是 CPU-bound 搜索、8 个 worker 抢 4 个核",
> 还据此改了配置、写进了文档。**那是编的** —— `src/sekai/deck/` 里没有一行搜索代码。
> 数据当时就在打脸:**2 个 worker、24 个请求、最慢 14.43 秒**,单次 12 秒 CPU 的话这在物理上不可能;
> 而且 **pool=1 和 pool=8 吞吐完全相同**,worker 根本不是瓶颈。我没去追这个矛盾,直接给了个顺耳的故事。

## 🔵 生产容量:实测(2026-07-14,OrbStack/linux-aarch64 镜像内,glibc)

**此前所有内存数字都是 macOS/libmalloc 上量的,而 macOS 在内存压力下会把页从测量脚下抽走
(整棵进程树的 RSS 会同时塌到 1/4)。以下是镜像里的真实数字。**

- **空转** cgroup `671 MB`(app 267 + 8 个 heavy worker × 47)。改成 4 worker 后 `590 MB`。
- **card/box 并发(2026-07-14 在修完流式 bug 的镜像上复测)**:cgroup 峰值 4/8/12 并发 =
  `1081 / 1155 / 1182 MB`。**峰值几乎不随并发涨**——glibc 复用 arena,且缩略图缓存跨请求共享且热。
  这与 macOS 上量到的"每并发 +110 MB"完全不同。
  (修复前量到的 996/1024/1113 MB 形状一致 ⇒ **这条结论没有被流式 bug 污染**,复测确认。)
- **但父进程自己的 RSS 是随并发线性涨的**,而这正是 `/ready` 唯一看得见的量:
  1/4/8/12 并发 = `483 / 757 / 838 / 958 MB`。**修复前量到的"父进程峰值 765 MB"偏低**,
  因为那时请求被发包路径串行化,同时在渲染的根本没那么多。
  ⇒ `readiness_unhealthy_rss_mb: 1536` 实际上是一道**并发闸**(约 20 并发触发),不是内存压力闸:
  它触发时 cgroup 还远没到 4G,而真正会吃掉 cgroup 的 heavy worker 它一个都看不见。
- **heavy worker 才是内存主项**,而且此前没人称过:worker 在启动时就 spawn,**只在崩溃/超时才重启
  (没有"跑够 N 个任务就回收"的策略)**,每个各建一套自己的资产/字体/栅格缓存。
  单个从空转 `47 MB` 涨到 `~500 MB`,8 个合计稳态 `~2.15 GB`,cgroup 稳态 **`2.54 GB`**。
  **会涨但收敛**(30 个任务后不再增长),是缓存工作集上界不是泄漏 ⇒ 配置能解决。
- ⇒ **`memory: 2G` 是撑不住的**:光 heavy pool 暖起来就越过它。deck_recommend 第一次来 8 个并发就 OOM。

**worker 数该定几个 —— A/B(24 请求 @ 并发 8,10 核开发机,已修完流式 bug):**

| pool | p50 | 吞吐 | CPU |
|---|---|---|---|
| 1 | 0.54s | 13.4 req/s | 2.6 core-s |
| 2 | 0.36s | 19.2 req/s | 3.2 core-s |
| **4** | **0.21s** | **23.3 req/s** | 4.3 core-s |
| 8 | 0.31s | 15.4 req/s | 7.3 core-s |

**4 个是拐点:8 个吞吐反而掉回去,CPU 却翻倍——这才是真正的超订。**
(4 核容器上最优值可能更低,但 4 个有余量,且远好于原来的 8。)

> ⚠️ **这张表推翻了本文档的前一个版本。** 那一版写的是"`deck_recommend` 是 CPU-bound 搜索,真实
> 12–13 秒",并据此解释"2→8 个 worker 只快 6%"。**两句都是错的**:`src/sekai/deck/` 里没有任何
> 搜索/求解代码(组卡结果是调用方算好、随 `DeckRequest.deck_data` 传进来的,本服务只负责画),
> 而那 12–13 秒是下面那个流式 bug 的产物。**当时的破绽就摆在数据里:2 个 worker 跑完 24 个请求
> 最慢的一个只用了 14.43 秒——如果单次真要 12 秒 CPU,这在物理上不可能。** 我没去看这个矛盾。

**已改**(`configs.yaml` + `configs.docker.yaml` + `docker-compose.yaml`):
`isolated_worker_pool_size: 8→4`、`readiness_unhealthy_rss_mb: 4096→0`(先试过 1536,最终**关掉**——它只看得见父进程,
本质是校准错了的并发闸)、新增 `readiness_unhealthy_cgroup_percent: 90`、`memory: 2G→4G`、`cpus: 2→4`
(**注意**:`deploy` 块此前一直挂在 screenshot-service 上,绘图服务其实没有限额;已归位,见 `6c4e138`)。

**验收(2026-07-14 修完流式 bug 后在 4G 容器里重测)**:空转 `588 MB`;混合负载
(12×card/box + 8×deck/recommend,10 并发)**峰值 `2039 MB` = 限额的 50%,20 个响应全 OK,`oom_kill 0`**。

> 注意峰值比修复前(1790 MB)**高了 250 MB**。这是修复的直接后果,不是退步:响应快了 32 倍,
> 同一时刻真正在渲染的请求就更多。**任何在旧代码上量的容量数字都偏低**,因为那时候大部分请求
> 卡在发包上,根本没在画图。4G 仍有一倍余量。

- [x] **`/ready` 改成看 cgroup**(2026-07-14):新增 `readiness_unhealthy_cgroup_percent`(默认 90),
      `read_cgroup_memory()` 读 `memory.current` vs `memory.max`(v2,回退 v1 的
      `usage_in_bytes`/`limit_in_bytes`),**这是唯一能看见 heavy worker 的信号**——实测空转时父进程
      RSS 只有 267 MB,而 cgroup 已经 585 MB,旧的门连一半都没看见。用**百分比**表达是为了让它
      不可能被配到硬限额之上(旧的 4096 就是那样,永远触发不了)。不在受限 cgroup 里(裸机/macOS)
      时返回 `None`,门自动失效,不猜。真容器验证:阈值压到 5% → `/ready` 返回 503 且理由为
      `cgroup_percent 9.4 >= 5 (385/4096 MB)`。
      **`readiness_unhealthy_rss_mb` 同时关掉(设 0)**:父进程 RSS 随并发线性涨
      (1/4/8/12 并发 = 483/757/838/958 MB),它其实是一道校准错了的并发门,会抢在真正的并发门
      (inflight 48)前面开火,而且照样看不见 worker。并发有 inflight 门,内存有 cgroup 门,它没位置了。

## 🟡 端点残余

- [x] **honor 迁移**(2026-07-13,group(mask=) 原语 + src/sekai/honor/skia.py 场景 + 三变体 payload + 路由 skia 先行;四用例对拍 ok——最后一个 Pillow 合成端点清零)。
- [ ] **custom profile**(见可行性文档,渐进 0→2):
  - [x] Phase 0(2026-07-18,纯 Python):进程级缓存落地——`custom_profile/cache.py`
        (BoundedCache + 字形 SDF/轮廓 L2 + sprite/atlas 池 + TMP 表缓存 + 线程本地字体),
        renderer 五处接线,FreeTypeMetrics 加锁(先于本工作的并发竞态)。微基准
        (`scripts/bench_custom_profile_glyph_cache.py`,合成 fixture + 真字体):轮廓
        851ms → **0.02ms**,load_font 24ms → 0.5ms。`/cache/stats` 第六键
        `custom_profile_caches`;knob 默认开启,归零即回滚。对抗性评审 6 条确认已修:
        **负缓存只留 L1**(瞬态失败不得在不变签名下毒化进程池,有回归测试钉住)、
        TMPFontAsset 补 frozen、清扫器假威胁注释纠正。13+1 个新测试;既有 1080 行零改动。
  - [x] Phase 1(2026-07-18,capability 7→8):IR 加 **Transform(矩阵,正向语义)** 节点 +
        `catmull_rom` 采样 + Transform 内跳过预栅格缓存/prewarm;`custom_profile/skia.py`
        hand-built 场景(第三个 sanctioned exception):**无旋转元素 = Python 两步 BICUBIC 预缩 +
        整数位贴(与 Pillow 逐像素对齐,实测 rgb max=1、alpha 精确)**,旋转元素走 Transform
        单 pass(替代 resize+rotate+2× 超采样,合成注入 30° 实测 p99=7,预算 24 内);白底矩形
        (render_card 是不透明白底)。踩过的坑:服务态 `position_scale=1.1181` 不是 1。
        接缝 `layer_transform_inputs`(布局数字单源,Pillow 重构后字节一致)。sweep 65 ok
        (含两 custom 案例,budget 断言)/warm parity 0 drift/cargo 19 测试/8 个新 pytest
        (含真实 native e2e)。暖速 1.06×(文字卡,栅格化仍占大头→Phase 2)~1.49×(无文字卡)。
  - [x] Phase 2(2026-07-18,capability 8→9,**SdfQuad 部分**):`SdfQuad` 节点(**Rust 像素循环**
        而非 SkSL——bit-parity 最可控)+ A8 raw-buffer 传输(RAW_BUFFER_CAPABILITY 1→2)。
        设计定型:**Python 保留布局 + PIL uint8 双三次场变形,只把逐像素着色/合成搬 Rust**
        (着色标量单源 `tmp_sdf_shading_scalars`,装饰 direct 路径接缝
        `prepare_direct_sdf_quads`,双方 shade 语义 bit 级对齐:banker's 舍入、f64 标量→f32
        逐像素)。金标三例(face/underlay+整数位移/gradient+bold)Δ≤1;装饰卡端到端
        rgb max=2、alpha 精确;28 quad 着色 **6ms**(numpy 版 ~200ms)。合成符号卡整卡 1.57×
        (真实符号卡 payload 采到后复测)。字节门:deco 重构前后 max_diff=0。
  - [ ] Phase 2b(可选,原 Phase 2 的 freetype-rs 半):glyph_sdf.rs(freetype-rs + 精确
        Felzenszwalb EDT + moka)+ `glyph:` 引用——彻底甩掉 fontTools 的**全进程 GIL 重启风险**。
        Phase 0 的进程级字形缓存已把 fontTools 成本压成一次性,紧迫性下降;等真实符号卡
        payload 与生产观察再定。
  - [x] 前置(2026-07-18,大部分落地):**response.json(真实 CN GetAnotherProfileResponse,
        2 张卡)取代了生产 dump 短窗**。已拉:cn custom_profile 资产 536M + tmp-font-assets 365M
        + static_images/customprofile(71 sprite,连带发现本地旧目录只有 9 个陈旧文件,已换
        symlink 指向拉取版)+ sc masterdata 270M + 按清单定向拉的 member_cutout/member_small
        ×5 bundle 与 honor_0078。`scripts/parity_payloads/gen_custom_profile.py` 从
        response.json + masterdata 离线重建 Cloud 形态的 `resources`(同名索引内联 + 派生
        imagePath/cardAssets/profileHonorRequests),**服务路径(masterdata=None)渲染与
        masterdata 模式 CLI 基线逐字节一致(max_diff=0,两卡)**,解析探针零失败。sweep 四例:
        custom_profile_card / _collections(pillow-only,有 payload)+ _symbol / _stamps
        (no-payload,等 dump 钩子采到用这些桶的卡)。
        采集机制就位:`HARUKI_DRAWING__DEBUG_DUMP_REQUEST_DIR`/`_PATHS` 中间件原始 body 落盘。
- [x] mysekai msr_map 多图网格拼接迁 IR(2026-07-13,合并 widget 树 + 双后端 tile 裁剪,Pillow 基线 max_diff=0;drawer.real.py 不进 git,注意与镜像 API 配对)。

## 🟠 生产化质量项(一次性切换时跳过的计划内容)

- [x] **可观测性(阶段 4)**(2026-07-14):`render_stats.py` 按端点计 skia/cache_hit/fallback/disabled/error,
      挂 `GET /render-stats`(含 `font_fallbacks`);`image.response` 日志加 `backend=skia|skia_cache|skia_fallback|pillow`
      (进程内走 contextvar,跨 heavy worker 进程走 `EncodedImagePayload.backend` 带回、父进程 replay);
      `_SkiaPayloadCache` 拆到 `payload_cache.py`,接入 `/cache/stats` 与全局缓存清理。
      **记录点在 `render_canvas_payload` 内部**,所以每个走 widget 树的 drawer 都被计数
      (**例外**:honor 与 chart 的水印外壳自己发 IR、不经 `render_canvas_payload`,各自调 `record_render`
      记账,见 `honor/skia.py`、`chart/drawer.py` 的 `_record`);端点名已全部穿参
      (`src/sekai` 下 51 处调用点无一遗漏,签名里的 `endpoint or "unknown"` 只是兜底)。**card/box 与 card/list 的整页 payload
      缓存已于 2026-07-14 删除**(它们把墙钟画进页面,命中必然发陈旧水印;见 `f088c41`),配套的
      `record_skia_cache_hit()` 一并删除——现在两者每次都走 `render_canvas_payload`,由那个记录点统一计数。
      **唯一**还有 Skia payload 缓存的端点是 honor:它的水印外壳自己发 IR,命中走自己的 `_record`
      (→`record_render`),不经过那个 helper。
- [x] **影子层结果缓存推广(阶段 5)——结论:整页 payload 缓存不做**(2026-07-14,所有者确认):
      **调用方 cloud 会先按 payload 查自己的缓存,命中就不会调 drawing**,所以同一个 payload 根本不会
      来第二次——drawing 侧再加一层页面级缓存**永远不可能命中**,而每次 miss 仍会 insert,把共享 LRU 里
      真能命中的条目挤出去,净负收益。profile/vlive_list/chart 的整页缓存(连同为它服务的 `bg_hour` 量化、
      asset signature 扫描、cache key 构造)已全部删除,只保留端点名穿参。
      **删的只是"整页"那层**:vlive/list 的逐条目 composed 缓存(`vlive_list_entry`,跨请求跨用户可命中)保留;
      当时 card/box、card/list、honor 的 Skia payload 缓存仍在,本条未动它们;**后来 card 的两页缓存已按同一条教训删除**
      (`f088c41`——任何带 `add_request_watermark` 的端点都不该做结果缓存),现在只剩 honor(它的 key 里烤进了水印文本,
      所以 `dt` 一变就 miss)。由 `tests/test_asset_signature_cache_key.py::test_card_pages_are_not_cached` 钉死。
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
- [x] **阶段 2 剩余安全/性能项**(2026-07-14,全部完成):
  - [x] N3:Rust 图片缓存已替换为 Moka 字节预算目标栅格缓存(2026-07-13，含 single-flight、mtime/size key、Rayon 预热和 stats/clear API)。
  - [x] 字体缺失响亮化(2026-07-14):Rust 解析不到字体时 ERROR 日志(带请求的字体名与试过的路径,按字体去重一次)
        + `AtomicU64` 计数,经 `renderer_cache_stats()` 的 `font_fallback_count`/`font_fallback_fonts` 和每次渲染的
        `native_metrics["font_fallbacks"]` 暴露;父进程在 `_record()` 里聚合进 `/render-stats` 的 `font_fallbacks`
        ——**必须走 payload 聚合**,因为 deck/生日卡在 spawn 出来的 heavy worker 里渲染,子进程的静态计数器父进程读不到。
        仍保留 sans-serif 回退(fail-open 不变)。
  - [x] **lifespan 字体自检**(已完成):`src/core/main.py:140 _self_check_fonts()`,由 lifespan 在 `main.py:255` 调用。
        Pillow 解析不到**正文**字体 → `RuntimeError` 拒绝启动;只缺 emoji → ERROR 日志但继续服务;
        只有**原生**渲染器解析不到 → 就地关掉 `use_skia_plot` 走 Pillow;探测本身出错则吞掉(fail-open)。
        用例:`tests/test_font_self_check.py`。
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
      `music_list` 双构建(`use_asset_refs` 标志)已删除,同一棵树两后端可绘。
      ~~进程池分发前在父进程物化全部 ref(像素经 `image_dict` 传递)~~ —— **已失效**:painter 进程池连同
      `image_dict` 编组已于 2026-07-14 整个删除(`7b553fa`),`image_dict` 在全仓库已无一处代码引用。
      仅剩的跨进程池是 `src/core/heavy_render_pool.py` 的两个重任务,它跨边界传的是**请求 dict**,
      由 worker 自己 validate 并从头渲染——**没有任何父进程侧的 ref 物化步骤,不要加回来**。`EncodedImageRef`
      以原始 encoded bytes 直传 Rust(`MemImage::Encoded`),无需 capability bump。
- [x] **`ImageBg` fade/blur 保持 lazy ref**(2026-07-18,capability 9→10):像素效果从
      `ImageBg.__init__` 移到共享 `Painter.image_bg`;Pillow replay 才 resolve,并保持历史
      blur→brightness→resize 顺序。`IRPainter` 不走旧的全画布 `IRBuilder.image_bg`,而是发普通
      `Image(path)` 节点:`catmull_rom` 采样、`floor((1-fade)*255)` multiply tint、按 source→destination
      比例换算的 `blur_sigma`。Rust blur 是 Image 装饰,目标 raster cache 仍存未装饰 resize,不同 blur
      消费者不会串味。card detail/list/box、event detail、gacha detail、chara-birthday 背景已改
      `AssetImageRef`;本地 proprietary `mysekai/drawer.real.py` 的背景调用点可按部署版本续迁。
- [x] **Moka 目标栅格缓存 + Rayon 并行预热**(2026-07-13):按 asset identity/source rect/target/sampling 缓存,
      696 项仅约 11.4 MiB;music_list 冷启动串行 raster build `6.36s -> 0.83s`,暴露 stats/clear API 与 native metrics。
- [x] **mtpng PNG 编码替换**(2026-07-13):默认多线程 fast encode,保留 `HARUKI_SKIA_PNG_ENCODER=skia` 回退;
      代表大图 encode 提升 `2.9-5.9x`,最终 63/63 SBS 通过,文件大小变化约 `-2%` 到 `+6%`。
- [x] **Chart 中间 PNG 消除**(2026-07-13):`pjsekai-scores-rs RasterImage` 以只读 N32 buffer 跨扩展借用，
      完整路径只做最终一次编码；PyPI `0.5.0` 正式 wheel 全量验收 `63/63 ok`。
- [x] ~~Scene.scale 整图 resize → canvas 矩阵直渲染~~ **实测否决**(2026-07-14):先量再改,量完发现不值得。
      `scale_elapsed` 在两个受益端点上分别是 profile `0.005s`/43ms、winrate `0.004s`/19ms——占端到端不到 12%,
      绝对值只有几毫秒。而矩阵直渲会把整个光栅化搬到放大后的分辨率上(draw 反而变贵),并改变文字 hinting
      与抗锯齿的落点,拿"几毫秒"去换一次全端点像素验收和长期的两后端字形漂移风险,不划算。**保持整图 resize**
      (且它与 Pillow `Canvas.get_img(scale)` 的"先渲染再 BILINEAR 缩放"语义天然一致,这本身就是对拍能过的原因)。
- [x] **`card_full_thumbnail` 子树化**(2026-07-13):`CardFullThumbnailBox(ImageBox)` 经 Painter 原语
      在两后端原生绘制(底图/等级条/框/特训 rank/属性/星级/圆角 clip),profile、card detail/list/box、
      event detail/list、gacha、deck 全部迁移;Pillow 预合成 `get_card_full_thumbnail` 及其 composed/disk
      缓存已删除(Skia 侧靠 Rust 路径栅格缓存,Pillow 回退为逐层小图绘制)。新增公共 Painter 原语
      `push_clip_roundrect/pop_clip`(Pillow=clip 矩形大小的离屏缓冲+alpha 遮罩,Skia=`Group{clip:rrect}`)
      与 `shadow_roundrect`(两端=模糊圆角矩形);clip 局部缓冲修复后 card_box Pillow 全量合成
      `2.2s -> 1.27s`,63/63 SBS 通过。
- [x] **Card List 回归共享 widget 树**(2026-07-14):~~最后一个手写 IR scene builder 退役~~
      (**更正**:退役的是最后一个**与既有 widget 树重复**的 scene builder;honor 当时仍是两套布局,
      已于 2026-07-14 收尾,见 ⚪ 收尾与防漂移 的「honor 回归共享 widget 树」条)——
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
      score/vlive/misc/stamp/profile/inventory 共 35 处转换;**刻意保留 eager 的位置见 migration.md**——现在
      只包括走 PIL 像素 API 的 `_circular_progress_avatar`/`concat_images`/mysekai site_image/harvest point/
      spawn_img。`ImageBg(fade/blur)` 已于 2026-07-18 解禁并完成仓库内六类背景调用点转换,不再是保留理由。
      `on_missing="raise"` 语义收窄为
      "缺失/非图片"(不再覆盖"像素截断"),已在 gacha 回退链注明。
- [x] **两处回退路径像素回归修复**(2026-07-13,详见 migration.md ✅8;对拍只比 Pillow↔Skia,均不暴露):
      ①ref paste 重采样从 BICUBIC 悄悄降级为 BILINEAR(新增 `PASTE_RESAMPLE`,resize 缓存 key 补 resample 维度);
      ②`CardFullThumbnailBox` 等级文字锚点差 4px(`ImageDraw` 的 la 锚点 vs `Painter.text` 的基线锚点,
      改用 `_ascender_top_to_painter_y` 按字体度量换算)、圆角内 alpha 被叠层 lerp 拉低产生光晕
      (`paste` → `paste_with_alpha_blend`,与 Skia 的 SrcOver 语义一致)。
      两者均有变异测试验证的回归用例(tests/test_card_thumbnail_box.py、tests/test_image_source.py)。
- [x] **文本测量缓存 + 路径解析缓存**(2026-07-14):**本节其余条目都在猜 Rust 侧,而热点根本不在 Rust。**
      先 profile 再动手:`inventory_list` 1.673s 里 native 只占 0.165s(~10%),draw→IR 却占 1.202s;
      cProfile 指向 `Font.getsize` —— **6816 次调用 / 0.966s,占整个 draw pass 的 84%**。原因是布局本身要测量:
      每个 widget 靠文字尺寸自算大小,而 widget 树在 sizing 时会反复测同一批字符串。
      ①`painter.get_text_size/get_text_offset` 加进程级 bbox 缓存(key=`(字体文件, 字号, 文本)`,emoji 走
      `getsize_emoji` 独立池)。②`utils._resolve_asset_path` 记忆化 realpath——`Path.resolve()` 是**逐路径段
      一次 lstat**,base 和资产路径各走一遍,`music_list`(696 张 jacket)光这一项就 **33134 次 lstat**;
      同时把 `is_file()+stat()` 两次系统调用并成一次。③`ir_painter._image_ref` 每个 image 节点都
      `resolve(strict=True)`,同样记忆化(`resolve_existing_asset_path`)。**lstat 33134 → 5 次/render。**
      成绩:对拍 skia 总时长 `9.79s → 5.41s`(**1.81x**,pillow `20.86s → 16.67s`),`inventory_list` 7.3x、
      `gacha_list` 4.2x、`score_control` 3.0x;63/63 通过,且 legacy 基线 55/55 `max_delta=0` **逐像素一致**。
      **缓存 stat 是不能碰的红线**:mtime/size 是所有图片缓存的 key,缓存了它资产同步就会静默失效
      (变异测试已钉死,见 tests/test_base_utils.py、tests/test_text_measure_cache.py)。
- [x] **Rust 侧文本微优化**(2026-07-14,**实测只值 ~0.4%,老实记下来**):每个 Text 节点原本都要新建一个
      emoji `Font`(哪怕整串没有一个 emoji 码位)、且 Left 对齐也会把 advance 测出来再丢掉。已改为
      `emoji_font_for()`(文本真含 emoji 码位才构造)与惰性 advance(仅 Center/Right 测)。
      像素中性已用**无背景纯文字场景**逐字节验证(三种对齐 × 有/无 emoji × 字间距 × CjkTop 基线 × 三种字重,
      前后 sha256 完全相同);对拍 skia `5.43s → 5.41s`——**噪声级**。
      结论:**这一条在 TODO 里被高估了**,真正的文本开销在 Python 的布局测量,不在 Rust 的绘制。
      因此同组的 `measure_str("哇")` 全局缓存**不做**:为 0.4% 量级的收益引入跨线程锁不划算。
- [ ] ~~fs::metadata TTL(S)~~ → 已由上面的路径解析缓存覆盖(在 Python 侧,不在 Rust)。
      余下:TriangleBg 按 seed 缓存 raster(M,播种前提已于 2026-07-14 解决,见下条;现在卡的是调色板按秒变);
      mem 图 content-hash 跨请求缓存(L,`get_asset_image_ref` 铺开后 mem 图已经很少,收益存疑)。
- [x] **三角形背景的随机源**(2026-07-14):**没有去移植 PRNG——把散布提成了数据。**
      两侧原本各掷各的骰子:Pillow 抽**全局未播种 `random`**(Mersenne Twister + `normalvariate` 的
      Kinderman-Monahan + `int()` 截断),Rust 用 `(width,height,hour)` 播种的 xorshift64*(+ Box-Muller
      + `round()`),连 preset 颜色都是 4 个 vs 3 个。**统一种子是不够的,得连 PRNG、正态算法、取整规则
      一起对齐**——那等于把同一份逻辑写两遍,正是 IR-first 规则要禁的事。
      改为:新增 `src/sekai/base/triangle_bg.py`,按显式种子生成三角形列表(x/y/rot/size/rgba/type),
      `Painter` 直接画这个列表,`IRPainter` 把同一个列表放进 `TriangleBg.tris`(IR_CAPABILITY 6→7)。
      **两个后端的分歧从构造上消失**,Rust 侧净删 276 行(`SimpleRng`/`weighted_edge`/`time_lightness`/
      preset 计算全部退役)。顺带把 Pillow 的三角形改成**亚像素落点**(原本 `int(x) - w//2` 整数对齐,
      与 Skia 的浮点 path 天然差半像素,种子对齐也救不了)。
      成绩:Pillow **逐次可复现**(原 ~12% 像素 churn)、Skia **逐次可复现**(原种子含毫秒,每 3.6s 变一次)、
      纯背景画布两后端 **mean 0.55 / max 41**(原本是完全不同的两组三角形)。
      对拍全量跑两遍,**非确定性端点 51 → 8**;剩下 8 个(`sk_*` + `gacha_detail`)画的是**实时倒计时**
      (`time_to_end = event_end - now`),属内容随时间变,不是渲染器的不确定性。
      `skia_parity_sweep.py` 补上 `HARUKI_BG_TEST_HOUR=12.0`(legacy 基线本来就钉着)——**种子按整点量化,
      但调色板仍随小数小时平滑变化**(这是设计要的),所以要字节稳定的 harness 必须钉住这个 env。
      legacy 基线:7 个无三角背景的端点**逐像素一致**,48 个有的**只有背景变**(mean_delta 0.1–1.2/255),
      内容零改动。变异测试三条(种子退回小数小时 / scatter 退回全局 random / IRPainter 少发一个三角形)
      各自被对应用例抓住,见 `tests/test_triangle_bg.py`。
      **仍未做**:TriangleBg raster 缓存。现在种子可缓存了,但调色板按秒变,整张 bg 仍不可跨秒复用——
      要缓存得先决定"调色板是否也量化",那是个产品取舍,不是技术阻塞。

## 🔵 对拍之外的三道门(补上 sweep 的系统性盲区)

> `skia_parity_sweep.py` 只回答一个问题:**同一棵树,Pillow 和 Skia 画得一样吗**。它有三个结构性盲区,
> 各由一道独立的门补上。四道门缺一不可。

- [x] **`scripts/skia_warm_parity.py`**(2026-07-14):**缓存全开**跑。对拍调 `bypass_caches()`,**主动把
      composed/disk/payload 缓存全关**(为了让计时诚实),所以它的 63/63 只证明"重新画一遍是对的",
      **命中缓存拿回来的对不对,从来没有被验证过**——而命中才是生产的常态。做法:冷渲染做基准,再正序、
      **逆序**各热跑一遍全部用例;逆序那趟让每个页面在**其他 62 个页面填满的缓存**上渲染,这才逼得出
      key 碰撞、命中后被就地改写(PIL 图是按引用发出去的)、以及 Rust 共享栅格池的跨端点串味。
      基线:0 drift。**注意被判"非确定"的用例每次不一样**(取决于跑的时候时钟跳没跳),绿 ≠ 固定的一组被查过。
- [x] **`scripts/skia_bench.py`**(2026-07-15):**唯一的基准**。对拍的 `elapsed_pillow`/`elapsed_skia`
      字段已删除——它们**两个方向同时错**:① 对拍先跑 Pillow 再跑 Skia,且不 bypass 图片解码缓存,
      Pillow 付冷解码、Skia 白捡暖缓存(`mysekai_music_record` 报 10.39x,实际 1.12x);
      ② `compose_*_image()` 返回 **PIL 图**、`try_render_*_payload()` 返回**已编码字节**,只给 Skia 记了
      PNG 编码的账——凭空造出 6 个"Skia 更慢"的端点,一个都不存在(Pillow 编码 1536×880 要 110ms,
      Skia 连画带编 44ms)。新基准两边都产出响应字节、都从热态起跑、交替顺序、min-of-N,带 `--cold`。
      实测:**稳态 3.65x / 冷启动 2.73x**;honor 系是**唯一**真的慢于 Pillow 的端点(380×110 摊不掉
      IR+FFI+encode 的固定开销),量它时必须清 payload 缓存,否则量到的是缓存命中。

## 🔵 对 legacy 的像素基线(补上对拍的另一个盲区)

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
      cargo test 链接配方、IR-first 规则,以及本轮踩到的 5 个陷阱——对拍的 legacy 盲区、ImageBg fade/blur
      的 ref 穿透边界（2026-07-18 已移入 `Painter.image_bg`）、
      Painter.text 基线锚点、Pillow paste 拖低 dst alpha、resize 缓存按 resample 分键)。
- [x] **结构性防呆 CI 测试**(2026-07-14,`tests/test_route_render_contract.py`):递归枚举全部 `/api/pjsk` 路由
      (FastAPI 不摊平被 include 的 router,得自己下钻),断言 ①每个绘图端点都调 `try_render_*_payload`
      ②每个调了的都还留着 Pillow `compose` 兜底(fail-open)③没有新的手写 IR scene builder。
      豁免走显式白名单并各自写明理由(custom-profile 自带渲染器、两个 heavy-worker 路由的 Skia 调用在
      worker 里而非路由体——后者另有一条测试反向证明它确实还在,免得白名单变成藏污纳垢的地方)。
      第 ③ 条**扫 import 而不是扫文本**:`IRBuilder` 和 `build_canvas_ir` 两个门都得看紧
      (后者交出的是**可变**builder,能绕开前者另起一套布局),而文本扫描会被注释里提到名字绊倒
      ——这条测试自己的说明文字就绊倒过它。
- [x] **honor 回归共享 widget 树**(2026-07-14):`honor/widget.py` 的 `HonorBadgeBox` 是唯一布局,
      `_compose_full_honor_image_sync` 与 `skia._build_badge_scene` 删除;新增公共 Painter 原语
      `push_mask`/`pop_mask`(两端同语义:Pillow `ImageChops.multiply` = Skia `Group{mask}` DstIn,
      无 IR 变更)与 `paste_src`(Porter-Duff Src,底图四通道原样写入),外加 `Canvas.get_img_sync()`
      (custom-profile 的三处同步调用点)和公共 helper `skia_renderer.canvas.build_canvas_ir()`。
      11 个基线逐位一致;bonds 头像的 crop 顺序 drift(maxΔ52/5513px)一并归零。详见迁移记录条目 12。
- [ ] **chart 仍手写 IR——但包的不是布局**(2026-07-14 复核):`chart/drawer.py` 直接用 `IRBuilder`
      拼水印页脚外壳(谱面栅格由 `pjsekai-scores-rs` 产出,两后端同源;Pillow 侧是 crate PNG +
      通用 `add_request_watermark_to_image`),重复面仅水印页脚度量,且两边共用
      `get_watermark_render_spec`。honor 同形(徽章本体已是共享树,外壳里只剩 `SelfImage` 页脚)。
      维持现状即可;若哪天想再收一层,可把「水印页脚」本身做成一个公共 IR 外壳 helper。
- [x] 删 GIF/APNG helpers 死代码(2026-07-14,img_utils 全仓零调用方,-285 行)。
- [ ] Pillow 退役决策(D8):全量 Skia 稳定 ≥2 个活动周期、fallback≈0 后再议——删端点级双实现 +
      扩展改启动必需 + 删静默泛型回退,`last-dual-backend` tag + 镜像回滚兜底;"永久保留"亦可接受。

## 已完成(2026-07-12,详见 restart-plan 执行日志)

真实 payload 生成器 + 59 用例对拍 harness 入库;607MB 资产同步;修复 card/list 水印 footer 4px、
winrate 请求原地修改、area_item payload;安全加固(fail-open、mem 图强引用、事件循环卸载、env>yaml);
默认开关翻转;组件库覆盖审计缺口清零(emoji 字形覆盖路由、BlurGlass blur、夜间三角衰减、card 背景 fade)
+ 资产缺失告警;储备原语(separate 渐变、pixelwise 自适应、glass corners/shadow_width、mix 颜色矩阵);
card/box shim-first(手写 builder 与专用门控退役);chart 水印壳一进一出。
