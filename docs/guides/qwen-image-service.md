# 接入 Qwen Image 2.1 文生图／图生图

服务使用现有 stable-diffusion.cpp 部署，旁路运行 Python 协议包装层。
不安装模型到 Pipeline Core，也不注册 Qwen 专用 Adapter。两项能力由
`/v1/service-descriptor` 动态声明，通用远程节点负责上传、提交、收取和导入。

## 在工作台添加

在“模型服务”填写 `http://172.16.88.217:18082`，检测后分别添加：

- **Qwen Image 2.1 文生图**：文字输入接 `prompt`，`image` 输出可预览或接图片缩放、分割。
- **Qwen Image 2.1 图生图**：文字输入接 `prompt`，RGB PNG 图片接 `source_image`。
  上一个文生图节点的 `image` 也可直接接这里。

模型原生支持透明度。本服务输出约定为 RGB PNG；原生 RGBA 通过服务端固定的白底 alpha 合成转换，转换实现进入部署身份。JPEG 上传先经显式 PNG 编码节点转换；RGBA 也不能伪装成 RGB
接入。512×512、4 steps 适合验证接入，不能用于判断模型最终质量；正常体验可使用
20 steps，并根据显存选择分辨率。

图生图的文字与原图允许来自不同来源，其动态契约引用
`independent_inputs@1`。这不是对共享相机、深度、坐标或 mask 来源的证明。
后续节点仍执行自己的输入及关系校验。

## 部署与恢复边界

包装层源代码、测试和身份生成脚本保存在 `examples/qwen_service_bridge/`。
服务器项目仍为 `/home/ypk/Workspace/Projects/qwen-image-2.1`，原生服务只监听
`127.0.0.1:18080`。包装层使用独立的耐久数据目录；不要删除 SQLite 或 Blob 来“解锁”。

- 输入 Artifact、Blob、请求和部署身份均校验摘要；相同提交键只登记一次。
- 包装层串行领取任务，成功结果耐久保存；重复查询不会再次推理。
- 原生接口不支持幂等提交或按键恢复。网络结果未知或遗留 running 会阻塞队列，
  不会盲目重发。需管理员核实上游后处理，不能承诺任意推理中断自动恢复。
- 权重、可执行文件、启动脚本、包装层及环境声明纳入部署清单；任务前后核验文件，
  空闲时不重复完整哈希。上游进程身份也固定，替换／重启上游需重新核验并生成部署身份。
- 独立的原演示网页也能请求同一模型。包装层串行门控不覆盖原生服务的其他调用者；
  验收和日常重任务应避免同时使用演示网页提交。

服务包装层只面向当前内网部署，不提供模型下载或自动理解任意第三方 API 的能力。

## 本次已启动的体验入口

工作台：`http://172.16.89.121:18767/`。
点击“保存 / 加载”，选择 `text_to_image` 或 `image_to_image`，确认替换画布。
在 prompt 输入节点填写文字并点击“应用文本”；图生图还需提供 RGB PNG 原图。
点击“启动新运行”。两个模型能力已经安装，无需再次添加服务。

已有验收结果可从顶部运行列表选择。文生图运行 ID 以 `dag_34b440` 开头；
图生图以 `dag_edc71a` 开头。原图和缩放输出均可查看。

最终服务数据目录 `bridge-data-reviewed-v2-20260929`，身份清单
`protocol-bridge/deployment-v2-20260929.json`。仅重启包装服务时继续使用同一清单和目录：

```bash
cd /home/ypk/Workspace/Projects/qwen-image-2.1
python3 qwen_generic_bridge.py \
  --directory "$PWD/bridge-data-reviewed-v2-20260929" \
  --manifest "$PWD/protocol-bridge/deployment-v2-20260929.json" \
  --port 18082
```

已在运行时不要重复启动，进程锁会拒绝第二个实例。
