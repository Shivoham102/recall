import { useEffect, useRef, useState } from "react";
import * as audioLevel from "../../services/audioLevel";
import { HALO, VERT, FRAG, PRESETS, mapState, type OrbState, type Vis } from "./orbShader";

export type { OrbState } from "./orbShader";

interface Props {
  state: OrbState;
  /** Core sphere diameter in px. The halo extends past it. */
  size?: number;
  onClick?: () => void;
  className?: string;
}

function lerp(a: number, b: number, k: number): number {
  return a + (b - a) * k;
}

// Amplitude per visual state: real audio tap when safe, else synthesized so
// speaking/error are never flat (idle/thinking move via flow, not level).
function sampleLevel(vis: Vis, time: number): number {
  if (vis === "listening") {
    if (audioLevel.isLive()) return audioLevel.getLevel(); // real mic in the app
    return Math.max(0, 0.28 + 0.14 * Math.sin(time * 3.0) + 0.07 * Math.sin(time * 5.3 + 0.7));
  }
  if (vis === "speaking") {
    if (audioLevel.isLive()) return audioLevel.getLevel(); // real TTS in the app
    return Math.max(0, 0.42 + 0.2 * Math.sin(time * 4.5) + 0.1 * Math.sin(time * 7.5 + 1.3));
  }
  if (vis === "error") return 0.45 + 0.3 * Math.sin(time * 9.0);
  return 0;
}

export function Orb({ state, size = 88, onClick, className }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [glFailed, setGlFailed] = useState(false);
  const [nonce, setNonce] = useState(0); // bumped on context-restore to rebuild GL
  const stateRef = useRef<OrbState>(state);
  stateRef.current = state;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const gl = (canvas.getContext("webgl", {
      alpha: true,
      premultipliedAlpha: false,
      antialias: true,
    }) || canvas.getContext("experimental-webgl")) as WebGLRenderingContext | null;

    if (!gl) {
      setGlFailed(true);
      return;
    }

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
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      setGlFailed(true); // shader didn't link (driver quirk) — drop to CSS fallback
      return;
    }
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const U = {
      res: gl.getUniformLocation(prog, "uRes"),
      time: gl.getUniformLocation(prog, "uTime"),
      flow: gl.getUniformLocation(prog, "uFlow"),
      level: gl.getUniformLocation(prog, "uLevel"),
      tremor: gl.getUniformLocation(prog, "uTremor"),
      colorA: gl.getUniformLocation(prog, "uColorA"),
      colorB: gl.getUniformLocation(prog, "uColorB"),
    };

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const px = Math.round(size * HALO);
    canvas.width = px * dpr;
    canvas.height = px * dpr;
    gl.viewport(0, 0, canvas.width, canvas.height);

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Animated state, lerped toward the active preset each frame.
    const init = PRESETS[mapState(stateRef.current)];
    const cur = {
      a: [...init.a] as number[],
      b: [...init.b] as number[],
      flow: init.flow,
      tremor: init.tremor,
      level: 0,
    };

    let raf = 0;
    let last = performance.now();
    let clock = 0;

    const draw = (level: number, time: number) => {
      gl.uniform2f(U.res, canvas.width, canvas.height);
      gl.uniform1f(U.time, time);
      gl.uniform1f(U.flow, cur.flow);
      gl.uniform1f(U.level, level);
      gl.uniform1f(U.tremor, cur.tremor);
      gl.uniform3f(U.colorA, cur.a[0], cur.a[1], cur.a[2]);
      gl.uniform3f(U.colorB, cur.b[0], cur.b[1], cur.b[2]);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };

    if (reduced) {
      draw(0, 0); // static frame — also the "logo" frame
      return () => {
        gl.deleteProgram(prog);
        gl.deleteBuffer(buf);
      };
    }

    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      clock += dt;

      const vis = mapState(stateRef.current);
      const tgt = PRESETS[vis];
      const k = 1 - Math.exp(-dt * 4); // ~0.4s tween
      for (let i = 0; i < 3; i++) {
        cur.a[i] = lerp(cur.a[i], tgt.a[i], k);
        cur.b[i] = lerp(cur.b[i], tgt.b[i], k);
      }
      cur.flow = lerp(cur.flow, tgt.flow, k);
      cur.tremor = lerp(cur.tremor, tgt.tremor, k);
      const target = sampleLevel(vis, clock);
      cur.level = lerp(cur.level, target, 1 - Math.exp(-dt * 7));

      draw(cur.level, clock);
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    const onVisibility = () => {
      if (document.hidden) {
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
      } else if (!raf) {
        last = performance.now();
        raf = requestAnimationFrame(frame);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    const onLost = (e: Event) => {
      e.preventDefault(); // allow restore
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    };
    const onRestored = () => setNonce((n) => n + 1); // rebuild GL resources
    canvas.addEventListener("webglcontextlost", onLost as EventListener);
    canvas.addEventListener("webglcontextrestored", onRestored);

    return () => {
      if (raf) cancelAnimationFrame(raf);
      document.removeEventListener("visibilitychange", onVisibility);
      canvas.removeEventListener("webglcontextlost", onLost as EventListener);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      gl.deleteProgram(prog);
      gl.deleteBuffer(buf);
    };
  }, [size, glFailed, nonce]);

  const px = Math.round(size * HALO);

  if (glFailed) {
    // No-GL fallback: CSS orb (styles in App.css). Core sits centered in the halo box.
    const vis = mapState(state);
    return (
      <div
        className={`orb-fallback orb-fallback--${vis} ${className ?? ""}`}
        style={{
          width: px,
          height: px,
          pointerEvents: onClick ? "auto" : "none",
          cursor: onClick ? "pointer" : "default",
        }}
        onClick={onClick}
      >
        <span className="orb-fallback__core" style={{ width: size, height: size }} />
      </div>
    );
  }

  return (
    <canvas
      ref={canvasRef}
      className={className}
      onClick={onClick}
      style={{
        width: px,
        height: px,
        display: "block",
        pointerEvents: onClick ? "auto" : "none",
        cursor: onClick ? "pointer" : "default",
      }}
    />
  );
}

export default Orb;
