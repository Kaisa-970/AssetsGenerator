import {
  HistoricalInputPreview,
  type InputOrigin,
} from "./HistoricalInputPreview";
import { useEffect, useState } from "react";
import { LocalInputPreview } from "./LocalInputPreview";

/** Preview the current draft binding, never a same-named input from a historical run. */
export function CurrentInputPreview({
  name,
  kind,
  artifactId,
  file,
  path,
  origin,
}: {
  name: string;
  kind?: string;
  artifactId?: string;
  file?: File;
  path?: string;
  origin?: InputOrigin;
}) {
  const [text, setText] = useState<{
    id: string;
    value?: string;
    error?: string;
  }>();
  useEffect(() => {
    if (kind !== "text" || !artifactId) return;
    const controller = new AbortController();
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
        if (!controller.signal.aborted)
          setText({ id: artifactId, value: value.text });
      } catch {
        if (!controller.signal.aborted)
          setText({
            id: artifactId,
            error: "无法读取当前绑定，请重新提供输入。",
          });
      }
    })();
    return () => controller.abort();
  }, [kind, artifactId]);
  return (
    <section aria-label="当前输入预览">
      <strong>当前草稿输入 · {name}</strong>
      <p>下次执行使用此绑定；切换历史运行不会改变当前输入。</p>
      {artifactId ? (
        <>
          {file ? (
            <LocalInputPreview file={file} label={`当前输入 ${name}`} />
          ) : origin?.artifactId === artifactId &&
            ["rgb_image", "rgba_image", "binary_mask"].includes(kind || "") ? (
            <HistoricalInputPreview origin={origin} artifactId={artifactId} />
          ) : kind === "text" ? (
            text?.id !== artifactId ? (
              <p>正在读取已绑定文本…</p>
            ) : text.error ? (
              <p role="alert">{text.error}</p>
            ) : (
              <pre>{text.value}</pre>
            )
          ) : (
            <p>已绑定历史或已有数据；此处没有本地缩略图。启动时核验内容。</p>
          )}
          <details>
            <summary>绑定身份</summary>
            <code>{artifactId}</code>
          </details>
        </>
      ) : path ? (
        <p>待导入服务器路径：{path}。启动准备时读取，尚无可预览绑定。</p>
      ) : (
        <p>尚未绑定。请在输入节点上传文件或应用文本。</p>
      )}
    </section>
  );
}
