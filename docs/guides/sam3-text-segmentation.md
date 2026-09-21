# SAM3 文字遮罩接入首版

新增 text_segmentation@1（图片输入，prompt/confidence 节点参数）与 select_text_mask@1。
模板 `pipelines/sam3_text_to_asset_v1.yaml`：文字分割 → 明确选择 → SAM3D → canonicalize → QA → 组装 → 发布。
候选默认只接受一个；零候选或多候选失败停止，用户需在 select 的 candidate_index 指定从 0 起的序号。
当前不是可视人工审查节点，不能将该序号选择声称为用户质量批准。没有自动并集。

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
未验收文字→选择→SAM3D 的完整真实画布链路；当前多候选会停止，尚无可视化选择 UI。

## 只分割或提取图片

画布可分别加载 sam3_text_masks_v1（只分割）与 extract_masked_image_v1（只提取）。
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
