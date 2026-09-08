# Pillow 退役审计（更新于 2026-09-08）

生产依赖已移除 Pillow、Matplotlib 和 Pilmoji；普通路由与重任务不再调用 Pillow 回退。
原生 wheel、字体启动校验及发布前的完整渲染门槛均为必需条件。Pillow 保留在开发对照组中。
本文保留迁移过程中的分阶段结果；旧阶段的“未提交”描述仅适用于当时快照。当前变更已推送至
PR #77，已整合 main 至 e38f447，尚未合并此 PR 或部署。Linux 验证容器运行在 ARM 主机上，
其吞吐量不能当作实际生产硬件的容量。

最新验收范围按用户要求限定为公共实现：私有 MySekai 已迁移并单独验证，不决定最终公共发布
门槛。69 个公共样本具备像素和无 Pillow 服务证据；symbol/stamps 两个缺样本用例也按用户
要求仅作诊断，出现实际问题再修，不阻塞验收。
迁移后的私有实现也已在实际卸载 Pillow 的 Linux 镜像中通过 HTTP 检查，详见下文 stage30。


## PR #77 合并主线后的验证（2026-09-08）

已处理与 main 的历史重叠及冲突，并整合通过检查的依赖 PR #50、#76、#63、#62、#59、#53、#52。
主线的超大名片图层裁剪、资源路径校验、场景诊断及新增 IR 节点已保留，能力握手为 29。

在 2ecee0c 上重新运行全量 Python 测试：2463 passed，覆盖率 90.21%，保留 90% 门槛。
Rust 验证为 118 passed，扩展已重新构建。缓存、占位图、BASIC 字体及预备画布相关的
120 项回归在 macOS ARM64 和 Linux x86_64 上均通过，缓存开关前后的 RGBA 必须完全相同。

Linux 回归定位并修复了两处差异：Pillow 的 font_variant 会把值为 0 的 BASIC 参数当成未指定，
现改为显式构造 BASIC 字体；x86 的原始 RGBA 直接贴图与 N32 子画布快照采用不同舍入路径，
现统一通过子画布快照回放。后者增加了一次快照回放，历史性能倍率不可作为此整合版本的新测量。
CI 的覆盖率任务补齐字体，macOS wheel 使用 Xcode 16.4 构建所需 C++20 代码。

严格暖缓存报告现在保留 cold_repeat 哈希，使活动规划器的已知动态内容豁免具备两次成功冷渲染
的实际证据；缺失或失败的第二次渲染仍阻止验收。其余非确定性和任何缓存漂移仍阻止验收。
最终严格暖缓存验证：两侧各 76 ok、1 个已知动态样本、2 个诊断缺样本，0 缓存漂移、0 strict issues；
证据保存在本地 out/pr77-final-warm/results.json，原始捕获素材不提交。
最终严格冷缓存验证为 77 ok、2 个诊断缺样本、0 strict failures，包含无 Pillow 门槛；
报告为 out/pr77-final-strict/results.json。macOS 与 Linux wheel 均已通过 GitHub CI。

## 已迁移边界

- `base/image_source.py` 的 `AssetImageRef`、`EncodedImageRef`、`MissingImageRef` 只携带描述。
  `base/image_info.py` 通过 native header API 探测尺寸；Pillow 探测单独留作失败恢复。
- 缺失占位图的尺寸、几何和文字来自同一份 `base/placeholder.py` 配方。
  Pillow 与 native 分别执行它。新文件到达、已解析文件消失都重新检查，不缓存缺失路径。
- `paint_types.py`、`paint_context.py`、`font_metrics.py`、`text_layout.py` 拆出共享类型、坐标和 BASIC
  布局服务。`IRPainter` 不再继承或导入 `Painter`。旧导出仍兼容，Pillow 适配器按需导入。
- event/vlive 条目保留为嵌套 Canvas，由两个后端消费同一棵树；card/box 圆形头像使用同样方式。
  新增 native bicubic 采样、圆弧和椭圆裁剪。能力握手已同步到 Rust、Python 和两处 CI 断言。
- `PreResizedImageBox` 保留 misc 别名封面 92→84、生日日历头像 40→32 的 BILINEAR→BICUBIC
  两步流水线，素材在共享子画布执行时才解码。`emoji_layout.py` 以纯计算保留 Pilmoji 的 run 宽度规则，
  native 布局不再为了测量 emoji 导入 Pillow。上述两个页面与旧 Pillow 输出逐字节一致。
- help 面板已迁为共享子画布。`roundrect_src` 表达无抗锯齿的像素替换与内描边，
  `drop_shadow_roundrect` 保留分别模糊 RGBA 通道的彩色阴影语义，`text(mask_lerp=True)`
  保留 ImageDraw 在半透明面板上的遮罩插值；没有改动普通文字的合成默认值。
  help 与旧 Pillow 输出逐字节一致，原生对拍 mean 0.351、p99 2.0，满足原有预算。
- 角色别名图保留为 `AlphaTrimImageBox`：原生扫描非零 alpha 边界，仅返回坐标；共享子画布执行
  自然尺寸裁剪和 alpha LUT，再做 bicubic 缩放。全透明图片保留原尺寸，低于阈值的边缘仍参与裁边。
  扫描限制解码像素为 64 MiB，不保留新的全局像素缓存；目前每次布局仍需扫描该素材。
  真实角色别名页与旧 Pillow 输出逐字节一致，原生对拍 mean 0.789、p99 10，满足原有预算。
- costume 预览的前景检测已经迁入 native：先查 alpha 边界，再做 BILINEAR 缩略采样和逐行背景色判别。
  Python 仅保留裁剪坐标。原生与旧算法在透明、空白、渐变背景和不同缩放比例上给出相同坐标；
  真实页面加合成预览图后，新旧 Pillow 输出逐字节一致。
- gacha 缺图回退返回惰性占位引用；稀有度星星保留为共享子画布，拼接完成后才统一缩放。
  原有请求和双重缺图分支的新旧 Pillow 输出逐字节一致。终极灰色占位图成为第六个共享配方。
- 禁止 Pillow 的检查现在也记录被业务代码捕获的导入异常，防止缺失图片被换成文字后虚假通过。
- native 显式启用 WebP 解码；PNG/JPEG/WebP 前景判定均通过回归。原生探测将容量超限与损坏输入区分，
  超限仍可回到现有 Pillow 适配器，禁止 Pillow 的发布检查则会拒绝它。能力握手已升至 24；
  两处 CI 还会运行 `skia_codec_smoke.py`，实际检查 WebP 头部、像素、前景扫描和 IR 重放。
- SK drawer 不再导入 Matplotlib；两张曲线也通过共享向量树绘制。旧专用线程池及其生命周期钩子已移除，
  Agg 仅在 Pillow 重放向量原语时按需导入。
- SK 曲线的筛选、排序、50–60 分钟时速窗口、参考线优先级、颜色与标注已移入不导入 Pillow/Matplotlib
  的不可变 `trace_spec.py`。旧图表适配器消费同一份规格，9 个正常与边界分支的新旧 Pillow 输出逐字节
  一致（`out/trace-spec-legacy-stage15.json`）。排序不再修改请求对象；后续向量迁移沿用这份规格。
- 曲线坐标范围、自动刻度、科学计数法、偏移标签及数据到像素的变换已迁至 `trace_axes.py`；
  旧适配器也使用这份布局，不再自行选择坐标范围与刻度。单点扩展、反向排名轴、昼夜背景造成的
  横轴扩展均保留；45 组时间/时区与 205 组数值范围对照通过，9 组完整页面仍与旧输出逐字节一致
  （`out/trace-spec-legacy-stage16.json`）。该模块在禁止 PIL/Matplotlib 的新进程中完成实际请求布局。
  `python-dateutil` 从已有间接依赖声明为直接依赖，以便后续删除 Matplotlib；刻度算法适配的许可证
  保留于 `docs/licenses/matplotlib.txt`。后续向量绘制也消费相同的轴布局。
- 共享 `VectorPath` / `VectorText` 原语现已提供浮点直线、二次/三次曲线、闭合路径、非零绕组填充、
  居中描边、连续虚线及基线锚定的旋转/描边文字。`Painter` 使用延迟导入的 Agg 适配器；`IRPainter`
  发出原生路径，文字由共享 FreeType 字形轮廓表达，禁止 PIL/Matplotlib 的新进程可直接渲染二者。
  原生路径在任何资产访问前检查命令数、坐标、描边及虚线展开量，并限制整棵树的命令总数。
  Pillow 临时图层只分配与画布相交的几何区域，避免为每个散点分配整页图层；没有新增图片缓存。
  IR 能力已升至 27，支持解析椭圆；独立字体能力 3 提供有命令数限制的 hinted glyph 轮廓和字体度量。
  原生轮廓在设备坐标下以 4 倍分辨率积分覆盖，预乘 RGBA 做 box 平均；临时表面、读回和结果副本
  都在分配前检查节点像素与 scene 剩余字节预算，且只分配可见几何区域。父级变换与裁剪仍只应用一次。
- **SK player/rank trace 已接入共享向量树。** 刻度、旋转文字、散点、参考线、描边标注与图例不再先生成
  Python 位图。9 组旧完整页面对照的 RGB mean 为 0.240–0.412、p99 为 1–7，均在原接口预算内
  （`out/trace-spec-legacy-stage18.json`）；该阶段不再声称旧/新 Pillow 逐字节相等。
  当前树 Pillow ↔ native 的 mean 分别为 0.397 / 0.370、p99 均为 5，未放宽 7.3 的尾部预算。
  两条真实服务路由均通过完整生命周期、禁止 Pillow 与原生成功调用检查。
  `TracePlotBox` 直接挂到页面树，避免对数千个路径命令做子画布深拷贝；两后端的 alpha 平均差
  也由约 0.4 降至 0.002、最大差为 1。没有新增布局缓存或页面缓存。
  26 项向量回归和 22 项字体回归涵盖曲线、透明度、裁剪、父级变换、临时内存上限、字体缓存状态、
  空文本和非法输入。拉丁字偶间距与复杂文本 shaping 的更广泛分支仍需补充，不以现有中文/日期样本
  推断所有用户名称都完全等价。
- 普通 Canvas 的输出缩放恢复为「逻辑尺寸完成合成 → 原生 RGBA BILINEAR 缩放」，通过
  `Scene.post_resize` 表达；原有 `Scene.scale` 矩阵变换仅供明确需要直绘的 IR 调用方使用。
  这修复了 profile 的像素超差，Pillow 布局与输出均未更改。缩放过程不编码中间 PNG、不进入 Python
  像素处理；读回、滤波临时内存及编码副本均受 scene 内存上限约束。能力握手已同步至 25。

custom profile 的共享导入边界也已拆开：`card_prefab.py` / `general_prefab.py` 只保留不可变绘制
描述、布局和度量协议；Pillow 重放移至 `pillow_card_prefab.py` / `pillow_general_prefab.py`，旧导入名
按需兼容。资源路径选择移至不加载绘图器的 `resource_paths.py`。`PNGRenderer` 中仍需 Pillow 的像素执行函数
显式延迟导入 `pillow_runtime.py`，构造元数据、排序 Unity 元素及原生场景构建不再先导入 Pillow；
没有代理或空实现来吞掉真正的像素处理。执行旧 `render_card()` 仍会触发 Pillow，并被禁止导入检查拒绝。
旧像素算法和解码策略保留，两个现有 custom profile 用例的前后对拍图逐字节相同
（`out/custom-before-stage19/`、`out/custom-after-stage19/`），且它们均通过完整服务纯原生检查。

已有样本通过不代表所有 custom profile 分支都已迁移。描边用例
`custom_profile_card_outlined_text` 从真实普通文字请求派生，只留下一个可见文本，将描边宽度设为
0.25、描边 alpha 设为 1。这个用例曾在像素差为零时仍触发 Pillow，现在已通过禁止 Pillow 的
绘图入口和完整服务检查，像素差仍为零。该用例一直保留在完整严格门槛中，沿用父接口预算；
生成器和前置契约会拒绝关闭描边、隐藏文本或混入其他元素的请求。

TMP 字形数据已继续迁至不可变 `GrayField`（`custom_profile/gray_field.py`）：FreeType 无 hinting
位图、向量距离场、位图距离场回退和动态字形缓存均保存紧密灰度字节，不再构造 `PIL.Image`。
既有字形缓存键与容量规则保持不变；NumPy 的零复制视图为只读，裁剪返回独立数据或原不可变对象。
原生 `resize_gray8_bicubic` 与 RGBA 共用滤波系数、逐轴 uint8 舍入及极高窄图的处理顺序，
按单通道分配，不扩展为 RGBA、不编码中间 PNG。独立 `GRAY_FIELD_CAPABILITY=1` 随 codec smoke
检查；没有新增 IR 节点，IR 能力仍为 27。输入/输出各限 16 Mi 像素，滤波临时内存与返回副本
受 128 MiB 工作预算约束；明确的原生容量拒绝不会在灰度适配器内重试 Pillow。

64 组灰度缩放对照逐字节一致。真实 TMP 字体在禁止 Pillow 的新进程中完成向量/位图距离场生成、
跨请求缓存命中及字符字段裁剪缩放；三个 custom profile 样本的前后完整对拍图也逐字节相同
（`out/custom-gray-legacy-stage20.json`）。

描边文本的布局、着色与合成也已继续迁移：`build_tmp_text_box_layout` 是两个后端共同使用的
文本框、基线、支点和审计信息来源；`tmp_sdf_text.py` 保存局部字形场与裁边描述。TMP 字体表或
源字体能回答度量时不创建 Pillow 字体，只有真实度量回退才加载旧字体并记录 `pillow_text_metric`。
Python 共用原着色器的 alpha 覆盖公式来确定 uint8 非零范围与裁边，不创建 RGBA 字形或页面；
原生 `SdfQuad` 完成着色，嵌套 `RasterSubscene` 保留裁边后 object scale → position scale 两次
独立 bicubic 缩放。整体旋转通过已有 `UnitySubscene` 放置成品层，并校正非居中支点；没有放宽
`Transform` 子树限制，没有新增 IR 节点。字形场累计容量在分配下一个字段前检查场景剩余预算。

四个描边变体（普通/旋转/非等比缩放/带颜色、粗体与透明度的富文本）分别在禁止 Pillow 的
新解释器中成功渲染，测试还禁止进入旧文字栅格函数。除了全页预算，也裁出实际字形区域比较：
未旋转样本 mean 为 0 / 0.0013；旋转样本 mean 为 0.476 / 1.078、p99 为 9 / 12，低于
固定的字形区域 mean 2、p99 25 预算（`out/outline-detail-stage21.json`）。三个完整 custom profile
样本的旧/新 Pillow 像素及整个对拍图逐字节相同（`out/custom-sdf-legacy-stage21.json`）。

该阶段尚未迁移静态 atlas 解码、每字旋转及其他旧文字模式；每字旋转仍有
明确的原子回退测试。上述四个变体不能证明所有富文本、字体回退和未捕获的 symbol/stamps 均已脱离 Pillow。

装饰文字的灰度场仿射变换已迁到 `gray_affine.rs` / `GrayField.transform_bicubic`。该实现依据
[Pillow 12.3 Geometry.c](https://raw.githubusercontent.com/python-pillow/Pillow/12.3.0/src/libImaging/Geometry.c)
保留像素中心映射、四邻域插值、越界零填充、边缘钳制和末尾 uint8 截断；不能用 resize 的
scale-adaptive bicubic 或 Skia 的 Catmull-Rom 代替。许可证保留在 `docs/licenses/pillow.txt`。
独立灰度能力升至 `GRAY_FIELD_CAPABILITY=2`，codec smoke 实际执行变换；IR 仍为 27。
仿射 API 在分配前限制维度、像素数、字节长度与有限系数，返回值加 Python 字节副本最多 32 MiB，
借用原输入而不复制。装饰场准备阶段还逐个检查保留字段的累计场景预算。

84 组原生仿射 ↔ Pillow 对照逐字节一致，涵盖极小图、非等比缩放、平移、旋转/剪切、镜像、
奇异映射、越界与随机矩阵。三个完整装饰文本变体在禁止 Pillow 的新进程中成功渲染。
新增正式用例 `custom_profile_card_decorative_text` 是从现有请求派生的装饰分支，包含彩色 markup、
23° 旋转和 1.4×0.7 缩放，契约禁止去掉 markup、旋转、非等比缩放或把元素移出画面；它没有
冒充尚未捕获的 symbol/stamps 请求。该用例像素差为零，并通过完整服务的纯原生检查。
四个 custom profile 样本的旧/新 Pillow 像素与整个对拍图逐字节相同
（`out/custom-affine-legacy-stage22.json`）。静态 atlas 转入灰度字段、度量回退等实际 Pillow
边界仍记录触碰，不能因为最终字段类型变成 GrayField 就把这些回退误报为纯原生。

静态 TMP atlas 也已接入原生 alpha 解码：`asset_alpha_field` 在 Rust 内解码 RGBA，向 Python
只返回不可变 A8 数据；`atlas_field.py` 将其装入 `GrayField`，静态描边字形沿用已有的共享布局和
原生 `SdfQuad` 着色。独立 `ALPHA_FIELD_CAPABILITY=1` 由 codec smoke 实际调用检查，IR 仍为 27。
配置的图层像素上限与硬上限（16 Mi 像素、单边 32767）都在像素分配前检查；明确的容量拒绝不会
在该适配器中重试 Pillow。显式 RGBA 与 alpha 缓冲峰值最多 80 MiB；RGBA 释放后才复制 Python
返回字节，此时两个 A8 副本最多 32 MiB。路径限制在操作员配置的 TMP atlas 目录内，允许元数据
位于全局素材根目录之外。

静态 atlas 使用既有 `SPRITE_ATLAS_CACHE` 的单通道容量计量，键带路径、mtime_ns、大小和独立
`atlas_alpha_gray8` 变体；删除了原有只按路径保存的实例缓存。每次访问重新检查文件签名，解码期间
文件变化则不写缓存；冷命中、热命中都检查当前请求的图层上限。扩展缺失、过旧或运行错误仍可
进入明确的旧解码边界，但结果不写入原生缓存，每次转换都保留 Pillow 触碰记录。

两套真实提取的 DB/EB OnDemand 字体、8-bit PNG/WebP（RGBA、RGB、LA、L、调色板透明度）和
16-bit RGBA/LA PNG 的 alpha 均与旧解码逐字节一致。替换、删除、解码期间替换、并发读取、缓存
禁用/容量不足、损坏与越界路径均有回归。两套字体的可见描边文字都在禁止 Pillow 的新解释器中
成功渲染。正式新增 `custom_profile_card_static_text`，固定静态字体、已存在字形和可见位置；
前置契约还要求对应静态 glyph 表与 atlas 文件存在，防止意外测成动态字体或占位图。
该样本完整页面的 mean/max/p99 与 alpha 差均为零，且实际服务请求报告 `native_pure=1`。
旧页面与旧静态 run 重放也逐字节一致（`out/static-atlas-legacy-stage23.json`）；旧模式仅在
裁出单个字形后进入 Pillow，不为每个字形复制整个 atlas。该阶段仍未覆盖每字旋转、度量回退和
其他旧文字模式，不能据此声称所有 TMP 分支都已退役 Pillow。

TMP 每字旋转现已继续迁移。`rotation_geometry.py` 保留旧 `Image.rotate(expand=True)` 的中心、
反向矩阵、15 位系数舍入、输出尺寸和直角快路径语义；`tmp_native_glyph_position` 是 Pillow 与
native 共用的字形放置计算。Alpha 裁边仍使用共享着色标量先量化到 uint8，再经已有的原生灰度
仿射操作求出旋转后的非零区域；旋转输出在分配前检查灰度硬上限、图层上限和场景剩余字段容量。

合成顺序保持「SDF 着色 → RGBA 字形旋转 → 文本框合成 → object scale → position scale」
顺序。每个旋转字形用已有 `UnitySubscene` 旋转着色结果，外层 `RasterSubscene` 保留展开尺寸和
整数位置，再进入原有文本框缩放。实际 RGBA 旋转采用已有 Skia Catmull-Rom 采样，在固定的
旋转字形误差预算内验证；不声称它与 Pillow bicubic 完全相同。没有把旋转提前到距离场，也没有
把 RGBA 字形传回 Python。
此次复用 IR 27 和灰度能力 2，未修改 Rust、能力编号或旧回退机制。

装饰文字与每字旋转的组合也走这条路径：旧 direct-field 分支会拒绝可见的旋转字形，随后转入
局部 RGBA 文本框；native 现在保留同样的选择。只给空白字符加 `<rotate>` 不会触发这个选择，
仍保持直接灰度场变换后着色，避免改变其他装饰文字的像素顺序。

44 组展开旋转 alpha 与 Pillow 逐字节一致，涵盖直角、接近直角、负角度、超一周角度及奇偶/极小
尺寸。七个普通/静态字体变体和两个装饰组合分别在全新禁止 Pillow 的解释器中成功渲染，包含
混合角度、颜色、透明度、对象旋转和非等比缩放；测试禁止进入旧字形绘制、RGBA 着色和 Pillow
旋转函数，并检查原生子画布内实际含有 SdfQuad、传输只有 A8 字段及可见字形区域的固定预算。
六个独立普通旋转样本的字形区域 mean 为 0–0.730、p99 为 0–8，90°/180°逐字节一致；
装饰旋转组合 mean 0.166、p99 5，均低于原有字形预算 2 / 25。六个旧/新 Pillow 页面逐字节
一致（`out/char-rotation-legacy-stage24.json`），装饰组合也与改动前的旧页面逐字节一致。
原有五个 custom profile 完整对拍图均逐字节不变（`out/custom-rotation-legacy-stage24.json`）。
相应证据还包括 `out/char-rotation-after-stage24/results.json`、
`out/decorative-rotated-stage24-result.json`、`out/char-rotation-tests-stage24.log`。

独立暖态 benchmark（两端都返回 PNG 字节、预热后 7 次取最小值）中，每字旋转样本 Pillow
13.5 ms、native 7.3 ms，装饰旋转组合 33.0 / 26.0 ms；普通装饰文字为 31.0 / 24.5 ms。
这是相对完整 Pillow 路径的本地单请求结果，不代表相对旧混合路径或发布环境的性能提升。
两个新样本的 PNG 分别从 52.6 / 22.8 KiB 增至 75.2 / 48.0 KiB；编码大小仍是实际的权衡。
证据保存在 `out/char-rotation-bench-stage24.json` 和同名 `.log`。

正式新增 `custom_profile_card_rotated_characters` 与 `custom_profile_card_rotated_decorative`，
均来自已有捕获请求的明确派生分支，沿用父接口预算；契约禁止移除每字旋转或隐藏元素。
这些用例不能证明未覆盖的 em-block、大字号文字、字体度量回退及其他旧渲染模式都已脱离 Pillow。

TMP 度量回退现已优先调用原生 BASIC 字体接口，扩展缺失、过旧或异常时仍进入显式旧适配器并
记录 Pillow 触碰。审计发现 U+200B 会触发源字体缺字度量：本地 FOT DB 在 90 px 下的旧 BASIC
行为是 bbox `(0, 81, 90, 81)`、advance 90；虽然字符不可见，不能擅自把 advance 改为零。
新适配器保留这些值及原有取整规则，没有新增字体缓存。

12 个实际 TMP 字体请求中，11 个已在禁止 Pillow 的新进程中成功；全部 12 个旧/新 Pillow 页面
逐字节一致。旧基准显式强制使用旧度量适配器，避免共享新布局掩盖漂移。原有七个 custom profile
完整对拍图也逐字节不变（`out/font-audit-stage25/after-results.json`、
`out/custom-font-legacy-stage25.json`）。剩余 FOT-RodinNTLGPro-EB-OnDemand 在静态 atlas
缺少 emoji、罕见字、组合字符等时仍进入旧字形栅格回退，属于独立于度量的实际阻塞。
新增正式用例 `custom_profile_card_font_fallback` 与 `custom_profile_card_static_missing_glyphs`，
契约固定字体、混合文本、字号和可见位置，并验证实际资产；前者纯原生，后者明确保持门槛失败。
因此严格失败数从 10 增至 11 是新增覆盖暴露了旧回退，并非已有用例退化。

本轮度量只验证 BASIC 基准。本地 macOS 和 Linux 隔离检查镜像的 Pillow 12.3.0 均未启用 RAQM；
严格退役检查已新增 RAQM 前提校验，并将全局问题写入结果 JSON。若基准 Pillow 默认使用 RAQM，
必须先建立相应布局等价性，不能让两后端共同使用 BASIC 后互相对拍而虚假通过。
Linux 另完成 11 个字体文件 × 7 种字号 × 10 组文本共 770 组旧/新度量对照；新解释器在禁止
Pillow 下执行 1540 次原生度量调用，结果全部一致（`out/font-adapter-linux-stage25.log`）。
这是 Ubuntu 隔离环境的度量适配器验证，不是带完整资产的生产 Linux 镜像验证。
106 项专项检查通过（`out/font-metrics-tests-stage25.log`）；本轮未修改 Rust，沿用此前已验证的扩展。

静态字体缺字的最后一级灰度 mask 生成已接入独立 `basic_text_mask` 原生接口，复用已有 BASIC
FreeType 布局与栅格代码，返回紧密 A8 字节、bbox、ascent 和 advance；新增独立
`TEXT_MASK_CAPABILITY=1`，IR 仍为 27，原生轮子检查会验证接口能力与参数拒绝。
输出在分配前限制为最多 16 Mi 像素、单边 32767，字符数、字号、调用方像素预算也有检查；
返回 Vec 与 Python bytes 副本合计最多 32 MiB，不创建 RGBA 或中间编码图片。
`font_field.py` 把结果包装成不可变 GrayField，空 ink 保留独立 bbox 中的非零 advance。
扩展缺失、过旧或运行失败仍通过 `pillow_fields.py` 显式恢复并记录触碰，容量拒绝不重试 Pillow。
没有新增字形像素缓存，字体 face 复用既有有界缓存。

`prepare_tmp_fallback_sdf_character` 共享默认逐字回退的 bbox、padding、横向缩放与灰度字段生成，
保留 FX 模式的字形居中缩放以及 x 模式的整张 mask 缩放。随后调用原有距离变换，保留未量化的
float32 距离场。旧 `render_tmp_sdf_run` 的逐字兜底现在消费这份字段；非默认多字符 run 的
小数定位旧分支仍待迁移，没有用整数位置冒充其完整语义。
19 项 mask 检查通过，包括缺字、组合字符、空 ink、字号取整、超限拒绝、恢复与并发访问。
38 项 TMP 专项检查通过，其中 252 组字符/字号/缩放组合的 float32 字段与旧 mask 流水线完全一致；
在全新禁止 Pillow 的解释器中，五个实际缺字字段由成功的原生 mask 调用生成。
证据为 `out/font-field-tests-stage27.log`、`out/fallback-field-tests-stage27.log`。
静态缺字完整页面与迁移前逐字节一致（`out/static-missing-legacy-stage27.json`）；
九个现有 custom profile 完整对拍图也逐字节不变（`out/custom-mask-legacy-stage27.json`）。
Linux amd64 隔离镜像也完成 100 项 Rust 回归及实际无 Pillow mask 生成。
另以 Pillow 12.3.0 BASIC 为基准，对 12 个实际字体文件、6 种字号、12 组文本共 864 组
mask 的 bbox、尺寸和字节做对照；全新禁止 Pillow 的解释器调用当前原生 API，全部逐字节一致
（`out/mask-linux-stage27.log`）。这仍是字体/原生接口验证，不代表带完整资产的 Linux 发布服务验证。

这一步仍未使静态缺字页面成为纯原生：字段的后续着色与页面重放仍进入旧 Pillow 分支。
现有 SdfQuad 只接受 A8，而该回退的距离变换输出是未量化 float32；直接转成 A8 会改变着色。
下一步需扩展原生字段传输/着色，保留其精度，并采用旧回退的基线和旋转放置规则；不能把它
按普通 TMP atlas 字形强行缩放到 mesh quad。严格门槛继续保留该用例为失败。

缺字回退的 float32 字段现已接入原生着色与局部文字层：`FloatField` 保存不可变、紧密的
little-endian float32 字节；`mem:` 传输新增 `f32le` 标量格式，复用 SdfQuad 的着色公式，
无需转换成 RGBA 或量化成 A8。IR 能力升至 28，RAW_BUFFER_CAPABILITY 升至 3；Rust、Python
与两处 CI 握手同步更新，codec smoke 实际绘制 float32 字段并确认其输出区别于 A8 量化结果。

字段仅允许有限的 0..1 样本、精确字节长度、紧密 stride 与不可变 bytes；可写数组及其只读
memoryview 均拒绝借用。Rust 保持 bytes owner 存活并按 little-endian 读取，不强制对齐、不复制
整个 float 字段。格式错误直接拒绝整个请求，float 字段也不能作为普通 Image 使用。
SdfQuad 着色前检查节点像素数和场景剩余内存，包括同时存活的 RGBA patch 与 SkData 副本；
嵌套子画布和请求字段已计入既有预算。失败不再静默跳过字形。

普通 atlas 和缺字回退保留各自的布局语义：atlas 字形按 mesh quad 放置，缺字字段保留自己的
bbox、padding、pen baseline，不能拉伸到 atlas quad。两条旧路径甚至使用相反的逐字旋转方向：
atlas 为 +rotate，`draw_run_at_baseline` 的回退为 -rotate；原生的着色后旋转与 alpha 裁边
现在分别遵循它们。之后仍执行原有的文本框合成、object scale、position scale 和对象旋转。

静态 EB 字体的 15 个装饰字符也缺少 atlas 字形（`out/decorative-missing-stage28.json`）。
Skia 现与旧后端一样先尝试装饰字段，整条 direct 计划拒绝后再准备局部文字层；已有装饰字形
仍保持 field warp → shade 的顺序，缺字则采用局部 shade → RGBA transform。测试禁止把旋转
字形提前做 field warp；没有把非旋转的正常装饰页改为局部文字层。

22 项 float32 检查覆盖未量化着色、透明度、underlay、非法字段、所有权、endianness、Image
误用及节点/场景临时内存拒绝（`out/float-field-tests-stage28.log`）。静态缺字的普通文字、
半透明文字、对象旋转/非等比缩放、混合逐字旋转和装饰文字均通过禁止 Pillow 的新进程，
固定的字形区域预算仍为 mean 2 / p99 25；两项原有装饰旋转顺序检查也通过
（`out/static-float-tests-stage28.log`）。单用例完整服务检查已报告 `native_pure=1`，无回退，
最大 RGB 差 1、alpha 完全一致（`out/parity-missing-stage28/results.json`）；单用例检查本身
因覆盖不全仍返回非零，不能代替下方完整严格门槛。

缓存变化：event/vlive 条目不再写入或读取旧 composed-image 磁盘层。Pillow 路径保留现有全局、
有容量限制的内存片段缓存；native 路径重新执行子树并复用 native 素材缓存。
未新增页面输出缓存，也未新增每请求 resize 缓存。冷启动/重启的性能影响尚需专门基准测量。

HonorDeck 的非自然尺寸称号现已接入现有原生 Lanczos 子画布能力。每个称号仍先按自然尺寸
合成共享 HonorBadgeBox，再缩放到槽位，最后对整个面板执行 Unity 的缩放与旋转；称号缩放
没有并入面板变换，也没有在 Python 中生成中间图片。同尺寸槽位保留原有路径。未添加 IR
节点或新缓存，仍使用 IR 28。所有槽位继续先解析后提交，缺失槽位仍原子回退，不能少画后
报告纯原生成功。

新增 `custom_profile_card_honor_deck_resized`，从捕获请求中保留 HonorDeck，并互换主副
槽位的实际称号，分别触发 180→380 和 380→180 缩放。旧尺寸拒绝条件在内存中恢复后，
同一请求确实进入 `render_general_honor_deck` 并触发 PIL 导入
（`out/honor-deck-before-stage29.json`）；源文件未为该复验回滚。新实现的轴对齐与
旋转 23°、非等比缩放变体均通过禁止 Pillow 的新进程。可见面板区域的 mean ≤2、p99 ≤30，
没有用白色页边稀释误差；整个旧/新 Pillow 基准 RGBA 逐字节一致
（`out/honor-deck-legacy-stage29.json`）。针对性回归 73 项通过
（`out/targeted-retirement-stage29.log`）。新增正式用例 mean 0.048、p99 1，alpha 完全一致，
沿用原接口 mean 2 / p99 25 的预算；此前九个 custom profile 完整对拍图逐字节不变
（`out/custom-honor-legacy-stage29.json`）。

分支生成器现会生成全部 12 个派生请求。此前三个文字构造函数虽已存在，`generate()` 漏掉了
装饰逐字旋转、缺字度量、静态缺字的调用；干净目录测试现要求所有已注册派生 Case 都有实际
输出文件。新增称号样本校验可见位置、三个槽位和实际素材尺寸，缺图或同尺寸素材不能让
缩放测试虚假通过。校验自身使用原生尺寸探测，可在禁止 Pillow 的进程内执行。

## 可复现证据

```bash
uv run python -X gil=0 scripts/skia_no_pillow.py
uv run python -X gil=0 scripts/skia_parity_sweep.py --strict
uv run python -X gil=0 scripts/skia_warm_parity.py --backend both
```

`skia_no_pillow.py` 对每个真实请求启动全新解释器，在导入 drawer 前拒绝所有 `PIL` 导入；
被捕获、吞掉的 Pillow 导入尝试也会失败；同时要求至少一次成功的 `native.render_scene` 调用、非回退结果和一致的编码尺寸。
片段/响应缓存关闭，避免旧像素使检查虚假通过。缺样本、崩溃、超时和回退都使命令失败。

`skia_parity_sweep.py --strict` 要求像素预算、上述独立检查和服务请求检查同时通过；没有纯原生证据不能算成功。
局部 `--only` 运行只供开发排查，不能代替完整发布验证。现有 CI pytest 覆盖门槛本身以及真实的
惰性圆形头像、两步缩放、alpha 裁剪与完整 help 渲染；完整资产/私有请求审计仍须在有数据的发布环境执行。

现有 67 个用例之外，新增 12 个分支用例：`costume_detail_preview`、`costume_detail_preview_webp`、
`gacha_list_missing_assets`、`gacha_detail_missing_assets`、`custom_profile_card_outlined_text`、
`custom_profile_card_decorative_text`、`custom_profile_card_static_text`、
`custom_profile_card_rotated_characters`、`custom_profile_card_rotated_decorative`、
`custom_profile_card_font_fallback`、`custom_profile_card_static_missing_glyphs`、
`custom_profile_card_honor_deck_resized`，沿用原接口预算。运行 `scripts/parity_payloads/gen_retirement_branches.py`
可从原请求生成它们；`run_all.py` 已包含这一步。描边文本额外需要先由 `gen_custom_profile.py`
生成真实基础请求；没有该捕获时不伪造输入，严格门槛会拒绝缺失的派生样本。合成预览图写入素材根目录的
`utils/retirement_fixtures/`。门槛会验证预览图存在、尺寸正确、请求确实激活该分支，并验证故意缺失的
素材确实不存在，避免占位图掩盖样本失效。

stage29 完整结果（保留当时包含私有实现的旧验收范围）：

| 检查 | 结果 | 本地证据 |
|---|---|---|
| 冷进程禁止 Pillow | 69 ok、8 blocked、2 no-payload | `out/parity-retirement-stage29/results.json` 中的 `no_pillow` |
| 当前树 Pillow ↔ Skia | 77 ok、2 no-payload，所有有样本用例均在预算内 | `out/parity-retirement-stage29/results.json` |
| 严格退役门槛（像素 + drawer + 服务） | 69 通过、10 失败；69 个服务请求均为纯原生 | `out/parity-retirement-stage29/results.json` |
| Linux 实际卸载 Pillow 的 HTTP 请求 | 69 ok、101 次成功原生调用、零 Pillow 导入；正常退出 | `out/linux-service-stage29/summary.json`、`container-state.json` |
| 旧 Pillow ↔ 新共享树 | event/list、vlive/list、card/box、misc alias/birthday、help、角色别名图、gacha/costume 分支逐字节一致 | `out/lazy-image-audit/legacy.json`、`avatar-legacy.json`、`misc-resize-legacy.json`、`help-legacy.json`、`alpha-trim-legacy.json`、`gacha-costume-legacy.json` |
| 热缓存正序/逆序 | 两个后端各 76 ok、1 nondeterministic、2 no-payload，零漂移 | `out/warm-retirement-stage29-results.json`、`out/warm-retirement-stage29.log` |
| Python 回归 | 1185 passed、2 skipped | `out/pytest-retirement-stage29.log` |
| Rust 回归 | macOS 与 Linux 各 94 单元测试、7 集成测试通过 | `out/rust-retirement-stage28.log`、`out/linux-retirement-stage28.log` |
profile 的超差已修复：mean 从 2.993 降到 1.547（预算 2.514），p99 从 47 降到 15（预算 32.5）。
没有放宽预算；绘图入口和完整服务请求均通过禁止 Pillow 的检查。合成透明图、两个 PNG 编码器、
JPEG、非整数倍率、微小倍率变化、非法尺寸和内存上限均有专项回归。

独立 `skia_bench.py` 暖态对照（profile、PNG、两边均输出响应字节、7 次取最小值）显示：
原生直绘约 73 ms，完成画布后缩放约 57 ms；对应 Pillow 约 143 ms。此次修正没有出现本地延迟回退，
但 PNG 体积由约 0.97 MiB 增至 1.01 MiB。结果位于 `out/profile-resize-bench-stage14/`，
只代表该本地样本，不代替发布环境的并发与内存测试。

曲线性能使用 `skia_bench.py::bench_case` 单独测量，两边均输出 PNG 字节、先预热、7 次取最小值。
玩家页曾因嵌套子画布的 IR 深拷贝回退至约 155 ms，改为直接挂载组件后约 113–118 ms，
与旧混合 Skia 路径的约 113 ms 接近；排名页从旧混合路径约 101 ms 降至 69–70 ms。
新原生 PNG 比旧混合路径大约 2.2% / 2.6%。旧纯 Pillow 页面约 172 / 159 ms，
新共享树的 Pillow 回退约 258 / 169 ms：玩家页的旧后端变慢，不能把新后端相对它的加速比例
当成相对旧线上路径的提升。证据为 `out/trace-bench-stage18.json`、`out/trace-bench-hybrid-stage18.json`；
这些是本地暖态单请求结果，实际并发、峰值内存和 Linux 完整服务性能仍须验证。

## 剩余工作与顺序

| 范围 | 当前阻塞 | 需要完成 |
|---|---|---|
| 字体布局环境 | 原生度量只完成 BASIC 等价验证；RAQM 基准被严格门槛拒绝 | 在实际发布字体/布局环境建立等价性 |
| custom profile 未覆盖文本分支 | 非默认 em-block/旧模式保留 Pillow；参数覆盖仍不足 | 补充实际字号、富文本和非默认模式样本；保留共享 Unity 布局载体 |
| 私有 MySekai | 已迁移；8 个原有样本和 3 个派生 HTTP 请求无 Pillow 成功 | 部署时挂载迁移后的真实文件；按用户要求单独记录，不作为公共发布验收标准 |
| symbol/stamps | 无真实 payload，用户已明确排除发布验收 | 作为诊断保留；出现实际问题时补样本修复 |

69 个通过只覆盖各自已有的请求样本，不证明所有参数分支都已脱离 Pillow。还需补充 emoji、
缺失/替换素材、不同字号、半透明素材、旋转缩放、不同导出格式及跨请求缓存污染场景。
costume 预览和 gacha 缺图分支现已加入固定门槛；其他接口仍需同样按实际分支逐项审计。

TMP 的 ctypes FreeType face 缓存失效已修复：每次访问检查 `(mtime_ns, size)`，替换或删除后
释放旧 face；打开期间签名变化则释放新 face 并拒绝本次读取，下一次调用重新打开，不缓存失败。
既有进程缓存改为最多保留 16 个 face 的 LRU；释放、淘汰与字形读取共用同一把锁，避免释放仍在
被其他线程使用的可变 glyph slot。没有新增每请求缓存，也没有改变字形加载或采样参数。

7 项专项测试覆盖两种字体的度量与 A8 位图、替换、删除与重建、损坏后恢复、打开期间替换/删除、
LRU 淘汰释放，以及并发字体替换、度量、栅格读取和 close。真实字体复验也通过：临时路径从 FOT DB
原子替换为 BabyPop 后，现有实例的 A advance 从 71.015625 更新为 53.1875，与新实例一致，
位图也一致；全新解释器拒绝 PIL 导入且成功执行整个复验。
九个现有 custom profile 完整对拍图与修复前逐字节一致
（`out/custom-face-cache-legacy-stage26.json`），两个后端的完整热缓存正序/逆序检查均零漂移。
旧故障证据为 `out/freetype-face-replacement-stage25.json`，修复证据为
`out/freetype-face-replacement-stage26.json`；没有修改真实字体资产。

静态 EB 字体缺字的默认路径已完成原生 mask、浮点距离场、着色与放置迁移，完整服务
门槛通过；旧路径轨迹保留在 `out/static-missing-path-stage26.json`。与上一阶段相比，九个
custom profile 用例的 Pillow 基准 RGB 均逐字节相同，八个完整对拍图相同；静态缺字的
Skia 输出最大 RGB 差为 1、alpha 完全一致（`out/custom-float-legacy-stage28.json`）。

对普通 API 的退役范围，还应区分以下发布工作与可选旧模式：

- 默认服务构造 `PNGRenderer` 时没有传入 CLI 的旧文字模式开关。非默认 em-block/旧模式
  是否继续支持应独立决定，不能把所有 CLI 选项都当成普通 API 的必需迁移范围；默认路径
  仍有 `_build_scene` 的 hybrid 重放入口，需要按真实资源、HonorDeck、字体及变换分支补证据。
- `.github/workflows/docker.yml` 已构建 wheel，但 Dockerfile 仍允许 wheel 目录为空且原生模块
  缺失时构建成功。退役版需要把安装、导入、能力握手与实际渲染 smoke 变为构建和启动必需条件。
- 当前严格检查脚本尚未作为完整资产发布作业接入 workflows；需要把冷进程、完整服务与
  热缓存检查设为对应发布范围的门槛，缺样本或 hybrid 不能默认为成功。
- `uv.lock` 显示 `matplotlib` 和 `pilmoji` 都依赖 Pillow。删除直接依赖时必须同时移走这些
  旧后端运行依赖；像素对照工具仍需要 Pillow，应放到独立开发/验证环境，再确认生产依赖树。
- 私有 MySekai 已按用户要求迁移，且用同一无 Pillow 验证镜像只读挂载真实文件验证成功。
  它不作为公共发布门槛；生产部署仍必须使用迁移后的真实文件，公共 stub 不提供私有绘图功能。

服务启动依赖现已拆开：MySekai 私有绘图器与 custom-profile 在对应请求到达时才加载；
普通 profile 和公共 housing 页面不再受它们的顶层导入影响。该阶段未修改公共 MySekai stub
和私有实现；私有实现后来经用户明确授权在 stage30 迁移，公共 stub 仍保持原样。
磁盘清理迁至不导入 Pillow 的 `painter_cache.py`，与 Painter 读写仍共用同一把锁。
字体自检优先直接验证 native 字体解析，成功时不再检查 Pillow；native 不可用时仍验证并启用旧后端。

新增 `scripts/skia_service_no_pillow.py`，通过临时 `sitecustomize` 在服务和所有 spawn 子进程安装导入守卫，
记录实际成功的原生渲染和所有 Pillow 导入尝试，包括被捕获的尝试。通过 TestClient 执行完整 FastAPI
启停、路由和中间件，并检查返回字节、Content-Length、原生调用和 `native_pure` 计数。
帮助页和两个重任务实际请求已通过（`out/service-retirement-stage13.json`、`out/service-heavy-stage13.json`）。
严格对拍现在要求像素合格且 drawer 纯原生的每个用例还通过该服务检查；用例通过 OpenAPI 请求模型
映射到真实路由，测试断言全部绘图路由都被覆盖。默认单独运行脚本仅为帮助页 smoke，不代表完整发布验证。
这条脚本本身仍是 ASGI 内部请求验证。下面的独立 Linux 网络检查补充实际卸载包后的运行证据。

本地 `haruki-service-retirement:stage29` 验证镜像沿用生产 Dockerfile 的 Debian trixie、
锁定依赖、FreeType 修复、CPython 3.14.3t 与 Granian 启动方式，注入现有 Linux IR 28 wheel，
再实际卸载 Pillow、matplotlib 和 pilmoji。容器内独立检查三者均不可发现且 GIL 关闭。
验证用 Dockerfile 另排除本地 Rust target 产物，生产 Dockerfile 未改、没有推送或部署。
构建记录为 `out/linux-service-build-stage29.log`，配方为 `out/Dockerfile.retirement-stage29`。

服务仅绑定本机回环端口，4 CPU / 4 GiB 限制、两个重任务 worker；素材和配置只读挂载。
本地字体实际在素材根目录，因此覆盖 `HARUKI_FONT__DIR=/pjskdata/Data`，保持配置中的
Source Han 与 Linux TwemojiMozilla 字体选择。没有将字体自检失败绕过：首次错误挂载的
失败日志单独保存在 `out/linux-service-stage29/startup-font-path.log`。

69 个已具备原生样本的请求通过真实 HTTP 顺序执行，每个均返回正确编码尺寸与
Content-Length，`native_pure` 和 `skia` 各增加 1，cache-hit/fallback/hybrid/error 均为 0。
包括两个重任务和新 HonorDeck 分支。四个进程安装导入守卫，主进程及重任务子进程均实际
渲染，共记录 101 次成功原生调用、0 次 Pillow 导入尝试。编码响应、逐请求结果和运行证明
位于 `out/linux-service-stage29/network/`、`summary.json`、`runtime.json`。测试后正常停止
并清理容器，退出状态与日志留存。这是已有样本的 Linux 无 Pillow 运行验证，不是 Linux
Pillow 基准的像素对拍，也不是并发压力测试；后续 stage30 补充如下。

WebP 的最小 Skia feature 组合没有上游预编译库，会进行源码构建；本地 macOS 已构建并通过回归，
CI 已补充构建工具依赖。Linux x86_64 隔离环境已完成源码构建、85 项 Rust 测试和无 Pillow 的 WebP 渲染检查
（`out/linux-retirement-stage11.log`）。最终能力版本 24 的增量复验最初复用了旧产物；
强制重新编译当前 crate 后，能力握手、WebP 检查及 85 项 Rust 测试全部通过
（`out/linux-retirement-stage12.log`）。能力版本 25 的最终画布缩放也已在同一环境通过 WebP/尺寸检查
及 87 项 Rust 测试（`out/linux-retirement-stage14.log`）。能力版本 27 的解析椭圆、设备坐标覆盖和 Source Han 字形轮廓也已在无 Pillow 的 Linux 新进程中
实际渲染通过，并完成 92 项 Rust 回归（`out/linux-retirement-stage18.log`）。
这不代替带完整资产的 Linux 页面对拍与发布验证。

上述迁移完成后，还需在 Linux 3.14t 发布环境验证字体/RAQM 差异、完整冷/热缓存像素结果及
并发内存/性能，并将已有无 Pillow 实际路由验证接入发布门槛。随后让 wheel 成为必需依赖、启动时验证 native 能力，最后删除
Pillow 回退与依赖。当前继续保留失败恢复，不能把扩展缺失或过旧直接变成线上 500。


## Stage30：Linux 像素、缓存、并发与私有 MySekai

公共 Linux 参考镜像 `haruki-service-reference:stage30` 与实际无 Pillow 镜像同源，保留 Pillow
12.3.0 / FreeType 2.14.3 / BASIC（RAQM 关闭）用于对照。69 个公共有样本用例全部满足原有
像素预算，并在冷进程与完整 ASGI 服务检查中拒绝 Pillow、实际成功调用 native。
Linux 双后端热缓存正序/逆序各 68 ok、1 event_planner 实时倒计时、8 私有未挂载、2 缺样本，零漂移。
证据：`out/linux-parity-stage30/cold/results.json`、`out/linux-warm-stage30/warm-parity/results.json`。

对照 stage29 已保存的实际无 Pillow HTTP 响应，69 个均满足该 Linux Pillow 基准的预算；其中
68 个解码 RGBA 与 Linux 原生冷参考逐字节相同，event_planner 因倒计时不同而不相同，仍在预算内。
证据为 `out/linux-parity-stage30/network-pixel-results.json`。`skia_parity_sweep.py --save-images`
现在可额外保存原始 RGBA 的 `_reference.png` / `_native.png`，供外部 HTTP 对照使用；不改变默认
输出或预算，测试覆盖半透明与全透明像素下的 RGB 保留。

默认缓存开启的无 Pillow 服务另执行 68 个稳定样本正序/逆序、四并发，共 136 个请求。
136 个编码响应与旧冷 HTTP 基准字节相同；132 次 Skia 渲染、4 次 honor payload-cache 命中，
`native_pure=136`，fallback/hybrid/error/disabled/unclassified 均为 0，188 次成功原生调用，
0 次 Pillow 导入尝试。容器 4 CPU / 4 GiB、2 个重任务 worker，250 ms 采样的 cgroup 峰值
2136.88 MiB、父进程 RSS 峰值 1425.38 MiB。81.83 秒的混合工作负载、1.662 req/s、p50 943 ms、
p95 10202 ms 仅记录该验证环境，不是迁移前后的加速比。

**这次并发脚本退出 1，不能报告为完整负载门槛通过。** 165 次 `/ready` 采样中 6 次返回 503，
对应异步任务数超过现有阈值 256；绘图请求全部成功，容器内存低于内存门槛。没有修改阈值来让测试
通过；发布容量仍需评估这些大素材列表的短时任务数峰值。证据：`out/linux-load-stage30/summary.json`、
`requests.json`、`memory.json`、`service.log`。测试容器已正常退出，未 OOM。

私有 `src/sekai/mysekai/drawer.real.py` 已在用户明确授权后保留备份并迁移，仍被 gitignore 排除。
共享颜色与类型改从 `paint_types` 导入，Pillow 仅留在旧 compose 适配器的延迟类型注解中。
生日天气先在共享 50×50 Canvas 中以原生 Lanczos 缩放，再用离散矩形行表达原有 5 像素叉号；
不等尺寸多地图用隔离子画布、最小宽高中心裁剪和整数拼接，保留无 mask 复制及最终合成顺序。
素材始终为 lazy refs，未新增每请求像素缓存、手写 IR 场景或 Rust 能力。

8 个原有请求的新旧 Pillow RGBA 全部逐字节一致；生日开始、透明背景、三地图半透明背景三个
派生请求也逐字节一致。另用奇数尺寸、透明/半透明像素和越界内容组成的小网格检查 Src 合成，
三个背景 alpha 下的新旧 Pillow 与新旧 Skia 各自逐字节一致。
证据为 `out/private-mysekai-stage30/legacy.json`、`checks.json`、`alpha-grid.json`。
`checks.json` 的本地服务部分仍加载公共 stub，因此记录 blocked；它没有被当作迁移失败或成功证明。

按实际部署方式，将真实文件只读挂到无 Pillow Linux 镜像中的 `drawer.py` 后，8 个原有请求与
3 个派生请求均通过真实 HTTP，16 次成功原生调用，请求阶段 0 次 Pillow 导入尝试。
8 个原有响应全部满足独立 Linux Pillow 预算，且 RGBA 与 Linux 原生参考逐字节一致；
Linux 热缓存两个后端各 8 ok，零漂移。证据位于 `out/private-mysekai-stage30/linux/` 的
`network/results.json`、`summary.json`、`network-pixels.json` 和 `runtime.json`，以及
`linux-reference/warm-parity/results.json`。请求结束后的包清单探针曾被导入守卫拒绝；该操作单独
记录于 `post-request-audit.json`，包缺失证明随后在同镜像独立进程完成，未混入请求阶段统计。

`Case.release_required` 由登记的 Case 决定，结果行不能自行伪造“可选”。严格覆盖检查仍拒绝
缺少公共结果、预算、重复登记与未映射样本；私有 MySekai 的缺失或诊断失败不改变公共发布结论。
`out/linux-parity-stage30/public-scope/results.json` 用新范围重新分类既有 Linux 证据：69 通过、
2 个 symbol/stamps 缺样本、8 个私有诊断不参与验收。它明确标注为范围重分类，未冒充新渲染。
专项门槛检查 41 passed；全仓 1193 passed、2 skipped；Ruff check/format 全部通过。
私有文件单独检查保留原有 4 项 lint 问题，未增加新问题；没有因此改写无关私有逻辑。

stage30 当时尚未删除生产依赖或回退。其时记录的剩余发布工作是补齐公共缺样本分支、接入完整资产门槛、处理容量
验证中的 readiness 表现，并使 wheel/原生启动成为必需条件，之后才能从生产依赖树中移走 Pillow
及依赖它的旧后端包。私有 MySekai 不再是该依赖删除工作的 Pillow 迁移阻塞项。


## Stage31：生产依赖与服务回退退役

用户进一步明确 symbol/stamps 不作为验收标准，出现实际问题再修。因此发布范围为 69 个必验
公共样本，另有 8 个私有 MySekai 与 2 个未捕获分支作为诊断。没有放宽像素预算。

普通路由和两个重任务统一使用 `require_native_payload`；返回 None 会报告明确的原生渲染错误，
不再执行旧 compose 或 Pillow 编码。custom-profile 的原生场景也移除了最后的局部 Pillow raster
恢复路径：不支持的可见元素标记 unresolved，整张卡拒绝输出，避免把缺失内容当成功。
校验 ValueError 仍可映射为 HTTP 400，不借助旧 composer 重现错误。

启动在创建后台任务和重任务 worker 前验证原生能力与文字字体。缺扩展、过旧扩展、文字字体
无法解析、`use_skia_plot=false` 均拒绝启动。Pillow/Matplotlib/Pilmoji 已移入 `legacy-renderer`
开发组，默认 dev 组包含它们供对拍使用；生产 `uv sync --frozen --no-dev` 不安装它们。
fontTools 仍被 TMP 轮廓与备用度量使用，已从旧 Matplotlib 间接依赖改为直接依赖，防止依赖清理
悄悄切换字形算法。

Docker 要求恰好一个匹配平台的 wheel，调用 `load_native_renderer()` 检查当前能力，实际执行
codec smoke，并断言三个旧后端包不可导入。标签工作流复用 `skia-wheels.yml`，在 GitHub
托管 runner 构建并安装 wheel，完成 ABI、能力握手与 codec smoke 后上传。镜像作业下载
同一次运行中已验证的 Linux wheel，构建成功后才推送。

2026-09-09 调整：完整素材对拍保留为手动验收，不再要求每次标签发布重复执行。
仅可选的 `renderer-release.yml` 手动工作流要求可信 Linux x86_64 runner 和 checkout 外的素材、payload、配置文件路径。
仓库变量为 `RENDER_VALIDATION_RUNNER`、`RENDER_ASSETS_DIR`、`RENDER_PAYLOAD_DIR`、
`RENDER_CONFIG_PATH`；配置内字体路径应适用于该 runner。未设置这些输入会在 preflight 失败，
该手动验收不会静默跳过；普通标签发布不依赖这些变量或自建 runner。

`skia_release_gate.py` 只接受全新输出目录，依次运行严格冷对拍/无 Pillow 完整服务和严格双后端
热缓存。超时、缺报告或任何阶段失败都令该手动验收失败。严格热缓存检查拒绝缺样本、无渲染路径、缺少
哈希、未知结果、重复结果及无法解释的漂移；只有 event_planner 的已知实时倒计时可在两个成功
冷渲染确实不同的前提下记录为 nondeterministic。返回 None 不能冒充时间变化。
严格冷门槛还核对实际 OpenAPI 绘图路由，防止新增路由通过“不登记 Case”逃过检查。

本地最新全量测试为 1215 passed，之后新增的路由覆盖门槛检查也通过（44 项专项）；Ruff 全树
检查与格式检查通过。证据：`out/native-required-stage31/pytest-final.log`、`coverage-tests.log`。
本地严格全扫的 69 个必验样本通过，10 个诊断不影响退出码：
`out/native-required-stage31/parity/results.json`。Linux 实际发布命令与最终生产镜像的请求/像素
证据保存在同目录的 `linux-release/` 和 `linux-final/`，最终结果如下。


### Stage31 最终验证结果

- Linux `skia_release_gate.py` 实际完成，cold/warm 均退出 0，`release.json` 的 `passed=true`。
  冷门槛 69 ok；热缓存 Pillow/Skia 各 68 ok + 1 已知倒计时 nondeterministic，无漂移。
  两阶段均另有 8 个私有 skipped、2 个 symbol/stamps no-payload，不参与必验范围。
- 新增 OpenAPI 覆盖检查与最新严格热缓存函数再次审计保存的 Linux 报告，零问题，记录在
  `linux-release/latest-gate-audit.json`。这是对已生成证据的再审计，不冒充重新渲染。
- 最终生产镜像 `haruki-production:stage31-final`（本地 image ID
  `sha256:2568b46d65d4da745d3023aff49707e96e4994aced2ad1c6a6426a143a66c3dd`）由生产 Dockerfile 构建，
  仅将 wheel 来源替换为已构建的匹配 Linux wheel。实际环境没有 PIL、matplotlib、pilmoji，
  保留 fontTools，GIL 关闭；构建时能力握手与原生 codec smoke 均通过。
- 该镜像经实际 HTTP 完成 80 个绘图请求（69 公共 + 8 私有 + 3 私有派生），全部 native_pure，
  113 次原生渲染调用，4 个进程安装导入守卫，2 个进程实际渲染；Pillow 导入尝试为 0。
- 69 公共及 8 私有响应均满足各自独立 Linux Pillow 参考的原有像素预算；其中 76 个与 Linux
  原生参考 RGBA 逐字节一致，唯一非一致的 event_planner 含实时倒计时、仍满足预算。
  另外 3 个私有派生响应与上一生产 HTTP 检查逐字节一致。
- 证据：`linux-final/runtime.json`、`summary.json`、`responses/results.json`、
  `network-pixels.json`；验证容器正常退出（exit 0、非 OOM）并已删除，保留日志与容器状态。
  Pillow 仅在宿主开发环境读取响应以作独立像素对比，未装入被测生产服务。

目标核对：惰性图片引用/原生探测与占位图、共享子画布合成、后端接口隔离均已有上文专项和完整
服务证据；私有真实实现已迁移且保持 gitignored；发布必验范围明确，普通及重任务路由均已撤掉
Pillow 恢复调用，生产依赖不含旧后端。开发用 Pillow composer 和惰性适配器仍保留供参考检查，
不构成生产渲染恢复能力。未采集的 symbol/stamps 按用户要求留待实际问题修复。

标签发布使用 GitHub 托管 runner，不再需要上述自建 runner 和素材变量；完整素材验收应在
渲染或缓存逻辑变化时手动执行，本地完成不等同于远端 CI 已运行。此前 stage30 压测中的 readiness 阈值触发仍是独立容量发现，未将该压测称为
全通过，也未擅自调整阈值；所有图像响应成功及 Pillow 退役结果不依赖容量门槛放宽。


## 慢端点优化：event/list 与 honor（2026-09-07）

修正测速口径：`skia_bench.py` 原先遗漏 `Case.route_watermark`，将 honor 的 Pillow 徽章本体与
Skia 徽章加页脚比较。现已两侧包含页脚，并检查最终尺寸一致。此前 4 个 honor 样本的速度比和
包含它们的总加速比作废；新的结果使用修正后的完整响应。

`native_fragment_cache` 是有大小、字节权重和 TTL 上限的进程级原生 PNG 子图缓存。显式 Canvas
缓存键按原有 composed-cache 契约携带请求、布局与时间依赖；快速命中另行核对画布尺寸、后端
配置、代码指纹及保存的素材/字体签名。只有包含 TriangleBg 的子图才受其当前小时影响，避免
静态条目每个请求都因背景时间变化而失效。直接 honor 子树以完整 IR 内容作键，不包含父级页脚。
它与 Pillow composed、honor payload 共用三项配置值，但占用独立的内存池；统计和全局清理均接入。

源子树允许素材引用和可识别的原生占位符字节；任意 encoded 输入和 Pillow 像素不进入缓存。
占位符调用者的键必须带缺图签名，以便素材到达后失效。缓存仅接收 native.render_scene 生成的 PNG。原有
`require_asset_backed=True` 的嵌入路径不改用内存图。条目冷渲染保持 RasterSubscene 严格素材与
采样语义：开发时发现 plain-root 渲染会改变 vlive 的缩放次序，已通过同样的隔离层修正。
最终 event/list、四个 honor 和 vlive 新旧 Skia RGBA 均逐字节一致，缓存命中输出也一致。

真实时间（未固定背景时刻）且每次 dt 不同的完整服务检查完成 12 次请求，记录 27 次原生片段
缓存命中、95 次原生调用、3 个守卫进程，Pillow 导入尝试为零。证据：
`out/skia-slow-endpoints/guarded-warm/summary.json`；新旧像素比较：`native-pixels.json`。


最终本机暖缓存测速（完整 PNG 响应、交替顺序、9 次取最小值）：event/list 的 Skia 从 219.59 ms
降至 41.74 ms（Pillow 60.62 ms），bonds 从 5.89 ms 降至 4.19 ms（Pillow 6.22 ms）。其余
honor 在修正页脚口径后本来就更快，缓存增加约 0.1–0.3 ms，未将此算作优化收益。6 个检查项
最终均快于当前 Pillow 合成路径。新增片段池在该轮计费 5.45 MiB / 27 项；首次生成需要额外 PNG
编码，收益针对暖缓存，未将其称作冷启动加速。完整前后数据见 `out/skia-slow-endpoints/SUMMARY.md`。

最终全量测试 1226 passed；严格冷对拍 77 ok（69 公共、8 私有诊断），零失败；热缓存两后端
各 76 ok + 1 已知倒计时 nondeterministic，严格门槛零问题。新旧原生像素比较涵盖所有受影响的
6 个样本，RGBA 完全一致。Ruff check/format 与 git diff --check 通过。本轮无 Rust/IR 能力变更。

## 旧 main 混合路径退化复核（2026-09-07）

旧侧固定为 goal 前取得的 origin/main `c616b79`（3.0.5，默认混合路径、IR 20），当前为
`9f46d39` 加上述片段缓存及本轮优化（IR 28）。两者不是单一父子提交链，不能把所有版本差异
都归因于退役 Pillow。

本轮修复透明边界重复扫描、原生占位内容无法命中片段缓存、重复依赖 stat 和原尺寸离屏复制；
将卡片元数据探测分批，并在 Rust 对大图的精确 bicubic 缩放并行处理独立行，保持原舍入顺序。
活动列表在共享树恢复遗漏的卡片编号，样本尺寸恢复至旧 main 的 994×739。

全部验证结束后独立三进程轮转测速，69 公共样本均成功；每侧预热后测 5 次取最小值，完整 PNG
响应合计旧 main 12.688 s、修复前 9.564 s、修复后 8.968 s：修复后相对旧 main 1.41×，相对
修复前 1.07×。独立 9 次重点复测：角色别名 101.79→23.54 ms，VLive 304.40→156.81 ms，
SK 查询 88.27→68.00 ms。活动列表、VLive、生日/普通别名页仍慢于旧 main，未宣称全部追回。
剩余主要方向是受内存上限约束的原生解码片段复用及两阶段缩放缓存，尚未实现。

68/69 公共样本修复前后 RGBA 完全一致，唯一变化为恢复编号的活动列表。Python 全套
1231 passed、Rust 101 passed；后续缓存细节另通过定向测试和最终完整热缓存门槛。严格冷
对拍 77 ok，最终两后端热缓存零漂移；12 次实际 ASGI 热请求有 53 次片段命中、零 Pillow
导入尝试。符号/贴纸无请求体样本按用户要求不作验收。完整排序、复测、限制及原始证据见
`out/regression-fix/REPORT.md`。本轮未提交或部署；Rust 改动需为目标平台重建 wheel。

## 第二轮退化优化：解码片段与两阶段缩放（2026-09-07）

原生片段池改为插入时解码一次、保存不可变预乘 RGBA，回放通过现有 raw transport 零拷贝读取。
容量/TTL 沿用原池，无新增独立缓存池；旧扩展和超过 64 MiB 的片段保留 PNG 传输。共享转换函数
同时覆盖 IRPainter 与 honor 的直接 IR 入口。PreResizedImageBox 缓存完整两阶段滤波结果，
已经完成缩放的缓存片段在整数原尺寸放置时省略重复的离屏转换。滤波及透明边缘规则保持不变。

同轮三进程完整 PNG 暖缓存测速，各 5 次取最小值：普通别名 28.73→18.66 ms（1.54×），
生日页 48.77→35.92 ms（1.36×），VLive 186.73→155.99 ms（1.20×），活动列表
38.54→32.31 ms（1.19×）。旧 main c616b79 对应耗时为 20.74、40.79、161.14、17.93 ms：
生日/普通别名已追回，VLive 接近持平略快，活动列表仍未追回。69 项合计本轮前 8.166 s、
本轮后 8.025 s（减少 1.7%），旧 main 11.271 s；重点端点的收益不等于全业务吞吐量收益。
另外复测首轮看似慢 >10% 的 5 个非重点样本，各 9 次后差距均在约 3% 内，未复现大幅退化。

最终 69/69 公共输出与本轮前 RGBA 完全一致；严格冷门槛 77 ok、零问题，完整两后端热门槛
零漂移。Python 全套 1241 passed，最终 transport 修正后 34 项定向通过；Rust 101 passed。
符号/贴纸无请求体样本继续按用户要求排除。完整结果、排序、指纹及验证见
`out/regression-fix-round2/REPORT.md`。本轮未提交、推送或部署。

## 活动列表正式提前命中（2026-09-07）

`base/canvas_cache.py::prepare_cached_canvas` 已接入共享缓存层；native 页面以异步 Canvas
工厂执行，命中时在素材加载/布局构建之前取得尺寸与片段强引用，miss 则构建原共享树。引用
保证本请求回放不受并发淘汰、清空或 TTL 到期影响；参考 composer 无 native 准备上下文，
继续使用共享 widget 树。复用现有有界片段池，没有独立尺寸池，页脚和背景仍逐请求绘制。

正式三方同轮测速各 15 次：旧 main c616b79 最小值/中位数 13.25/14.10 ms，本轮前纯 Skia
22.08/22.54 ms，正式提前命中 14.35/14.69 ms。最小值较本轮前加速 1.54×；相对旧 main
仍慢 1.10 ms，中位数仍慢约 0.60 ms，接近追平。PNG 与本轮前逐字节一致。

1257 项 Python 测试通过；严格冷门槛 77 ok、完整热门槛零漂移。实际服务两次活动列表请求
间穿插其他页面，19 个条目仅首次构建，后续无需重建；12 请求仍零 Pillow 导入。本轮无
Rust/IR 改动。详情及全部证据见 `out/event-list-early-cache/REPORT.md`，尚未提交或部署。

## 原生背景复用与 VLive 追平（2026-09-08）

原生 TriangleBg 的不可变瓦片复用既有 Rust raster 池，键覆盖真实调色板和三角形输入；
水印、内容、倒计时与毛玻璃仍逐请求绘制。内存/单项预算沿用原配置，没有新增整页缓存。
24 次完整响应复测：VLive main/本轮前/当前为 135.96/149.41/124.45 ms，当前比 main
快约 9.2%；活动列表为 22.07/23.11/18.55 ms。69 公共样本修复前后 PNG 全部一致。
仍未全面超越：旋转装饰名片这一轮比 main 慢约 5.7%；背景每次变化时有约 3 ms 的保存代价。

1307 项 Python 测试、105 项 Rust 测试通过，严格冷门槛公共失败 0。标准热回归无缓存漂移，
但相对日期文本跨天数边界引起严格失败；精确复现该时钟变化后，固定显示时钟的完整严格热
复验通过，未扩大豁免名单。12 次实际 ASGI 热请求全部 native_pure、无 Pillow 导入，真实
背景时钟正常推进。用户排除的私有 MySekai 和缺少符号/贴纸捕获样本仍不作为公共验收项。
完整结果、初始失败原因和原始证据见 `out/native-background-optimization/REPORT.md`。
本轮未提交或部署，目标平台需要重建包含本次 Rust 改动的 wheel。

## 装饰名片缺字查询复用（2026-09-08）

已消除自定义名片对同一源字体重复解析缺字 cmap 的开销。确认缺字结果共用现有有界
轮廓缓存，按字体文件签名失效；FreeType 优先、临时失败不缓存，清空/禁用入口不变。
24 次完整响应热请求中位数：旋转装饰名片本轮前 42.96→14.72 ms，比 main c616b79
默认混合路径快 2.848×；装饰文字 40.93→11.28 ms，字体回退 109.85→21.51 ms，
描边文字 49.20→20.77 ms。69 公共样本本轮前后 PNG 全部一致。

这些是热请求收益；装饰名片冷请求仍约为 main 的三倍，全量六次热测的 chart 也慢约
2.3%，不能视为所有状态全面超越。背景渐变拆分缓存试验因未命中退化已撤回，本轮最终
无 Rust/IR 改动。完整三方排序、冷请求数据、实验与验证见
`out/native-followup-optimization/REPORT.md`。本轮未提交、推送或部署。

本轮验证：1315 项 Python、105 项 Rust 测试通过；严格冷回归 77 ok、公共服务检查
69/69 ok。固定相对日期显示时钟的完整严格热回归，两侧各 76 ok、1 个既有非确定项、
2 无样本，零漂移/错误，未扩充豁免名单。18 次真实 ASGI 热请求全部 native_pure、
零 Pillow 导入，新缺字缓存命中 13 次；生产背景时钟未固定。Ruff 与 diff 检查通过。

## 装饰名片冷缓存追平（2026-09-08）

通过 FontTools 字体表按需读取、请求内安全复用 reader、TMP 度量按需构造，以及保持
原 float32 算法的 Rust 距离场计算，消除了两个目标约三倍的冷请求退化。24 次完整 PNG
响应中位数，装饰文字 main/本轮前/当前为 137.91/352.04/114.97 ms，旋转装饰为
112.58/327.75/111.78 ms。前者快于 main，后者基本追平；不把 0.80 ms 差距当作稳定
领先。字体回退名片冷请求另外从 850.69 降到 141.17 ms。重点热请求基本持平。

68 个公共样本本轮前后 PNG 全部一致；chart 按用户要求不再评估性能。1348 项 Python、
105 项 Rust 测试通过；18 个实际 ASGI 热请求全部纯原生、零 Pillow 导入，验证了新
距离场 helper 和按需度量确实在服务中执行。IR 仍为 28，需重建 wheel 并重新加载服务
才能使用新 helper。本轮未提交、推送或部署。完整记录见
`out/native-cold-profile-optimization/REPORT.md`。

本轮最终严格冷回归 77 ok、零问题；固定相对日期显示时钟的完整严格热回归两侧各
76 ok、1 个既有非确定项、2 无样本，零缓存漂移/错误，未新增豁免。全部流水线退出码 0。


### 2026-09-08：保持 native 并恢复 main 普通文字外观

普通页面 IRPainter 文字恢复 main 的 Skia glyph 路径；嵌入 NativeSubtree 和显式 mask-lerp
保留原生 FreeType BASIC。活动列表整页保留 BASIC 作为紧像素预算的兼容例外；不在 drawer
复制布局、不放宽预算。WebP 补画和已有缩放/透明度修复保留。

68 公共热样本与 main：53 项 RGB mean 缩小、15 项不变、0 项变差；多人查房/查询/停车榜/
分数表平均差下降约 99.7%–99.8%，逐像素完全相同仍为 6 项。完整 PNG 中位数累计耗时
相对修改前增加 4.13%，仍比 main 快 1.79×。最终 Python 1349 passed；严格冷 77 ok；
严格热两侧各 76 ok、1 既有非确定性、2 无样本，零漂移/错误；公共 no-Pillow 服务门槛通过。
范围、初版失败记录、最终数据和剩余差异见本地 out/main-text-parity/REPORT.md。
