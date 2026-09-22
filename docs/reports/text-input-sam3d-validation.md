# 文本输入到 SAM3D 发布验证（2026-09-22）

证据目录：`<VALIDATION_ROOT>/text-asset-validation`，真实输入为已有机器人测试图，文本 robot。
浏览器上传 image/text 并启动，无人工 mask 决定。

首次运行 `dag_b0509bde648b4ca3bed90d95188e7845` 分割成功，SAM3D 返回 422 明确拒绝。
新版服务把 bake_view_resolution / bake_filter 的默认值纳入请求摘要，而旧客户端未包含。
修复显式支持扩展参数（不改变旧默认请求），新模板指定 512 / mipmap。

第二次运行 `dag_ef85f632589541d5a1f2dfd5b05457d6` 显式复用首次成功分割，
其余五个节点真实执行，最终六节点成功。start-v2.json、latest-v2.json、restored.json 和 validation.json 保存结果。
GLB 回读：352012 顶点、703958 三角面、vertex visual。默认 bake=false，验证的是顶点颜色保留，不证明烘焙纹理质量。
完成后显式恢复，node_states 完全一致，无新增推理 attempt。

50 项相关测试通过（文字分割、SAM3D HTTP、HTTP 发布链）。未运行全量测试。
本次不证明所有提示词、多物体生成质量或推理中异常退出恢复。
服务部署摘要变化时使用新身份/独立兼容数据库，不覆盖旧运行。
