import { createPortal } from "react-dom";
import { useEffect, useState } from "react";
import type { Pipeline } from "./graph";
import type { ReactNode } from "react";
import { LocalInputPreview } from "./LocalInputPreview";
type Props = {
  pipeline: Pipeline;
  inputHosts?: Record<string, HTMLElement>;
  historySelector?: (name: string) => ReactNode;
  clearInput: (name: string) => void;
  workspaceRevision?: number;
  multiInput: boolean;
  multiView: boolean;
  rgbaInput: boolean;
  pending: boolean;
  uploading: boolean;
  uploadMessage: string;
  inputRefs: Record<string, Record<string, unknown>>;
  inputFiles: Record<string, { file: File; artifactId: string }>;
  historicalInputs?: {
    run_id: string;
    snapshot_ref: { artifact_id: string };
    inputs: Record<
      string,
      { artifact_id: string; identity?: Record<string, unknown> }
    >;
  };
  useHistoricalInput?: (name: string) => void;
  inputOrigins: Record<
    string,
    {
      artifactId: string;
      runId: string;
      nodeId: string;
      port: string;
      snapshot?: string;
    }
  >;
  setInputArtifact: (name: string, value: string) => void;
  uploadInputArtifact: (name: string, file?: File) => Promise<void>;
  observationFiles: File[];
  observationsId: string;
  setObservationsId: (value: string) => void;
  uploadObservations: (files: File[]) => Promise<void>;
  imageSource: string;
  setImageSource: (value: string) => void;
  reusedImage?: { artifact_id: string };
  setReusedImage: (value: { artifact_id: string } | undefined) => void;
  imagePath: string;
  setImagePath: (value: string) => void;
  uploaded?: { name: string; rgba: boolean; ref: Record<string, unknown> };
  uploadedFile?: File;
  upload: (file?: File) => Promise<void>;
};
function TextInput({
  name,
  artifactId,
  disabled,
  apply,
  invalidate,
}: {
  name: string;
  artifactId: string;
  disabled: boolean;
  apply: (file: File) => void;
  invalidate: () => void;
}) {
  const [text, setText] = useState("");
  const [bound, setBound] = useState<{
    id: string;
    text?: string;
    error?: string;
  }>();
  useEffect(() => {
    if (!artifactId) {
      setBound(undefined);
      return;
    }
    setText("");
    setBound(undefined);
    const controller = new AbortController();
    let active = true;
    void (async () => {
      try {
        const response = await fetch(
          `/api/inputs/text?artifact_id=${encodeURIComponent(artifactId)}`,
          { signal: controller.signal },
        );
        const value = await response.json();
        if (
          !response.ok ||
          value.artifact_id !== artifactId ||
          typeof value.text !== "string"
        )
          throw Error("无法核实文本内容");
        if (active) setBound({ id: artifactId, text: value.text });
      } catch {
        if (active)
          setBound({
            id: artifactId,
            error: "无法读取已绑定文本，请检查引用或重新输入并应用文本。",
          });
      }
    })();
    return () => {
      active = false;
      controller.abort();
    };
  }, [artifactId]);
  return (
    <div>
      <label>
        编辑中的文本（应用后生效）
        <textarea
          aria-label={`文本输入 ${name}`}
          value={text}
          disabled={disabled}
          placeholder="例如 chair"
          onChange={(event) => {
            setText(event.target.value);
            invalidate();
          }}
        />
      </label>
      <button
        disabled={disabled || !text.trim()}
        onClick={() =>
          apply(new File([text], "input.txt", { type: "text/plain" }))
        }
      >
        应用文本
      </button>
      <section aria-label={`已绑定文本 ${name}`}>
        <strong>实际运行使用的文本</strong>
        {!artifactId ? (
          <p>尚未绑定；编辑中的文本尚未应用。</p>
        ) : bound?.id !== artifactId ? (
          <p>正在读取已绑定文本…</p>
        ) : bound.error ? (
          <p role="alert">{bound.error}</p>
        ) : (
          <pre>{bound.text}</pre>
        )}
      </section>
    </div>
  );
}

export function ExecutionInputs({
  pipeline: effectivePipeline,
  inputHosts,
  historySelector,
  clearInput,
  workspaceRevision = 0,
  multiInput,
  multiView,
  rgbaInput,
  pending,
  uploading,
  uploadMessage,
  inputRefs,
  inputFiles,
  inputOrigins,
  historicalInputs,
  useHistoricalInput,
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
}: Props) {
  const content = (
    <>
      {multiInput ? (
        <div>
          <p>
            为每个输入上传文件，或从下方历史运行选择“用作输入”。 支持 RGB/RGBA
            图片、二值 PNG 遮罩和文本。
          </p>
          {Object.entries(effectivePipeline.inputs).map(([name, port]) => {
            const artifactId = String(inputRefs[name]?.artifact_id || "");
            const kind = port.kind || port.kinds?.[0];
            const control = (
              <div className="run-node blueprint-input-value" key={name}>
                <div className="blueprint-input-value-header">
                  <strong>{name}</strong>
                  <span>
                    {port.kind || port.kinds?.join(" | ") || "未声明类型"}
                  </span>
                </div>
                {kind === "text" && (
                  <TextInput
                    key={`${workspaceRevision}:${name}:${JSON.stringify(port)}`}
                    name={name}
                    artifactId={artifactId}
                    disabled={pending || uploading}
                    invalidate={() => setInputArtifact(name, "")}
                    apply={(file) => {
                      void uploadInputArtifact(name, file);
                    }}
                  />
                )}
                {(kind === "rgb_image" ||
                  kind === "rgba_image" ||
                  kind === "binary_mask") && (
                  <input
                    className="blueprint-input-file"
                    type="file"
                    accept={kind === "binary_mask" ? "image/png" : "image/*"}
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
                {historicalInputs?.inputs[name] &&
                  historicalInputs.inputs[name].artifact_id !== artifactId && (
                    <div className="blueprint-input-origin">
                      <p>
                        历史来源：运行 {historicalInputs.run_id} · 输入 {name}
                        {historicalInputs.inputs[name].identity?.kind
                          ? ` · ${String(historicalInputs.inputs[name].identity.kind)}`
                          : ""}
                      </p>
                      <details>
                        <summary>查看来源摘要</summary>
                        <p>
                          快照：
                          {historicalInputs.snapshot_ref.artifact_id.slice(
                            0,
                            20,
                          )}
                          …
                        </p>
                        <p>
                          Artifact：
                          {historicalInputs.inputs[name].artifact_id.slice(
                            0,
                            20,
                          )}
                          …
                        </p>
                      </details>
                      <button
                        disabled={pending || uploading}
                        onClick={() => useHistoricalInput?.(name)}
                      >
                        使用历史输入 · {name}
                      </button>
                    </div>
                  )}
                {inputOrigins[name]?.artifactId === artifactId && (
                  <p className="blueprint-input-origin">
                    下游输入来源：{inputOrigins[name].runId} /{" "}
                    {inputOrigins[name].nodeId}.{inputOrigins[name].port}。
                    切换查看其他运行不会改变此绑定。
                  </p>
                )}
                {uploadMessage && inputHosts?.[name] && (
                  <p role="status">{uploadMessage}</p>
                )}
                {artifactId && (
                  <button
                    className="blueprint-input-primary"
                    disabled={pending || uploading}
                    onClick={() => clearInput(name)}
                  >
                    清除输入 · {name}
                  </button>
                )}
                {historySelector?.(name)}
                <details className="blueprint-input-more">
                  <summary>高级：输入引用</summary>
                  <input
                    aria-label={`输入 ${name} Artifact ID`}
                    placeholder="sha256:…"
                    value={artifactId}
                    disabled={pending || uploading}
                    onChange={(e) => setInputArtifact(name, e.target.value)}
                  />
                </details>
                <p>
                  {artifactId
                    ? "已绑定输入；启动后使用此内容。"
                    : "尚未提供输入。"}
                </p>
              </div>
            );
            return inputHosts?.[name]
              ? createPortal(control, inputHosts[name], name)
              : control;
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
          {(observationsId || observationFiles.length > 0) && (
            <button
              disabled={pending || uploading}
              onClick={() =>
                clearInput(Object.keys(effectivePipeline.inputs)[0])
              }
            >
              清除观测输入
            </button>
          )}
          {observationFiles.length > 0 && (
            <details>
              <summary>
                {observationsId ? "已导入" : "待导入"} {observationFiles.length}{" "}
                个视图 · 查看顺序与缩略图
              </summary>
              <p>
                调整顺序或移除后重新导入为新观测包；至少保留两张照片，完成前不能启动。
              </p>
              <ol className="observation-thumbnails">
                {observationFiles.map((file, i) => (
                  <li key={i}>
                    <LocalInputPreview file={file} label={`视图 ${i + 1}`} />
                    <button
                      disabled={pending || uploading || i === 0}
                      onClick={() => {
                        const next = [...observationFiles];
                        [next[i - 1], next[i]] = [next[i], next[i - 1]];
                        void uploadObservations(next);
                      }}
                    >
                      前移视图 {i + 1}
                    </button>
                    <button
                      disabled={
                        pending ||
                        uploading ||
                        i === observationFiles.length - 1
                      }
                      onClick={() => {
                        const next = [...observationFiles];
                        [next[i], next[i + 1]] = [next[i + 1], next[i]];
                        void uploadObservations(next);
                      }}
                    >
                      后移视图 {i + 1}
                    </button>
                    <button
                      disabled={
                        pending || uploading || observationFiles.length <= 2
                      }
                      onClick={() => {
                        void uploadObservations(
                          observationFiles.filter((_, index) => index !== i),
                        );
                      }}
                    >
                      移除视图 {i + 1}
                    </button>
                  </li>
                ))}
              </ol>
            </details>
          )}
          {!observationsId && observationFiles.length > 0 && (
            <button
              disabled={pending || uploading}
              onClick={() => void uploadObservations(observationFiles)}
            >
              {uploading ? "正在导入照片…" : "重试导入照片"}
            </button>
          )}
          {uploadMessage && <p role="status">{uploadMessage}</p>}
          <details>
            <summary>高级：观测包引用</summary>
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
          </details>
        </div>
      ) : (
        <>
          {(uploaded || reusedImage || imagePath) && (
            <button
              className="blueprint-input-primary"
              disabled={pending || uploading}
              onClick={() => clearInput("image")}
            >
              清除输入 · image
            </button>
          )}
          {historySelector?.("image")}
          <details className="blueprint-input-more">
            <summary>更多操作</summary>
          </details>
          {rgbaInput && (
            <p>请提供已处理好的 RGBA PNG，透明区域为背景；此流程不自动抠图。</p>
          )}
          <p aria-label="当前图片来源">
            当前来源：
            {reusedImage || imageSource === "reference"
              ? "历史输出"
              : imageSource === "path"
                ? "服务器路径（高级设置）"
                : "浏览器上传"}
          </p>
          <details>
            <summary>高级：图片来源</summary>
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
            {imageSource === "path" && !reusedImage && (
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
            )}
          </details>
          {(reusedImage || imageSource !== "upload") && (
            <button
              disabled={pending || uploading}
              onClick={() => {
                setReusedImage(undefined);
                setImageSource("upload");
              }}
            >
              改为上传图片
            </button>
          )}
          {reusedImage || imageSource === "reference" ? (
            <div>
              <p>展开“从历史结果选择”，明确选用一个输出。</p>
              {reusedImage && (
                <details>
                  <summary>高级：输入引用</summary>
                  <p className="run-identity">{reusedImage.artifact_id}</p>
                </details>
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
            <p>
              {imagePath
                ? "已指定服务器路径；启动时核实文件。"
                : "请展开高级图片来源填写路径。"}
            </p>
          ) : (
            <div>
              <label>
                上传图片（最多 20 MiB）
                <input
                  className="blueprint-input-file"
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
                <div>
                  <p>{uploaded.name} · 已上传，尚未执行</p>
                  <details>
                    <summary>高级：输入引用</summary>
                    <p className="run-identity">
                      {String(uploaded.ref.artifact_id)}
                    </p>
                  </details>
                </div>
              )}
              {uploadMessage && <p role="status">{uploadMessage}</p>}
            </div>
          )}
        </>
      )}
    </>
  );
  const name = Object.keys(effectivePipeline.inputs)[0];
  return !multiInput && inputHosts?.[name]
    ? createPortal(content, inputHosts[name], name)
    : content;
}
