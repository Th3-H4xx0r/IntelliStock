<!-- <script setup>
import TheNavbar from '../components/TheNavbar.vue'
import TheFooter from '../components/TheFooter.vue'
import HeroSection from '../components/landing/HeroSection.vue'
import StrategySection from '../components/landing/StrategySection.vue'
import NexusSection from '../components/landing/NexusSection.vue'
import BacktestSection from '../components/landing/BacktestSection.vue'
import DeploySection from '../components/landing/DeploySection.vue'
</script>

<template>
  <div class="min-h-screen bg-[#080b0f] text-slate-100">
    <TheNavbar />
    <main class="pt-20">
      <HeroSection />
      <StrategySection />
      <NexusSection />
      <BacktestSection />
      <DeploySection />
    </main>
    <TheFooter />
  </div>
</template> -->

<script setup>
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import TheNavbar from '../components/TheNavbar.vue'
import TheFooter from '../components/TheFooter.vue'
import StrategySection from '../components/landing/StrategySection.vue'
import NexusSection from '../components/landing/NexusSection.vue'
import BacktestSection from '../components/landing/BacktestSection.vue'
import DeploySection from '../components/landing/DeploySection.vue'
import { isPreviewMode, GITHUB_URL } from '../utils/preview.js'

const router = useRouter()
const preview = isPreviewMode()
const canvasRef = ref(null)

let mouseX = 0
let mouseY = 0
let curRotX = 0
let curRotY = 0
let rafId = null

const NEWS_ITEMS = [
  'RSI reaching 72 on NVDA — sell signal',
  'Fed rate decision: +0.25% confirmed',
  'MACD bullish crossover detected on AAPL',
  'S&P 500 breaks resistance at 5,250',
  'VIX spike above 22 — hedge advised',
  'NVDA earnings beat: +14% after hours',
  'BTC oversold on 4H — long opportunity',
  'Death cross forming on SPY daily',
  'Unusual options flow: TSLA $300 calls',
  'Market breadth weakening — reduce risk',
  'Two more rate cuts priced in for 2025',
  'Strong payrolls beat — USD strengthening',
  'GOOGL gap fill — watching for reversal',
  'Sector rotation: tech → energy underway',
  'AMZN testing 200-day moving average',
  'CPI in-line — soft landing holds',
  'META momentum fading — trim position',
  'MSFT breakout above $430 confirmed',
  'Put/call ratio 1.3 — elevated fear',
  'Gold breaking $2,500 resistance',
  'Yield curve steepening — risk-on signal',
  'IWM small-cap surge: +2.4% today',
  'Insider buy: PLTR $5M purchase detected',
  'Bearish engulfing on QQQ weekly chart',
  'Oil draw: XOM building momentum',
  'SPX 5,100 support holding — watching',
  'COIN at Fibonacci 61.8% retracement',
  'AMD volume surge — accumulation phase',
  'DXY below 104 — dollar weakness',
  'Bollinger squeeze on SOX — breakout near',
  'Consumer confidence –4pts — go defensive',
  'RIVN short squeeze risk: 28% short float',
  'XLF technical target $46 reached',
  'CRWD gap-and-go pattern forming',
  'Commodity risk premium rising',
  'SMCI AI momentum: +9% heavy volume',
  'Crypto descending wedge break — bullish',
  'Healthcare outperforming S&P by 3.1%',
  'Block trade: 2.1M shares NFLX detected',
  'Emerging markets outperforming: EEM +1.8%',
]

const TRADE_TICKERS = [
  ['NVDA', 142.50], ['AAPL', 213.80], ['TSLA', 248.30], ['MSFT', 415.20],
  ['GOOGL', 178.40], ['AMZN', 196.60], ['META', 536.90], ['AMD', 162.10],
  ['NFLX', 698.40], ['PLTR', 24.80],  ['COIN', 238.50], ['SPY', 524.30],
  ['QQQ', 448.20],  ['SOFI', 12.40],  ['SMCI', 42.60],  ['ARM',  118.70],
  ['AVGO', 198.30], ['CRWD', 368.50], ['SNOW', 148.20], ['UBER', 74.30],
  ['RIVN', 11.20],  ['IONQ', 32.40],  ['TSM',  165.80], ['BRK.B', 452.10],
]

const activeCards = ref([])
let cardIdCounter = 0
let spawnTimer = null

const tradeCards = ref([])
let tradeCardId = 0
let tradeTimer = null

function killTradeCard(id) {
  const c = tradeCards.value.find((c) => c.id === id)
  if (!c || c.dying) return
  c.dying = true
  c.alive = false
  setTimeout(() => { tradeCards.value = tradeCards.value.filter((c) => c.id !== id) }, 500)
}

// Only use tickers that are mentioned in the news feed (keeps cards thematically linked)
const NEWS_TRADE_POOL = TRADE_TICKERS.filter(([sym]) =>
  NEWS_ITEMS.some((n) => n.includes(sym))
)
const TRADE_POOL = NEWS_TRADE_POOL.length >= 4 ? NEWS_TRADE_POOL : TRADE_TICKERS

function spawnTradeCard() {
  if (tradeCards.value.filter((c) => !c.dying).length >= 4) return
  const side = Math.random() > 0.52 ? 'BUY' : 'SELL'
  const [sym, basePrice] = TRADE_POOL[Math.floor(Math.random() * TRADE_POOL.length)]
  const price = (basePrice * (0.985 + Math.random() * 0.03)).toFixed(2)
  const shares = Math.round((100 + Math.random() * 4900) / 100) * 100
  const id = tradeCardId++
  // Pick angle around the orb, ≥50° from any existing trade or news card
  const usedAngles = [
    ...tradeCards.value.filter((c) => !c.dying).map((c) => c.angle),
    ...activeCards.value.filter((c) => !c.dying).map((c) => c.angle),
  ]
  let angle, tries = 0
  do {
    angle = Math.random() * 360
    tries++
  } while (
    usedAngles.some((a) => { const d = Math.abs(angle - a) % 360; return Math.min(d, 360 - d) < 50 }) &&
    tries < 20
  )
  // x/y start at center; tick() will place them correctly on first frame
  tradeCards.value.push({ id, side, sym, price, shares, angle, x: 50, y: 50, alive: false, dying: false })
  setTimeout(() => { const c = tradeCards.value.find((c) => c.id === id); if (c) c.alive = true }, 40)
  setTimeout(() => killTradeCard(id), 2200 + Math.random() * 2000)
}

function m4mul(a, b) {
  const out = new Float32Array(16)
  for (let i = 0; i < 4; i += 1) {
    for (let j = 0; j < 4; j += 1) {
      for (let k = 0; k < 4; k += 1) {
        out[j + i * 4] += a[j + k * 4] * b[k + i * 4]
      }
    }
  }
  return out
}

const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const cross3 = (a, b) => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
]
const norm3 = (a) => {
  const len = Math.sqrt(dot3(a, a))
  return len > 1e-9 ? [a[0] / len, a[1] / len, a[2] / len] : [0, 0, 1]
}

const m4perspective = (fov, aspect, near, far) => {
  const scale = 1 / Math.tan(fov * 0.5)
  const nf = 1 / (near - far)
  return new Float32Array([
    scale / aspect, 0, 0, 0,
    0, scale, 0, 0,
    0, 0, (far + near) * nf, -1,
    0, 0, 2 * far * near * nf, 0,
  ])
}

function m4lookAt(eye, center, up) {
  const z = norm3(sub3(eye, center))
  const x = norm3(cross3(up, z))
  const y = cross3(z, x)
  return new Float32Array([
    x[0], y[0], z[0], 0,
    x[1], y[1], z[1], 0,
    x[2], y[2], z[2], 0,
    -dot3(x, eye), -dot3(y, eye), -dot3(z, eye), 1,
  ])
}

const rotY = (angle) => {
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  return new Float32Array([
    c, 0, -s, 0,
    0, 1, 0, 0,
    s, 0, c, 0,
    0, 0, 0, 1,
  ])
}

const rotX = (angle) => {
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  return new Float32Array([
    1, 0, 0, 0,
    0, c, s, 0,
    0, -s, c, 0,
    0, 0, 0, 1,
  ])
}

const m4scale = (s) => new Float32Array([
  s, 0, 0, 0,
  0, s, 0, 0,
  0, 0, s, 0,
  0, 0, 0, 1,
])

const rotZ = (angle) => {
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  return new Float32Array([
    c, s, 0, 0,
    -s, c, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
  ])
}

const mat3FromMat4 = (m) => new Float32Array([
  m[0], m[1], m[2],
  m[4], m[5], m[6],
  m[8], m[9], m[10],
])

// High-poly UV sphere — vertices on unit sphere, noise displacement done in vertex shader
function uvSphere(latSegs, lonSegs) {
  const pos = [], nrm = [], idx = []
  for (let lat = 0; lat <= latSegs; lat++) {
    const theta = (lat / latSegs) * Math.PI
    const sinT = Math.sin(theta)
    const cosT = Math.cos(theta)
    for (let lon = 0; lon <= lonSegs; lon++) {
      const phi = (lon / lonSegs) * Math.PI * 2
      const x = sinT * Math.cos(phi)
      const y = cosT
      const z = sinT * Math.sin(phi)
      pos.push(x, y, z)
      nrm.push(x, y, z)
    }
  }
  for (let lat = 0; lat < latSegs; lat++) {
    for (let lon = 0; lon < lonSegs; lon++) {
      const a = lat * (lonSegs + 1) + lon
      const b = a + lonSegs + 1
      idx.push(a, b, a + 1, b, b + 1, a + 1)
    }
  }
  return { p: new Float32Array(pos), n: new Float32Array(nrm), ru: null, wy: null, id: new Uint16Array(idx) }
}

function stars(count) {
  const positions = []
  const brightness = []

  for (let i = 0; i < count; i += 1) {
    const theta = Math.random() * Math.PI * 2
    const phi = Math.acos(2 * Math.random() - 1)
    const radius = 3.6 + Math.random() * 4.2
    positions.push(
      radius * Math.sin(phi) * Math.cos(theta),
      radius * Math.sin(phi) * Math.sin(theta) * 0.6,
      radius * Math.cos(phi),
    )
    brightness.push(0.18 + Math.random() * 0.72)
  }

  return {
    p: new Float32Array(positions),
    br: new Float32Array(brightness),
    count,
  }
}

// Shared 3D Perlin noise GLSL (embedded in both vertex and fragment shaders)
const NOISE_GLSL = `
vec3 _h(vec3 p) {
  p = fract(p * vec3(0.1031, 0.1030, 0.0973));
  p += dot(p, p.yxz + 19.19);
  return fract((p.xxy + p.yxx) * p.zyx) * 2.0 - 1.0;
}
float _n(vec3 p) {
  vec3 i = floor(p), f = fract(p), u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(dot(_h(i),f),           dot(_h(i+vec3(1,0,0)),f-vec3(1,0,0)),u.x),
        mix(dot(_h(i+vec3(0,1,0)),f-vec3(0,1,0)),dot(_h(i+vec3(1,1,0)),f-vec3(1,1,0)),u.x),u.y),
    mix(mix(dot(_h(i+vec3(0,0,1)),f-vec3(0,0,1)),dot(_h(i+vec3(1,0,1)),f-vec3(1,0,1)),u.x),
        mix(dot(_h(i+vec3(0,1,1)),f-vec3(0,1,1)),dot(_h(i+vec3(1,1,1)),f-vec3(1,1,1)),u.x),u.y),u.z);
}
float fbm(vec3 p, float t) {
  p += vec3(t * 0.06, t * 0.04, t * 0.03);
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 5; i++) { v += a * _n(p); p = p * 2.0 + vec3(5.2, 1.3, 2.8); a *= 0.5; }
  return v;
}
`

// Sphere vertex shader — displaces vertices along normals using animated noise
const sphereVertexShader = `
precision highp float;
attribute vec3 a_p;
attribute vec3 a_n;
uniform mat4 u_mvp;
uniform mat4 u_mv;
uniform mat3 u_nm;
uniform float u_t;
uniform float u_r;
varying vec3 v_n;
varying vec3 v_vp;
varying vec3 v_wp;
` + NOISE_GLSL + `
void main() {
  float disp = fbm(a_p * 2.2, u_t) * 0.09;

  /* Audio-wave rim jitter — two frequencies layered for organic wave look */
  vec3 vNorm = normalize(u_nm * a_n);
  float rimW = pow(max(1.0 - abs(vNorm.z), 0.0), 1.4);
  float j1 = _n(a_p * 10.0 + vec3(u_t * 5.5, u_t * 4.2, u_t * 4.8));
  float j2 = _n(a_p * 18.0 + vec3(u_t * 8.0, u_t * 6.5, u_t * 7.2)) * 0.45;
  float jitter = (j1 + j2) * 0.068 * rimW;

  vec3 displaced = a_p * (u_r + disp + jitter);
  v_wp = a_p;
  v_n  = normalize(u_nm * a_n);
  vec4 mv = u_mv * vec4(displaced, 1.0);
  v_vp = mv.xyz;
  gl_Position = u_mvp * vec4(displaced, 1.0);
}
`

// Sphere fragment shader — iso-contour lines + iridescence + Fresnel glass
const sphereFragmentShader = `
precision highp float;
varying vec3 v_n;
varying vec3 v_vp;
varying vec3 v_wp;
uniform float u_t;
uniform vec3 u_l1;
uniform vec3 u_l2;
uniform vec3 u_l3;
uniform vec2 u_mouse;
` + NOISE_GLSL + `
void main() {
  vec3 N = normalize(v_n);
  vec3 V = normalize(-v_vp);
  float ndv = abs(dot(N, V));

  /* Glass Fresnel — bright at grazing edges, transparent face-on */
  float fresnel = pow(1.0 - ndv, 2.5);

  /* Two noise layers combined for complex flowing iso-contour lines */
  float nv  = fbm(v_wp * 1.1, u_t) * 0.5 + 0.5;
  float nv2 = fbm(v_wp * 0.65 + vec3(3.7, 2.1, 4.5), u_t * 0.7) * 0.5 + 0.5;
  float field = nv * 0.70 + nv2 * 0.30;

  /* Iso-contour lines — 38 topographic levels, wide and clearly visible */
  float c      = fract(field * 38.0);
  float dist   = min(c, 1.0 - c);
  float lw     = 0.082;
  float onLine = 1.0 - smoothstep(0.0, lw, dist);

  /* Thin-film iridescence — shifts with view angle + noise + time */
  float iriShift = ndv * 3.5 + nv * 2.2 + u_t * 0.038;
  float ir = 0.60 + 0.40 * sin(iriShift * 6.28);
  float ig = 0.35 + 0.50 * sin(iriShift * 6.28 + 2.09);
  float ib = 0.75 + 0.25 * sin(iriShift * 6.28 + 4.19);

  /* Line color — pink/cyan/lavender iridescent palette */
  vec3 lineCol = mix(vec3(1.0, 0.42, 0.78), vec3(0.42, 0.70, 1.0), ib);
  lineCol = mix(lineCol, vec3(0.88, 0.58, 1.0), ir * 0.38);
  lineCol *= 1.5 + fresnel * 0.4;  /* brighten lines at rim */

  /* Rim glow — colored Fresnel edge */
  vec3 rimCol = mix(vec3(0.72, 0.28, 1.0), vec3(0.35, 0.62, 1.0), 0.5 + 0.5 * sin(u_t * 0.17));

  /* Specular highlights — gated on onLine so they only appear on lines */
  float s1 = pow(max(dot(N, normalize(normalize(u_l1) + V)), 0.0), 300.0) * 4.0;
  float s2 = pow(max(dot(N, normalize(normalize(u_l2) + V)), 0.0), 220.0) * 2.5;
  /* Mouse shimmer — wide soft area glow, no specular dot */
  float mouseMag = length(u_mouse);
  vec3 Lm = normalize(vec3(u_mouse.x * 2.2, -u_mouse.y * 2.2, 2.0));
  float smRaw = pow(max(dot(N, normalize(Lm + V)), 0.0), 5.0) * 0.25;
  float sm = smRaw * smoothstep(0.05, 0.25, mouseMag);

  /* Very subtle inner ambient tint — NOT a bright blob, only tints lines near rim */
  float pulse    = 1.0 + 0.25 * sin(u_t * 0.28);
  vec3 innerCol  = mix(vec3(1.0, 0.82, 0.94), vec3(0.78, 0.86, 1.0), 0.5 + 0.5 * sin(u_t * 0.22));
  float innerGlow = fresnel * 0.18 * pulse;  /* rim-only, tiny */

  /* Assemble — lines are the dominant visual, sphere body transparent */
  vec3 shimmerCol = mix(vec3(0.85, 0.60, 1.0), vec3(0.55, 0.85, 1.0), 0.5 + 0.5 * sin(u_t * 0.3));
  vec3 col = lineCol * onLine;
  col += rimCol * fresnel * 1.8;
  col += innerCol * innerGlow * onLine;
  col += vec3(1.0, 0.97, 1.0) * (s1 + s2 * 0.8) * onLine;
  col += shimmerCol * sm * onLine;  /* color-only, no alpha — prevents white dot */

  /* Alpha — lines and rim only; body is transparent glass */
  float alpha = onLine * (0.90 + fresnel * 0.28)
              + fresnel * 0.55;

  gl_FragColor = vec4(col, clamp(alpha, 0.0, 1.0));
}
`

// Glow sphere vertex shader (no noise displacement — used for bloom layers)
const glowVertexShader = `
attribute vec3 a_p;
attribute vec3 a_n;
uniform mat4 u_mvp;
uniform mat4 u_mv;
uniform mat3 u_nm;
varying vec3 v_n;
varying vec3 v_vp;
void main() {
  v_n  = normalize(u_nm * a_n);
  v_vp = (u_mv * vec4(a_p, 1.0)).xyz;
  gl_Position = u_mvp * vec4(a_p, 1.0);
}
`

// Glow sphere fragment — soft bloom contribution, no contour lines
const glowFragmentShader = `
precision mediump float;
varying vec3 v_n;
varying vec3 v_vp;
uniform float u_t;
uniform float u_br;
void main() {
  vec3 N = normalize(v_n);
  vec3 V = normalize(-v_vp);
  float ndv = abs(dot(N, V));
  float rim  = pow(1.0 - ndv, 1.8);
  float pulse = 1.0 + 0.28 * sin(u_t * 0.28);
  vec3 rimCol = mix(vec3(0.55, 0.28, 0.96), vec3(0.94, 0.52, 0.86), 0.5 + 0.5 * sin(u_t * 0.20));
  vec3 col  = rimCol * rim * 0.60 * u_br;
  float alpha = rim * 0.42 * u_br * pulse;
  gl_FragColor = vec4(col, clamp(alpha, 0.0, 1.0));
}
`

const starVertexShader = `
attribute vec3 a_p;
attribute float a_br;

uniform mat4 u_mvp;
uniform float u_t;

varying float v_br;

void main() {
  v_br = a_br;
  float twinkle = 0.72 + 0.28 * sin(u_t * 2.6 + a_br * 53.0);
  gl_Position = u_mvp * vec4(a_p, 1.0);
  gl_PointSize = (1.1 + a_br * 1.8) * twinkle;
}
`

const starFragmentShader = `
precision mediump float;

varying float v_br;

void main() {
  vec2 uv = gl_PointCoord * 2.0 - 1.0;
  float dist = dot(uv, uv);
  if (dist > 1.0) {
    discard;
  }

  float glow = pow(1.0 - dist, 2.6) * v_br * 0.48;
  gl_FragColor = vec4(0.78, 0.68, 1.0, glow);
}
`

const ORB_RADIUS = 1.30

function makeProgram(gl, vertexSource, fragmentSource) {
  const makeShader = (type, source) => {
    const shader = gl.createShader(type)
    gl.shaderSource(shader, source)
    gl.compileShader(shader)
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      console.error(gl.getShaderInfoLog(shader))
    }
    return shader
  }

  const program = gl.createProgram()
  gl.attachShader(program, makeShader(gl.VERTEX_SHADER, vertexSource))
  gl.attachShader(program, makeShader(gl.FRAGMENT_SHADER, fragmentSource))
  gl.linkProgram(program)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.error(gl.getProgramInfoLog(program))
  }
  return program
}

function makeBuffer(gl, data, type = gl.ARRAY_BUFFER) {
  const buffer = gl.createBuffer()
  gl.bindBuffer(type, buffer)
  gl.bufferData(type, data, gl.STATIC_DRAW)
  return buffer
}

function uploadGeometry(gl, geometry) {
  return {
    pb: makeBuffer(gl, geometry.p),
    nb: makeBuffer(gl, geometry.n),
    rub: geometry.ru ? makeBuffer(gl, geometry.ru) : null,
    wyb: geometry.wy ? makeBuffer(gl, geometry.wy) : null,
    brb: geometry.br ? makeBuffer(gl, geometry.br) : null,
    ib: makeBuffer(gl, geometry.id, gl.ELEMENT_ARRAY_BUFFER),
    ct: geometry.id.length,
  }
}

function uploadStars(gl, geometry) {
  return {
    pb: makeBuffer(gl, geometry.p),
    brb: makeBuffer(gl, geometry.br),
    ct: geometry.count,
  }
}

function drawRing(gl, program, buffers, model, view, proj, uniforms) {
  gl.useProgram(program)

  const bindAttribute = (buffer, name, size) => {
    if (!buffer) {
      return
    }
    const location = gl.getAttribLocation(program, name)
    if (location < 0) {
      return
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.enableVertexAttribArray(location)
    gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0)
  }

  bindAttribute(buffers.pb, 'a_p', 3)
  bindAttribute(buffers.nb, 'a_n', 3)
  bindAttribute(buffers.rub, 'a_ru', 1)
  bindAttribute(buffers.wyb, 'a_wy', 1)

  const mv = m4mul(view, model)
  const mvp = m4mul(proj, mv)

  gl.uniformMatrix4fv(gl.getUniformLocation(program, 'u_mv'), false, mv)
  gl.uniformMatrix4fv(gl.getUniformLocation(program, 'u_mvp'), false, mvp)
  gl.uniformMatrix3fv(gl.getUniformLocation(program, 'u_nm'), false, mat3FromMat4(mv))

  Object.entries(uniforms).forEach(([key, value]) => {
    const location = gl.getUniformLocation(program, key)
    if (location === null) {
      return
    }
    if (typeof value === 'number') {
      gl.uniform1f(location, value)
      return
    }
    if (value.length === 2) {
      gl.uniform2fv(location, new Float32Array(value))
      return
    }
    gl.uniform3fv(location, new Float32Array(value))
  })

  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, buffers.ib)
  gl.drawElements(gl.TRIANGLES, buffers.ct, gl.UNSIGNED_SHORT, 0)
}

function drawStars(gl, program, buffers, view, proj, t) {
  gl.useProgram(program)

  const bindAttribute = (buffer, name, size) => {
    const location = gl.getAttribLocation(program, name)
    if (location < 0) {
      return
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.enableVertexAttribArray(location)
    gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0)
  }

  bindAttribute(buffers.pb, 'a_p', 3)
  bindAttribute(buffers.brb, 'a_br', 1)

  const identity = new Float32Array([
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
  ])
  const mv = m4mul(view, identity)
  const mvp = m4mul(proj, mv)

  gl.uniformMatrix4fv(gl.getUniformLocation(program, 'u_mvp'), false, mvp)
  gl.uniform1f(gl.getUniformLocation(program, 'u_t'), t)
  gl.drawArrays(gl.POINTS, 0, buffers.ct)
}

function killCard(id) {
  const card = activeCards.value.find((c) => c.id === id)
  if (!card || card.dying) return
  card.dying = true
  card.alive = false
  setTimeout(() => {
    activeCards.value = activeCards.value.filter((c) => c.id !== id)
  }, 480)
}

function spawnNewsCard() {
  const alive = activeCards.value.filter((c) => !c.dying)
  if (alive.length >= 2) return

  // Pick an angle at least 120° away from any existing card
  const existingAngles = activeCards.value.filter((c) => !c.dying).map((c) => c.angle)
  let angle, attempts = 0
  do {
    angle = Math.random() * 360
    const tooClose = existingAngles.some((a) => {
      const diff = Math.abs(angle - a) % 360
      return Math.min(diff, 360 - diff) < 120
    })
    if (!tooClose || attempts > 20) break
    attempts++
  } while (true)
  const rad = (angle * Math.PI) / 180

  // Card anchor in SVG coords — fixed relative to canvas center
  // pathD and dashOffset are computed live in tick() so they track the breathing orb
  const cardR = 51
  const x2 = 50 + cardR * Math.cos(rad)
  const y2 = 50 + cardR * Math.sin(rad)

  const isLeft = x2 < 50
  const cardStyle = {
    left: x2 + '%',
    top: y2 + '%',
    transform: isLeft ? 'translate(-105%, -50%)' : 'translate(5%, -50%)',
  }

  const id = cardIdCounter++
  const msg = NEWS_ITEMS[Math.floor(Math.random() * NEWS_ITEMS.length)]
  activeCards.value.push({
    id, angle, msg, cardStyle,
    x2, y2,
    pathD: '',
    pathLen: 12,
    dashOffset: 12,
    drawing: false,
    alive: false,
    dying: false,
    drawStartT: null,  // set by tick() when drawing begins
  })

  // Start drawing after one tick
  setTimeout(() => {
    const card = activeCards.value.find((c) => c.id === id)
    if (card) card.drawing = true
  }, 30)

  // Card appears after line finishes drawing (~1.1s)
  setTimeout(() => {
    const card = activeCards.value.find((c) => c.id === id)
    if (card && !card.dying) card.alive = true
  }, 1150)

  // Auto-remove after display time
  setTimeout(() => killCard(id), 1150 + 3400 + Math.random() * 1400)
}

onMounted(() => {
  const canvas = canvasRef.value
  const gl = canvas?.getContext('webgl', {
    antialias: true,
    alpha: true,
    premultipliedAlpha: false,
  })

  if (!gl) {
    return
  }

  gl.enable(gl.DEPTH_TEST)
  gl.enable(gl.BLEND)

  const sphereProgram = makeProgram(gl, sphereVertexShader, sphereFragmentShader)
  const glowProgram   = makeProgram(gl, glowVertexShader,   glowFragmentShader)
  const starProgram   = makeProgram(gl, starVertexShader,   starFragmentShader)

  // High-poly sphere geometry — used for both main sphere and bloom glow layers
  const sphereBuf  = uploadGeometry(gl, uvSphere(96, 96))
  const starBuffers = uploadStars(gl, stars(140))

  let lastWidth = 0
  let lastHeight = 0

  const resize = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const width = Math.round(canvas.clientWidth * dpr)
    const height = Math.round(canvas.clientHeight * dpr)
    if (width === lastWidth && height === lastHeight) {
      return
    }

    canvas.width = width
    canvas.height = height
    gl.viewport(0, 0, width, height)
    lastWidth = width
    lastHeight = height
  }

  const handleMouseMove = (event) => {
    mouseX = (event.clientX / window.innerWidth) * 2 - 1
    mouseY = (event.clientY / window.innerHeight) * 2 - 1
  }

  const handleMouseLeave = () => {
    mouseX = 0
    mouseY = 0
  }

  window.addEventListener('mousemove', handleMouseMove)
  window.addEventListener('mouseleave', handleMouseLeave)

  const startedAt = performance.now()

  const tick = () => {
    const t = (performance.now() - startedAt) * 0.001

    curRotY += (mouseX * 0.14 - curRotY) * 0.024
    curRotX += (-mouseY * 0.09 - curRotX) * 0.024

    resize()
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT)

    const aspect = (lastWidth || 1) / (lastHeight || 1)
    const proj = m4perspective(0.7, aspect, 0.1, 50)
    const view = m4lookAt([0, 0, 5.1], [0, 0, 0], [0, 1, 0])
    // Breathing pulse
    const breathe = 1.0 + 0.055 * Math.sin(t * 1.8)

    // Live-track AI news card lines to sphere surface + animate draw progress
    const orbVisR = 36.1 * breathe + 2.8  // projected sphere radius (SVG units) + gap
    activeCards.value.forEach((c) => {
      if (c.dying) return
      const r = (c.angle * Math.PI) / 180
      const x1 = 50 + orbVisR * Math.cos(r)
      const y1 = 50 + orbVisR * Math.sin(r)
      const dx = c.x2 - x1
      const dy = c.y2 - y1
      const ll = Math.sqrt(dx * dx + dy * dy)
      if (ll > 0.1) {
        const cpX = (x1 + c.x2) / 2 + (-dy / ll) * 4
        const cpY = (y1 + c.y2) / 2 + (dx / ll) * 4
        c.pathD = `M ${x1.toFixed(2)} ${y1.toFixed(2)} Q ${cpX.toFixed(2)} ${cpY.toFixed(2)} ${c.x2.toFixed(2)} ${c.y2.toFixed(2)}`
        c.pathLen = ll * 1.09
      }
      // JS-driven draw animation — ease-in-out cubic
      if (c.drawing) {
        if (c.drawStartT === null) c.drawStartT = t
        const prog = Math.min((t - c.drawStartT) / 1.1, 1)
        const eased = prog < 0.5 ? 4 * prog ** 3 : 1 - (-2 * prog + 2) ** 3 / 2
        c.dashOffset = c.pathLen * (1 - eased)
      } else {
        c.dashOffset = c.pathLen
      }
    })

    // Update trade card positions — orbit the breathing orb at a fixed radius past its edge
    const tradeCardR = orbVisR + 11
    tradeCards.value.forEach((c) => {
      if (c.dying) return
      const r = (c.angle * Math.PI) / 180
      c.x = 50 + tradeCardR * Math.cos(r)
      c.y = 50 + tradeCardR * Math.sin(r)
    })

    // Spin around a diagonally tilted axis (like a tilted top/gyroscope)
    const tilt = 0.52 + Math.sin(t * 0.07) * 0.04  // ~30° diagonal tilt, slow wobble
    const baseRotation = m4mul(
      m4mul(
        m4mul(
          rotZ(tilt + curRotY * 0.3),
          rotY(t * 0.48 + curRotX * 0.5),
        ),
        rotX(Math.sin(t * 0.11) * 0.04),
      ),
      m4scale(breathe),
    )

    // Three independent orbiting lights — each creates its own caustic hotspot
    const norm = (x, y, z) => { const l = Math.sqrt(x*x+y*y+z*z); return [x/l, y/l, z/l] }
    const lightDir  = norm(Math.cos(t*0.58)*0.90, 0.52+Math.sin(t*0.34)*0.36, -Math.sin(t*0.58)*0.62-0.45)
    const lightDir2 = norm(Math.cos(t*0.41+2.09)*0.78, 0.28+Math.sin(t*0.26+1.3)*0.44, -Math.sin(t*0.41+2.09)*0.54-0.32)
    const lightDir3 = norm(Math.cos(t*0.27+4.71)*0.65, 0.68+Math.sin(t*0.19+3.2)*0.26, -Math.sin(t*0.27+4.71)*0.48-0.58)

    gl.depthMask(false)
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE)

    // Stars
    drawStars(gl, starProgram, starBuffers, view, proj, t)

    // Bloom glow layers — soft spheres at 3 scales create the "light bleeding out" bloom effect
    // These use a simple rotation (no breathe scale) — scale is baked into m4scale
    const rotOnly = m4mul(m4mul(rotZ(tilt + curRotY * 0.3), rotY(t * 0.48 + curRotX * 0.5)), rotX(Math.sin(t * 0.11) * 0.04))
    const glowScales = [[1.80, 0.07], [1.40, 0.14], [1.15, 0.28]]
    glowScales.forEach(([scale, br]) => {
      const model = m4mul(rotOnly, m4scale(ORB_RADIUS * scale * breathe))
      drawRing(gl, glowProgram, sphereBuf, model, view, proj, { u_t: t, u_br: br })
    })

    // Main sphere — vertex shader handles ORB_RADIUS + displacement via u_r
    // baseRotation includes breathe scale which affects the vertex-displaced sphere correctly
    drawRing(gl, sphereProgram, sphereBuf, rotOnly, view, proj, {
      u_t:     t,
      u_r:     ORB_RADIUS * breathe,
      u_l1:    lightDir,
      u_l2:    lightDir2,
      u_l3:    lightDir3,
      u_mouse: [mouseX, -mouseY],
    })

    gl.depthMask(true)
    rafId = requestAnimationFrame(tick)
  }

  rafId = requestAnimationFrame(tick)

  canvas._cleanup = () => {
    cancelAnimationFrame(rafId)
    window.removeEventListener('mousemove', handleMouseMove)
    window.removeEventListener('mouseleave', handleMouseLeave)
  }

  spawnNewsCard()
  spawnTimer = setInterval(spawnNewsCard, 2400)

  spawnTradeCard()
  tradeTimer = setInterval(spawnTradeCard, 1820)
})

onUnmounted(() => {
  canvasRef.value?._cleanup?.()
  clearInterval(spawnTimer)
  clearInterval(tradeTimer)
})
</script>

<template>
  <div class="page">
    <TheNavbar />

    <section class="hero">
      <div class="hero-noise" />
      <div class="hero-ambient hero-ambient-left" />
      <div class="hero-ambient hero-ambient-right" />

      <div class="hero-shell">
        <div class="hero-text">

          <h1 class="title hero-item hero-item-2">
            The AI-powered
            <span class="accent">trading platform.</span>
          </h1>

          <p class="sub hero-item hero-item-3">
            Combine neural graph intelligence, real-time market data, and automated
            execution in one refined workspace built to surface higher-conviction trades.
          </p>

          <div class="ctas hero-item hero-item-4">
            <!-- "Get Started" only makes sense when there's a portal to enter.
                 In preview mode the GitHub link is the primary CTA. -->
            <button
              v-if="!preview"
              class="btn-fill"
              @click="router.push('/login')"
            >Get Started</button>
            <a
              :href="GITHUB_URL"
              target="_blank"
              rel="noopener"
              :class="preview ? 'btn-fill' : 'btn-ghost'"
            >
              <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true" style="margin-right: 0.5rem;">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
              </svg>
              View on GitHub
            </a>
          </div>
        </div>

        <div class="canvas-wrap hero-item hero-item-5">
          <!-- Trade signal cards — orbit the orb, tracking its breathing radius -->
          <div
            v-for="card in tradeCards"
            :key="card.id"
            class="trade-card"
            :class="{ alive: card.alive, dying: card.dying, buy: card.side === 'BUY', sell: card.side === 'SELL' }"
            :style="{
              left: card.x + '%',
              top:  card.y + '%',
              '--tc-sx': card.x < 50 ? '-105%' : '5%',
            }"
          >
            <span class="tc-side">{{ card.side }}</span>
            <span class="tc-ticker">{{ card.sym }}</span>
            <span class="tc-detail">{{ card.shares.toLocaleString() }} sh @ ${{ card.price }}</span>
          </div>

          <canvas ref="canvasRef" class="glc" />

          <!-- Animated connecting lines -->
          <svg class="orb-lines" viewBox="0 0 100 100" preserveAspectRatio="none">
            <defs>
              <filter id="orb-line-glow" x="-60%" y="-60%" width="220%" height="220%">
                <feGaussianBlur in="SourceGraphic" stdDeviation="0.7" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>
            <path
              v-for="card in activeCards"
              :key="'l' + card.id"
              :d="card.pathD"
              :style="{
                strokeDasharray: card.pathLen,
                strokeDashoffset: card.dashOffset,
              }"
              class="orb-line-el"
              :class="{ drawing: card.drawing, dying: card.dying }"
              filter="url(#orb-line-glow)"
            />
          </svg>

          <!-- AI news cards -->
          <div
            v-for="card in activeCards"
            :key="card.id"
            class="news-card"
            :class="{ alive: card.alive, dying: card.dying }"
            :style="card.cardStyle"
          >
            <div class="nc-pip" />
            <p class="nc-msg">{{ card.msg }}</p>
          </div>
        </div>
      </div>

      <div class="hero-floor" />
    </section>

    <div class="rest">
      <StrategySection />
      <NexusSection />
      <BacktestSection />
      <DeploySection />
    </div>

    <TheFooter />
  </div>
</template>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap');

*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

.page {
  background:
    radial-gradient(circle at 16% 18%, rgba(118, 55, 242, 0.18), transparent 24%),
    radial-gradient(circle at 82% 28%, rgba(210, 78, 255, 0.18), transparent 20%),
    linear-gradient(180deg, #010107 0%, #02030a 46%, #04040c 100%);
  color: #f7f6fb;
  font-family: 'Manrope', 'Inter', system-ui, sans-serif;
  min-height: 100vh;
  overflow-x: hidden;
}

.page :deep(nav) {
  background: rgba(4, 5, 13, 0.72) !important;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08) !important;
  box-shadow: 0 16px 42px rgba(0, 0, 0, 0.28);
  backdrop-filter: blur(18px);
}

.page :deep(nav > div) {
  width: min(1320px, calc(100% - 72px));
  max-width: none;
  padding-left: 0;
  padding-right: 0;
}

.page :deep(nav > div > a > .size-9) {
  background: linear-gradient(180deg, rgba(112, 62, 214, 0.34) 0%, rgba(63, 28, 132, 0.2) 100%) !important;
  border: 1px solid rgba(215, 196, 255, 0.14);
  box-shadow: 0 14px 30px rgba(75, 31, 166, 0.24);
}

.page :deep(nav .material-symbols-outlined) {
  color: #f8f7ff !important;
}

.page :deep(nav .text-xl) {
  color: #ffffff !important;
  font-family: 'Manrope', 'Inter', system-ui, sans-serif;
  font-size: 1.82rem;
  font-weight: 800;
  letter-spacing: -0.05em;
}

.page :deep(nav .text-slate-400) {
  color: rgba(200, 204, 224, 0.72) !important;
}

.page :deep(nav > div > .flex.items-center.gap-4 > button),
.page :deep(nav > div > .flex.items-center.gap-4 > a) {
  color: #ecebfb !important;
  transition: color 0.18s ease, transform 0.18s ease, filter 0.18s ease;
}

.page :deep(nav > div > .flex.items-center.gap-4 > button:hover),
.page :deep(nav > div > .flex.items-center.gap-4 > a:hover) {
  color: #ffffff !important;
}

.page :deep(nav > div > .flex.items-center.gap-4 > .rounded-lg) {
  background: linear-gradient(135deg, #1e1034 0%, #5b25c8 100%) !important;
  border: 1px solid rgba(222, 208, 255, 0.12);
  box-shadow: 0 16px 32px rgba(80, 33, 179, 0.3);
}

.page :deep(nav > div > .flex.items-center.gap-4 > .rounded-lg:hover) {
  filter: brightness(1.08);
  transform: translateY(-1px);
}

.page :deep(nav > div > .flex.items-center.gap-4 > .rounded-full) {
  background: rgba(255, 255, 255, 0.04) !important;
  border-color: rgba(255, 255, 255, 0.1) !important;
  color: rgba(204, 209, 229, 0.72) !important;
}

.hero {
  align-items: center;
  display: flex;
  min-height: 100vh;
  overflow: hidden;
  padding: 112px 0 88px;
  position: relative;
  isolation: isolate;
}

.hero-shell {
  align-items: center;
  display: grid;
  gap: clamp(2.5rem, 5vw, 6rem);
  grid-template-columns: minmax(0, 540px) minmax(0, 1fr);
  margin: 0 auto;
  position: relative;
  width: min(1320px, calc(100% - 72px));
  z-index: 2;
}

.hero-text {
  display: flex;
  flex-direction: column;
  gap: 1.6rem;
  max-width: 560px;
}

.pill {
  align-items: center;
  backdrop-filter: blur(14px);
  background: linear-gradient(135deg, rgba(59, 24, 102, 0.62) 0%, rgba(26, 13, 50, 0.42) 100%);
  border: 1px solid rgba(182, 139, 255, 0.3);
  border-radius: 999px;
  box-shadow: 0 10px 24px rgba(64, 19, 143, 0.22);
  color: #e5d6ff;
  display: inline-flex;
  font-size: 0.86rem;
  font-weight: 600;
  gap: 0.55rem;
  letter-spacing: 0.01em;
  padding: 0.45rem 1rem;
  width: fit-content;
}

.pill-dot {
  background: #d3b4ff;
  border-radius: 999px;
  box-shadow: 0 0 12px rgba(220, 196, 255, 0.85);
  flex-shrink: 0;
  height: 0.42rem;
  width: 0.42rem;
}

.title {
  color: #f8f8fd;
  font-size: clamp(3.15rem, 6.35vw, 5.2rem);
  font-weight: 700;
  letter-spacing: -0.07em;
  line-height: 0.96;
  max-width: 9ch;
  padding-bottom: 0.35rem;
}

.accent {
  background: linear-gradient(135deg, #fbf5ff 0%, #d9abff 42%, #9850ff 100%);
  background-clip: text;
  display: block;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.sub {
  color: rgba(213, 217, 232, 0.78);
  font-size: clamp(0.96rem, 1.08vw, 1.08rem);
  line-height: 1.85;
  max-width: 33rem;
}

.ctas {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  padding-top: 0.4rem;
}

.btn-fill,
.btn-ghost {
  align-items: center;
  border-radius: 0.85rem;
  cursor: pointer;
  display: inline-flex;
  font-family: inherit;
  font-size: 0.88rem;
  font-weight: 650;
  justify-content: center;
  min-width: 10.25rem;
  padding: 0.82rem 1.45rem;
  transition: transform 0.18s ease, filter 0.18s ease, background 0.18s ease, border-color 0.18s ease;
}

.btn-fill {
  background: linear-gradient(180deg, #ffffff 0%, #f1edf8 100%);
  border: none;
  box-shadow: 0 18px 36px rgba(255, 255, 255, 0.08);
  color: #0b0912;
}

.btn-fill:hover {
  filter: brightness(0.98);
  transform: translateY(-1px);
}

.btn-ghost {
  backdrop-filter: blur(14px);
  background: rgba(11, 12, 22, 0.52);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #ebe8f7;
}

.btn-ghost:hover {
  background: rgba(18, 19, 32, 0.72);
  border-color: rgba(223, 212, 255, 0.18);
  transform: translateY(-1px);
}

.canvas-wrap {
  aspect-ratio: 1 / 1;
  justify-self: end;
  width: min(100%, 46vw, 62vh);
  position: relative;
  margin-right: clamp(2rem, 4vw, 5rem);
}

.glc {
  inset: 0;
  position: absolute;
  display: block;
  height: 100%;
  position: relative;
  width: 100%;
  z-index: 2;
}

.hero-noise {
  background-image: radial-gradient(circle at 1px 1px, rgba(255, 255, 255, 0.22) 1px, transparent 0);
  background-size: 32px 32px;
  inset: 0;
  mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.28), rgba(0, 0, 0, 0.06) 68%, transparent);
  opacity: 0.08;
  position: absolute;
  z-index: 0;
}

.hero-ambient {
  border-radius: 999px;
  filter: blur(95px);
  pointer-events: none;
  position: absolute;
  z-index: 0;
}

.hero-ambient-left {
  background: rgba(82, 32, 181, 0.22);
  height: 420px;
  left: -140px;
  top: 22%;
  width: 420px;
}

.hero-ambient-right {
  background: rgba(214, 55, 222, 0.18);
  height: 520px;
  right: -40px;
  top: 16%;
  width: 520px;
}

.hero-floor {
  background:
    radial-gradient(65% 140% at 22% 100%, rgba(76, 38, 182, 0.34) 0%, rgba(76, 38, 182, 0) 68%),
    radial-gradient(48% 110% at 80% 100%, rgba(232, 69, 149, 0.2) 0%, rgba(232, 69, 149, 0) 66%),
    linear-gradient(180deg, rgba(5, 6, 14, 0) 0%, rgba(5, 6, 14, 0.88) 100%);
  bottom: 0;
  height: 220px;
  left: 0;
  pointer-events: none;
  position: absolute;
  right: 0;
  z-index: 1;
}

.rest {
  background:
    radial-gradient(circle at 15% 4%, rgba(103, 50, 207, 0.16), transparent 18%),
    radial-gradient(circle at 85% 18%, rgba(194, 73, 211, 0.12), transparent 20%),
    linear-gradient(180deg, #05040c 0%, #030309 100%);
  display: grid;
  gap: clamp(4rem, 8vw, 7rem);
  padding: clamp(1.5rem, 3vw, 2.5rem) 0 clamp(2.75rem, 5vw, 4rem);
  position: relative;
}

.rest::before {
  background:
    linear-gradient(180deg, rgba(13, 9, 28, 0) 0%, rgba(13, 9, 28, 0.18) 14%, rgba(13, 9, 28, 0.36) 100%),
    radial-gradient(circle at 50% 0%, rgba(116, 47, 238, 0.06), transparent 42%);
  content: '';
  inset: 0;
  pointer-events: none;
  position: absolute;
  z-index: 0;
}

.rest :deep(section) {
  background: transparent !important;
  border-color: rgba(179, 145, 245, 0.1) !important;
  position: relative;
  z-index: 1;
}

.rest :deep(.max-w-7xl),
.page :deep(footer > div) {
  position: relative;
  z-index: 1;
}

.rest :deep(.text-primary) {
  color: #c98fff !important;
}

.rest :deep(.bg-primary) {
  background: linear-gradient(180deg, #d2a8ff 0%, #8c42ff 100%) !important;
}

.rest :deep(.bg-primary\/10) {
  background: rgba(173, 105, 255, 0.12) !important;
}

.rest :deep(.bg-primary\/5) {
  background: rgba(173, 105, 255, 0.08) !important;
}

.rest :deep(.border-primary\/20) {
  border-color: rgba(173, 105, 255, 0.24) !important;
}

.rest :deep(.border-primary\/10) {
  border-color: rgba(173, 105, 255, 0.16) !important;
}

.rest :deep(.bg-surface),
.rest :deep(.card-hover),
.rest :deep(.glass-card) {
  background: linear-gradient(180deg, rgba(23, 14, 45, 0.9) 0%, rgba(11, 8, 24, 0.82) 100%) !important;
  border-color: rgba(188, 154, 255, 0.12) !important;
  box-shadow: 0 22px 44px rgba(5, 2, 12, 0.3);
}

.rest :deep(.glass-card) {
  backdrop-filter: blur(18px);
}

.rest :deep(.bg-surface\/30) {
  background: linear-gradient(180deg, rgba(13, 9, 28, 0.82) 0%, rgba(6, 5, 14, 0.2) 100%) !important;
}

.rest :deep(.bg-background-dark) {
  background: linear-gradient(180deg, rgba(10, 7, 22, 0.96) 0%, rgba(6, 4, 15, 0.96) 100%) !important;
  border-color: rgba(188, 154, 255, 0.12) !important;
}

.rest :deep(.bg-gradient-to-b) {
  background-image: linear-gradient(180deg, rgba(8, 6, 18, 0.94) 0%, rgba(19, 12, 39, 0.9) 100%) !important;
}

.rest :deep(.border-border-subtle),
.page :deep(footer .border-border-subtle) {
  border-color: rgba(188, 154, 255, 0.12) !important;
}

.rest :deep(.text-slate-200) {
  color: rgba(244, 239, 251, 0.93) !important;
}

.rest :deep(.text-slate-300),
.page :deep(footer .text-slate-300) {
  color: rgba(228, 223, 243, 0.85) !important;
}

.rest :deep(.text-slate-400),
.page :deep(footer .text-slate-400) {
  color: rgba(207, 200, 226, 0.76) !important;
}

.rest :deep(.text-slate-500),
.page :deep(footer .text-slate-500) {
  color: rgba(171, 164, 194, 0.72) !important;
}

.rest :deep(.text-slate-600),
.page :deep(footer .text-slate-600) {
  color: rgba(144, 138, 167, 0.68) !important;
}

.rest :deep(.card-hover:hover) {
  border-color: rgba(212, 176, 255, 0.2) !important;
  box-shadow: 0 24px 48px rgba(19, 10, 45, 0.34), 0 0 0 1px rgba(215, 180, 255, 0.06) !important;
}

.page :deep(footer) {
  background:
    radial-gradient(circle at 20% 0%, rgba(94, 42, 187, 0.16), transparent 24%),
    linear-gradient(180deg, #06040f 0%, #030208 100%) !important;
  border-color: rgba(188, 154, 255, 0.1) !important;
  /* Footer is a single-row brand + GitHub button now; the old 4-column
     link grid is gone, so the generous padding-top/bottom is too much. */
  margin-top: clamp(1rem, 2vw, 1.5rem);
  padding-top: 1.5rem !important;
  padding-bottom: 1.5rem !important;
}

.page :deep(footer .size-8) {
  background: linear-gradient(180deg, rgba(116, 62, 220, 0.44) 0%, rgba(67, 29, 138, 0.28) 100%) !important;
  border: 1px solid rgba(214, 196, 255, 0.12);
  box-shadow: 0 14px 30px rgba(48, 20, 110, 0.24);
}

.page :deep(footer .material-symbols-outlined) {
  color: #f7f2ff !important;
}

.page :deep(footer a:hover) {
  color: #d39eff !important;
}

/* ── Trade signal cards ────────────────────────────────────────── */
.trade-card {
  --tc-sx: 5%;
  align-items: center;
  backdrop-filter: blur(14px);
  border-radius: 8px;
  display: flex;
  gap: 6px;
  opacity: 0;
  padding: 5px 10px;
  pointer-events: none;
  position: absolute;
  transform: translate(var(--tc-sx), calc(-50% - 8px)) scale(0.94);
  transition: opacity 0.38s ease, transform 0.38s ease;
  white-space: nowrap;
  z-index: 8;
}

.trade-card.buy {
  background: rgba(8, 28, 18, 0.84);
  border: 1px solid rgba(52, 211, 110, 0.38);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.3), 0 0 10px rgba(52, 211, 110, 0.10);
}

.trade-card.sell {
  background: rgba(28, 8, 14, 0.84);
  border: 1px solid rgba(248, 80, 100, 0.38);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.3), 0 0 10px rgba(248, 80, 100, 0.10);
}

.trade-card.alive {
  opacity: 1;
  transform: translate(var(--tc-sx), -50%) scale(1);
}

.trade-card.dying {
  opacity: 0;
  transform: translate(var(--tc-sx), calc(-50% - 5px)) scale(0.96);
  transition: opacity 0.45s ease, transform 0.45s ease;
}

.tc-side {
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.07em;
}

.trade-card.buy .tc-side { color: #4ade80; }
.trade-card.sell .tc-side { color: #f87171; }

.tc-ticker {
  color: #f0eeff;
  font-size: 0.76rem;
  font-weight: 700;
}

.tc-detail {
  color: rgba(208, 202, 230, 0.68);
  font-size: 0.64rem;
}

/* ── AI news cards ─────────────────────────────────────────────── */
.orb-lines {
  height: 100%;
  inset: 0;
  pointer-events: none;
  position: absolute;
  width: 100%;
  z-index: 5;
  overflow: visible;
}

.orb-line-el {
  fill: none;
  opacity: 0;
  stroke: rgba(228, 210, 255, 0.72);
  stroke-linecap: round;
  stroke-width: 0.48;
  transition: opacity 0.35s ease;
}

.orb-line-el.drawing {
  opacity: 1;
}

.orb-line-el.dying {
  opacity: 0;
  transition: opacity 0.45s ease;
}

.news-card {
  backdrop-filter: blur(16px);
  background: linear-gradient(135deg, rgba(22, 10, 48, 0.88) 0%, rgba(10, 6, 26, 0.76) 100%);
  border: 1px solid rgba(195, 158, 255, 0.28);
  border-radius: 10px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.38), 0 0 14px rgba(130, 70, 240, 0.14);
  opacity: 0;
  padding: 9px 12px;
  pointer-events: none;
  position: absolute;
  transform: scale(0.88) translateY(5px);
  transition: opacity 0.38s ease, transform 0.38s ease;
  width: 148px;
  z-index: 10;
}

.news-card.alive {
  opacity: 1;
  transform: scale(1) translateY(0);
}

.news-card.dying {
  opacity: 0;
  transform: scale(0.92) translateY(3px);
}

.nc-pip {
  background: rgba(210, 170, 255, 0.85);
  border-radius: 999px;
  box-shadow: 0 0 6px rgba(195, 148, 255, 0.7);
  display: inline-block;
  height: 5px;
  margin-bottom: 6px;
  width: 5px;
}

.nc-msg {
  color: rgba(232, 224, 252, 0.92);
  font-size: 0.67rem;
  font-weight: 500;
  line-height: 1.46;
}

@media (max-width: 720px) {
  .news-card,
  .trade-card,
  .orb-lines {
    display: none;
  }
}

@media (min-width: 721px) {
  .hero-text {
    gap: 1.2rem;
  }

  .title {
    font-size: clamp(2.55rem, 5.2vw, 4.35rem);
    line-height: 1.09;
    max-width: 10.8ch;
    padding-bottom: 0.32rem;
  }

  .sub {
    font-size: clamp(0.88rem, 0.9vw, 0.98rem);
    line-height: 1.68;
  }

  .ctas {
    padding-top: 0.2rem;
  }

  .accent {
    line-height: 1.08;
    padding-bottom: 0.08em;
  }
}

@media (max-width: 1120px) {
  .hero {
    min-height: auto;
    padding-bottom: 64px;
  }

  .hero-shell {
    gap: 2.75rem;
    grid-template-columns: 1fr;
    width: min(100% - 48px, 840px);
  }

  .hero-text {
    max-width: none;
  }

  .title {
    max-width: 10ch;
  }

  .canvas-wrap {
    justify-self: center;
    margin-right: 0;
    width: min(100%, 620px, 56vh);
  }

  .hero-floor {
    height: 170px;
  }
}

@media (max-width: 720px) {
  .page :deep(nav > div) {
    width: calc(100% - 32px);
  }

  .page :deep(nav .text-xl) {
    font-size: 1.55rem;
  }

  .hero {
    padding: 122px 0 52px;
  }

  .hero-shell {
    width: calc(100% - 32px);
  }

  .hero-text {
    align-items: center;
    max-width: none;
    padding-top: 0.55rem;
    text-align: center;
    width: 100%;
  }

  .pill {
    font-size: 0.78rem;
    margin-inline: auto;
  }

  .title {
    font-size: clamp(2.35rem, 11.4vw, 3.2rem);
    line-height: 1.14;
    margin-inline: auto;
    max-width: 11.5ch;
    padding-bottom: 0.35rem;
    text-align: center;
  }

  .accent {
    padding-bottom: 0.08em;
  }

  .sub {
    font-size: 0.94rem;
    line-height: 1.7;
    margin-inline: auto;
    text-align: center;
  }

  .ctas {
    justify-content: center;
    width: 100%;
  }

  .btn-fill,
  .btn-ghost {
    flex: 1 1 100%;
    font-size: 0.84rem;
    min-width: 0;
    padding: 0.78rem 1.2rem;
    width: 100%;
  }

  .canvas-wrap {
    aspect-ratio: 1 / 0.96;
    width: min(100%, 52vh);
  }

  .hero-ambient-left {
    height: 280px;
    left: -120px;
    top: 24%;
    width: 280px;
  }

  .hero-ambient-right {
    height: 340px;
    right: -120px;
    top: 44%;
    width: 340px;
  }

  .hero-floor {
    height: 130px;
    opacity: 0.72;
  }
}
</style>
