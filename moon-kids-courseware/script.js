/* ======================================================================
   白天为什么也能看到月亮 —— 给 4-5 岁小朋友的月亮课
   核心科学逻辑：
   1) 月亮自己不发光。太阳光照到月亮，月亮把光反射进我们的眼睛，我们才看见月亮。
   2) 月亮永远只有朝向太阳的一半被照亮（k = (1+cosφ)/2 是我们在地球看到的亮面比例）。
   3) 白天能看到月亮，是因为月亮白天也在天上，它的反射光比蓝天亮；
      只要它和太阳在天上隔得够远（不被太阳强光盖住）、又够亮，白天就能看见。
   ====================================================================== */

// ---------- DOM ----------
const spaceCanvas = document.getElementById("spaceCanvas");
const skyCanvas = document.getElementById("skyCanvas");
const phaseCanvas = document.getElementById("phaseCanvas");
const sctx = spaceCanvas.getContext("2d");
const kctx = skyCanvas.getContext("2d");
const pctx = phaseCanvas.getContext("2d");

const lessonTitle = document.getElementById("lessonTitle");
const lessonText = document.getElementById("lessonText");
const promptText = document.getElementById("promptText");
const teacherNote = document.getElementById("teacherNote");
const stepCount = document.getElementById("stepCount");
const phaseNameEl = document.getElementById("phaseName");
const phaseBrightEl = document.getElementById("phaseBright");
const skyStatus = document.getElementById("skyStatus");

const playPause = document.getElementById("playPause");
const prevStep = document.getElementById("prevStep");
const nextStep = document.getElementById("nextStep");
const speedRange = document.getElementById("speedRange");
const lightPathToggle = document.getElementById("lightPathToggle");
const dayZoneToggle = document.getElementById("dayZoneToggle");
const voiceToggle = document.getElementById("voiceToggle");

// ---------- 5 节课 ----------
// phi：月亮在轨道上的角度。phi=0 满月；phi=π 新月；phi=π/2 上弦；phi=3π/2 下弦
const lessons = [
  {
    phi: Math.PI / 2,
    title: "太阳是一盏大灯",
    text: "太阳又大又亮，自己会发光。它的光跑到地球上，也跑到月亮上。",
    prompt: "找一找：太阳的光照到了谁？",
    teacher: "月亮自己不会发光。我们之所以能看见月亮，是因为太阳光照到月亮上，月亮又把光反射到我们的眼睛里。",
  },
  {
    phi: (2 * Math.PI) / 3,
    title: "月亮把太阳光弹回来",
    text: "月亮自己不会发光。太阳光照到月亮上，月亮就像一面镜子，把光弹回来，这就叫“反射”。",
    prompt: "跟着箭头看：光从太阳，走到月亮，再走到地球。",
    teacher: "月亮表面是灰色的岩石，它把照到的太阳光大约反射出 7%。就是这一点点反射光，让我们看见了月亮。",
  },
  {
    phi: Math.PI / 3,
    title: "月亮绕着地球转，样子会变",
    text: "月亮每天都绕着地球走一点点。因为它和太阳的位置在变，所以我们看到的亮面也在变，有时圆，有时弯。",
    prompt: "拖动月亮转一圈，看看亮的部分怎么变。",
    teacher: "这就是“月相”。月亮永远只有朝向太阳的一半被照亮，但我们在地球上看它的角度变了，亮面看起来就一会儿大、一会儿小。",
  },
  {
    phi: (3 * Math.PI) / 2,
    title: "白天也能看到月亮！",
    text: "白天，月亮常常也在天上。它反射的光比蓝天亮一点点；只要它不紧挨着太阳，我们白天就能看见它。",
    prompt: "看下面的天空：太阳和月亮是不是同时在？",
    teacher: "白天最容易看到月亮的时候是上弦月（下午）和下弦月（清晨）：这时月亮和太阳在天上隔得比较远，月亮又够亮，就能在蓝天中被看出来。",
  },
  {
    phi: Math.PI,
    title: "什么时候白天看不到月亮？",
    text: "当月亮跑到太阳和地球中间，它黑的一面朝着我们，又被太阳的强光盖住，这时白天、晚上都看不到月亮，叫“新月”。",
    prompt: "你看，月亮躲到太阳旁边去了，还能看见吗？",
    teacher: "另一种看不到的情况是满月：月亮和太阳在天空的两边，白天月亮还在地平线下面，要等到天黑它才升起来，所以满月的夜晚最亮。",
  },
];

// ---------- 状态 ----------
let currentStep = 0;
let phi = lessons[0].phi;
let isPlaying = true;
let isDragging = false;
let lastTime = 0;

// ---------- 配音模块（预留） ----------
// 现在用浏览器内置语音合成占位；将来可替换成录音文件播放，例如：
//   new Audio(`audio/step${currentStep}.mp3`).play();
const narration = {
  enabled: true,
  speak(text) {
    if (!this.enabled || !("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "zh-CN";
    u.rate = 0.85;
    u.pitch = 1.15;
    window.speechSynthesis.speak(u);
  },
  stop() {
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  },
};

// ---------- 物理 / 几何 ----------
// 在地球看到的亮面比例：月亮只有朝太阳的一半被照亮
function illumFraction(angle) {
  return (1 + Math.cos(angle)) / 2;
}
// 距角：从地球看，月亮和太阳在天空里的夹角（弧度）。太阳在左 → 方向 (-1,0)
function elongation(angle) {
  return Math.acos(clamp(-Math.cos(angle), -1, 1));
}
function clamp(v, a, b) {
  return Math.max(a, Math.min(b, v));
}
function toDeg(rad) {
  return (rad * 180) / Math.PI;
}
function phaseName(k) {
  if (k > 0.92) return "满月";
  if (k > 0.6) return "大半个月亮";
  if (k >= 0.4) return "半个月亮";
  if (k > 0.08) return "弯弯的月亮";
  return "新月";
}

// 通用：画一个月亮盘（亮的一边朝向太阳方向）。litRight=true → 亮面在右
function drawMoonDisk(c, cx, cy, r, k, litRight) {
  r = Math.max(2, r);
  c.save();
  c.beginPath();
  c.arc(cx, cy, r, 0, Math.PI * 2);
  c.clip();
  // 暗面底色
  c.fillStyle = "#4a5266";
  c.fillRect(cx - r, cy - r, 2 * r, 2 * r);
  if (k > 0.005) {
    c.save();
    c.translate(cx, cy);
    if (!litRight) c.scale(-1, 1); // 镜像后统一按“亮在右”处理
    c.translate(-cx, -cy);
    const rx = r * Math.abs(1 - 2 * k); // 终止线椭圆的横向半轴
    c.fillStyle = "#f6f1dc";
    c.beginPath();
    c.moveTo(cx, cy - r); // 顶
    c.arc(cx, cy, r, -Math.PI / 2, Math.PI / 2, false); // 右半圆边
    c.ellipse(cx, cy, rx, r, 0, Math.PI / 2, -Math.PI / 2, k < 0.5); // 终止线
    c.closePath();
    c.fill();
    c.restore();
  }
  c.restore();
  // 外圈
  c.strokeStyle = "rgba(255,255,255,0.55)";
  c.lineWidth = 1.5;
  c.beginPath();
  c.arc(cx, cy, r, 0, Math.PI * 2);
  c.stroke();
}

// ---------- 画布尺寸（高 DPI） ----------
function fitCanvas(canvas, ctx) {
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * ratio);
  canvas.height = Math.round(rect.height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { w: rect.width, h: rect.height };
}
function resizeAll() {
  fitCanvas(spaceCanvas, sctx);
  fitCanvas(skyCanvas, kctx);
  // phaseCanvas 固定尺寸
  const pr = window.devicePixelRatio || 1;
  phaseCanvas.width = 130 * pr;
  phaseCanvas.height = 130 * pr;
  pctx.setTransform(pr, 0, 0, pr, 0, 0);
}

// ===================== 画面一：太空俯视 =====================
function drawSpace() {
  const { w, h } = fitCanvas(spaceCanvas, sctx);
  sctx.clearRect(0, 0, w, h);

  // 背景星空
  sctx.fillStyle = "#0e2340";
  sctx.fillRect(0, 0, w, h);
  sctx.fillStyle = "rgba(255,255,255,0.5)";
  for (let i = 0; i < 40; i++) {
    const x = (i * 97) % w;
    const y = (i * 53) % h;
    sctx.fillRect(x, y, 2, 2);
  }

  // 场景坐标：太阳在左，地球在中右
  const sun = { x: w * 0.12, y: h * 0.5, r: Math.min(w, h) * 0.1 };
  const earth = { x: w * 0.6, y: h * 0.5, r: Math.min(w, h) * 0.085 };
  const orbitR = Math.min(w, h) * 0.3;
  const moon = {
    x: earth.x + Math.cos(phi) * orbitR,
    y: earth.y + Math.sin(phi) * orbitR,
    r: Math.max(12, Math.min(w, h) * 0.04),
  };

  // 白天看月亮的“好位置”：距角 25°~150°
  if (dayZoneToggle.checked) {
    const steps = 90;
    sctx.lineWidth = 14;
    sctx.strokeStyle = "rgba(255,209,125,0.55)";
    sctx.beginPath();
    let drawing = false;
    for (let i = 0; i <= steps; i++) {
      const a = (i / steps) * Math.PI * 2;
      const e = toDeg(elongation(a));
      const ok = e > 25 && e < 150 && illumFraction(a) > 0.04;
      const px = earth.x + Math.cos(a) * orbitR;
      const py = earth.y + Math.sin(a) * orbitR;
      if (ok) {
        if (!drawing) {
          sctx.moveTo(px, py);
          drawing = true;
        } else {
          sctx.lineTo(px, py);
        }
      } else {
        drawing = false;
      }
    }
    sctx.stroke();
  }

  // 太阳与阳光方向
  drawSun(sctx, sun.x, sun.y, sun.r);
  if (lightPathToggle.checked) {
    drawLightArrows(sctx, sun, moon, earth);
  }

  // 月亮轨道（虚线）
  sctx.beginPath();
  sctx.arc(earth.x, earth.y, orbitR, 0, Math.PI * 2);
  sctx.setLineDash([7, 9]);
  sctx.lineWidth = 2;
  sctx.strokeStyle = "rgba(255,255,255,0.4)";
  sctx.stroke();
  sctx.setLineDash([]);

  // 地球：左半（朝太阳）白天亮，右半夜晚暗
  drawEarth(sctx, earth.x, earth.y, earth.r);

  // 月亮：太空中，被照亮的一半永远朝向太阳（左侧）
  drawMoonDisk(sctx, moon.x, moon.y, moon.r, 0.5, false);

  // 标注
  label(sctx, sun.x, sun.y - sun.r - 6, "太阳", "#ffd27a");
  label(sctx, earth.x, earth.y - earth.r - 6, "地球", "#cfe8ff");
  label(sctx, moon.x + moon.r + 4, moon.y - moon.r - 2, "月亮", "#f6f1dc");
}

function drawSun(c, x, y, r) {
  // 光晕
  const g = c.createRadialGradient(x, y, r * 0.4, x, y, r * 2.2);
  g.addColorStop(0, "rgba(255,200,80,0.55)");
  g.addColorStop(1, "rgba(255,200,80,0)");
  c.fillStyle = g;
  c.beginPath();
  c.arc(x, y, r * 2.2, 0, Math.PI * 2);
  c.fill();
  // 本体
  c.fillStyle = "#ffb22c";
  c.beginPath();
  c.arc(x, y, r, 0, Math.PI * 2);
  c.fill();
  c.strokeStyle = "#fff0a9";
  c.lineWidth = 4;
  c.stroke();
}

function drawLightArrows(c, sun, moon, earth) {
  // 1) 太阳 → 月亮（照亮月亮朝太阳的一面）
  drawRay(c, sun.x, sun.y, moon.x - moon.r, moon.y, "#ffe08a", "太阳光");
  // 2) 月亮 → 地球（反射光进入眼睛）
  drawRay(c, moon.x, moon.y, earth.x, earth.y, "#9be1ff", "反射的光");
}
function drawRay(c, x1, y1, x2, y2, color, text) {
  c.save();
  c.strokeStyle = color;
  c.fillStyle = color;
  c.lineWidth = 3;
  c.setLineDash([10, 7]);
  c.beginPath();
  c.moveTo(x1, y1);
  c.lineTo(x2, y2);
  c.stroke();
  c.setLineDash([]);
  // 箭头
  const ang = Math.atan2(y2 - y1, x2 - x1);
  const hx = x2 - Math.cos(ang) * 10;
  const hy = y2 - Math.sin(ang) * 10;
  c.beginPath();
  c.moveTo(x2, y2);
  c.lineTo(hx - Math.cos(ang - 0.5) * 9, hy - Math.sin(ang - 0.5) * 9);
  c.lineTo(hx - Math.cos(ang + 0.5) * 9, hy - Math.sin(ang + 0.5) * 9);
  c.closePath();
  c.fill();
  // 文字
  if (text) {
    c.font = "700 13px Microsoft YaHei, sans-serif";
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2 - 8;
    c.fillStyle = "rgba(10,24,40,0.7)";
    const tw = c.measureText(text).width;
    c.fillRect(mx - tw / 2 - 5, my - 13, tw + 10, 18);
    c.fillStyle = color;
    c.textAlign = "center";
    c.fillText(text, mx, my);
    c.textAlign = "left";
  }
  c.restore();
}

function drawEarth(c, x, y, r) {
  c.save();
  c.beginPath();
  c.arc(x, y, r, 0, Math.PI * 2);
  c.clip();
  // 左半（白天，朝太阳）亮蓝；右半（夜晚）暗
  const g = c.createLinearGradient(x - r, y, x + r, y);
  g.addColorStop(0, "#7fd0ff");
  g.addColorStop(0.5, "#2a8fd0");
  g.addColorStop(0.5, "#123a5e");
  g.addColorStop(1, "#0c2238");
  c.fillStyle = g;
  c.fillRect(x - r, y - r, 2 * r, 2 * r);
  // 陆地
  c.fillStyle = "#5fae6a";
  c.beginPath();
  c.ellipse(x - r * 0.3, y - r * 0.1, r * 0.22, r * 0.14, -0.5, 0, Math.PI * 2);
  c.fill();
  c.beginPath();
  c.ellipse(x + r * 0.2, y + r * 0.25, r * 0.18, r * 0.1, 0.4, 0, Math.PI * 2);
  c.fill();
  c.restore();
  c.strokeStyle = "rgba(255,255,255,0.8)";
  c.lineWidth = 2;
  c.beginPath();
  c.arc(x, y, r, 0, Math.PI * 2);
  c.stroke();
  // 白天/夜晚小标
  c.font = "700 12px Microsoft YaHei, sans-serif";
  c.fillStyle = "rgba(255,255,255,0.85)";
  c.textAlign = "center";
  c.fillText("白天", x - r * 0.55, y + r * 0.05);
  c.fillText("夜晚", x + r * 0.55, y + r * 0.05);
  c.textAlign = "left";
}

function label(c, x, y, text, color) {
  c.font = "700 14px Microsoft YaHei, sans-serif";
  c.textAlign = "center";
  c.fillStyle = "rgba(10,24,40,0.65)";
  const w = c.measureText(text).width + 12;
  c.fillRect(x - w / 2, y - 18, w, 18);
  c.fillStyle = color;
  c.fillText(text, x, y - 5);
  c.textAlign = "left";
}

// ===================== 画面二：地面天空 =====================
function drawSky() {
  const { w, h } = fitCanvas(skyCanvas, kctx);
  kctx.clearRect(0, 0, w, h);

  const E = elongation(phi); // 距角（弧度）
  const Edeg = toDeg(E);
  const k = illumFraction(phi);

  // 可见性判定
  let visible = false;
  let reason = "";
  if (Edeg <= 25) {
    visible = false;
    reason = "月亮离太阳太近，被太阳的强光盖住了 → 白天看不到";
  } else if (Edeg >= 150) {
    visible = false;
    reason = "月亮和太阳在天空的两边，白天月亮还在地平线下面 → 天黑才升起";
  } else if (k <= 0.04) {
    visible = false;
    reason = "月亮被照亮的部分太少 → 看不到";
  } else {
    visible = true;
    reason = "✅ 白天能看到月亮！它反射的光穿过蓝天，被我们看见。";
  }

  // 天空背景：白天蓝天（无论可见与否，本画面就是“白天的天空”）
  const sky = kctx.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, "#5ec0ff");
  sky.addColorStop(1, "#bfe9ff");
  kctx.fillStyle = sky;
  kctx.fillRect(0, 0, w, h);
  // 几朵云
  drawCloud(kctx, w * 0.2, h * 0.28, w * 0.06);
  drawCloud(kctx, w * 0.85, h * 0.18, w * 0.05);

  const horizonY = h * 0.82;
  // 地平线 / 草地
  const ground = kctx.createLinearGradient(0, horizonY, 0, h);
  ground.addColorStop(0, "#86c46a");
  ground.addColorStop(1, "#5a9c4a");
  kctx.fillStyle = ground;
  kctx.beginPath();
  kctx.moveTo(0, horizonY);
  kctx.quadraticCurveTo(w * 0.5, horizonY - 12, w, horizonY);
  kctx.lineTo(w, h);
  kctx.lineTo(0, h);
  kctx.closePath();
  kctx.fill();

  // 太阳：固定在天空右侧偏上
  const sunX = w * 0.72;
  const sunY = horizonY - h * 0.42;
  const sunR = Math.min(w, h) * 0.07;
  drawSun(kctx, sunX, sunY, sunR);
  label(kctx, sunX, sunY - sunR - 6, "太阳", "#ff9a2e");

  // 月亮在天空中的位置：水平随距角变化，垂直随距角成弧形
  const leftMargin = w * 0.08;
  const maxAlt = h * 0.46;
  const moonX = sunX - (E / Math.PI) * (sunX - leftMargin);
  const moonY = horizonY - Math.sin(E) * maxAlt;
  const moonR = Math.max(11, Math.min(w, h) * 0.045);

  if (visible) {
    // 清晰画出月亮（亮面朝向太阳，即在右侧）
    kctx.save();
    kctx.globalAlpha = 1;
    drawMoonDisk(kctx, moonX, moonY, moonR, k, true);
    kctx.restore();
    label(kctx, moonX, moonY - moonR - 6, "月亮", "#3a4250");
  } else if (Edeg >= 150) {
    // 满月：藏到地平线下面（只露一点，半透明）
    kctx.save();
    kctx.globalAlpha = 0.5;
    drawMoonDisk(kctx, moonX, horizonY + moonR * 0.4, moonR, k, true);
    kctx.restore();
    label(kctx, moonX, horizonY + moonR * 0.4 - moonR - 6, "月亮（在地下）", "#3a4250");
  } else {
    // 新月：贴近太阳，被强光冲淡
    kctx.save();
    kctx.globalAlpha = 0.35;
    drawMoonDisk(kctx, moonX, moonY, moonR, k, true);
    kctx.restore();
    label(kctx, moonX, moonY - moonR - 6, "月亮（被阳光盖住）", "#3a4250");
  }

  // 底部状态条
  skyStatus.textContent = reason;
  skyStatus.style.background = visible
    ? "rgba(63,157,106,0.92)"
    : "rgba(20,40,64,0.82)";
}

function drawCloud(c, x, y, r) {
  c.fillStyle = "rgba(255,255,255,0.85)";
  c.beginPath();
  c.arc(x, y, r, 0, Math.PI * 2);
  c.arc(x + r * 0.9, y + r * 0.1, r * 0.8, 0, Math.PI * 2);
  c.arc(x - r * 0.9, y + r * 0.15, r * 0.7, 0, Math.PI * 2);
  c.arc(x, y + r * 0.35, r * 0.9, 0, Math.PI * 2);
  c.fill();
}

// ===================== 右侧月相小预览 =====================
function drawPhase() {
  const cx = 65;
  const cy = 65;
  const r = 48;
  pctx.clearRect(0, 0, 130, 130);
  // 背板
  pctx.fillStyle = "#0e2340";
  pctx.fillRect(0, 0, 130, 130);
  const k = illumFraction(phi);
  drawMoonDisk(pctx, cx, cy, r, k, true);
  const name = phaseName(k);
  phaseNameEl.textContent = name;
  phaseBrightEl.textContent = "亮的部分：" + Math.round(k * 100) + "%";
}

// ---------- 课文切换 ----------
function updateLesson(speak) {
  const item = lessons[currentStep];
  lessonTitle.textContent = item.title;
  lessonText.textContent = item.text;
  promptText.textContent = item.prompt;
  teacherNote.textContent = item.teacher;
  stepCount.textContent = `${currentStep + 1} / ${lessons.length}`;
  phi = item.phi;
  redraw();
  if (speak) narration.speak(`${item.title}。${item.text}`);
}
function setStep(next, speak) {
  currentStep = (next + lessons.length) % lessons.length;
  updateLesson(speak);
}

function redraw() {
  drawSpace();
  drawSky();
  drawPhase();
}

// ---------- 动画循环 ----------
function animate(time) {
  const dt = lastTime ? (time - lastTime) / 1000 : 0;
  lastTime = time;
  if (isPlaying && !isDragging) {
    phi += dt * Number(speedRange.value) * 0.5;
    if (phi > Math.PI * 2) phi -= Math.PI * 2;
    redraw();
  }
  requestAnimationFrame(animate);
}

// ---------- 事件 ----------
playPause.addEventListener("click", () => {
  isPlaying = !isPlaying;
  playPause.textContent = isPlaying ? "暂停" : "播放";
});
prevStep.addEventListener("click", () => setStep(currentStep - 1));
nextStep.addEventListener("click", () => setStep(currentStep + 1));
lightPathToggle.addEventListener("change", drawSpace);
dayZoneToggle.addEventListener("change", drawSpace);
voiceToggle.addEventListener("click", () =>
  narration.speak(`${lessonTitle.textContent}。${lessonText.textContent}`),
);

// 拖动月亮（在太空画布上）
function pointerPhi(e) {
  const rect = spaceCanvas.getBoundingClientRect();
  const ex = e.clientX - rect.left;
  const ey = e.clientY - rect.top;
  const earthX = rect.width * 0.6;
  const earthY = rect.height * 0.5;
  return Math.atan2(ey - earthY, ex - earthX);
}
spaceCanvas.addEventListener("pointerdown", (e) => {
  isDragging = true;
  spaceCanvas.setPointerCapture(e.pointerId);
  phi = pointerPhi(e);
  redraw();
});
spaceCanvas.addEventListener("pointermove", (e) => {
  if (!isDragging) return;
  phi = pointerPhi(e);
  redraw();
});
spaceCanvas.addEventListener("pointerup", () => {
  isDragging = false;
});
spaceCanvas.addEventListener("pointercancel", () => {
  isDragging = false;
});

window.addEventListener("resize", () => {
  resizeAll();
  redraw();
});

// ---------- 启动 ----------
resizeAll();
updateLesson(false);
requestAnimationFrame(animate);
