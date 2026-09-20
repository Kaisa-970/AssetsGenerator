import { remoteStatusMessage } from "./remoteStatus";
import { RunGraph } from "./RunGraph";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { Pipeline } from "./graph";

const GlbPreview = lazy(() =>
  import("./GlbPreview").then((module) => ({ default: module.GlbPreview })),
);

type NodeState = {
  status: string;
  recovery_blocked_reason?: string | null;
  attempts?: {
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
    revision: number;
    node_states: Record<string, NodeState>;
  };
};
type Envelope = {
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
  pipeline: Pipeline;
  idempotency_key: string;
  image_path?: string;
  image_ref?: Record<string, unknown>;
  observations_ref?: Record<string, unknown>;
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
          !value.image_ref)
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
}: {
  pipeline: Pipeline;
  profile?: string;
  executionReason?: string;
}) {
  const [creation, setCreation] = useState(restoreCreation);
  const [preview, setPreview] = useState<{
    runId: string;
    nodeId: string;
    url: string;
  }>();
  const [showReview, setShowReview] = useState(false);
  const [showGraph, setShowGraph] = useState(false);
  const [imagePath, setImagePath] = useState("");
  const [observationsId, setObservationsId] = useState("");
  const [observationFiles, setObservationFiles] = useState<string[]>([]);
  const multiView =
    Object.keys(pipeline.inputs).length === 1 &&
    "observations" in pipeline.inputs;
  const rgbaInput = pipeline.inputs.image?.kind === "rgba_image";
  const [imageSource, setImageSource] = useState("path");
  const [uploaded, setUploaded] = useState<{
    name: string;
    rgba: boolean;
    ref: Record<string, unknown>;
  }>();
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
    const value: Envelope = await request("/api/runs", submitted);
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
  const executing = pending || !!envelope?.busy;
  const upload = async (file?: File) => {
    if (uploadPending.current) return;
    setUploaded(undefined);
    setUploadMessage("");
    if (!file) return;
    if (!file.size || file.size > 20 * 1024 * 1024) {
      setUploadMessage("请选择非空且不超过 20 MiB 的图片。");
      return;
    }
    uploadPending.current = true;
    setUploading(true);
    try {
      const response = await fetch(rgbaInput ? "/api/inputs/rgba" : "/api/inputs/image", {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      });
      const value = await response.json();
      if (!response.ok) throw Error(value.error || `HTTP ${response.status}`);
      if (!value.image_ref || typeof value.image_ref.artifact_id !== "string")
        throw Error("服务未返回有效的图片引用");
      setUploaded({ name: file.name, rgba: rgbaInput, ref: value.image_ref });
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
      <div className="section-label">创建新运行</div>
      <p>已配置模型：{profile || "本地服务配置"}</p>
      {multiView ? (
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
          {rgbaInput && <p>请提供已处理好的 RGBA PNG，透明区域为背景；此流程不自动抠图。</p>}
          <label>
            图片来源
            <select
              aria-label="图片来源"
              value={imageSource}
              disabled={pending || uploading}
              onChange={(e) => setImageSource(e.target.value)}
            >
              <option value="path">服务器本地路径</option>
              <option value="upload">从浏览器上传</option>
            </select>
          </label>
          {imageSource === "path" ? (
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
      {executionReason && (
        <p role="alert">当前入口不可运行：{executionReason}</p>
      )}
      <button
        className="primary"
        disabled={
          pending ||
          unresolvedCreation ||
          uploading ||
          (multiView
            ? !observationsId.trim()
            : imageSource === "path"
              ? !imagePath.trim()
              : !uploaded || uploaded.rgba !== rgbaInput) ||
          !!executionReason
        }
        onClick={() =>
          void mutate(() =>
            submitCreation({
              pipeline: structuredClone(pipeline),
              idempotency_key: crypto.randomUUID(),
              ...(multiView
                ? { observations_ref: { artifact_id: observationsId.trim() } }
                : imageSource === "path"
                  ? { image_path: imagePath.trim() }
                  : { image_ref: structuredClone(uploaded!.ref) }),
            }),
          )
        }
      >
        启动新运行
      </button>
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
            {item.run_id} · {item.status}
          </option>
        ))}
      </select>
      {run && (
        <>
          <p className="run-identity">{run.run_id}</p>
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
            运行状态：{run.status}
            {envelope.busy ? " · 后台处理中" : ""}
          </strong>
          <p>状态仅对应此运行的固定计划，画布仍是可编辑草稿。</p>
          {snapshot?.runId === run.run_id &&
            snapshot.pipeline !== JSON.stringify(pipeline) && (
              <p>草稿已修改，与此运行的计划不同。</p>
            )}
          {envelope.error && <p role="alert">{envelope.error}</p>}
          <button
            disabled={executing}
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
          {!!envelope.outputs?.length && (
            <div className="run-node">
              <strong>可查看的节点输出</strong>
              {envelope.outputs
                .filter((output) => output.kind === "gltf_asset" || (!output.kind && output.port === "glb"))
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
              <span>{state.status}</span>
              {state.status === "running" && state.attempts?.at(-1)?.remote_binding && (
                <div>
                  <p>{remoteStatusMessage(state.attempts.at(-1)?.error_code)}</p>
                  <p>页面轮询只读取已保存状态，不主动查询服务或执行模型。</p>
                  <p>服务：{state.attempts.at(-1)!.remote_binding!.service_id}</p>
                  <label>
                    远程作业标识 · {id}
                    <input readOnly value={state.attempts.at(-1)!.remote_binding!.submission_key} />
                  </label>
                </div>
              )}
              {state.recovery_blocked_reason && (
                <p>{state.recovery_blocked_reason}</p>
              )}
              {!!state.attempts?.length && (
                <details>
                  <summary>执行记录 · {state.attempts.length} 次</summary>
                  {state.attempts.map((attempt, index) => (
                    <p key={index}>
                      #{attempt.attempt || index + 1} · {attempt.status}
                      <br />
                      {attempt.error_code} {attempt.error_detail}
                    </p>
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
                <button
                  disabled={executing}
                  onClick={() =>
                    void mutate(async () =>
                      accept(
                        await request(
                          `/api/runs/${encodeURIComponent(run.run_id)}/retry`,
                          { node_id: id, expected_revision: run.dag!.revision },
                        ),
                      ),
                    )
                  }
                >
                  显式重试 · {id}
                </button>
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
