// Shared orb shader source + visual presets.
//
// Framework-agnostic so the React component (OrbCanvas), the standalone
// preview/master-render harness, and the marketing-site copy all use the
// exact same GLSL and palette. Keep this file dependency-free.

// Public state mirrors RecorderState (+ "error") so callers don't churn.
export type OrbState = "idle" | "recording" | "processing" | "speaking" | "error";

// Internal visual state (one hue + motion; red is errors only).
export type Vis = "idle" | "listening" | "thinking" | "speaking" | "error";

export function mapState(s: OrbState): Vis {
  if (s === "recording") return "listening";
  if (s === "processing") return "thinking";
  return s as Vis; // idle | speaking | error
}

// Canvas is larger than the core so the soft halo has room to bleed.
export const HALO = 1.8;

export interface Preset {
  a: [number, number, number]; // deep base color
  b: [number, number, number]; // bright hue color
  flow: number; // internal churn speed
  tremor: number; // jitter (error only)
}

export const PRESETS: Record<Vis, Preset> = {
  idle: { a: [0.02, 0.05, 0.18], b: [0.05, 0.85, 1.0], flow: 0.16, tremor: 0 },
  listening: { a: [0.02, 0.06, 0.2], b: [0.25, 1.0, 1.0], flow: 0.34, tremor: 0 },
  thinking: { a: [0.05, 0.03, 0.2], b: [0.55, 0.25, 1.0], flow: 0.52, tremor: 0 },
  speaking: { a: [0.02, 0.07, 0.18], b: [0.12, 1.0, 0.82], flow: 0.3, tremor: 0 },
  error: { a: [0.2, 0.02, 0.05], b: [1.0, 0.28, 0.36], flow: 0.42, tremor: 1 },
};

export const VERT = `
attribute vec2 aPos;
void main(){ gl_Position = vec4(aPos, 0.0, 1.0); }
`;

export const FRAG = `
precision highp float;
uniform vec2 uRes;
uniform float uTime;
uniform float uFlow;
uniform float uLevel;
uniform float uTremor;
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
  if(uTremor > 0.0){
    p += uTremor * 0.05 * vec2(
      noise(vec3(p * 8.0, uTime * 40.0)) - 0.5,
      noise(vec3(p * 8.0 + 5.0, uTime * 40.0)) - 0.5);
  }
  float r = length(p) * HALO_SCALE;
  vec3 col = vec3(0.0);
  float alpha = 0.0;
  float t = uTime * uFlow;

  if(r < 1.0){
    float z = sqrt(max(0.0, 1.0 - r * r));
    vec3 n = vec3(p * HALO_SCALE, z);
    vec3 sp = n * 1.6;
    // Constant-speed plasma churn. Audio level changes INTENSITY, not speed —
    // multiplying time by a time-varying level makes motion accelerate forever.
    float churn = t;
    vec3 q = vec3(
      fbm(sp + vec3(0.0, 0.0, churn)),
      fbm(sp + vec3(3.2, 1.7, churn)),
      fbm(sp + vec3(1.1, 4.3, churn)));
    float pattern = fbm(sp + q * (1.4 + uLevel * 1.3) + vec3(0.0, 0.0, churn * 0.6));
    // amplitude layer: finer turbulence (constant speed), blended in by level
    float voice = fbm(n * 3.4 + vec3(q.x, q.y, churn * 3.0));
    pattern = mix(pattern, voice, clamp(uLevel * 0.6, 0.0, 0.6));
    pattern = clamp(pattern, 0.0, 1.0);

    vec3 L = normalize(vec3(-0.4, 0.6, 0.7));
    float diff = clamp(dot(n, L) * 0.5 + 0.5, 0.0, 1.0);
    vec3 base = mix(uColorA, uColorB, pattern);
    base *= 0.5 + 0.7 * diff;
    base += uColorB * uLevel * 0.35;
    float fres = pow(1.0 - z, 2.5);
    base += uColorB * fres * (0.9 + uLevel * 0.5);
    float spec = pow(clamp(dot(n, L), 0.0, 1.0), 24.0);
    base += vec3(spec) * 0.55;
    col = base;
    alpha = smoothstep(1.0, 0.95, r);
  } else {
    float h = smoothstep(1.6, 1.0, r);
    col = uColorB * h * 0.5 * (0.6 + uLevel * 0.8);
    alpha = h * 0.5 * (0.5 + uLevel * 0.5);
  }

  gl_FragColor = vec4(col, alpha);
}
`.replace(/HALO_SCALE/g, HALO.toFixed(1));
