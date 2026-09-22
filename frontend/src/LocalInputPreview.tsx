import { useEffect, useState } from "react";

/** Local display only; the server remains authoritative for imported evidence. */
export function LocalInputPreview({
  file,
  label,
}: {
  file: File;
  label: string;
}) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const next = URL.createObjectURL(file);
    setUrl(next);
    setFailed(false);
    return () => URL.revokeObjectURL(next);
  }, [file]);
  return (
    <figure aria-label={`${label}本地预览`}>
      {url && !failed && (
        <img
          src={url}
          alt={`${label}缩略图`}
          onError={() => setFailed(true)}
          style={{ maxWidth: "100%", maxHeight: 180, objectFit: "contain" }}
        />
      )}
      {failed && <p>浏览器无法预览此图片，请以服务端上传校验结果为准。</p>}
      <figcaption>{file.name} · 本地预览；此时未执行任何模型。</figcaption>
    </figure>
  );
}
