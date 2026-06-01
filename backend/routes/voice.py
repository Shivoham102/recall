from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_AUTH_CALLBACK_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Recall — Signed in</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0D0E14; color: #E8EAF2;
      font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
      display: flex; align-items: center; justify-content: center;
      height: 100vh; -webkit-font-smoothing: antialiased;
    }
    .wrap { display: flex; flex-direction: column; align-items: center; gap: 28px; text-align: center; }
    /* CSS fallback (shown only when WebGL unavailable) */
    .orb-css { display: none; border-radius: 50%; animation: breathe 4.2s ease-in-out infinite; }
    .orb-css .core {
      border-radius: 50%;
      background:
        radial-gradient(circle at 34% 30%, rgba(255,255,255,0.85) 0%, rgba(255,255,255,0.22) 10%, transparent 32%),
        radial-gradient(circle at center, #93C5FD 0%, #3B82F6 28%, #1E40AF 58%, #0C1228 100%);
      box-shadow: 0 0 24px rgba(75,142,247,0.35), inset 0 -6px 14px rgba(0,0,0,0.4);
      animation: breathe 4.2s ease-in-out infinite;
    }
    @keyframes breathe { 0%,100%{transform:scale(0.9)} 50%{transform:scale(1)} }
    h2 { font-size: 18px; font-weight: 600; letter-spacing: -0.02em; color: #E8EAF2; margin-bottom: 6px; }
    p  { font-size: 13px; color: rgba(180,188,230,0.48); }
  </style>
</head>
<body>
  <div class="wrap">
    <div id="orb-wrap">
      <canvas id="orb-canvas"></canvas>
      <div class="orb-css" id="orb-css">
        <div class="core"></div>
      </div>
    </div>
    <div>
      <h2>Signed in to Recall</h2>
      <p>You can close this window.</p>
    </div>
  </div>
  <script>
    // Deep-link handoff — must run before anything blocks
    (function(){
      var hash = window.location.hash.slice(1);
      if (hash) { var a = document.createElement('a'); a.href = 'recall://auth#' + hash; a.click(); }
    })();

    // ── Exact same WebGL orb shader as the desktop app ──────────────
    var HALO = 1.8, SIZE = 88;
    var dpr  = Math.min(window.devicePixelRatio || 1, 2);
    var px   = Math.round(SIZE * HALO);   // canvas CSS size in px

    var canvas = document.getElementById('orb-canvas');
    canvas.width  = px * dpr;
    canvas.height = px * dpr;
    canvas.style.width  = px + 'px';
    canvas.style.height = px + 'px';

    var gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: false, antialias: true })
          || canvas.getContext('experimental-webgl');

    if (!gl) {
      canvas.style.display = 'none';
      var fb = document.getElementById('orb-css');
      fb.style.display = 'flex';
      fb.style.width = fb.querySelector('.core').style.width = SIZE + 'px';
      fb.style.height = fb.querySelector('.core').style.height = SIZE + 'px';
    } else {
      var VERT = "attribute vec2 aPos;void main(){gl_Position=vec4(aPos,0.0,1.0);}";
      var FRAG = [
        "precision highp float;",
        "uniform vec2 uRes; uniform float uTime,uFlow,uLevel,uTremor;",
        "uniform vec3 uColorA,uColorB;",
        "float hash(vec3 p){p=fract(p*0.3183099+0.1);p*=17.0;return fract(p.x*p.y*p.z*(p.x+p.y+p.z));}",
        "float noise(vec3 x){vec3 i=floor(x),f=fract(x);f=f*f*(3.0-2.0*f);",
        "return mix(mix(mix(hash(i),hash(i+vec3(1,0,0)),f.x),mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),",
        "mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),f.z);}",
        "float fbm(vec3 p){float v=0.0,a=0.5;for(int i=0;i<5;i++){v+=a*noise(p);p*=2.0;a*=0.5;}return v;}",
        "void main(){",
        "  vec2 p=(gl_FragCoord.xy-0.5*uRes)/(0.5*min(uRes.x,uRes.y));",
        "  float r=length(p)*1.8; vec3 col=vec3(0.0); float alpha=0.0,t=uTime*uFlow;",
        "  if(r<1.0){",
        "    float z=sqrt(max(0.0,1.0-r*r)); vec3 n=vec3(p*1.8,z),sp=n*1.6; float churn=t;",
        "    vec3 q=vec3(fbm(sp+vec3(0,0,churn)),fbm(sp+vec3(3.2,1.7,churn)),fbm(sp+vec3(1.1,4.3,churn)));",
        "    float pattern=fbm(sp+q*(1.4+uLevel*1.3)+vec3(0,0,churn*0.6));",
        "    float voice=fbm(n*3.4+vec3(q.x,q.y,churn*3.0));",
        "    pattern=mix(pattern,voice,clamp(uLevel*0.6,0.0,0.6)); pattern=clamp(pattern,0.0,1.0);",
        "    vec3 L=normalize(vec3(-0.4,0.6,0.7)); float diff=clamp(dot(n,L)*0.5+0.5,0.0,1.0);",
        "    vec3 base=mix(uColorA,uColorB,pattern); base*=0.5+0.7*diff;",
        "    base+=uColorB*uLevel*0.35;",
        "    float fres=pow(1.0-z,2.5); base+=uColorB*fres*(0.9+uLevel*0.5);",
        "    float spec=pow(clamp(dot(n,L),0.0,1.0),24.0); base+=vec3(spec)*0.55;",
        "    col=base; alpha=smoothstep(1.0,0.95,r);",
        "  } else {",
        "    float h=smoothstep(1.6,1.0,r); col=uColorB*h*0.5*(0.6+uLevel*0.8); alpha=h*0.5*(0.5+uLevel*0.5);",
        "  }",
        "  gl_FragColor=vec4(col,alpha);",
        "}"
      ].join("\\n");

      function mkShader(type, src) {
        var sh = gl.createShader(type); gl.shaderSource(sh, src); gl.compileShader(sh); return sh;
      }
      var prog = gl.createProgram();
      gl.attachShader(prog, mkShader(gl.VERTEX_SHADER, VERT));
      gl.attachShader(prog, mkShader(gl.FRAGMENT_SHADER, FRAG));
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
        canvas.style.display = 'none';
        document.getElementById('orb-css').style.display = 'flex';
      } else {
        gl.useProgram(prog);
        var buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,3,-1,-1,3]), gl.STATIC_DRAW);
        var aPos = gl.getAttribLocation(prog, 'aPos');
        gl.enableVertexAttribArray(aPos);
        gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.viewport(0, 0, canvas.width, canvas.height);

        var U = {
          res:    gl.getUniformLocation(prog, 'uRes'),
          time:   gl.getUniformLocation(prog, 'uTime'),
          flow:   gl.getUniformLocation(prog, 'uFlow'),
          level:  gl.getUniformLocation(prog, 'uLevel'),
          tremor: gl.getUniformLocation(prog, 'uTremor'),
          colorA: gl.getUniformLocation(prog, 'uColorA'),
          colorB: gl.getUniformLocation(prog, 'uColorB'),
        };

        // Idle preset — same values as orbShader.ts PRESETS.idle
        var cA = [0.02, 0.05, 0.18], cB = [0.05, 0.85, 1.0], flow = 0.16;
        var start = null;
        function frame(now) {
          if (!start) start = now;
          var t = (now - start) / 1000;
          gl.uniform2f(U.res, canvas.width, canvas.height);
          gl.uniform1f(U.time, t);
          gl.uniform1f(U.flow, flow);
          gl.uniform1f(U.level, 0);
          gl.uniform1f(U.tremor, 0);
          gl.uniform3f(U.colorA, cA[0], cA[1], cA[2]);
          gl.uniform3f(U.colorB, cB[0], cB[1], cB[2]);
          gl.clearColor(0, 0, 0, 0);
          gl.clear(gl.COLOR_BUFFER_BIT);
          gl.drawArrays(gl.TRIANGLES, 0, 3);
          requestAnimationFrame(frame);
        }
        requestAnimationFrame(frame);
      }
    }
  </script>
</body>
</html>"""


@router.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback():
    return HTMLResponse(_AUTH_CALLBACK_HTML)
