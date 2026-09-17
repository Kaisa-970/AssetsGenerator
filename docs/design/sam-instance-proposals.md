# SAM v1 实例候选契约

Phase 7 使用 SAM v1 AutomaticMaskGenerator 产生**未知类别的 mask 候选**。Core 通过独立
Python 进程调用本地 checkpoint，不导入 Torch/SAM，也不下载模型。候选不是检测结果：
可能重叠、嵌套、只覆盖物体部件或遗漏对象，必须显式选择后才能进入对象资产生成。

`instance_proposals` / `InstanceProposals@1.0` 引用输入图像、原分辨率二值 mask、包含式
xywh 像素 bbox、area、原始 predicted_iou/stability_score、label=unknown、source=estimated，
并记录 checkpoint/runner/environment digest、依赖安装身份和完整 AMG 参数。两个分数不是
语义类别置信度。proposal_id 绑定图像与 mask Artifact identity；排序按面积降序和 mask digest，
去重后再截断。空候选是合法运行，空 mask 不得进入提取。

`instance_selection` / `InstanceSelection@1.0` 保存 selected/unselected proposal IDs、检查人及时间。
选择输出 objects.json，逐个引用精确 mask，并将 selection Artifact 写入 manifest。提取流程验证
selection 的图像、顺序和 mask identity，来源标记 selected_model_proposals；未选择候选仍保留
在决定证据中。身份是自行填写，不是认证账户。

`review-instances` 提供仅绑定 `127.0.0.1` 的可视检查入口。服务只读取当前 proposals 引用的
原图和 mask，浏览器明确维护 selected_ids 顺序，并通过带 session token、同源和 Host 校验的
发布请求调用同一个 `select_instance_proposals` 边界。UI 不产生新的自动判断，也不改变
`InstanceSelection@1.0` 的身份语义。

默认 ViT-H：points_per_side=16、crop_n_layers=0、max_instances=20、min_area_pixels=64、
pred_iou_thresh=.88、stability_score_thresh=.95。真实可用性必须固定 checkpoint digest 并完成
GPU smoke；mock 测试只证明契约。SAM 不提供语义标签、世界位姿、尺度或对象关系。
