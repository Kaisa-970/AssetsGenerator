import { OutputComparison, type ComparisonSlot } from "./OutputComparison";
import { NodeActionHint, type RunActionAdvice } from "./NodeActionHint";
import { ContinueExtraction } from "./ContinueExtraction";
import { ExecutionPreflight } from "./ExecutionPreflight";
import { LocalInputPreview } from "./LocalInputPreview";
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

type NodeState = {
  status: string;
  recovery_blocked_reason?: string | null;
  dispatch_block_reason?: string | null;
  attempts?: {
    request?: { artifact_id: string };
    decision?: { artifact_id: string };
    reused_from?: { artifact_id: string };
    resolved_inputs?: Record<string, unknown>;
    outputs?: Record<string, unknown>;
    provenance?: Record<string, unknown>;
    operator?: string;
    adapter?: string;
    input_digest?: string;
    binding_digest?: string;
    parameters_digest?: string;
    started_at?: string;
    finished_at?: string | null;
    error_code?: string;
    error_detail?: string;
    status?: string;
    attempt?: number;
    remote_binding?: { service_id: string; submission_key: string };
  }[];
};
type Run = {
  run_id: string;
  status: string;
  dag?: {
    plan_id?: string;
    named_actual_inputs?: Record<string, { artifact_id: string }>;
    revision: number;
    invalid_evidence?: Record<string, string>;
    unassigned_evidence_blocks?: Record<string, string>;
    node_states: Record<string, NodeState>;
  };
};
type Envelope = {
  actions?: RunActionAdvice;
  snapshot_ref?: { artifact_id: string };
  run: Run;
  busy?: boolean;
  error?: string | null;
  outputs?: { node_id: string; port: string; kind?: string; url: string }[];
};
async function request(path: string, body?: unknown, signal?: AbortSignal) {
  const response = await fetch(path, {
    ...(body === undefined
      ? {}
      : { method: "POST", body: JSON.stringify(body) }),
    headers: { "Content-Type": "application/json" },
    signal,
  });
  const value = await response.json();
  if (!response.ok) throw Error(value.error || `HTTP ${response.status}`);
  return value;
}
type CreationRequest = {
  reuse_source?: { artifact_id: string };
  pipeline: Pipeline;
  idempotency_key: string;
  preflight_digest?: string;
  image_path?: string;
  image_ref?: Record<string, unknown>;
  observations_ref?: Record<string, unknown>;
  input_refs?: Record<string, Record<string, unknown>>;
};
const creationStorageKey = "assets-generator:pending-creation:v1";
function restoreCreation(): { request?: CreationRequest; error?: string } {
  try {
    const raw = sessionStorage.getItem(creationStorageKey);
    if (!raw) return {};
    const value = JSON.parse(raw);
    if (
      !value ||
      typeof value !== "object" ||
      typeof value.idempotency_key !== "string" ||
      !/^[A-Za-z0-9_-]{1,128}$/.test(value.idempotency_key) ||
      !value.pipeline ||
      typeof value.pipeline !== "object" ||
      !(
        (typeof value.image_path === "string" &&
          value.image_path.length > 0 &&
          !value.image_ref &&
          !value.observations_ref) ||
        (value.image_ref &&
          typeof value.image_ref.artifact_id === "string" &&
          !value.image_path &&
          !value.observations_ref) ||
        (value.observations_ref &&
          typeof value.observations_ref.artifact_id === "string" &&
          !value.image_path &&
          !value.image_ref) ||
        (value.input_refs &&
          typeof value.input_refs === "object" &&
          !Array.isArray(value.input_refs) &&
          !value.image_path &&
          !value.image_ref &&
          !value.observations_ref &&
          Object.values(value.input_refs).every(
            (ref) =>
              !!ref &&
              typeof ref === "object" &&
              typeof (ref as Record<string, unknown>).artifact_id === "string",
          ))
      )
    )
      throw Error("保存的请求格式无效");
    return { request: value };
  } catch (error) {
    return { error: `无法读取待确认请求：${String(error)}` };
  }
}
export function ExecutionPanel({
  pipeline,
  profile,
  executionReason,
  selectedNode,
  previewHost,
  onLoadDraft,
}: {
  pipeline: Pipeline;
  profile?: string;
  executionReason?: string;
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
  const [creation, setCreation] = useState(restoreCreation);
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
  const multiInput = Object.keys(effectivePipeline.inputs).length > 1;
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
    // Persist before sending so an uncertain response retains the original intent.
    sessionStorage.setItem(creationStorageKey, JSON.stringify(submitted));
    setCreation({ request: submitted });
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(submitted),
    });
    const value = await response.json();
    if (!response.ok) {
      if (response.status === 409 && value.code === "preflight_changed") {
        sessionStorage.removeItem(creationStorageKey);
        setCreation({});
      }
      throw Error(value.error || `HTTP ${response.status}`);
    }
    if (!value.run?.run_id) throw Error("服务未返回有效运行，原请求已保留");
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
    sessionStorage.removeItem(creationStorageKey);
    setCreation({});
    setMessage("运行已创建；请在下方查看真实节点状态。");
  };
  const unresolvedCreation = !!creation.request || !!creation.error;
  const run = envelope?.run;
  const actionAdvice =
    envelope?.actions?.run_id === run?.run_id &&
    envelope?.actions?.revision === run?.dag?.revision
      ? envelope?.actions
      : undefined;
  const reuseStates = run?.dag?.node_states;
  const [nodeFreshness, setNodeFreshness] = useState<Record<string, string>>(
    {},
  );
  const sourcePlan = useRef<{ runId: string; plan: any } | undefined>(
    undefined,
  );
  useEffect(() => {
    if (!selected || !reuseResults) {
      setNodeFreshness({});
      return;
    }
    const controller = new AbortController();
    setNodeFreshness({});
    const timer = setTimeout(() => {
      void (async () => {
        const old =
          sourcePlan.current?.runId === selected
            ? sourcePlan.current.plan
            : await request(
                `/api/runs/${encodeURIComponent(selected)}/plan`,
                undefined,
                controller.signal,
              );
        sourcePlan.current = { runId: selected, plan: old };
        const compiled = await request(
          "/api/compile",
          { pipeline: effectivePipeline },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        const stable = (v: any): string =>
          JSON.stringify(v, (_k, x) =>
            x && typeof x === "object" && !Array.isArray(x)
              ? Object.fromEntries(
                  Object.keys(x)
                    .sort()
                    .map((k) => [k, x[k]]),
                )
              : x,
          );
        const current = compiled.bound_plan;
        const statuses: Record<string, string> = {};
        const oldBound = old.plan || old;
        for (const n of current?.static_plan?.nodes || []) {
          const previous = oldBound.static_plan?.nodes?.find(
            (v: any) => v.node_id === n.node_id,
          );
          const dependencies =
            current.static_plan.dependencies[n.node_id] || [];
          statuses[n.node_id] = !previous
            ? "新节点 · 需要执行"
            : stable(oldBound.bindings?.[n.node_id]) !==
                  stable(current.bindings[n.node_id]) ||
                stable(previous.inputs) !== stable(n.inputs) ||
                previous.operator_contract_digest !== n.operator_contract_digest
              ? "配置已改变 · 需要更新"
              : dependencies.some(
                    (id: string) =>
                      statuses[id] !== "配置匹配 · 启动时核验输入和证据",
                  )
                ? "上游需更新 · 需要重新核验"
                : "配置匹配 · 启动时核验输入和证据";
        }
        setNodeFreshness(statuses);
      })().catch(() => {
        if (!controller.signal.aborted) setNodeFreshness({});
      });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [effectivePipeline, selected, reuseResults]);

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
    const kind = effectivePipeline.inputs[name]?.kind;
    const endpoint =
      kind === "binary_mask"
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
      const reference = value.image_ref || value.mask_ref;
      if (!response.ok || typeof reference?.artifact_id !== "string")
        throw Error(value.error || "服务未返回有效 Artifact 引用");
      if (inputGeneration.current.revision !== revision) {
        setUploadMessage("上传期间输入契约已修改，请为当前输入重新选择文件。");
        return;
      }
      setInputArtifact(name, reference.artifact_id);
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
            <p>
              显示所选运行的历史结果；画布或输入改变后，启动时逐节点核验是否可复用。
            </p>
            <label>
              输出端口
              <select
                aria-label="预览输出端口"
                value={previewPort}
                onChange={(e) => setPreviewPort(e.target.value)}
              >
                <option value="">默认输出</option>
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
              envelope?.outputs
                ?.filter((o) => o.node_id === selectedNode)
                .filter((o, i, all) =>
                  all.some((item) => item.port === previewPort)
                    ? o.port === previewPort
                    : all.some((item) => item.port === "mask")
                      ? o.port === "mask"
                      : i === 0,
                )
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
                  ) : output.kind === "gltf_asset" ? (
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
              <p>该节点在当前运行中尚无可预览结果。</p>
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
      <div className="section-label">创建新运行</div>
      <p>执行环境：{profile || "本地服务配置"}</p>
      {multiInput ? (
        <div>
          <p>
            为每个输入上传文件，或从下方历史运行选择“用作输入”。 支持 RGB/RGBA
            图片和二值 PNG 遮罩。
          </p>
          {Object.entries(effectivePipeline.inputs).map(([name, port]) => {
            const artifactId = String(inputRefs[name]?.artifact_id || "");
            return (
              <div className="run-node" key={name}>
                <strong>{name}</strong> ·{" "}
                {port.kind || port.kinds?.join(" | ") || "未声明类型"}
                {(port.kind === "rgb_image" ||
                  port.kind === "rgba_image" ||
                  port.kind === "binary_mask") && (
                  <input
                    type="file"
                    accept={
                      port.kind === "binary_mask" ? "image/png" : "image/*"
                    }
                    aria-label={`上传输入 ${name}`}
                    disabled={pending || uploading}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.target.value = "";
                      void uploadInputArtifact(name, file);
                    }}
                  />
                )}
                {inputFiles[name]?.artifactId === artifactId && (
                  <LocalInputPreview
                    file={inputFiles[name].file}
                    label={`输入 ${name}`}
                  />
                )}
                {inputOrigins[name]?.artifactId === artifactId && (
                  <p>
                    下游输入来源：{inputOrigins[name].runId} /{" "}
                    {inputOrigins[name].nodeId}.{inputOrigins[name].port}。
                    切换查看其他运行不会改变此绑定。
                  </p>
                )}
                <input
                  aria-label={`输入 ${name} Artifact ID`}
                  placeholder="sha256:…"
                  value={artifactId}
                  disabled={pending || uploading}
                  onChange={(e) => setInputArtifact(name, e.target.value)}
                />
              </div>
            );
          })}
          {uploadMessage && <p role="status">{uploadMessage}</p>}
        </div>
      ) : multiView ? (
        <div>
          <label>
            选择多视图照片（RGB，2–32 张）
            <input
              type="file"
              multiple
              accept="image/png,image/jpeg,image/webp"
              aria-label="上传多视图照片"
              disabled={pending || uploading}
              onChange={(e) => {
                const files = Array.from(e.target.files || []);
                e.target.value = "";
                if (files.length) void uploadObservations(files);
              }}
            />
          </label>
          <p>按选择顺序分配视图 ID；不自动补充相机、mask 或深度。</p>
          {observationFiles.length > 0 && (
            <ol>
              {observationFiles.map((name, i) => (
                <li key={i}>{name}</li>
              ))}
            </ol>
          )}
          {uploadMessage && <p role="status">{uploadMessage}</p>}
          <label>
            已导入的 ObservationBundle Artifact ID
            <input
              aria-label="观测包 Artifact ID"
              value={observationsId}
              placeholder="sha256:…"
              disabled={pending || uploading}
              onChange={(e) => setObservationsId(e.target.value)}
            />
            <p>也可使用 import-observations 导入后的已有引用。</p>
          </label>
        </div>
      ) : (
        <>
          {rgbaInput && (
            <p>请提供已处理好的 RGBA PNG，透明区域为背景；此流程不自动抠图。</p>
          )}
          <label>
            图片来源
            <select
              aria-label="图片来源"
              value={reusedImage ? "reference" : imageSource}
              disabled={pending || uploading}
              onChange={(e) => {
                setImageSource(e.target.value);
                if (e.target.value !== "reference") setReusedImage(undefined);
              }}
            >
              <option value="path">服务器本地路径</option>
              <option value="upload">从浏览器上传</option>
              <option value="reference">使用历史输出</option>
            </select>
          </label>
          {reusedImage || imageSource === "reference" ? (
            <div>
              <p>在下方选择历史运行，点击匹配输出的“用作输入”。</p>
              {reusedImage && (
                <p className="run-identity">{reusedImage.artifact_id}</p>
              )}
              {reusedImage &&
                inputOrigins.image?.artifactId === reusedImage.artifact_id && (
                  <p>
                    下游输入来源：{inputOrigins.image.runId} /{" "}
                    {inputOrigins.image.nodeId}.{inputOrigins.image.port}。
                    切换查看其他运行不会改变此绑定。
                  </p>
                )}
            </div>
          ) : imageSource === "path" ? (
            <label>
              服务所在电脑的图片绝对路径
              <input
                aria-label="运行图片路径"
                placeholder="/path/to/image.png"
                value={imagePath}
                disabled={pending}
                onChange={(e) => setImagePath(e.target.value)}
              />
            </label>
          ) : (
            <div>
              <label>
                上传图片（最多 20 MiB）
                <input
                  type="file"
                  aria-label="上传运行图片"
                  accept={rgbaInput ? "image/png" : "image/*"}
                  disabled={pending || uploading}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    void upload(file);
                  }}
                />
              </label>
              {uploadedFile && uploaded?.rgba === rgbaInput && (
                <LocalInputPreview file={uploadedFile} label="运行图片" />
              )}
              {uploading && <p>正在上传并验证图片…</p>}
              {uploaded && (
                <p className="run-identity">
                  {uploaded.name} · {String(uploaded.ref.artifact_id)}
                </p>
              )}
              {uploadMessage && <p role="status">{uploadMessage}</p>}
            </div>
          )}
        </>
      )}
      <p>启动时后端重新编译当前草稿并固定计划。修改画布只影响下一次新运行。</p>
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
          !!effectiveReason
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
        label={runToSelection ? "运行到这里" : "启动新运行"}
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
                sessionStorage.removeItem(creationStorageKey);
                setCreation({});
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
          {run.status === "succeeded" && !envelope.outputs?.length && (
            <p>
              运行已完成，暂无可预览图片；分割可能未找到符合提示词和阈值的候选。
            </p>
          )}
          {!!envelope.outputs?.length && (
            <div className="run-node">
              <strong>可查看的节点输出</strong>
              {envelope.outputs
                .filter(
                  (output) =>
                    output.kind === "gltf_asset" ||
                    (!output.kind && output.port === "glb"),
                )
                .map((output) => (
                  <button
                    key={`preview:${output.node_id}`}
                    onClick={() =>
                      setPreview({
                        runId: run.run_id,
                        nodeId: output.node_id,
                        url: output.url,
                      })
                    }
                  >
                    预览模型 · {output.node_id}
                  </button>
                ))}
              {envelope.snapshot_ref &&
                envelope.outputs
                  .filter(
                    (output) =>
                      output.kind === "binary_mask" && output.port === "mask",
                  )
                  .map((output) => (
                    <ContinueExtraction
                      key={`${run.run_id}:${output.node_id}:${envelope.snapshot_ref!.artifact_id}`}
                      runId={run.run_id}
                      nodeId={output.node_id}
                      snapshot={envelope.snapshot_ref!.artifact_id}
                      disabled={executing || uploading || unresolvedCreation}
                      onConfirm={(prepared) =>
                        mutate(() =>
                          submitCreation({
                            ...prepared,
                            idempotency_key: crypto.randomUUID(),
                          }),
                        )
                      }
                    />
                  ))}
              {envelope.snapshot_ref &&
                envelope.outputs
                  .filter((output) =>
                    [
                      "rgb_image",
                      "rgba_image",
                      "binary_mask",
                      "gltf_asset",
                    ].includes(output.kind || ""),
                  )
                  .flatMap((output) =>
                    (["A", "B"] as const).map((side) => (
                      <button
                        key={`compare:${side}:${output.node_id}:${output.port}`}
                        disabled={pending || uploading}
                        onClick={() => {
                          const snapshot = envelope.snapshot_ref!.artifact_id;
                          void mutate(async () => {
                            const source = await request(
                              `/api/runs/${encodeURIComponent(run.run_id)}/snapshot-reference/${encodeURIComponent(output.node_id)}/${encodeURIComponent(output.port)}?snapshot=${encodeURIComponent(snapshot)}`,
                            );
                            if (
                              source.source_run_id !== run.run_id ||
                              source.node_id !== output.node_id ||
                              source.port !== output.port ||
                              source.source_snapshot?.artifact_id !==
                                snapshot ||
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
                            setMessage(
                              `已固定比较项 ${side}，不改变下游输入。`,
                            );
                          });
                        }}
                      >
                        加入比较 {side} · {output.node_id} · {output.port}
                      </button>
                    )),
                  )}
              {envelope.outputs
                .filter((output) => output.kind === "asset_release")
                .map((output) => (
                  <a
                    key={`archive:${output.node_id}:${output.port}`}
                    href={`/api/runs/${encodeURIComponent(run.run_id)}/archives/${encodeURIComponent(output.node_id)}/${encodeURIComponent(output.port)}`}
                    download={`${run.run_id}-${output.node_id}.zip`}
                  >
                    下载发布包 · {output.node_id}
                  </a>
                ))}
              {envelope.outputs
                .filter((output) =>
                  ["rgb_image", "rgba_image", "binary_mask"].includes(
                    output.kind || "",
                  ),
                )
                .map((output) => (
                  <ImageOutput
                    key={`${run.run_id}:${output.node_id}:${output.port}:${output.url}`}
                    runId={run.run_id}
                    nodeId={output.node_id}
                    port={output.port}
                    url={output.url}
                  />
                ))}
              {(multiInput ||
                (!multiView && "image" in effectivePipeline.inputs)) &&
                envelope.outputs.flatMap((output) =>
                  Object.entries(effectivePipeline.inputs)
                    .filter(([, port]) =>
                      (port.kinds || [port.kind]).includes(output.kind),
                    )
                    .map(([name, port]) => (
                      <button
                        key={`reuse:${output.node_id}:${output.port}:${name}`}
                        disabled={pending || uploading || unresolvedCreation}
                        onClick={() => {
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
                              !(port.kinds || [port.kind]).includes(
                                source.kind,
                              ) ||
                              (port.schema_name &&
                                source.schema_name !== port.schema_name) ||
                              (port.schema_version &&
                                source.schema_version !==
                                  port.schema_version) ||
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
                        }}
                      >
                        用作输入 {name} · {output.node_id} · {output.port}
                      </button>
                    )),
                )}
              {envelope.outputs.map((output) => (
                <a
                  key={`${output.node_id}:${output.port}`}
                  href={output.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {output.node_id} · {output.port} ↗
                </a>
              ))}
            </div>
          )}
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
