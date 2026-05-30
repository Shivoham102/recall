'use client'

import { useEffect, useRef, useState } from 'react'

// Self-contained copy of the app's voice-orb shader (idle state) so the
// marketing hero shows the same living orb. App build (Vite) and this site
// (Next) are separate bundles with no shared package, so the GLSL is
// intentionally duplicated — keep it in sync with app/src/components/Orb/orbShader.ts.

const HALO = 1.8

const VERT = `
attribute vec2 aPos;
void main(){ gl_Position = vec4(aPos, 0.0, 1.0); }
`

const FRAG = `
precision highp float;
uniform vec2 uRes;
uniform float uTime;
uniform float uFlow;
uniform vec3 uColorA;
uniform vec3 uColorB;

float hash(vec3 p){
  p = fract(p * 0.3183099 + 0.1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float noise(vec3 x){
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(hash(i + vec3(0,0,0)), hash(i + vec3(1,0,0)), f.x),
        mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
    mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x),
        mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y),
    f.z);
}
float fbm(vec3 p){
  float v = 0.0, a = 0.5;
  for(int i = 0; i < 5; i++){ v += a * noise(p); p *= 2.0; a *= 0.5; }
  return v;
}

void main(){
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / (0.5 * min(uRes.x, uRes.y));
  float r = length(p) * HALO_SCALE;
  vec3 col = vec3(0.0);
  float alpha = 0.0;
  float t = uTime * uFlow;

  if(r < 1.0){
    float z = sqrt(max(0.0, 1.0 - r * r));
    vec3 n = vec3(p * HALO_SCALE, z);
    vec3 sp = n * 1.6;
    vec3 q = vec3(
      fbm(sp + vec3(0.0, 0.0, t)),
      fbm(sp + vec3(3.2, 1.7, t)),
      fbm(sp + vec3(1.1, 4.3, t)));
    float pattern = fbm(sp + q * 1.4 + vec3(0.0, 0.0, t * 0.6));
    pattern = clamp(pattern, 0.0, 1.0);

    vec3 L = normalize(vec3(-0.4, 0.6, 0.7));
    float diff = clamp(dot(n, L) * 0.5 + 0.5, 0.0, 1.0);
    vec3 base = mix(uColorA, uColorB, pattern);
    base *= 0.5 + 0.7 * diff;
    float fres = pow(1.0 - z, 2.5);
    base += uColorB * fres * 0.9;
    float spec = pow(clamp(dot(n, L), 0.0, 1.0), 24.0);
    base += vec3(spec) * 0.55;
    col = base;
    alpha = smoothstep(1.0, 0.95, r);
  } else {
    float h = smoothstep(1.6, 1.0, r);
    col = uColorB * h * 0.5 * 0.6;
    alpha = h * 0.5 * 0.5;
  }

  gl_FragColor = vec4(col, alpha);
}
`.replace(/HALO_SCALE/g, HALO.toFixed(1))

const COLOR_A: [number, number, number] = [0.02, 0.05, 0.18]
const COLOR_B: [number, number, number] = [0.05, 0.85, 1.0]
const FLOW = 0.16

export default function HeroOrb({ size = 200 }: { size?: number }) {
  const ref = useRef<HTMLCanvasElement | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return

    const gl = (canvas.getContext('webgl', {
      alpha: true,
      premultipliedAlpha: false,
      antialias: true,
    }) || canvas.getContext('experimental-webgl')) as WebGLRenderingContext | null
    if (!gl) {
      setFailed(true)
      return
    }

    const compile = (type: number, src: string) => {
      const sh = gl.createShader(type)!
      gl.shaderSource(sh, src)
      gl.compileShader(sh)
      return sh
    }
    const prog = gl.createProgram()!
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT))
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG))
    gl.linkProgram(prog)
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      setFailed(true)
      return
    }
    gl.useProgram(prog)

    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    const aPos = gl.getAttribLocation(prog, 'aPos')
    gl.enableVertexAttribArray(aPos)
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0)

    const uRes = gl.getUniformLocation(prog, 'uRes')
    const uTime = gl.getUniformLocation(prog, 'uTime')
    const uFlow = gl.getUniformLocation(prog, 'uFlow')
    const uColorA = gl.getUniformLocation(prog, 'uColorA')
    const uColorB = gl.getUniformLocation(prog, 'uColorB')

    gl.enable(gl.BLEND)
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA)

    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const px = Math.round(size * HALO)
    canvas.width = px * dpr
    canvas.height = px * dpr
    gl.viewport(0, 0, canvas.width, canvas.height)

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    const draw = (time: number) => {
      gl.uniform2f(uRes, canvas.width, canvas.height)
      gl.uniform1f(uTime, time)
      gl.uniform1f(uFlow, FLOW)
      gl.uniform3f(uColorA, COLOR_A[0], COLOR_A[1], COLOR_A[2])
      gl.uniform3f(uColorB, COLOR_B[0], COLOR_B[1], COLOR_B[2])
      gl.clearColor(0, 0, 0, 0)
      gl.clear(gl.COLOR_BUFFER_BIT)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
    }

    if (reduced) {
      draw(0)
      return () => {
        gl.deleteProgram(prog)
        gl.deleteBuffer(buf)
      }
    }

    let raf = 0
    const start = performance.now()
    const frame = (now: number) => {
      draw((now - start) / 1000)
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    return () => {
      if (raf) cancelAnimationFrame(raf)
      gl.deleteProgram(prog)
      gl.deleteBuffer(buf)
    }
  }, [size])

  if (failed) {
    // No-WebGL fallback: the CSS orb (styles in globals.css).
    return (
      <div className="orb-wrap">
        <div className="orb-glow" />
        <div className="orb" />
      </div>
    )
  }

  const px = Math.round(size * HALO)
  return (
    <div className="orb-wrap">
      <canvas ref={ref} style={{ width: px, height: px, display: 'block' }} />
    </div>
  )
}
