# Skia 迁移重启计划(除 custom profile 外全量)

> 2026-07-12 制定。基于对分支 `feat/rust-skia-render-ir` 的六路代码审计(门控/缺口/CI构建/性能/分支考古/逐端点矩阵)
> 与三视角方案对抗评审的综合定稿。配套阅读:[`rust-skia-renderer-migration.md`](./rust-skia-renderer-migration.md)(做了什么)、
> [`skia-pillow-coverage-gaps.md`](./skia-pillow-coverage-gaps.md)(能力差距;注意两份文档均有过时项,见阶段 8 文档修正清单)。

## 现状一句话

代码完成度 ~90%(50/53 端点已接线、Rust 已收敛为纯 IR 解释器、实测 1.4×–4× 收益),**上线完成度 ~0%**
(开关全关、生产镜像无扩展、CI 零 Rust、无灰度机制、无可观测性)。迁移是 06-23/24 一次 26.5 小时冲刺的产物,
之后被 release v2.3.19 与 main 上的 card box 业务需求(+744 行 Pillow)插队搁置。

日历预期:**到 Skia 成为默认后端约 5–7 周;到 Pillow 收尾处置约 10–12 周**。实际动手量约 4–5 周,其余为并行浸泡观察期。

## 已拍板决策

| # | 事项 | 决定(2026-07-12) |
|---|---|---|
| D1 | mysekai 私有实现托管 | **维持现状**:真实逻辑写在 `drawer.real.py`(gitignore),公开侧只有 stub。不做外部托管。API 漂移防护靠冒烟脚本(见阶段 3),镜像与 real 文件成对部署写入发布 checklist |
| D2 | wheel 分发 | **不发布到任何 index**。仓库 GitHub Actions 构建 wheel(artifact 形式),**可多平台**(生产 linux 架构 + macOS arm64 开发机)。pyproject/uv.lock **不声明**该依赖,保持 importlib 懒加载 + fail-open;Docker 构建消费 CI wheel artifact 并做 import 自检 |

待拍板(按截止时间排序,均附默认建议):

| # | 事项 | 何时 | 建议 |
|---|---|---|---|
| D3 | 三角形背景 RNG:确定性播种(可对拍可缓存,同参数出图恒定)vs 保持随机 | 波次 1 前 | 确定性,seed 去掉小时分量、测试可注入 |
| D4 | card/detail 字形步进差、mysekai 各端点:人工看图签字 | 各自放量前 | — |
| D5 | card/box:shim-first vs 手工移植 vs 重设计布局 | 阶段 7 前 | **shim-first**(见阶段 7) |
| D6 | sk 双 trace 永久保留 matplotlib 混合方案 | 波次 3 前 | 永久混合,不投入 L 级原语开发 |
| D7 | 过渡期双缓存(Pillow composed 池 + Skia payload 池)预算分配 | 阶段 5 | 共享单预算 |
| D8 | Pillow 退役力度与时间点 | 全量稳定 ≥4 周后 | 删端点级双实现,`last-dual-backend` tag + 镜像回滚兜底 |

## 关键路径

```
阶段0(资产救援) → 阶段1(合并origin/main+回main) → ┬ 阶段2(必修闸门)─┐
                                                    ├ 阶段3(部署链路)──┼→ 波次1 → 波次2 → 波次3 → 切默认 → Pillow退役
                                                    ├ 阶段4(门控/观测)─┘   (阶段7收尾项穿插在浸泡期)
                                                    └ 阶段5(缓存)──(阻塞对应端点入波)
```

---

## 阶段 0 — 资产救援(半天,立即)

1. **推送本地 9 个未推送提交**(28d3fd9..c126a25,含 card/detail 迁移、渐变文字、card 渲染器去重)到 origin。
2. `drawer.real.py`:按 D1 维持本机 gitignore。单机丢失风险由所有者接受;建议至少保证本机时间机器/快照覆盖。
3. **清点本机未跟踪资产**:`git status --ignored` + 扫 `out/`——文档中 50 端点对拍 mean/p99 数字的工具与 payload 语料
   必有来源,丢失则阶段 4 的重建成本从 M 滑向 L。
4. ~~修 stale 测试 `tests/test_ir_painter.py`~~(已完成 2026-07-12:渐变文字测试改为断言字形 overlay 渐变 fill)。
5. 给搁置点打 tag(如 `skia-sprint-freeze`)作回退锚点。

## 阶段 1 — 合并 origin/main,杀死长命分支(2–3 天)

本地 main 落后 origin/main 23 个提交;分支每多活一天漂移越大(+744 行缺口即由此而来)。

- merge(不 rebase);configs.yaml / pyproject.toml / uv.lock 机械解,uv.lock 用 `uv lock` 重生成。
- **`src/sekai/card/drawer.py` 实质冲突:以 main 侧为唯一真相源**(+744 行收集统计全保留,它们跑在 Pillow 路径上)。
  Skia 的 `card_render.py` box 渲染器**暂不追平:标记 stale,`use_skia_card_box` 误开时启动打 ERROR** 防呆。
- 合并后**尽快以"开关全关"状态 PR 回 main 并删除分支**——所有门控默认 false 且镜像无扩展,合入对生产零行为变化。
  此后一切工作以小 PR 落 main,不再养长命分支;与 main 同步从一次性动作变为例行项。
- 合并质量门:先做出批量对拍工具**最小版**(5–10 个代表端点、固定 payload、像素 diff),合并前后各跑一遍 Pillow 输出对比。
- **合并后重跑 card/list 等一层端点对拍**——"逐字节一致"是 06-24 对 06-17 Pillow 的旧证据,main 动过 card/drawer.py 后作废。

## 阶段 2 — 放量前必修硬闸门(3–5 天;任一项未完成禁止开任何 Skia 开关)

| # | 问题 | 修法 | 量级 |
|---|---|---|---|
| 2.1 | **canvas.py native import 在 try 之外**:扩展缺失+开关开 → 50 端点(含 heavy worker)直接 500 | 移入 try 回退 Pillow(fail-open);lifespan 启动自检:配置开了 Skia 但扩展缺失 → ERROR + `/ready` 暴露。响亮降级,拒绝"悄悄降级"与"直接 500"两个极端 | S |
| 2.2 | **N1 正确性(最恶劣)**:`ir_painter._mem_by_id[id(img)]` 不持引用,GC 后地址复用可能**渲染成别人的图** | 持强引用;回归测试用结构性断言(render 全程 mem 图引用存活),不写依赖分配器行为的 flaky 复现 | S |
| 2.3 | **N2**:card_render 的 build_scene+json.dumps 在事件循环线程执行,千卡 box 卡全局 | 对齐 canvas.py,整体进池 | S |
| 2.4 | **N3**:Rust IMAGE_CACHE 单 Mutex + 驱逐 O(n) 持锁全表扫描 + 无字节预算(RSS 无上界) | RwLock/分片 + AtomicU64 + 字节计账,上限可配 | M |
| 2.5 | **Rust 字体缺失静默回退系统 sans-serif** | 缺字体 ERROR + 计数;lifespan 字体自检失败即拒绝启用 Skia(自检放启动时——字体是运行时挂载,不能放镜像构建期) | S |
| 2.6 | **配置优先级陷阱**:yaml 写死的键屏蔽 HARUKI_ env(已实测)——事故现场 env 回滚会失效 | **根治**:settings.py 加 `settings_customise_sources` 使 env > yaml + 回归测试;立规矩"Skia 开关永不写 yaml,只留保守代码默认值 + env 翻",删 yaml 里 5 个写死的 card 键(删除后断言 effective 值不变) | S |
| 2.7 | Skia 影子层无 4096×4096 画布防护(Pillow 有) | 补同等守卫 | S |
| 2.8 | **N6**:IRBuilder 每请求重新解析 PIL 测量字体 | 提升为进程级缓存(不修会污染放量期 p99 对比基线) | S |
| 2.9 | 最后 9 个提交是冲刺末尾产物,未经审查 | code review 一遍,重点看 84f95e6 渲染器去重有无行为漂移 | S–M |

顺带修(不阻塞闸门):serde 解析移入 `py.detach`、mem 图拷贝链削减(Rust 借用一次成型 + Python 跳过冗余 convert)、
TYPEFACE_CACHE 锁外加载。

## 阶段 3 — 部署链路 wheel/Docker/CI(约 1 周,与阶段 2/4 并行)

按 D2:wheel 由仓库 CI 构建为 artifact,不发布 index。

- 新 workflow:maturin 构建 **cp314t wheel**(无 abi3,与 free-threaded 小版本强绑定——CI 断言 wheel tag 与 Dockerfile
  Python 版本一致;升 Python 必须同步重建 wheel,写入发布 checklist)。多平台矩阵:生产 linux 架构必做
  (x86_64-gnu;aarch64-gnu 视生产宿主,skia-safe 0.99 预构建覆盖需实机验证)+ macOS arm64(开发机便利)。
  cargo/sccache 缓存必配(本地 target 1.4GB,冷编译 CI 不可用)。
- **pyproject/uv.lock 不声明该依赖**(无 index 可解析);运行时保持 importlib 懒加载 + 2.1 的 fail-open。
  本机开发继续 `maturin develop`,或取 macOS wheel artifact。
- Docker:docker workflow 下载 linux wheel artifact → 镜像内 pip install → **import 自检**(仿现有 pjsekai_scores_rs
  自检模式)。已有 libfreetype6/fontconfig/libgl1 应覆盖 skia-safe 动态依赖,容器内实测确认。
- **CI 字体供给是 native 测试进 CI 的硬前置**:check in 可再分发的测试字体子集(思源黑体 OFL)或 CI 下载 + cache;
  游戏资产类对拍只在本机跑,CI 只跑节点级 parity。
- CI 测试 job:装 wheel 后真跑 parity/ir_painter/profile_card 测试(消灭 skipif 全跳盲区)+ cargo test/clippy。
- **IR 版本握手**:IR JSON 带 capability 版本号,旧 wheel 遇新 IR 抛显式错误进回退计数——否则版本错配只表现为
  "回退率无故飙升"。wheel artifact 与 git commit 关联,放量前核对生产镜像 wheel 含 2.x 修复。
- **drawer.real.py API 冒烟脚本**(D1 的配套):import + try_render_* 签名检查,列入每次动 skia_renderer API 后的
  checklist;发布 checklist 写明"镜像版本与 real 文件成对部署"。
- **全关金丝雀 48h**:含扩展的镜像上生产、开关全关——把"镜像风险"与"渲染风险"解耦。金丝雀验收必须包含 mysekai 端点 200。

## 阶段 4 — 门控与可观测性(2–4 天,与阶段 3 并行)

现状"一个全局布尔管 48 端点 + 零 metrics"= 盲飞。

- **门控 schema 一次定型**:`skia_default` + 逐端点 override(把 use_skia_card_list / use_skia_card_box 一并收编,
  不留三套机制)。override 的 env 形态用**扁平字符串**(如 `HARUKI_DRAWING__SKIA_DISABLE_ENDPOINTS="card/box,profile"`),
  不用嵌套 dict。切默认时只翻 `skia_default`,不改 schema。新门控一律现场读 settings,禁止模块级常量快照。
- **可观测最小集**:image.response 日志加 `backend=skia|pillow|skia_fallback` 字段;每端点 成功/回退(带原因)/直连
  Pillow 计数器,挂 `/render-stats`(或并入 /cache-stats)。**heavy worker 是 spawn 子进程,父进程计数器数不到它**:
  EncodedImagePayload 加 backend 字段随结果带回父进程计数。
- `_SkiaPayloadCache` 接入 /cache-stats;全局缓存清理纳入 Skia 池;删死配置 `skia_card_list_log_visual_metrics`。
- **批量对拍/基准工具正式落库 `scripts/`**:固定 payload 集 → 双后端渲染 → 尺寸断言 + 像素 diff + 耗时报告。
  diff 报告必须区分"新增 diff"与"已知可接受 diff"(TriangleBg 区域掩膜/固定 seed 注入、字形差异阈值白名单),
  否则签字流程退化为"反正有 diff 扫一眼"。payload 语料作为显式子任务(生产采样或手工构造并 check in)。
- 三角背景确定性播种(D3):统一 seed 去掉小时分量、测试可注入。
- parity 补缺:Shadow/TriangleBg/ImageBg/Watermark/Group-clip 五个节点无像素对拍。

## 阶段 5 — 缓存补齐(2–3 天)

**硬规则:Pillow 时代有 composed/disk 缓存的端点,未恢复等价缓存前禁止进放量名单。** 逐端点核实名单,
至少含 **profile、event/list、vlive/list**(misc/alias-list 已接)。否则开关一开这些端点从"缓存命中"变
"每请求全量重渲",CPU 不降反升,会得出"Skia 更慢"的假结论污染放量判断。

- 泛化 payload-cache 需给 try_render 调用点穿端点名+缓存键——与阶段 4 门控的端点名穿参**合并为一次签名改造**。
- 考虑给 profile/event 的 Skia 结果缓存补磁盘层:放量期"改 env + 重启"频繁,纯内存缓存每次重启全冷。
- 双池预算按 D7:过渡期共享单预算(现状各吃满一份 = 翻倍)。
- **头像框 9-slice 加 composed 缓存**(现在每个带框 /profile 请求重做 700×700 合成)——两个后端同时受益,立刻可做。

## 阶段 6 — 分层放量(日历 3–4 周)

统一验收线(每波,浸泡 48–72h):回退率 <0.5%(目标 <0.1%)且每次可解释、5xx 零新增、p99 不劣化 >10%
(**重启后暖机窗口不计入**)、RSS 走平、对拍报告通过。任一不达标 → env 摘除该端点,修完重进。
**波次 1 开启当天演练一次完整回滚链路**(env 摘除→重启→确认回 Pillow→计数器反映)。

**并发浸泡关**(每波):N1/锁竞争类 free-threaded 竞态,单请求对拍与低压烘焙测不出——
`scripts/concurrent_fetch_images.py` 以 ≥线程池大小的并发压 30min+,断言零 5xx、抽查输出 hash 零错图、RSS 平稳。

- **波次 1**(证据最全;合并后重跑对拍再进):card/list、vlive/list(缓存补齐后)、gacha/*、event/list(缓存补齐后)、
  score/* 4 个。**deck/recommend 移出首波**——heavy worker 子进程形态特殊(扩展加载/字体自检/缓存各一份),
  补子进程故障注入验证后单独放。
- **波次 2**:music/*6、education/*7、costume/*2、stamp、event/detail+record、misc/*2、sk/*6(scale 各异,csb 两档都对拍);
  card/detail 需人工看图签字(D4);**profile 本波最后**(最大流量 + scale1.5),低峰开;p99 不达标先做 fpnge PNG 编码替换
  (encode 占 Skia 耗时 ~45%,scale 端点像素 2.25×)再重试。
- **波次 3**:mysekai(每端点人工看图签字——无 parity、不在 CI,回退率仪表防不住"渲染成功但画错");
  card/box 等阶段 7 收口后放;sk 双 trace 按 D6 永久混合。

性能后备队(不阻塞,指标不达标时提前):fpnge/mtpng 编码(M)、Scene.scale→canvas 矩阵直渲染(M,需视觉验收)、
文本测量/Font 缓存(S–M)、fs::metadata TTL、输出双拷贝、TriangleBg seed 缓存、mem 图 content-hash 跨请求缓存(L)。

## 阶段 7 — 残余面积收尾(与波次浸泡穿插,1–2 周)

- **card/box:shim-first(D5 默认)**。main 的新功能写在 `compose_box_image` 的 plot.py widget 树里,card/detail 已证明
  card 端点可走 IRPainter 影子层——给 card/box 接影子层即**免费获得 main 的全部新语义**,并结构性消灭
  "改 Pillow 忘改 Skia"的漂移。`card_render.py` 手写 box builder 在 shim 版压测达标后**直接废弃**;
  只有性能不达标才考虑 dedicated builder(届时再谈移植/重设计)。user_info 分支随影子层自然覆盖
  (get_profile_card keystone 已验证)。card/list 顺带评估同样收敛。
- honor:新增 Group image-alpha-mask 原语(Skia saveLayer+DstIn,覆盖 bonds 的 putalpha)+ IRBuilder 重写
  (~135 行,无布局引擎依赖)+ parity 节点测试。(M)
- chart 水印壳(S,性价比最高,可提前穿插):crate 出的 PNG bytes 直接作 mem 图 + IR Watermark,
  消灭每请求大图 Pillow 解码→水印→重编码往返。
- mysekai msr_map 多图网格拼接迁 IR(drawer.real.py 内,注意与镜像 API 配对)。(M)
- 删 GIF/APNG helpers(死代码,全仓零调用方、零动图端点);separate 渐变/任意 mask 不做,登记为已知不支持。
- card_full_thumbnail 等子渲染:**保留 mem 图混合方案**(mem+disk 缓存健康,100 图 ≈4–8ms)——
  目标是"最终合成单路径",不是教条式清零 PIL import。

## 阶段 8 — 切默认与终态(观察 ≥4 周后)

- 切默认前置检查:所有未通过浸泡的端点已在 override **显式**关闭,再翻 `skia_default=true`。
  `HARUKI_DRAWING__SKIA_DEFAULT=false` 即全局 kill switch。
- **防再漂移机制(本计划最重要的长期交付物)**:
  1. CI native parity 每 PR 常驻(阶段 3 已建);
  2. 结构性防呆测试:枚举全部路由,断言每个绘图端点绑定 widget 树/IR 路径(白名单显式豁免),新端点没接 → CI 红;
  3. CLAUDE.md 写入规则(注意方向):**"绘图端点唯一布局载体是 widget 树(经 IRPainter 双后端通用);缺原语才登记
     gaps 临时 Pillow 兜底;手写 dedicated scene builder 需专门论证性能收益"**——写成"先写 scene builder"
     反而鼓励制造下一个 card_render.py 式双实现。
- Pillow 处置(D8):稳定 ≥2 个活动周期、fallback≈0 后,推荐删除各端点 Pillow 最终合成路径、扩展改启动必需、
  删静默泛型回退(单路径时代它是掩盖 bug 的机制);删除前打 `last-dual-backend` tag,应急靠镜像回滚。
  保守选项"永久保留回退"亦可接受。
- 文档修正:migration.md 四处过时(card/detail 已完成、径向渐变已实现、字体跨请求缓存已完成 c693bb9、
  PNG encode 调优已完成 8b21c8c);gaps.md 三处错误(gacha 无条目缓存、honor 仅 mem 缓存、profile 模块预渲染是死代码);
  CLAUDE.md 补 Skia 后端章节(开关约定、wheel/CI 链路、回滚手册、Python 升版顺序)。

## 收益兑现点

阶段 0–5 约两周完成后,波次 1(card/list、vlive、gacha、event/list、score)在第 3 周吃到实测 1.4×–4× 收益;
收益主体随 profile(第 4–5 周)落地。
