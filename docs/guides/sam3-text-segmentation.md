# SAM3 文字遮罩接入首版

新增 text_segmentation@1（图片输入，prompt/confidence 节点参数）与 select_text_mask@1。
模板 `pipelines/sam3_text_to_asset_v1.yaml`：文字分割（联合遮罩）→ SAM3D → canonicalize → QA → 组装 → 发布。
如果不需要人工确认，使用 `pipelines/sam3_text_auto_extract_v1.yaml`：文字分割完成后，模型返回的全部候选会自动取并集，`segment.mask` 直接进入提取节点；整条路径不会创建人工等待或选择节点。
文字分割默认输出所有候选的并集 mask，同时保留 candidates。零候选明确失败；不生成整图遮罩。
只有需要逐个处理对象时才使用 select_text_mask，通过 candidate_index 指定候选序号。
当前不是可视人工审查节点，不能将该序号选择声称为用户质量批准。联合遮罩使用 all_candidates_union@1 策略，证据绑定原图与候选包。

在 remote-config 的 profiles 中增加 sam3：operator=text_segmentation@1，endpoint 为 SSH
隧道的回环地址，service_id/backend_digest 使用远端固定 profile。保留 sam3d 配置。
提示词填写简短目标名，首个验证使用英文 robot；未验证中文识别表现。

远端独立服务目录 `<SAM3_SERVICE_ROOT>`，模型仓库 `<SAM3_REPO>`，使用已有 sam3 conda 环境。
新增代码来源是本仓库 services/sam3/infer.py 及 src/assets_generator/sam3_text_*.py，未修改
原 SAM3 仓库已有工作。模型仅在独立、耐久授权的进程执行。无模型/PyTorch 下载。
监听仅回环，经 SSH 隧道访问；不公开无鉴权端口。CLI init/serve/work 使用固定 profile。
执行器崩溃后的 running 不自动重跑，当前没有 SAM3 原进程结果恢复导入机制，需要诊断。
服务串行门控只覆盖此服务实例，尚无与 SAM3D 的跨服务 GPU 互斥；不要并行运行两边推理。

身份包含模型源码、词表、权重、runner、服务源码、Python 内容/路径和关键包版本。
关键包版本不是完整 wheel 内容证明；运行期间不得修改部署资源。启动和领取前校验部署。

真实验证：561×688 机器人图片，prompt=robot/confidence=0.5，通过统一 HTTP 服务成功生成
两个候选（0.77734375、0.6640625），下载和本地候选导入校验通过。初次 BF16 转 NumPy 失败，
runner 增加 float 转换后使用新身份、新数据库重跑；旧失败保留。
证据目录 `<DATASET_ROOT>/sam3-text-validation-20260921`，mask_0.png、mask_1.png、evidence.json。
未验收文字→选择→SAM3D 的完整真实画布链路；已支持多候选并集，尚无可视化单候选人工选择节点。

## 只分割或提取图片

画布可分别加载 sam3_text_masks_v1（只分割）、sam3_text_auto_extract_v1（自动并集后提取）与 extract_masked_image_v1（只提取）。
分割成功后，“可查看的节点输出”会逐个显示 candidates~0、candidates~1 等遮罩，
可展开预览和下载 PNG。空候选时不会虚构遮罩。
加载提取模板后，在运行列表选择已完成的分割记录，点击目标候选“用作输入 mask”；
为 image 上传同一张原图，再启动提取。输出是原尺寸透明背景 RGBA PNG，不生成 3D，
不隐式裁剪。如果希望自动连接，可以将 select_text_mask 的 mask 连到 apply_binary_mask。
候选读取与复用均核对该输出所属运行及完整引用证据，不接受任意 Store 文件路径。

本轮已通过真实服务与浏览器验证：只分割机器人得到两个候选，展开遮罩预览；
将 candidates~0 通过“用作输入 mask”绑定到提取模板，上传原图后仅 extract 节点执行，
产出 561×688 RGBA PNG，alpha 同时包含 0 和 255。该路径未启动任何 3D 节点。
只指定 Backend 且存在唯一兼容 Adapter 时，现在也会显示 prompt/confidence 参数表单。
在左侧加载 sam3_text_masks_v1，点 segment 节点，在“配置”中填写 prompt，
再到“运行”上传图片并启动。结果在“可查看的节点输出”内展开。
真实证据存于 `<DATASET_ROOT>/sam3-text-validation-20260921` 的
mask-preview.png、extract-preview.png 和 extracted.png。

审查修复：通过候选输出按钮复用时，新 mask 身份引用持久化的 TextMaskSelection，
记录候选包、原图、mask 和来源运行/节点/端口；启动时要求输入 image 与原图身份一致。
绑定证据进入递归完整性检查，提取算子也检查原图绑定。手动上传的普通 mask 仍是独立输入。
SAM3 输入边界拒绝 PNG 透明信息和非标准 EXIF 方向。空闲 worker 不重复哈希权重；
领取任务后在进程授权前核验部署身份。部署代码更新后必须重新固定身份，不能冒用旧身份。


## 节点连线与预览

推荐加载 `sam3_text_extract_v1`，它将 segment.mask 直接连接 extract.mask。
点击 segment 在配置中填写 prompt（如 chair）；上传图片后可启动完整图，
也可勾选“只运行到选中节点”，选择 segment 后点击“运行到这里”，仅执行分割。
右下角节点预览窗口随画布选中节点切换；多输出通过端口下拉选择。
预览来自所选运行，画布参数改动不会更新历史图像。运行列表可选择来源运行。
本轮的“运行到这里”创建包含目标及全部祖先的独立计划，由后端重新编译；
不执行下游或无关分支。切换目标再次启动会新建运行，可按下文复用所选历史运行的有效结果。
旧版图及结果仍保留，但旧计划使用旧契约/Adapter 身份，不能作为新版本自动恢复执行。

本轮真实验证：新部署独立目录及数据库保留旧身份运行；浏览器加载提取模板，
选中 segment 后“运行到这里”，父运行仅含 segment，一个 attempt 成功。
两个真实候选逐像素取并集，与输出 mask 完全一致；该 mask 直接输入提取节点成功产出 RGBA。
画布预览在切到“配置”标签后仍可见，截图为
`<DATASET_ROOT>/sam3-text-validation-20260921/node-preview-union.png`。
遮罩当前预览为黑白图，尚未实现原图叠加；模型仍通过预览按钮打开交互窗口。

本轮审查修复：运行到选中节点时，输入表单、必填检查、提交内容和执行资格统一基于
裁剪后的子图；子图单独请求后端编译，整图未满足的无关分支不再阻止启动。
联合/候选 mask 的原图绑定校验由图片提取和远程 masked shape 共同调用，
同尺寸但不同 Artifact 的原图在远端提交前被拒绝；普通上传 mask 保持原有规则。

## 复用分割结果继续调试下游

1. 在运行列表选择已经完成分割的运行，保留“复用所选运行的有效节点结果”勾选。
2. 点击“使用所选运行的原图”，或上传同一张图。
3. 在画布连接提取节点，启动完整流程或运行到提取节点。
4. 若分割输入、参数、算子契约、Adapter 实现、Backend 身份都一致，直接复用遮罩，
   不再向 SAM3 提交任务。改动的节点及依赖它的新节点按需执行。

复用来自明确选择的运行快照，不扫描全局缓存。新运行记录 reused_from 并保存来源
BuildRun Artifact，provenance 引用该快照；执行摘要标记 cached。源运行索引删除后，
仍可通过不可变快照回查。来源证据损坏会拒绝复用/阻塞恢复，不通过重算掩盖缺失证据。
请求幂等身份包含源快照，HTTP 重试不会自动改选另一份来源。
目前自动复用范围为远程文字分割、远程形状生成及指定的纯图片算子；人工节点、
子流程、发布节点不自动复用。当前保守核对整个来源快照证据闭包，历史无关证据损坏
也可能阻止复用；不支持跨节点实例 ID 自动匹配。

界面的“配置匹配”是提示，不承诺命中缓存：输入和证据会在启动时由后端核验。
修改参数后的预览仍显示历史结果，直到新运行完成。配置变化会提示需要更新，
上游配置变化会传递到下游提示。取消复用勾选可明确创建全新执行。

真实浏览器验证：复用已完成 SAM3 的 segment 到新的 segment→extract 图；新运行
segment 没有 remote_binding 或 worker execution，extract 正常执行并输出 RGBA。
恢复后仍成功，截图位于 `<DATASET_ROOT>/sam3-text-validation-20260921/node-reuse.png`。
本轮未重新运行 GPU 推理，使用之前真实 GPU 输出验证复用。


## 用输入节点提供文字

`sam3_text_auto_extract_v1` 使用 `text_segmentation@2`，有 image、text 两个输入。
加载模板后，在运行面板上传原图，在文本框填写 `chair`，点击“应用文本”，再启动。
text 输入的格式是 text / plain_text@1.0，载体为 ArtifactRef；也能连接其他节点输出的同契约文本。
文本以 UTF-8 保存，分割提示词必须非空且最多 256 字符。修改文本会改变输入身份，不能复用旧文字的分割结果。
V2 没有 prompt 参数；旧的 text_segmentation@1 及其 prompt 参数仍保留。
这是自动分割，不需要人工选择 mask。
