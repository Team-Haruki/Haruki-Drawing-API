# Skia 迁移剩余工作清单

> 2026-07-12 盘点。迁移本体已完成:**59 用例对拍 58 ok / 1 pillow-only(honor)/ 0 失败**,
> `use_skia_plot`/`use_skia_card_list` 默认开,card/box shim-first 与 chart 一进一出均已落地。
> 本清单是切换后的收尾与生产化工作,按"挡在生产收益前面 → 端点残余 → 质量项 → 性能 → 收尾"排序。
> 完成一项就地打勾并注日期。相关:[`skia-migration-restart-plan.md`](./skia-migration-restart-plan.md)、
> [`custom-profile-skia-feasibility.md`](./custom-profile-skia-feasibility.md)。

## 🔴 挡在生产收益前面(不做这些,生产永远 fail-open 回退 Pillow)

- [ ] **CI wheel 流水线**(D2:仓库 GitHub Actions 构建 artifact,不发 index):maturin 出 cp314t wheel
      (生产 linux 架构 + macOS arm64 开发机),cargo/sccache 缓存,CI 断言 wheel tag ↔ Python 小版本。
- [ ] **CI 跑 native 测试**:装 wheel 后 parity/ir_painter/safety 测试不再 skipif 全跳;cargo fmt/clippy/test
      进 CI。前置:CI 字体供给(入库 OFL 思源子集或下载+cache);游戏资产类对拍仅本机跑。
- [ ] **Docker 集成**:docker workflow 取 wheel artifact → 镜像 pip install → import 自检
      (仿 pjsekai_scores_rs 模式);确认 libfreetype6/fontconfig/libgl1 覆盖 skia-safe 动态依赖。
- [ ] **IR capability 版本握手**:IR JSON 带版本号,旧 wheel 遇新 IR 抛显式错误进回退计数
      (wheel 与代码不经锁文件耦合,错配否则只表现为回退率飙升)。
- [ ] **全关金丝雀 → 生产放量验收**:带扩展镜像先全关(env)跑 48h 证明镜像无害,再开;
      验收含 mysekai 端点 200(drawer.real.py 与镜像 API 配对是 CI 盲区)。
- [ ] **PR #33 合并**(所有者暂缓中;分支每多活一天,main 插队漂移风险多一天)。

## 🟡 端点残余

- [ ] **honor 迁移**(M):① Group 级 image-alpha-mask 原语(saveLayer + DstIn,pixelwise 自适应的
      分层结构可照搬);② honor 场景构建器(~135 行绝对坐标合成);③ bonds/生日/活动/fc_ap 变体
      payload(生成器逻辑已写好,fixture 无对应称号,需合成);④ 水印壳照抄 chart 模式。
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

- [ ] **fpnge/mtpng PNG 编码替换**(M):encode 占 Skia 耗时 ~45%;music_list(0.81×)、
      mysekai_map(0.95×)、event_detail(0.95×)三个慢于 Pillow 的端点全是编码/传输主导。
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
