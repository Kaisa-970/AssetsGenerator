import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** Read-only output viewer; mount by immutable run/node/port identity. */
export function ImageOutput({
  runId,
  nodeId,
  port,
  url,
  defaultOpen = false,
}: {
  runId: string;
  nodeId: string;
  port: string;
  url: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [enlarged, setEnlarged] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (enlarged) dialog.current?.showModal();
  }, [enlarged]);
  useEffect(() => {
    setEnlarged(false);
  }, [runId, nodeId, port, url]);
  const [readAttempt, setReadAttempt] = useState(0);
  const [status, setStatus] = useState<"loading" | "loaded" | "failed">(
    "loading",
  );
  const [source, setSource] = useState<string>();
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setStatus("loading");
    setSource(undefined);
    void (async () => {
      try {
        const response = await fetch(url, {
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok) throw Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      } catch {
        if (!controller.signal.aborted) setStatus("failed");
      }
    })();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [open, url, readAttempt]);
  return (
    <section
      className="image-output"
      aria-label={`图片输出 · ${nodeId} · ${port}`}
    >
      <button
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
          setEnlarged(false);
          setStatus("loading");
        }}
      >
        {open ? "收起图片" : "预览图片"} · {nodeId} · {port}
      </button>
      {open && (
        <>
          <p className="run-identity">
            {runId} / {nodeId} / {port}
          </p>
          {status === "loading" && <p role="status">正在读取图片…</p>}
          {status === "failed" && (
            <>
              <p role="alert">图片读取失败，请检查输出证据。</p>
              <button onClick={() => setReadAttempt((attempt) => attempt + 1)}>
                重新读取图片 · {nodeId} · {port}
              </button>
            </>
          )}
          {source && status === "loaded" && (
            <button onClick={() => setEnlarged(true)}>
              放大图片 · {nodeId} · {port}
            </button>
          )}
          {enlarged &&
            source &&
            status === "loaded" &&
            createPortal(
              <dialog
                ref={dialog}
                className="image-output-dialog"
                aria-label="放大图片"
                onCancel={() => setEnlarged(false)}
                onClose={() => setEnlarged(false)}
              >
                <header>
                  <strong>图片预览</strong>
                  <button autoFocus onClick={() => setEnlarged(false)}>
                    关闭放大图片
                  </button>
                </header>
                <p className="run-identity">
                  {runId} / {nodeId} / {port}
                </p>
                <img
                  src={source}
                  alt={`放大：节点 ${nodeId} 的 ${port} 输出`}
                />
              </dialog>,
              document.body,
            )}
          {source && (
            <img
              src={source}
              alt={`节点 ${nodeId} 的 ${port} 输出`}
              hidden={status !== "loaded"}
              onLoad={() => setStatus("loaded")}
              onError={() => setStatus("failed")}
            />
          )}
        </>
      )}
    </section>
  );
}
