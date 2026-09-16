# TripoSR 独立环境与预检

TripoSR 必须运行在独立于 Pipeline Core 的 Python 环境中。本文只描述环境准备和只读验证；Backend 接口和输出语义见 [TripoSR Shape Backend 接入契约](../design/triposr-backend.md)。在真实 GPU smoke 完成前，只能确认环境检查结果，不能宣称 TripoSR Backend 可用。

## 目录约定

下面的路径均为本机配置，不提交仓库：

```bash
export TRIPOSR_REPO=<TRIPOSR_REPO>
export TRIPOSR_PYTHON=<TRIPOSR_ENV>/bin/python
export TRIPOSR_MODEL=<TRIPOSR_MODEL_SNAPSHOT>
```

`TRIPOSR_REPO` 是官方 TripoSR checkout。`TRIPOSR_MODEL` 应指向已经下载完成的本地 Hugging Face snapshot，至少包含非空的 `config.yaml` 和 `model.ckpt`；正式运行和 provenance 使用该快照的文件内容计算模型身份。模型权重不得放入本仓库。

## 优先复用现有 CUDA 环境

先检查已有的模型环境，不要直接运行 TripoSR 上游 `requirements.txt`。上游文件固定了较旧的 Pillow、Transformers 和 Trimesh 版本，直接安装可能替换现有的 PyTorch/CUDA 依赖并破坏 TRELLIS.2 环境。

当前可优先评估已有 `torch 2.9.1+cu128` 环境：

```bash
export TRIPOSR_PYTHON=<EXISTING_MODEL_ENV>/bin/python
"$TRIPOSR_PYTHON" -c 'import torch; print(torch.__version__, torch.version.cuda, torch.cuda.get_arch_list())'
"$TRIPOSR_PYTHON" -m pip check
```

将 TripoSR checkout 和本地模型路径配置完成后，先运行本文的只读预检。它会列出缺失或无法导入的包。确认兼容性后，只增量安装缺失依赖，并避免让 pip 解析或替换 PyTorch：

```bash
git clone https://github.com/VAST-AI-Research/TripoSR.git "$TRIPOSR_REPO"
"$TRIPOSR_PYTHON" -m pip install --no-deps <MISSING_PACKAGE>
"$TRIPOSR_PYTHON" -m pip check
```

若 TripoSR 所需包与现有环境发生版本冲突，克隆现有环境后再调整，保留已验证的 PyTorch/CUDA 构建并复用本地 conda/pip 缓存：

```bash
conda create --name <TRIPOSR_ENV_NAME> --clone <EXISTING_MODEL_ENV_NAME>
export TRIPOSR_PYTHON=<CONDA_ROOT>/envs/<TRIPOSR_ENV_NAME>/bin/python
"$TRIPOSR_PYTHON" -m pip check
```

克隆会占用额外磁盘空间，但通常可复用本地包缓存，避免再次下载大体积 PyTorch wheel。不要在 Core 环境安装模型依赖。只有现有环境和克隆方案都不兼容时，才按 [PyTorch 官方安装选择器](https://pytorch.org/get-started/locally/) 新建环境。

TripoSR 官方依赖包含 PyTorch、Transformers、`torchmcubes`、xatlas 和图像处理包。官方 requirements 当前从 Git 构建 `torchmcubes`。该扩展必须针对目标 PyTorch/CUDA 和 GPU 架构编译；更换 PyTorch、CUDA toolkit 或 GPU 后应重新构建。预检会检查模块能否加载以及 PyTorch 是否声明支持当前 Compute Capability，但不会执行 marching cubes，因此最终仍以真实 GPU smoke 为准。

### RTX 50 系列与 CUDA 扩展

RTX 5060 的 Compute Capability 为 `12.0`（`sm_120`）。使用支持该架构的 PyTorch CUDA 12.8 或更新构建，并准备匹配的 CUDA toolkit；`nvidia-smi` 显示的 CUDA 版本是驱动能力，不代表 `nvcc` 已安装。检查：

```bash
nvidia-smi
nvcc --version
"$TRIPOSR_PYTHON" -c 'import torch; print(torch.__version__, torch.version.cuda, torch.cuda.get_arch_list())'
```

若 toolkit 位于非默认目录，在编译前设置 `CUDA_HOME` 并将其 `bin` 加入 `PATH`。对于 `torchmcubes`，使用 CUDA 12.8 toolkit 和支持的主机 C++ 编译器；CMake 构建必须包含架构 `120`。可在独立环境内显式重建：

```bash
export CMAKE_ARGS="-DCMAKE_CUDA_ARCHITECTURES=120"
export TORCH_CUDA_ARCH_LIST="12.0"
"$TRIPOSR_PYTHON" -m pip install --no-build-isolation --no-cache-dir --force-reinstall --no-deps \
  git+https://github.com/tatsy/torchmcubes.git
```

具体上游 revision 是否接受这些构建参数应根据安装日志确认。import 成功不证明 CUDA kernel 已编译正确；CPU-only 扩展、ABI 不匹配和 `no kernel image` 仍可能在 smoke 阶段暴露。不要为解决扩展安装问题替换 Core 或 TRELLIS.2 环境的依赖。

默认单图推理约需 6 GB 显存，这是上游给出的 A100 环境参考值，不是本项目硬件上的保证。真实测试需要串行运行，避免与 TRELLIS.2 或其他 GPU 任务竞争显存。

## 本地模型快照

环境预检不会下载模型。可以使用已安装的 Hugging Face CLI 预先下载固定 revision，并将 `TRIPOSR_MODEL` 指向返回的 snapshot 目录：

```bash
<TRIPOSR_ENV>/bin/huggingface-cli download \
  stabilityai/TripoSR \
  --revision <MODEL_REVISION> \
  --local-dir <TRIPOSR_MODEL_SNAPSHOT>
```

如果所用 `huggingface_hub` 版本提供 `hf` 命令，也可使用等价的 `hf download`。revision 应固定为 commit，而非浮动分支名。许可证接受、网络凭据和缓存位置由部署者在 Backend 环境中管理。

## 只读预检

从 AssetsGenerator checkout 执行：

```bash
.venv/bin/python -m assets_generator.backends.triposr_preflight \
  --python "$TRIPOSR_PYTHON" \
  --repo "$TRIPOSR_REPO" \
  --model "$TRIPOSR_MODEL"
```

预检只执行以下读取操作：

- 检查目标 Python、TripoSR checkout 和本地模型 snapshot；
- 通过 `nvidia-smi` 读取 GPU、驱动和显存信息；
- 在目标 Python 中导入 TripoSR 推理所需模块；
- 读取 PyTorch、CUDA、GPU Compute Capability 和编译架构列表。

它不会创建环境、安装包、下载模型、加载权重或运行推理。全部必要检查通过时退出码为 `0`，否则为 `1`。自动化采集可以添加 `--json`：

```bash
.venv/bin/python -m assets_generator.backends.triposr_preflight \
  --python "$TRIPOSR_PYTHON" \
  --repo "$TRIPOSR_REPO" \
  --model "$TRIPOSR_MODEL" \
  --json > <LOCAL_EVIDENCE_DIR>/triposr-preflight.json
```

传入 `stabilityai/TripoSR` 等远程模型 ID 会失败，因为只读预检无法确认本地权重内容。此时应先准备固定 revision 的本地 snapshot。

## 通过后的验证顺序

环境预检通过后仍需依次完成：

1. 使用一个 RGBA 输入运行真实 GPU smoke，记录模型 digest、耗时、峰值显存及错误输出。
2. 独立 reload 导出的非对称轴标记 GLB，确认 TripoSR native frame 后再冻结 frame 声明。
3. 在 Phase 3 保留的飞机与自行车输入上串行运行，通过同一 review 页面与 TRELLIS.2 比较。

这些真实运行结果应进入 `docs/reports/`，外部输入与生成资产以 `<DATASET_ROOT>` 等占位符引用，不提交二进制文件。
