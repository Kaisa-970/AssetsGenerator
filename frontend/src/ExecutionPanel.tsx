import { selectedPreviewPort, previewEmptyMessage } from "./selectedPreview";
import { ReuseHumanDecision } from "./ReuseHumanDecision";
import { useResultFreshness } from "./useResultFreshness";
import { request, type Envelope } from "./executionApi";
import { useRunCreation, type CreationRequest } from "./useRunCreation";
import { OutputComparison, type ComparisonSlot } from "./OutputComparison";
import { NodeActionHint } from "./NodeActionHint";
import { RunOutputs, type RunOutput } from "./RunOutputs";
import { ExecutionPreflight } from "./ExecutionPreflight";
import { ExecutionInputs } from "./ExecutionInputs";
import { MeshAppearancePanel } from "./MeshAppearancePanel";
import { QualityEvidencePanel } from "./QualityEvidencePanel";
import { HumanDecisionSummary } from "./HumanDecisionSummary";
import { executionStatus } from "./executionStatus";
import { createPortal } from "react-dom";
import { remoteStatusMessage } from "./remoteStatus";
import { ImageOutput } from "./ImageOutput";
import { RunGraph } from "./RunGraph";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { pipelineThrough, type Pipeline } from "./graph";

const GlbPreview = lazy(() =>
  import("./GlbPreview").then((module) => ({ default: module.GlbPreview })),
);

export function ExecutionPanel({
  pipeline,
  profile,
  executionReason,
  configurationBlockedReason,
  selectedNode,
  previewHost,
  onLoadDraft,
}: {
  pipeline: Pipeline;
  profile?: string;
  executionReason?: string;
  configurationBlockedReason?: string;
  selectedNode?: string;
  previewHost?: HTMLElement | null;
  onLoadDraft: (pipeline: Pipeline) => void;
}) {
  const [comparison, setComparison] = useState<
    Partial<Record<"A" | "B", ComparisonSlot>>
  >({});
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [reuseResults, setReuseResults] = useState(true);
  const [previewPort, setPreviewPort] = useState("");
  const [runToSelection, setRunToSelection] = useState(false);
  const targetPlan = useMemo(() => {
    try {
      return {
        pipeline: runToSelection
          ? pipelineThrough(pipeline, selectedNode || "")
          : pipeline,
        error: "",
      };
    } catch (error) {
      return { pipeline, error: String(error) };
    }
  }, [pipeline, runToSelection, selectedNode]);
  const effectivePipeline = targetPlan.pipeline;
  const [sliceEligibility, setSliceEligibility] = useState<{
    plan: Pipeline;
    reason: string;
  }>();
  useEffect(() => {
    if (!runToSelection || targetPlan.error) return;
    const controller = new AbortController();
    request("/api/compile", { pipeline: effectivePipeline }, controller.signal)
      .then((result) => {
        setSliceEligibility({
          plan: effectivePipeline,
          reason:
            result.ok && result.execution_ready
              ? ""
              : result.execution_reason ||
                "目标子图无法执行，请检查节点绑定与输入契约",
        });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setSliceEligibility({
            plan: effectivePipeline,
            reason: String(error),
          });
      });
    return () => controller.abort();
  }, [effectivePipeline, targetPlan.error, runToSelection]);
  const effectiveReason = runToSelection
    ? targetPlan.error ||
      (sliceEligibility?.plan === effectivePipeline
        ? sliceEligibility.reason
        : "正在检查目标子图…")
    : executionReason;
  const {
    creation,
    submit: sendCreation,
    clear: clearCreation,
  } = useRunCreation();
  const [preview, setPreview] = useState<{
    runId: string;
    nodeId: string;
    url: string;
  }>();
  const [showReview, setShowReview] = useState(false);
  const [showGraph, setShowGraph] = useState(false);
  const [imagePath, setImagePath] = useState("");
  const [reusedImage, setReusedImage] = useState<{ artifact_id: string }>();
  const [observationsId, setObservationsId] = useState("");
  const [observationFiles, setObservationFiles] = useState<string[]>([]);
  const [inputRefs, setInputRefs] = useState<
    Record<string, Record<string, unknown>>
  >({});
  const [inputOrigins, setInputOrigins] = useState<
    Record<
      string,
      {
        artifactId: string;
        snapshot?: string;
        runId: string;
        nodeId: string;
        port: string;
      }
    >
  >({});
  const inputSignature = JSON.stringify(effectivePipeline.inputs);
  const inputGeneration = useRef({ signature: inputSignature, revision: 0 });
  if (inputGeneration.current.signature !== inputSignature) {
    inputGeneration.current = {
      signature: inputSignature,
      revision: inputGeneration.current.revision + 1,
    };
  }
  useEffect(() => {
    setInputRefs({});
    setInputOrigins({});
    setReusedImage(undefined);
  }, [inputSignature]);
  useEffect(() => {
    if (reusedImage) setImageSource("reference");
  }, [reusedImage]);
  const multiView =
    Object.keys(effectivePipeline.inputs).length === 1 &&
    "observations" in effectivePipeline.inputs;
  const multiInput =
    Object.keys(effectivePipeline.inputs).length > 1 ||
    Object.values(effectivePipeline.inputs).some(
      (port) => port.kind === "text" || port.kinds?.[0] === "text",
    );
  const rgbaInput = effectivePipeline.inputs.image?.kind === "rgba_image";
  const [imageSource, setImageSource] = useState("upload");
  const [uploaded, setUploaded] = useState<{
    name: string;
    rgba: boolean;
    ref: Record<string, unknown>;
  }>();
  const [inputFiles, setInputFiles] = useState<
    Record<string, { file: File; artifactId: string }>
  >({});
  const [uploadedFile, setUploadedFile] = useState<File>();
  useEffect(() => {
    setInputFiles({});
    setUploadedFile(undefined);
  }, [inputSignature]);
  const [uploading, setUploading] = useState(false);
  const uploadPending = useRef(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [runs, setRuns] = useState<{ run_id: string; status: string }[]>([]);
  const [selected, setSelected] = useState("");
  const selectedRef = useRef("");
  const [envelope, setEnvelope] = useState<Envelope>();
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const [review, setReview] = useState<{
    runId: string;
    nodeId: string;
    url: string;
  }>();
  const [snapshot, setSnapshot] = useState<{
    runId: string;
    pipeline: string;
  }>();
  const refreshRuns = async () => setRuns((await request("/api/runs")).runs);
  useEffect(() => {
    void refreshRuns().catch((error) => setMessage(String(error)));
  }, []);
  const choose = (id: string) => {
    setPreview(undefined);
    setShowGraph(false);
    setShowReview(false);
    selectedRef.current = id;
    setSelected(id);
    setEnvelope(undefined);
    setReview(undefined);
    setMessage("");
  };
  const accept = (value: Envelope) => {
    if (value.run.run_id !== selectedRef.current) return;
    setEnvelope((old) =>
      old?.run.run_id === value.run.run_id &&
      (old.run.dag?.revision || 0) > (value.run.dag?.revision || 0)
        ? old
        : value,
    );
  };
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await request(
          `/api/runs/${encodeURIComponent(selected)}`,
          undefined,
          controller.signal,
        );
        if (!controller.signal.aborted) accept(value);
      } catch (error) {
        if (!controller.signal.aborted)
          setMessage(`读取运行失败：${String(error)}`);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 1500);
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [selected]);
  const mutate = async (action: () => Promise<void>) => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setMessage("");
    try {
      await action();
    } catch (error) {
      setMessage(
        `${String(error)}。未自动重发；若响应丢失，请刷新运行列表核实。`,
      );
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };
  const submitCreation = async (submitted: CreationRequest) => {
    const value = await sendCreation(submitted);
    choose(value.run.run_id);
    accept(value);
    setSnapshot({
      runId: value.run.run_id,
      pipeline: JSON.stringify(submitted.pipeline),
    });
    setRuns((old) => [
      { run_id: value.run.run_id, status: value.run.status },
      ...old.filter((item) => item.run_id !== value.run.run_id),
    ]);
    clearCreation();
    setMessage("运行已创建；请在下方查看真实节点状态。");
  };
  const unresolvedCreation = !!creation.request || !!creation.error;
  const run = envelope?.run;
  useEffect(() => {
    setPreviewPort("");
  }, [selectedNode, run?.run_id]);
  const actionAdvice =
    envelope?.actions?.run_id === run?.run_id &&
    envelope?.actions?.revision === run?.dag?.revision
      ? envelope?.actions
      : undefined;
  const reuseStates = run?.dag?.node_states;
  const nodeFreshness = useResultFreshness(effectivePipeline, selected);

  const executing = pending || !!envelope?.busy;
  const setInputArtifact = (name: string, artifactId: string) => {
    setInputFiles((old) => {
      const next = { ...old };
      delete next[name];
      return next;
    });
    setInputRefs((old) => ({
      ...old,
      [name]: artifactId.trim() ? { artifact_id: artifactId.trim() } : {},
    }));
  };
  const uploadInputArtifact = async (name: string, file?: File) => {
    if (!file || uploadPending.current) return;
    const revision = inputGeneration.current.revision;
    setInputArtifact(name, "");
    setUploadMessage("");
    if (!file.size || file.size > 20 * 1024 * 1024) {
      setUploadMessage("请选择非空且不超过 20 MiB 的图片。");
      return;
    }
    const kind =
      effectivePipeline.inputs[name]?.kind ||
      effectivePipeline.inputs[name]?.kinds?.[0];
    const endpoint =
      kind === "text"
        ? "/api/inputs/text"
        : kind === "binary_mask"
          ? "/api/inputs/mask"
          : kind === "rgba_image"
            ? "/api/inputs/rgba"
            : kind === "rgb_image"
              ? "/api/inputs/image"
              : undefined;
    if (!endpoint) {
      setUploadMessage(
        `输入 ${name} 暂不支持浏览器上传，请填写已有 Artifact ID。`,
      );
      return;
    }
    uploadPending.current = true;
    setUploading(true);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      });
      const value = await response.json();
      const reference = value.image_ref || value.mask_ref || value.text_ref;
      if (!response.ok || typeof reference?.artifact_id !== "string")
        throw Error(value.error || "服务未返回有效 Artifact 引用");
      if (inputGeneration.current.revision !== revision) {
        setUploadMessage("上传期间输入契约已修改，请为当前输入重新选择文件。");
        return;
      }
      setInputArtifact(name, reference.artifact_id);
      if (kind !== "text")
        setInputFiles((old) => ({
          ...old,
          [name]: { file, artifactId: reference.artifact_id },
        }));
      setUploadMessage(`输入 ${name} 已上传并绑定。`);
    } catch (error) {
      setUploadMessage(`输入 ${name} 上传失败：${String(error)}`);
    } finally {
      uploadPending.current = false;
      setUploading(false);
    }
  };
  const upload = async (file?: File) => {
    if (uploadPending.current) return;
    setUploaded(undefined);
    setUploadedFile(undefined);
    setUploadMessage("");
    if (!file) return;
    if (!file.size || file.size > 20 * 1024 * 1024) {
      setUploadMessage("请选择非空且不超过 20 MiB 的图片。");
      return;
    }
    uploadPending.current = true;
    setUploading(true);
    try {
      const response = await fetch(
        rgbaInput ? "/api/inputs/rgba" : "/api/inputs/image",
        {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file,
        },
      );
      const value = await response.json();
      if (!response.ok) throw Error(value.error || `HTTP ${response.status}`);
      if (!value.image_ref || typeof value.image_ref.artifact_id !== "string")
        throw Error("服务未返回有效的图片引用");
      setUploaded({ name: file.name, rgba: rgbaInput, ref: value.image_ref });
      setUploadedFile(file);
      setUploadMessage("图片已上传；点击启动新运行才会执行模型。");
    } catch (error) {
      setUploadMessage(`上传失败：${String(error)}`);
    } finally {
      uploadPending.current = false;
      setUploading(false);
    }
  };
  const uploadObservations = async (files: File[]) => {
    if (uploadPending.current) return;
    setObservationsId("");
    setObservationFiles([]);
    setUploadMessage("");
    if (
      files.length < 2 ||
      files.length > 32 ||
      files.some((f) => !f.size || f.size > 20 * 1024 * 1024)
    ) {
      setUploadMessage("请选择 2–32 张 RGB 图片，每张不超过 20 MiB。");
      return;
    }
    uploadPending.current = true;
    setUploading(true);
    try {
      const images = [];
      for (const [index, file] of files.entries()) {
        setUploadMessage(`正在上传 ${index + 1}/${files.length}：${file.name}`);
        const response = await fetch("/api/inputs/image", {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file,
        });
        const value = await response.json();
        if (!response.ok) throw Error(value.error || "上传失败");
        images.push(value.image_ref);
      }
      const bundle = await request("/api/inputs/observations", { images });
      setObservationsId(bundle.observations_ref.artifact_id);
      setObservationFiles(files.map((f) => f.name));
      setUploadMessage("观测包已创建；点击启动才会执行模型。");
    } catch (error) {
      setUploadMessage(
        `导入失败：${String(error)}。未创建运行，可重新选择文件。`,
      );
    } finally {
      uploadPending.current = false;
      setUploading(false);
    }
  };
  const compareOutput = (output: RunOutput, side: "A" | "B") => {
    if (!run || !envelope?.snapshot_ref) return;
    const snapshot = envelope.snapshot_ref!.artifact_id;
    void mutate(async () => {
      const source = await request(
        `/api/runs/${encodeURIComponent(run.run_id)}/snapshot-reference/${encodeURIComponent(output.node_id)}/${encodeURIComponent(output.port)}?snapshot=${encodeURIComponent(snapshot)}`,
      );
      if (
        source.source_run_id !== run.run_id ||
        source.node_id !== output.node_id ||
        source.port !== output.port ||
        source.source_snapshot?.artifact_id !== snapshot ||
        source.kind !== output.kind ||
        typeof source.reference?.artifact_id !== "string"
      )
        throw Error("比较输出来源无法核实");
      setComparison((old) => ({
        ...old,
        [side]: {
          runId: run.run_id,
          nodeId: output.node_id,
          port: output.port,
          kind: source.kind,
          snapshot,
          reference: source.reference,
          schema_name: source.schema_name,
          schema_version: source.schema_version,
        },
      }));
      setMessage(`已固定比较项 ${side}，不改变下游输入。`);
    });
  };
  const reuseOutput = (output: RunOutput, name: string) => {
    if (!run) return;
    const port = effectivePipeline.inputs[name];
    if (!multiInput) setImageSource("reference");
    const revision = inputGeneration.current.revision;
    void mutate(async () => {
      const source = await request(
        `/api/runs/${encodeURIComponent(run.run_id)}/references/${encodeURIComponent(output.node_id)}/${encodeURIComponent(output.port)}`,
      );
      if (inputGeneration.current.revision !== revision)
        throw Error("输入契约已修改，请重新选择输出");
      if (
        source.source_run_id !== run.run_id ||
        source.node_id !== output.node_id ||
        source.port !== output.port ||
        !(port.kinds || [port.kind]).includes(source.kind) ||
        (port.schema_name && source.schema_name !== port.schema_name) ||
        (port.schema_version &&
          source.schema_version !== port.schema_version) ||
        typeof source.reference?.artifact_id !== "string"
      )
        throw Error("输出引用与目标输入契约不匹配");
      setInputOrigins((old) => ({
        ...old,
        [name]: {
          artifactId: source.reference.artifact_id,
          runId: source.source_run_id,
          nodeId: source.node_id,
          port: source.port,
        },
      }));
      if (multiInput) {
        setInputRefs((old) => ({
          ...old,
          [name]: {
            ...source.reference,
            ...(output.port.includes("~")
              ? {
                  source: {
                    run_id: source.source_run_id,
                    node_id: source.node_id,
                    port: source.port,
                  },
                }
              : {}),
          },
        }));
      } else {
        setReusedImage({
          artifact_id: source.reference.artifact_id,
        });
        setImageSource("reference");
      }
      setMessage(
        `已将 ${run.run_id}/${output.node_id}.${output.port} 绑定到 ${name}，点击启动才会创建新运行。`,
      );
    });
  };
  const selectedOutputs = (envelope?.outputs || []).filter(
    (o) => o.node_id === selectedNode,
  );
  const activePreviewPort = selectedPreviewPort(selectedOutputs, previewPort);
  return (
    <section className="execution-panel">
      {(comparison.A || comparison.B) && (
        <button onClick={() => setComparisonOpen(true)}>比较两次结果</button>
      )}
      {comparisonOpen && (
        <div
          className="run-graph-overlay"
          role="dialog"
          aria-label="比较两次结果"
        >
          <header>
            <strong>比较两次结果</strong>
            <button onClick={() => setComparisonOpen(false)}>关闭比较</button>
          </header>
          <OutputComparison
            slots={comparison}
            inputs={effectivePipeline.inputs}
            disabled={executing || uploading || unresolvedCreation}
            selectedInputs={Object.fromEntries(
              Object.keys(effectivePipeline.inputs).map((name) => [
                name,
                {
                  ...(inputOrigins[name] || {}),
                  artifactId: String(
                    multiInput
                      ? inputRefs[name]?.artifact_id || ""
                      : reusedImage?.artifact_id || "",
                  ),
                },
              ]),
            )}
            onClear={(side) =>
              setComparison((old) => ({ ...old, [side]: undefined }))
            }
            onUse={(slot, name) => {
              const revision = inputGeneration.current.revision;
              void mutate(async () => {
                const source = await request(
                  `/api/runs/${encodeURIComponent(slot.runId)}/snapshot-reference/${encodeURIComponent(slot.nodeId)}/${encodeURIComponent(slot.port)}?snapshot=${encodeURIComponent(slot.snapshot)}`,
                );
                if (revision !== inputGeneration.current.revision)
                  throw Error("输入契约已变化，请重新选择");
                const port = effectivePipeline.inputs[name];
                if (
                  source.reference?.artifact_id !==
                    slot.reference.artifact_id ||
                  source.source_run_id !== slot.runId ||
                  source.node_id !== slot.nodeId ||
                  source.port !== slot.port ||
                  source.source_snapshot?.artifact_id !== slot.snapshot ||
                  !port ||
                  !(port.kinds || [port.kind]).includes(source.kind) ||
                  (port.schema_name &&
                    port.schema_name !== source.schema_name) ||
                  (port.schema_version &&
                    port.schema_version !== source.schema_version)
                )
                  throw Error("比较结果与输入绑定无法核实");
                setInputOrigins((old) => ({
                  ...old,
                  [name]: {
                    artifactId: source.reference.artifact_id,
                    runId: slot.runId,
                    nodeId: slot.nodeId,
                    port: slot.port,
                    snapshot: slot.snapshot,
                  },
                }));
                if (multiInput)
                  setInputRefs((old) => ({
                    ...old,
                    [name]: {
                      ...source.reference,
                      ...(slot.port.includes("~")
                        ? {
                            source: {
                              run_id: slot.runId,
                              node_id: slot.nodeId,
                              port: slot.port,
                              snapshot: { artifact_id: slot.snapshot },
                            },
                          }
                        : {}),
                    },
                  }));
                else {
                  setReusedImage(source.reference);
                  setImageSource("reference");
                }
                setMessage(
                  `已明确选择 ${slot.runId}/${slot.nodeId}.${slot.port} 作为 ${name}，预览其他结果不会改变绑定。`,
                );
              });
            }}
          />
        </div>
      )}
      {previewHost &&
        createPortal(
          <section className="selected-node-preview" aria-label="选中节点预览">
            <div className="section-label">
              节点预览 · {selectedNode || "请选择节点"}
            </div>
            <p>来源运行：{run?.run_id || "未选择运行"}。历史结果保持不变。</p>
            <p aria-label="预览配置状态">
              {selectedNode && nodeFreshness[selectedNode]
                ? nodeFreshness[selectedNode].includes("需要更新") ||
                  nodeFreshness[selectedNode].includes("上游需更新")
                  ? "结果过期：配置或上游已变化；保留旧预览供比较。"
                  : nodeFreshness[selectedNode]
                : "待核验：尚未确认此结果是否对应当前配置。"}
            </p>
            <label>
              输出端口
              <select
                aria-label="预览输出端口"
                value={activePreviewPort}
                onChange={(e) => setPreviewPort(e.target.value)}
              >
                {!selectedOutputs.length && <option value="">暂无输出</option>}
                {envelope?.outputs
                  ?.filter((o) => o.node_id === selectedNode)
                  .map((o) => (
                    <option key={o.port} value={o.port}>
                      {o.port === "mask" ? "mask · 联合遮罩" : o.port}
                    </option>
                  ))}
              </select>
            </label>
            {run &&
              selectedOutputs
                .filter((o) => o.port === activePreviewPort)
                .map((output) =>
                  ["rgb_image", "rgba_image", "binary_mask"].includes(
                    output.kind || "",
                  ) ? (
                    <ImageOutput
                      key={`selected:${run.run_id}:${output.node_id}:${output.port}`}
                      runId={run.run_id}
                      nodeId={output.node_id}
                      port={output.port}
                      url={output.url}
                      defaultOpen
                    />
                  ) : output.kind === "gltf_asset" ||
                    output.kind === "triangle_mesh" ? (
                    <button
                      key={output.port}
                      onClick={() =>
                        setPreview({
                          runId: run.run_id,
                          nodeId: output.node_id,
                          url: output.url,
                        })
                      }
                    >
                      预览模型 · {output.port}
                    </button>
                  ) : (
                    <a
                      key={output.port}
                      href={output.url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {output.port}
                    </a>
                  ),
                )}
            {!envelope?.outputs?.some((o) => o.node_id === selectedNode) && (
              <p>
                {previewEmptyMessage(
                  selectedNode,
                  run?.run_id,
                  selectedNode
                    ? run?.dag?.node_states[selectedNode]?.status
                    : undefined,
                )}
              </p>
            )}
          </section>,
          previewHost,
        )}
      {run?.dag?.named_actual_inputs?.image && !multiInput && !multiView && (
        <button
          onClick={() => {
            setReusedImage(run.dag!.named_actual_inputs!.image);
            setImageSource("reference");
          }}
        >
          使用所选运行的原图
        </button>
      )}
      {reuseStates && (
        <section aria-label="节点复用状态">
          <div className="section-label">所选运行的节点结果</div>
          {Object.entries(reuseStates).map(([id, state]) => (
            <p key={id}>
              {id} ·{" "}
              {state.attempts?.at(-1)?.reused_from
                ? `此运行已复用 · ${nodeFreshness[id] || "正在核验当前配置"}`
                : state.status === "succeeded"
                  ? nodeFreshness[id] || "已有历史结果 · 正在核验配置"
                  : "尚无成功结果"}
            </p>
          ))}
        </section>
      )}
      {Object.values(nodeFreshness).some(
        (value) => value.includes("需要更新") || value.includes("上游需更新"),
      ) && (
        <section aria-label="配置变化影响">
          <p>
            受影响节点：
            {Object.entries(nodeFreshness)
              .filter(
                ([, value]) =>
                  value.includes("需要更新") || value.includes("上游需更新"),
              )
              .map(([id]) => id)
              .join("、")}
          </p>
          <p>
            旧运行和预览保留。下方启动将创建新运行；是否复用其他节点，以执行预检为准。
          </p>
        </section>
      )}
      <div className="section-label">创建新运行</div>
      <p>执行环境：{profile || "本地服务配置"}</p>
      <ExecutionInputs
        pipeline={effectivePipeline}
        {...{
          multiInput,
          multiView,
          rgbaInput,
          pending,
          uploading,
          uploadMessage,
          inputRefs,
          inputFiles,
          inputOrigins,
          setInputArtifact,
          uploadInputArtifact,
          observationFiles,
          observationsId,
          setObservationsId,
          uploadObservations,
          imageSource,
          setImageSource,
          reusedImage,
          setReusedImage,
          imagePath,
          setImagePath,
          uploaded,
          uploadedFile,
          upload,
        }}
      />
      <p>启动时后端重新编译当前草稿并固定计划。修改画布只影响下一次新运行。</p>
      {configurationBlockedReason && (
        <p role="alert">{configurationBlockedReason}</p>
      )}
      {effectiveReason && (
        <p role="alert">当前入口不可运行：{effectiveReason}</p>
      )}
      {envelope?.snapshot_ref && (
        <label>
          <input
            type="checkbox"
            checked={reuseResults}
            onChange={(e) => setReuseResults(e.target.checked)}
          />
          复用所选运行的有效节点结果（后端核对身份与证据）
        </label>
      )}
      <label>
        <input
          type="checkbox"
          checked={runToSelection}
          onChange={(e) => setRunToSelection(e.target.checked)}
        />
        只运行到选中节点（包含必要上游）
      </label>
      {runToSelection && (
        <p>
          目标：{selectedNode || "请在画布选择算子"}
          。本次创建独立运行，不执行下游。
        </p>
      )}
      <ExecutionPreflight
        disabled={
          (runToSelection &&
            (!selectedNode || !pipeline.nodes[selectedNode])) ||
          pending ||
          unresolvedCreation ||
          uploading ||
          (multiInput
            ? Object.keys(effectivePipeline.inputs).some(
                (name) => !String(inputRefs[name]?.artifact_id || "").trim(),
              )
            : multiView
              ? !observationsId.trim()
              : imageSource === "reference"
                ? !reusedImage
                : imageSource === "path"
                  ? !imagePath.trim()
                  : !uploaded || uploaded.rgba !== rgbaInput) ||
          !!effectiveReason ||
          !!configurationBlockedReason
        }
        intent={{
          pipeline: structuredClone(effectivePipeline),
          ...(reuseResults && envelope?.snapshot_ref
            ? { reuse_source: envelope.snapshot_ref }
            : {}),
          ...(multiInput
            ? {
                input_refs: Object.fromEntries(
                  Object.keys(effectivePipeline.inputs).map((name) => [
                    name,
                    structuredClone(inputRefs[name]),
                  ]),
                ),
              }
            : multiView
              ? { observations_ref: { artifact_id: observationsId.trim() } }
              : imageSource === "reference"
                ? { image_ref: structuredClone(reusedImage!) }
                : imageSource === "path"
                  ? { image_path: imagePath.trim() }
                  : { image_ref: structuredClone(uploaded?.ref || {}) }),
        }}
        onConfirm={(prepared) =>
          mutate(() =>
            submitCreation({
              ...prepared,
              idempotency_key: crypto.randomUUID(),
            }),
          )
        }
        label={
          runToSelection
            ? "运行到这里"
            : reuseResults &&
                envelope?.snapshot_ref &&
                Object.values(nodeFreshness).some(
                  (value) =>
                    value.includes("需要更新") || value.includes("上游需更新"),
                )
              ? "重新执行受影响节点"
              : "启动新运行"
        }
      />
      {unresolvedCreation && (
        <div className="run-node">
          <p role="alert">
            {creation.error ||
              "上次创建请求尚未确认。重试会发送原来的管线和图片，不使用当前草稿；不会重复创建已登记的运行。"}
          </p>
          {creation.request && (
            <>
              <p className="run-identity">
                请求 ID：{creation.request.idempotency_key}
              </p>
              <button
                disabled={pending}
                onClick={() =>
                  void mutate(() => submitCreation(creation.request!))
                }
              >
                重试原创建请求
              </button>
            </>
          )}
          <p>
            放弃本地请求不会取消服务器可能已创建的运行；请先检查运行列表，再发起新的运行。
          </p>
          <button
            disabled={pending}
            onClick={() => {
              try {
                clearCreation();
                setMessage("已放弃本地待确认请求；服务器运行不会被取消。");
              } catch (error) {
                setMessage(`无法清除请求：${String(error)}`);
              }
            }}
          >
            放弃待确认请求，允许新建
          </button>
        </div>
      )}
      <hr />
      <div className="section-label">运行记录</div>
      <button
        disabled={pending}
        onClick={() =>
          void refreshRuns().catch((error) => setMessage(String(error)))
        }
      >
        刷新运行列表
      </button>
      <select
        aria-label="选择运行"
        value={selected}
        disabled={pending}
        onChange={(e) => choose(e.target.value)}
      >
        <option value="">选择运行…</option>
        {runs.map((item) => (
          <option key={item.run_id} value={item.run_id}>
            {item.run_id} · {executionStatus(item.status)}
          </option>
        ))}
      </select>
      {run && (
        <>
          <p className="run-identity">{run.run_id}</p>
          <button
            disabled={pending || !!creation.request}
            onClick={() => {
              if (
                !window.confirm(
                  "将原运行配置载入画布，覆盖当前未保存草稿？不会启动运行或复用人工决定。",
                )
              )
                return;
              void mutate(async () => {
                const draft = await request(
                  `/api/runs/${encodeURIComponent(run.run_id)}/draft`,
                );
                if (
                  draft.source_run_id !== run.run_id ||
                  draft.source_plan_id !== run.dag?.plan_id
                )
                  throw Error("原运行计划身份不匹配");
                onLoadDraft(draft.pipeline);
                setMessage(
                  "已载入原运行配置。请重新编译，提供输入并明确启动新运行；人工节点需要重新确认。",
                );
              });
            }}
          >
            将配置载入画布
          </button>
          {run.dag?.plan_id && (
            <button onClick={() => setShowGraph(true)}>查看固定运行图</button>
          )}
          {showGraph && run.dag?.plan_id && (
            <RunGraph
              runId={run.run_id}
              planId={run.dag.plan_id}
              states={run.dag.node_states}
              onClose={() => setShowGraph(false)}
            />
          )}

          <strong>
            执行状态：{executionStatus(run.status)}
            {envelope.busy ? " · 后台处理中" : ""}
          </strong>
          <p>状态仅对应此运行的固定计划，画布仍是可编辑草稿。</p>
          <MeshAppearancePanel
            runId={run.run_id}
            outputs={envelope.outputs || []}
          />
          <QualityEvidencePanel
            runId={run.run_id}
            outputs={envelope.outputs || []}
          />
          {snapshot?.runId === run.run_id &&
            snapshot.pipeline !== JSON.stringify(pipeline) && (
              <p>草稿已修改，与此运行的计划不同。</p>
            )}
          {envelope.error && <p role="alert">{envelope.error}</p>}
          {!!Object.keys(run.dag?.invalid_evidence || {}).length && (
            <details>
              <summary>已登记的证据问题</summary>
              <p>
                这些记录来自恢复校验，可能包含历史问题；当前是否阻塞以节点和运行状态为准。展开不会修复文件或执行模型。
              </p>
              <pre>{JSON.stringify(run.dag?.invalid_evidence, null, 2)}</pre>
            </details>
          )}
          {!!Object.keys(run.dag?.unassigned_evidence_blocks || {}).length && (
            <div role="alert" aria-label="未定位的证据阻塞">
              <p>以下证据问题尚未定位到具体节点，整个运行仍被阻塞。</p>
              <pre>
                {JSON.stringify(run.dag?.unassigned_evidence_blocks, null, 2)}
              </pre>
            </div>
          )}
          <NodeActionHint advice={actionAdvice?.resume} />
          <button
            disabled={executing || !actionAdvice?.resume?.can_request}
            onClick={() =>
              void mutate(async () =>
                accept(
                  await request(
                    `/api/runs/${encodeURIComponent(run.run_id)}/resume`,
                    { expected_revision: run.dag!.revision },
                  ),
                ),
              )
            }
          >
            恢复 / 继续此运行
          </button>
          <RunOutputs
            runId={run.run_id}
            status={run.status}
            outputs={envelope.outputs || []}
            snapshot={envelope.snapshot_ref?.artifact_id}
            inputs={
              multiInput || (!multiView && "image" in effectivePipeline.inputs)
                ? effectivePipeline.inputs
                : {}
            }
            disabled={{
              continuation:
                executing ||
                uploading ||
                unresolvedCreation ||
                !!configurationBlockedReason,
              comparison: pending || uploading,
              inputBinding: pending || uploading || unresolvedCreation,
            }}
            onPreview={setPreview}
            onContinueExtraction={(prepared) =>
              mutate(() =>
                submitCreation({
                  ...prepared,
                  idempotency_key: crypto.randomUUID(),
                }),
              )
            }
            onCompare={compareOutput}
            onUseAsInput={reuseOutput}
          />
          {Object.entries(run.dag?.node_states || {}).map(([id, state]) => (
            <div className="run-node" key={id}>
              <strong>{id}</strong>
              <span>{executionStatus(state.status)}</span>
              {(state.attempts?.at(-1)?.request ||
                state.attempts?.at(-1)?.decision ||
                state.status === "waiting_for_input") && (
                <HumanDecisionSummary
                  runId={run.run_id}
                  nodeId={id}
                  decisionId={state.attempts?.at(-1)?.decision?.artifact_id}
                  nodeFinishedAt={state.attempts?.at(-1)?.finished_at}
                />
              )}
              {state.status === "running" &&
                state.attempts?.at(-1)?.remote_binding && (
                  <div>
                    <p>
                      {remoteStatusMessage(state.attempts.at(-1)?.error_code)}
                    </p>
                    <p>页面轮询只读取已保存状态，不主动查询服务或执行模型。</p>
                    <p>
                      服务：{state.attempts.at(-1)!.remote_binding!.service_id}
                    </p>
                    <label>
                      远程作业标识 · {id}
                      <input
                        readOnly
                        value={
                          state.attempts.at(-1)!.remote_binding!.submission_key
                        }
                      />
                    </label>
                  </div>
                )}
              {["failed", "interrupted"].includes(state.status) &&
                (state.attempts?.at(-1)?.error_code ||
                  state.attempts?.at(-1)?.error_detail) && (
                  <div role="alert" aria-label={`执行错误 · ${id}`}>
                    <p>{state.attempts?.at(-1)?.error_code}</p>
                    <p>{state.attempts?.at(-1)?.error_detail}</p>
                  </div>
                )}
              {state.dispatch_block_reason && (
                <div role="status" aria-label={`派发受阻 · ${id}`}>
                  <p>当前节点暂不能派发：{state.dispatch_block_reason}</p>
                  <p>
                    这是已保存的检查结果。核实原因后，点击“恢复 /
                    继续此运行”重新检查。
                  </p>
                </div>
              )}
              {state.recovery_blocked_reason && (
                <p>{state.recovery_blocked_reason}</p>
              )}
              {!!state.attempts?.length && (
                <details>
                  <summary>执行记录 · {state.attempts.length} 次</summary>
                  {state.attempts.map((attempt, index) => (
                    <div key={index}>
                      <p>
                        #{attempt.attempt || index + 1} ·{" "}
                        {executionStatus(attempt.status)}
                        <br />
                        {attempt.error_code} {attempt.error_detail}
                      </p>
                      <details>
                        <summary>
                          输入输出证据 · {id} · #{attempt.attempt || index + 1}
                        </summary>
                        <p>
                          这是该次执行保存的引用与摘要；展开不读取 Blob
                          或重新执行节点。
                        </p>
                        <pre>
                          {JSON.stringify(
                            {
                              operator: attempt.operator,
                              adapter: attempt.adapter,
                              resolved_inputs: attempt.resolved_inputs,
                              outputs: attempt.outputs,
                              provenance: attempt.provenance,
                              input_digest: attempt.input_digest,
                              binding_digest: attempt.binding_digest,
                              parameters_digest: attempt.parameters_digest,
                              started_at: attempt.started_at,
                              finished_at: attempt.finished_at,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    </div>
                  ))}
                </details>
              )}
              {state.status === "waiting_for_input" && (
                <ReuseHumanDecision
                  key={`${run.run_id}/${id}`}
                  runId={run.run_id}
                  nodeId={id}
                  revision={run.dag!.revision}
                  runs={runs}
                  disabled={executing}
                  mutate={mutate}
                />
              )}
              {state.status === "waiting_for_input" && (
                <button
                  disabled={executing}
                  onClick={() =>
                    void mutate(async () => {
                      const value = await request(
                        `/api/runs/${encodeURIComponent(run.run_id)}/review`,
                        { node_id: id },
                      );
                      const url = new URL(value.url, window.location.href);
                      if (
                        url.protocol !== "http:" ||
                        !["localhost", "127.0.0.1"].includes(url.hostname)
                      )
                        throw Error("无效的本地审查地址");
                      setShowReview(false);
                      setReview({
                        runId: run.run_id,
                        nodeId: id,
                        url: url.href,
                      });
                    })
                  }
                >
                  准备人工审查 · {id}
                </button>
              )}
              {["failed", "interrupted", "recovery_blocked"].includes(
                state.status,
              ) && (
                <>
                  <NodeActionHint
                    advice={
                      actionAdvice?.nodes[id]?.retry && {
                        ...actionAdvice.nodes[id].retry!,
                        guidance: actionAdvice.nodes[id].guidance,
                      }
                    }
                  />
                  <button
                    disabled={
                      executing || !actionAdvice?.nodes[id]?.retry?.can_request
                    }
                    onClick={() =>
                      void mutate(async () =>
                        accept(
                          await request(
                            `/api/runs/${encodeURIComponent(run.run_id)}/retry`,
                            {
                              node_id: id,
                              expected_revision: run.dag!.revision,
                            },
                          ),
                        ),
                      )
                    }
                  >
                    请求核验并重试 · {id}
                  </button>
                </>
              )}
              {review?.runId === run.run_id &&
                review.nodeId === id &&
                state.status === "waiting_for_input" && (
                  <>
                    <button onClick={() => setShowReview(true)}>
                      在工作台审查 mask
                    </button>
                    <a href={review.url} target="_blank" rel="noreferrer">
                      打开 mask 审查页面 ↗
                    </a>
                  </>
                )}
            </div>
          ))}
        </>
      )}
      {showReview && review && run?.run_id === review.runId && (
        <div
          className="run-graph-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="mask 审查"
        >
          <header>
            <strong>mask 审查 · {review.nodeId}</strong>
            <button onClick={() => setShowReview(false)}>收起审查</button>
          </header>
          <p>确认将在下方审查页提交。收起此面板不会取消运行或撤销决定。</p>
          <iframe
            className="mask-review-frame"
            title={`mask 审查 ${review.nodeId}`}
            src={review.url}
            referrerPolicy="no-referrer"
          />
        </div>
      )}
      {preview && (
        <Suspense fallback={<p role="status">正在加载预览组件…</p>}>
          <GlbPreview {...preview} onClose={() => setPreview(undefined)} />
        </Suspense>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
