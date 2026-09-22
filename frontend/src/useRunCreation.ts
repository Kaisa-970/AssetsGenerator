import { useState } from "react";
import type { Pipeline } from "./graph";
export type CreationRequest = {
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

export function useRunCreation() {
  const [creation, setCreation] = useState(restoreCreation);
  const clear = () => {
    sessionStorage.removeItem(creationStorageKey);
    setCreation({});
  };
  const submit = async (submitted: CreationRequest) => {
    sessionStorage.setItem(creationStorageKey, JSON.stringify(submitted));
    setCreation({ request: submitted });
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(submitted),
    });
    const value = await response.json();
    if (!response.ok) {
      if (response.status === 409 && value.code === "preflight_changed")
        clear();
      throw Error(value.error || `HTTP ${response.status}`);
    }
    if (!value.run?.run_id) throw Error("服务未返回有效运行，原请求已保留");
    return value;
  };
  return { creation, submit, clear };
}
