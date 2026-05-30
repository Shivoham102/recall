// Dev-only visual harness for the voice orb. Open /orb-preview.html under the
// Vite dev server to eyeball every state side by side. Not part of the build.
//
// The "Download icon PNG" button renders the EXACT idle shader frame to a
// 1024×1024 transparent PNG (2x supersampled) — this is the master used for
// the app icons, so the icon matches the live orb pixel-for-pixel.
import { createRoot } from "react-dom/client";
import { Orb, type OrbState } from "./components/Orb/OrbCanvas";
import { VERT, FRAG, PRESETS } from "./components/Orb/orbShader";

const STATES: { state: OrbState; label: string }[] = [
  { state: "idle", label: "idle" },
  { state: "recording", label: "listening" },
  { state: "processing", label: "thinking" },
  { state: "speaking", label: "speaking" },
  { state: "error", label: "error" },
];

function renderIdleIcon(out = 1024, ss = 2): string {
  const px = out * ss;
  const gc = document.createElement("canvas");
  gc.width = gc.height = px;
  const gl = gc.getContext("webgl", {
    alpha: true,
    premultipliedAlpha: false,
    antialias: true,
    preserveDrawingBuffer: true, // required so toDataURL/drawImage sees the frame
  }) as WebGLRenderingContext | null;
  if (!gl) return "";

  const compile = (type: number, src: string) => {
    const sh = gl.createShader(type)!;
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    return sh;
  };
  const prog = gl.createProgram()!;
  gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
  gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
  gl.linkProgram(prog);
  gl.useProgram(prog);

  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const aPos = gl.getAttribLocation(prog, "aPos");
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

  const P = PRESETS.idle;
  gl.uniform2f(gl.getUniformLocation(prog, "uRes"), px, px);
  gl.uniform1f(gl.getUniformLocation(prog, "uTime"), 7.0);
  gl.uniform1f(gl.getUniformLocation(prog, "uFlow"), P.flow);
  gl.uniform1f(gl.getUniformLocation(prog, "uLevel"), 0);
  gl.uniform1f(gl.getUniformLocation(prog, "uTremor"), 0);
  gl.uniform3f(gl.getUniformLocation(prog, "uColorA"), P.a[0], P.a[1], P.a[2]);
  gl.uniform3f(gl.getUniformLocation(prog, "uColorB"), P.b[0], P.b[1], P.b[2]);

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.viewport(0, 0, px, px);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.drawArrays(gl.TRIANGLES, 0, 3);

  // downsample to the target size for crisp edges
  const tc = document.createElement("canvas");
  tc.width = tc.height = out;
  const ctx = tc.getContext("2d")!;
  ctx.drawImage(gc, 0, 0, out, out);
  return tc.toDataURL("image/png");
}

function downloadIcon() {
  const url = renderIdleIcon(1024, 2);
  if (!url) {
    alert("WebGL unavailable — cannot capture icon.");
    return;
  }
  const a = document.createElement("a");
  a.href = url;
  a.download = "orb-master.png";
  a.click();
}

function Preview() {
  return (
    <>
      <div style={{ textAlign: "center", marginBottom: 8 }}>
        <button
          onClick={downloadIcon}
          style={{
            background: "#0c1f7a",
            color: "#cfe6ff",
            border: "1px solid #2348e6",
            borderRadius: 8,
            padding: "8px 16px",
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          Download icon PNG (1024, idle)
        </button>
      </div>
      <div className="grid">
        {STATES.map(({ state, label }) => (
          <div className="cell" key={label}>
            <Orb state={state} size={140} />
            <div className="label">{label}</div>
          </div>
        ))}
      </div>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<Preview />);
