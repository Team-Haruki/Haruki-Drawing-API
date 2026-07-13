# Skia 迁移剩余工作清单

> 2026-07-12 盘点,2026-07-13 更新。迁移本体已完成:**63 用例对拍 63 ok / 0 失败(pillow-only 已归零)**,
> `use_skia_plot`/`use_skia_card_list` 默认开,card/box shim-first 与 chart 一进一出均已落地。
> 本清单是切换后的收尾与生产化工作,按"挡在生产收益前面 → 端点残余 → 质量项 → 性能 → 收尾"排序。
> 完成一项就地打勾并注日期。相关:[`skia-migration-restart-plan.md`](./skia-migration-restart-plan.md)、
> [`custom-profile-skia-feasibility.md`](./custom-profile-skia-feasibility.md)。

## 🔴 挡在生产收益前面(不做这些,生产永远 fail-open 回退 Pillow)

- [x] **CI wheel 流水线**(2026-07-13,skia-wheels.yml:linux-x86_64 + macos-arm64 矩阵、rust-cache、wheel tag 断言、IR_CAPABILITY 冒烟、artifact 上传)。
- [x] **CI 跑 native 测试**(2026-07-13,quick-check native-tests job:maturin develop + OFL 字体下载缓存 + 全量 pytest;素材类 parity 自动跳过)。
- [x] **Docker 集成**(2026-07-13,docker.yml 先构 wheel → docker/skia-wheels → 镜像条件安装 + 构建期自检;无 wheel 时 fail-open 构建仍绿,双分支本地实测)。
- [x] **IR capability 版本握手**(2026-07-13,native 暴露 IR_CAPABILITY=4,load_native_renderer 校验不足抛 ImportError 走 fail-open)。
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

- [ ] **可观测性(阶段 4)**:image.response 日志加 `backend=skia|pillow|skia_fallback` 字段;
      每端点成功/回退计数器挂 /render-stats(heavy worker 经 EncodedImagePayload 带回,否则对
      deck/chara-birthday 失明);`_SkiaPayloadCache` 接 /cache-stats + 纳入全局缓存清理。
- [ ] **影子层结果缓存推广(阶段 5)**:Pillow 时代有 composed/disk 缓存的端点在 Skia 路径补等价缓存
      (至少 profile、event/list、vlive/list;card/list、card/box、alias-list、chart? 已有 payload 缓存——
      chart 未接,顺手补);与门控端点名穿参合并为一次 try_render 签名改造;考虑给 profile/event 补磁盘层
      (重启即冷);双池预算共享计账。**本地对拍绕缓存所以没暴露,生产会 CPU 反升。**
- [ ] 头像框 9-slice 加 composed 缓存(每个带框 /profile 请求重做 700×700 合成,两后端同时受益)。
- [ ] **阶段 2 剩余安全/性能项**:
  - [ ] N3:Rust IMAGE_CACHE 单 Mutex + O(n) 持锁驱逐 + 无字节预算(RSS 上界 + 8 路并发扩展性)。
  - [ ] 字体缺失响亮化:Rust 静默回退 sans-serif 改 ERROR + 计数;lifespan 字体自检失败拒绝启用 Skia。
  - [ ] Skia 影子层补 4096² 画布守卫(Pillow 侧有断言,Skia 侧无防护)。
  - [ ] N6:IRBuilder._pil_font_cache 提升为进程级(现在每请求重新 truetype 解析)。
  - [ ] serde_json 解析移入 py.detach;TYPEFACE_CACHE 锁外加载字体;render 输出字节双拷贝消除。

## 🟢 性能后备队(指标不达标时按需提前)

- [x] **原始 asset 路径直传 + draw-time 缩放**(2026-07-13):pristine 图片发安全相对路径,Rust image LRU 解码;
      Skia 单次 draw 融合 resize + composite,生成/修改图自动回退 mem。代表场景 raw mem transport 下降 24%-100%,
      63/63 SBS 通过;当前 wall time 基本中性,收益集中在 FFI 拷贝与瞬时内存。
- [ ] **lazy AssetRef 穿透 widget/Canvas**(M):`music_list` 试点已完成,696 张 jacket 不再进入 Python 像素 cache,
      冷态 `2.44x`、热态约 `4.7x`,RSS 约 `2.0 GiB -> 0.30 GiB`;其余 builder 仍需按收益接入并复测 composed widget。
- [x] **Moka 目标栅格缓存 + Rayon 并行预热**(2026-07-13):按 asset identity/source rect/target/sampling 缓存,
      696 项仅约 11.4 MiB;music_list 冷启动串行 raster build `6.36s -> 0.83s`,暴露 stats/clear API 与 native metrics。
- [x] **mtpng PNG 编码替换**(2026-07-13):默认多线程 fast encode,保留 `HARUKI_SKIA_PNG_ENCODER=skia` 回退;
      代表大图 encode 提升 `2.9-5.9x`,最终 63/63 SBS 通过,文件大小变化约 `-2%` 到 `+6%`。
- [ ] Scene.scale 整图 resize → canvas 矩阵直渲染(M,需视觉验收;profile 1.5×/winrate 2× 受益)。
- [ ] 文本渲染缓存:每 Text 节点重复 measure_str("哇")、Left 对齐白测宽度、每节点新建 Font、
      adaptive 双重布局(S-M)。
- [ ] fs::metadata TTL(S);TriangleBg 按 seed 缓存 raster(M);mem 图 content-hash 跨请求缓存(L)。

## ⚪ 收尾与防漂移

- [ ] 文档修正:migration.md 过时项(card/detail 已完成、径向渐变已实现、字体缓存/PNG 调优已完成)、
      gaps.md 错误项(gacha 无条目缓存、honor 仅 mem、profile 预渲染是死代码)。
- [ ] CLAUDE.md:Skia 后端章节(开关约定 env-only、wheel/CI 链路、回滚手册、Python 升版顺序)+
      **IR-first 规则**("绘图端点唯一布局载体是 widget 树;缺原语才登记 gaps 临时 Pillow 兜底;
      手写 dedicated scene builder 需专门论证性能收益")。
- [ ] 结构性防呆 CI 测试:枚举全部路由,断言每个绘图端点绑定 widget 树/IR 路径(白名单显式豁免)。
- [ ] 删 GIF/APNG helpers 死代码(img_utils,全仓零调用方)。
- [ ] Pillow 退役决策(D8):全量 Skia 稳定 ≥2 个活动周期、fallback≈0 后再议——删端点级双实现 +
      扩展改启动必需 + 删静默泛型回退,`last-dual-backend` tag + 镜像回滚兜底;"永久保留"亦可接受。

## 已完成(2026-07-12,详见 restart-plan 执行日志)

真实 payload 生成器 + 59 用例对拍 harness 入库;607MB 资产同步;修复 card/list 水印 footer 4px、
winrate 请求原地修改、area_item payload;安全加固(fail-open、mem 图强引用、事件循环卸载、env>yaml);
默认开关翻转;组件库覆盖审计缺口清零(emoji 字形覆盖路由、BlurGlass blur、夜间三角衰减、card 背景 fade)
+ 资产缺失告警;储备原语(separate 渐变、pixelwise 自适应、glass corners/shadow_width、mix 颜色矩阵);
card/box shim-first(手写 builder 与专用门控退役);chart 水印壳一进一出。
