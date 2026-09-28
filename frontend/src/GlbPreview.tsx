import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

export function validateGlb(data: ArrayBuffer) {
  const bytes = new DataView(data);
  if (
    data.byteLength < 20 ||
    data.byteLength > 128 * 1024 * 1024 ||
    bytes.getUint32(0, true) !== 0x46546c67 ||
    bytes.getUint32(4, true) !== 2 ||
    bytes.getUint32(8, true) !== data.byteLength
  )
    throw Error("无效或过大的 GLB（最多 128 MiB）");
  const length = bytes.getUint32(12, true);
  if (bytes.getUint32(16, true) !== 0x4e4f534a || length > data.byteLength - 20)
    throw Error("GLB 缺少有效 JSON chunk");
  const document = JSON.parse(
    new TextDecoder().decode(new Uint8Array(data, 20, length)),
  );
  // Only self-contained BIN/bufferView resources. This includes rejecting data URIs.
  for (const item of [
    ...(document.buffers || []),
    ...(document.images || []),
  ]) {
    if ("uri" in item)
      throw Error("预览仅支持 GLB 内嵌资源，不允许外部或 data URI");
  }
  return document;
}

function dispose(root: THREE.Object3D) {
  root.traverse((object) => {
    const mesh = object as THREE.Mesh;
    mesh.geometry?.dispose();
    for (const material of mesh.material
      ? Array.isArray(mesh.material)
        ? mesh.material
        : [mesh.material]
      : []) {
      for (const value of Object.values(material))
        if (value instanceof THREE.Texture) {
          value.dispose();
          if (
            typeof ImageBitmap !== "undefined" &&
            value.image instanceof ImageBitmap
          )
            value.image.close();
        }
      material.dispose();
    }
  });
}

export function GlbPreview({
  runId,
  nodeId,
  url,
  onClose,
  embedded = false,
}: {
  runId: string;
  nodeId: string;
  url: string;
  onClose: () => void;
  embedded?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const reset = useRef<() => void>(() => {});
  const [status, setStatus] = useState("正在加载模型…");
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let renderer: THREE.WebGLRenderer | undefined;
    let controls: OrbitControls | undefined;
    let observer: ResizeObserver | undefined;
    let scene: THREE.Scene | undefined;
    let frame: number | undefined;
    let removeRenderListener: (() => void) | undefined;
    setStatus("正在加载模型…");
    const load = async () => {
      const endpoint = new URL(url, window.location.href);
      if (endpoint.origin !== window.location.origin)
        throw Error("模型地址必须来自当前服务");
      const response = await fetch(endpoint, { signal: controller.signal });
      if (!response.ok) throw Error(`输出读取失败：HTTP ${response.status}`);
      if (Number(response.headers.get("content-length")) > 128 * 1024 * 1024)
        throw Error("模型超过 128 MiB 预览限制");
      const data = await response.arrayBuffer();
      if (!active) return;
      validateGlb(data);
      const manager = new THREE.LoadingManager();
      manager.setURLModifier((resource) => {
        if (!resource.startsWith("blob:")) throw Error("模型包含外部资源");
        return resource;
      });
      let resourceError = false;
      manager.onError = () => {
        resourceError = true;
      };
      const gltf = await new GLTFLoader(manager).parseAsync(data, "");
      if (!active) {
        dispose(gltf.scene);
        return;
      }
      scene = new THREE.Scene();
      scene.background = new THREE.Color(0x252932);
      scene.add(gltf.scene);
      if (resourceError) throw Error("模型纹理加载失败");
      const box = new THREE.Box3().setFromObject(gltf.scene);
      if (
        box.isEmpty() ||
        !Number.isFinite(box.min.length() + box.max.length())
      )
        throw Error("模型没有有效边界");
      renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      host.current!.appendChild(renderer.domElement);
      const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 10000);
      controls = new OrbitControls(camera, renderer.domElement);
      // OrbitControls has no damping or auto-rotation here. A static preview
      // needs a new frame only when its view changes, not an endless GPU loop.
      const requestRender = () => {
        if (!active || !renderer || frame !== undefined) return;
        frame = requestAnimationFrame(() => {
          frame = undefined;
          if (active && renderer && scene) renderer.render(scene, camera);
        });
      };
      controls.addEventListener("change", requestRender);
      const renderControls = controls;
      removeRenderListener = () =>
        renderControls.removeEventListener("change", requestRender);
      const center = box.getCenter(new THREE.Vector3());
      const radius = Math.max(
        box.getSize(new THREE.Vector3()).length() / 2,
        0.001,
      );
      reset.current = () => {
        const distance =
          (radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2))) *
          Math.max(1, 1 / camera.aspect);
        camera.position
          .copy(center)
          .add(
            new THREE.Vector3(1, 0.7, 1)
              .normalize()
              .multiplyScalar(distance * 1.2),
          );
        camera.near = radius / 1000;
        camera.far = radius * 1000;
        camera.updateProjectionMatrix();
        controls!.target.copy(center);
        controls!.update();
        requestRender();
      };
      scene.add(new THREE.HemisphereLight(0xffffff, 0x888888, 2));
      const light = new THREE.DirectionalLight(0xffffff, 3);
      light.position.set(1, 2, 3);
      scene.add(light);
      const axes = new THREE.AxesHelper(radius);
      axes.position.copy(center);
      scene.add(axes);
      const resize = () => {
        if (!host.current || !renderer) return;
        const { width, height } = host.current.getBoundingClientRect();
        renderer.setSize(width, height);
        camera.aspect = width / Math.max(height, 1);
        camera.updateProjectionMatrix();
        requestRender();
      };
      observer = new ResizeObserver(resize);
      observer.observe(host.current!);
      resize();
      reset.current();
      setStatus("模型已加载 · 拖动旋转，滚轮缩放，右键平移");
    };
    void load().catch((error) => {
      if (active) {
        if (frame !== undefined) cancelAnimationFrame(frame);
        frame = undefined;
        removeRenderListener?.();
        observer?.disconnect();
        controls?.dispose();
        if (scene) {
          dispose(scene);
          scene = undefined;
        }
        renderer?.dispose();
        renderer?.forceContextLoss();
        renderer?.domElement.remove();
        renderer = undefined;
        reset.current = () => {};
        setStatus(`预览失败：${String(error)}`);
      }
    });
    return () => {
      active = false;
      controller.abort();
      if (frame !== undefined) cancelAnimationFrame(frame);
      frame = undefined;
      removeRenderListener?.();
      observer?.disconnect();
      controls?.dispose();
      if (scene) dispose(scene);
      renderer?.dispose();
      renderer?.forceContextLoss();
      renderer?.domElement.remove();
      reset.current = () => {};
    };
  }, [url, runId, nodeId]);
  return (
    <div
      className={embedded ? "comparison-mesh-preview" : "run-graph-overlay"}
      role={embedded ? "region" : "dialog"}
      aria-modal={embedded ? undefined : true}
      aria-label="模型预览"
    >
      <header>
        <strong>模型预览 · {nodeId}</strong>
        <button onClick={onClose}>关闭模型预览</button>
      </header>
      <p>{runId}</p>
      <p role="status">{status}</p>
      <button onClick={() => reset.current()}>重置视角</button>
      <div
        ref={host}
        style={{
          flex: 1,
          minHeight: 300,
          height: embedded ? 360 : undefined,
          width: "100%",
        }}
      />
      <a href={url} download>
        下载原始 GLB
      </a>
    </div>
  );
}
