let canvas = null;
let context = null;
let width = 0;
let height = 0;
let points = [];
let active = false;
let scene = 'chat';
let timerId = 0;
let pauseUntil = 0;
let config = null;
let dotRgb = null;
let ghostRgb = null;

const CHAT_SOURCES = [
  { bx: 0.22, by: 0.30, ax: 0.10, ay: 0.08, px: 0.0, py: 1.2, sp: 0.000095, freq: 7.2, amp: 0.52 },
  { bx: 0.78, by: 0.25, ax: 0.08, ay: 0.10, px: 2.1, py: 0.4, sp: 0.000075, freq: 6.5, amp: 0.48 },
  { bx: 0.50, by: 0.72, ax: 0.12, ay: 0.07, px: 1.0, py: 2.8, sp: 0.000110, freq: 7.8, amp: 0.55 },
  { bx: 0.18, by: 0.70, ax: 0.07, ay: 0.09, px: 3.3, py: 0.9, sp: 0.000085, freq: 6.0, amp: 0.40 },
  { bx: 0.82, by: 0.68, ax: 0.09, ay: 0.06, px: 0.7, py: 3.1, sp: 0.000100, freq: 7.5, amp: 0.44 },
];

function resize(nextWidth, nextHeight) {
  width = Math.max(1, Math.round(nextWidth));
  height = Math.max(1, Math.round(nextHeight));
  canvas.width = width;
  canvas.height = height;
  context.setTransform(1, 0, 0, 1, 0, 0);
  points = [];
  for (let y = -config.dotSpacing * 2; y <= height + config.dotSpacing * 2; y += config.dotSpacing) {
    let colIndex = 0;
    for (let x = -config.dotSpacing * 2; x <= width + config.dotSpacing * 2; x += config.dotSpacing) {
      points.push({
        x,
        y,
        colIndex,
        px: (x - width / 2) * config.noiseScale * 4.2,
        py: (y - height / 2) * config.noiseScale * 4.2,
        radial: Math.max(0.22, 1 - Math.hypot((x - width / 2) / width, (y - height / 2) / height) * 1.06),
      });
      colIndex += 1;
    }
  }
}

function scheduleFrame() {
  if (!active || timerId) return;
  timerId = setTimeout(draw, 1000 / 15);
}

function draw() {
  timerId = 0;
  if (!active) return;
  const timestamp = performance.now();
  if (timestamp < pauseUntil) {
    scheduleFrame();
    return;
  }
  const t = timestamp * 0.00105 * config.speed;
  context.clearRect(0, 0, width, height);
  const chatSources = scene === 'chat'
    ? CHAT_SOURCES.map(source => {
        const sourceTime = timestamp * source.sp;
        return {
          ...source,
          cx: (source.bx + Math.sin(sourceTime + source.px) * source.ax - 0.5) * (width * config.noiseScale * 4.2),
          cy: (source.by + Math.cos(sourceTime * 0.73 + source.py) * source.ay - 0.5) * (height * config.noiseScale * 4.2),
        };
      })
    : null;

  for (const point of points) {
    const px = point.px;
    const py = point.py;
    const warpX = Math.sin(py * 0.92 + t * 0.54) * 0.42 + Math.cos(px * 0.58 - t * 0.38) * 0.26;
    const warpY = Math.cos(px * 0.84 - t * 0.46) * 0.34 - Math.sin(py * 0.62 + t * 0.42) * 0.24;
    const sx = px + warpX;
    const sy = py + warpY;
    let wave = 0;
    if (scene === 'chat') {
      for (const source of chatSources) {
        const dx = sx - source.cx;
        const dy = sy - source.cy;
        wave += Math.cos(Math.hypot(dx, dy) * source.freq - t * 2.6 + source.px) * source.amp;
      }
      wave /= 2.2;
    } else if (scene === 'code') {
      const distance = Math.hypot(sx, sy) * 2.8;
      wave += Math.cos(distance * 1.40 - t * 1.60) * 0.68;
      wave += Math.sin(distance * 2.80 - t * 2.80) * 0.20;
      wave += Math.cos(sx * 1.10 - sy * 1.10 + t * 0.52) * 0.16;
    } else {
      wave += Math.sin(sx * 1.08 + t * 1.14) * Math.cos(sy * 0.98 - t * 0.88) * 0.72;
      wave += Math.cos((sx + sy) * 1.42 - t * 0.86) * 0.22;
      wave += Math.sin(sx * 2.46 - sy * 1.74 + t * 1.36) * 0.18;
    }
    const shimmer = 0.94 + Math.sin(point.colIndex * 0.11 + t * 0.3) * 0.03;
    const brightness = Math.max(0, (wave + 1.0) * 0.5) * (0.42 + point.radial * 0.68) * shimmer;
    const alpha = Math.min(config.maxAlpha, Math.pow(brightness, config.contrastPower) * config.brightnessGain);
    if (alpha < config.minAlpha) continue;

    context.beginPath();
    const rgb = alpha > config.maxAlpha * 0.56 ? dotRgb : ghostRgb;
    context.fillStyle = `rgba(${rgb.r},${rgb.g},${rgb.b},${alpha})`;
    context.arc(point.x + warpX * 5.4, point.y + warpY * 5.0, 1.16 + alpha * 0.12, 0, Math.PI * 2);
    context.fill();
  }
  scheduleFrame();
}

self.onmessage = event => {
  const data = event.data || {};
  if (data.type === 'init') {
    canvas = data.canvas;
    context = canvas.getContext('2d');
    config = data.config;
    dotRgb = data.dotRgb;
    ghostRgb = data.ghostRgb;
    resize(data.width, data.height);
    return;
  }
  if (!canvas) return;
  if (data.type === 'resize') {
    resize(data.width, data.height);
    return;
  }
  if (data.type !== 'state') return;
  if (data.scene === 'chat' || data.scene === 'cowork' || data.scene === 'code') {
    if (scene !== data.scene) pauseUntil = performance.now() + 180;
    scene = data.scene;
  }
  active = Boolean(data.active);
  if (!active && timerId) {
    clearTimeout(timerId);
    timerId = 0;
  }
  scheduleFrame();
};
