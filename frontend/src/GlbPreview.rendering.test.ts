import { afterEach, beforeEach, expect, it, vi } from "vitest";
const s = vi.hoisted(() => ({
  effect: undefined as (() => () => void) | undefined,
  refs: [] as Array<{ current: any }>,
  changes: new Set<() => void>(),
  render: vi.fn(),
  dispose: vi.fn(),
  controlsDispose: vi.fn(),
  disconnect: vi.fn(),
  resize: undefined as (() => void) | undefined,
  parse: vi.fn(),
}));
vi.mock("react", () => ({
  useEffect: (effect: () => () => void) => {
    s.effect = effect;
  },
  useRef: (current: unknown) => {
    const ref = { current };
    s.refs.push(ref);
    return ref;
  },
  useState: () => ["", vi.fn()],
}));
vi.mock("three", async (original) => ({
  ...(await original<typeof import("three")>()),
  WebGLRenderer: class {
    domElement = { remove: vi.fn() };
    setPixelRatio = vi.fn();
    setSize = vi.fn();
    render = s.render;
    dispose = s.dispose;
    forceContextLoss = vi.fn();
  },
}));
vi.mock("three/addons/controls/OrbitControls.js", () => ({
  OrbitControls: class {
    target = { copy: vi.fn() };
    update() {
      s.changes.forEach((fn) => fn());
    }
    addEventListener(_: string, fn: () => void) {
      s.changes.add(fn);
    }
    removeEventListener(_: string, fn: () => void) {
      s.changes.delete(fn);
    }
    dispose = s.controlsDispose;
  },
}));
vi.mock("three/addons/loaders/GLTFLoader.js", () => ({
  GLTFLoader: class {
    parseAsync = s.parse;
  },
}));
import * as THREE from "three";
import { GlbPreview } from "./GlbPreview";
let frames: Map<number, FrameRequestCallback>;
let cleanup: (() => void) | undefined;
function flush() {
  const pending = [...frames.values()];
  frames.clear();
  pending.forEach((fn) => fn(0));
}
async function mount() {
  GlbPreview({ runId: "run", nodeId: "mesh", url: "/mesh.glb", onClose() {} });
  s.refs[0].current = {
    appendChild: vi.fn(),
    getBoundingClientRect: () => ({ width: 640, height: 480 }),
  };
  cleanup = s.effect!();
  await vi.waitFor(() => expect(frames.size).toBe(1));
}
beforeEach(() => {
  vi.clearAllMocks();
  s.refs = [];
  s.changes.clear();
  frames = new Map();
  let id = 0;
  vi.stubGlobal("window", {
    location: { href: "http://localhost/", origin: "http://localhost" },
    devicePixelRatio: 1,
  });
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => {
    const key = id++;
    frames.set(key, fn);
    return key;
  });
  vi.stubGlobal("cancelAnimationFrame", (key: number) => frames.delete(key));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(fn: () => void) {
        s.resize = fn;
      }
      observe() {}
      disconnect = s.disconnect;
    },
  );
  const bytes = new ArrayBuffer(24),
    view = new DataView(bytes);
  [0x46546c67, 2, 24, 4, 0x4e4f534a].forEach((v, i) =>
    view.setUint32(i * 4, v, true),
  );
  new Uint8Array(bytes, 20).set(new TextEncoder().encode("{}  "));
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue({
        ok: true,
        headers: new Headers(),
        arrayBuffer: async () => bytes,
      }),
  );
  s.parse.mockResolvedValue({
    scene: new THREE.Mesh(
      new THREE.BoxGeometry(),
      new THREE.MeshBasicMaterial(),
    ),
  });
});
afterEach(() => {
  cleanup?.();
  cleanup = undefined;
  vi.unstubAllGlobals();
});
it("renders once initially and coalesces control, resize and reset updates without an idle loop", async () => {
  await mount();
  flush();
  expect(s.render).toHaveBeenCalledTimes(1);
  expect(frames.size).toBe(0);
  s.changes.forEach((fn) => fn());
  s.changes.forEach((fn) => fn());
  expect(frames.size).toBe(1);
  flush();
  expect(s.render).toHaveBeenCalledTimes(2);
  s.resize!();
  flush();
  expect(s.render).toHaveBeenCalledTimes(3);
  s.refs[1].current();
  flush();
  expect(s.render).toHaveBeenCalledTimes(4);
  expect(frames.size).toBe(0);
});
it("closing cancels even frame zero, removes listeners and prevents late redraws", async () => {
  await mount();
  const late = [...frames.values()][0];
  cleanup!();
  cleanup = undefined;
  expect(frames.size).toBe(0);
  expect(s.changes.size).toBe(0);
  expect(s.disconnect).toHaveBeenCalledOnce();
  expect(s.controlsDispose).toHaveBeenCalledOnce();
  expect(s.dispose).toHaveBeenCalledOnce();
  late(0);
  s.resize!();
  s.refs[1].current();
  flush();
  expect(s.render).not.toHaveBeenCalled();
  expect(frames.size).toBe(0);
});
