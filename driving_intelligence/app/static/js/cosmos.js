/* Driving Intelligence — "twin-line" cosmos background.
 *
 * Two pairs of light lines (a double-line lamp motif) are born at a distant point of
 * light in deep space, sweep outward toward the viewer, and then run down both page
 * gutters all the way to the footer. Light pulses travel along them from far to near
 * (perspective: slow and thin when far, fast and bright when close). A starfield drifts
 * out of the same vanishing point.
 *
 * - Home page: an element with [data-cosmos-anchor] marks where the light source sits.
 *   Other pages: the source is above the viewport, so only the descending lines show.
 * - Geometry is built in document coordinates once per resize/layout change; each frame
 *   only translates by scrollY, so scrolling is cheap and the lines truly "continue down".
 * - No per-frame allocations of geometry; DPR capped at 1.5; paused when the tab is
 *   hidden or while the engine-start intro is on screen; prefers-reduced-motion renders
 *   a static frame (redrawn only on scroll/resize).
 * - CSP-safe: no inline script or style attributes.
 */
(function () {
  "use strict";

  var canvas = document.querySelector("[data-cosmos]");
  if (!canvas || !canvas.getContext) return;
  var ctx = canvas.getContext("2d");
  if (!ctx) return;

  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var reduced = !!(mq && mq.matches);
  var anchor = document.querySelector("[data-cosmos-anchor]");

  var GAP_FAR = 0.6, GAP_NEAR = 7;          // css px between the two lines of a lamp
  var W = 0, H = 0, dpr = 1, scrollY = 0;
  var vp = { x: 0, y: 0 }, endY = 0;
  var lamps = [];                           // [{pts: Float64Array, n, len, curveLen}]
  var pulses = [];
  var stars = [];
  var raf = 0, last = 0, dirty = true, alive = true, resizeTimer = 0;

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

  // ------------------------------------------------------------------ geometry
  // Sample layout per point (stride 6): x, y, nx, ny, depth k (0 far … 1 near), arc s
  var STRIDE = 6;

  function bez(p0, p1, p2, p3, t) {
    var u = 1 - t;
    return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3;
  }

  function buildLamp(side, gx, drop) {
    var x0 = vp.x, y0 = vp.y, gX = side < 0 ? gx : W - gx, gY = vp.y + drop;
    var dx = gX - x0;
    // a long, almost level wing out of the source that only turns down inside the gutter,
    // so the lines never cross the page content
    var c1x = x0 + dx * 0.62, c1y = y0 + drop * 0.05, c2x = gX, c2y = y0 + drop * 0.32;
    var raw = [], i, t, x, y;
    var CURVE = 72;
    for (i = 0; i <= CURVE; i++) {
      t = i / CURVE;
      raw.push(bez(x0, c1x, c2x, gX, t), bez(y0, c1y, c2y, gY, t));
    }
    var curveCount = raw.length / 2;
    var step = 36;
    for (y = gY + step; y < endY; y += step) raw.push(gX, y);
    if (endY > gY) raw.push(gX, endY);

    var n = raw.length / 2, pts = new Float64Array(n * STRIDE), s = 0, curveLen = 0;
    for (i = 0; i < n; i++) {
      x = raw[i * 2]; y = raw[i * 2 + 1];
      if (i > 0) s += Math.hypot(x - raw[i * 2 - 2], y - raw[i * 2 - 1]);
      if (i === curveCount - 1) curveLen = s;
      var a = Math.max(0, i - 1), b = Math.min(n - 1, i + 1);
      var tx = raw[b * 2] - raw[a * 2], ty = raw[b * 2 + 1] - raw[a * 2 + 1], tl = Math.hypot(tx, ty) || 1;
      var o = i * STRIDE;
      pts[o] = x; pts[o + 1] = y; pts[o + 2] = -ty / tl; pts[o + 3] = tx / tl; pts[o + 5] = s;
    }
    for (i = 0; i < n; i++) {
      var f = curveLen > 0 ? Math.min(1, pts[i * STRIDE + 5] / curveLen) : 1;
      pts[i * STRIDE + 4] = Math.pow(f, 1.7);
    }
    return { pts: pts, n: n, len: s, curveLen: curveLen };
  }

  function build() {
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    W = canvas.clientWidth || window.innerWidth;
    H = canvas.clientHeight || window.innerHeight;
    var pw = Math.round(W * dpr), ph = Math.round(H * dpr);
    if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }

    var sy = window.pageYOffset || root.scrollTop || 0;
    var mainEl = document.querySelector(".main") || document.body;
    var wrapLeft = Math.max(0, mainEl.getBoundingClientRect().left);
    var gx = clamp(wrapLeft * 0.5, 7, 90);
    var footer = document.querySelector(".footer");
    endY = footer ? footer.getBoundingClientRect().top + sy : root.scrollHeight;

    var drop;
    if (anchor) {
      var r = anchor.getBoundingClientRect();
      vp.x = W / 2; vp.y = r.top + sy + r.height * 0.42;
      drop = clamp(H * 0.12, 64, 128);
    } else {
      vp.x = W / 2; vp.y = -H * 0.9; drop = H * 0.9 - 40;   // source far above: lines enter from the top
    }
    lamps = [buildLamp(-1, gx, drop), buildLamp(1, gx, drop)];

    // pulses: 3 per lamp, staggered
    pulses = [];
    for (var l = 0; l < 2; l++) for (var p = 0; p < 3; p++) pulses.push({ lamp: l, s: -p * 700 - Math.random() * 300 - l * 230 });

    // stars, density by area
    var count = Math.round(clamp(W * H / 7000, 70, 260));
    stars = [];
    for (var i = 0; i < count; i++) stars.push(spawn({}, true));
    dirty = true;
  }

  function spawn(st, anyDepth) {
    st.x = (Math.random() * 2 - 1) * 1.15; st.y = (Math.random() * 2 - 1) * 1.15;
    st.z = anyDepth ? 0.08 + Math.random() * 0.92 : 1;
    st.w = Math.random() < 0.18;            // warm-tinted
    st.tw = Math.random() * 6.28;
    return st;
  }

  // arc-length lookup (binary search) → writes into out [x, y, nx, ny, k]
  var tmp = [0, 0, 0, 0, 0];
  function pointAt(lamp, s, out) {
    var pts = lamp.pts, lo = 0, hi = lamp.n - 1;
    if (s <= 0) { lo = hi = 0; }
    else if (s >= lamp.len) { lo = hi = lamp.n - 1; }
    else {
      while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (pts[mid * STRIDE + 5] < s) lo = mid; else hi = mid; }
    }
    var a = lo * STRIDE, b = hi * STRIDE, span = pts[b + 5] - pts[a + 5];
    var f = span > 0 ? (s - pts[a + 5]) / span : 0;
    for (var j = 0; j < 5; j++) out[j] = pts[a + j] + (pts[b + j] - pts[a + j]) * f;
    return out;
  }

  function gapAt(k) { return GAP_FAR + (GAP_NEAR - GAP_FAR) * k; }

  // ------------------------------------------------------------------ drawing
  function drawLamp(lamp) {
    var pts = lamp.pts, n = lamp.n, top = scrollY - 30, bottom = scrollY + H + 30;
    for (var line = -1; line <= 1; line += 2) {
      var open = false, key = -1;
      for (var i = 1; i < n; i++) {
        var a = (i - 1) * STRIDE, b = i * STRIDE;
        var ya = pts[a + 1], yb = pts[b + 1];
        var visible = !((ya < top && yb < top) || (ya > bottom && yb > bottom));
        if (!visible) { if (open) { flush(key); open = false; } continue; }
        var k = pts[b + 4];
        var fade = clamp((endY - yb) / 260, 0, 1);
        var nk = Math.round(k * 10) * 16 + Math.round(fade * 15);
        if (!open || nk !== key) {
          if (open) flush(key);
          key = nk; open = true;
          var ga = gapAt(pts[a + 4]) * 0.5 * line;
          ctx.beginPath(); ctx.moveTo(pts[a] + pts[a + 2] * ga, pts[a + 1] - scrollY + pts[a + 3] * ga);
        }
        var gb = gapAt(k) * 0.5 * line;
        ctx.lineTo(pts[b] + pts[b + 2] * gb, yb - scrollY + pts[b + 3] * gb);
      }
      if (open) flush(key);
    }
  }
  // stroke the current path: glow pass + core pass, styled by depth and end fade
  function flush(key) {
    var k = Math.floor(key / 16) / 10, fade = (key % 16) / 15;
    var alpha = (0.12 + 0.7 * k) * fade;
    if (alpha <= 0.005) return;
    ctx.strokeStyle = "rgba(120,185,255," + (alpha * 0.16).toFixed(3) + ")";
    ctx.lineWidth = 1.5 + 6 * k;
    ctx.stroke();
    ctx.strokeStyle = "rgba(232,243,255," + alpha.toFixed(3) + ")";
    ctx.lineWidth = 0.35 + 0.95 * k;
    ctx.stroke();
  }

  function drawPulse(p) {
    var lamp = lamps[p.lamp];
    if (p.s <= 0 || p.s - 10 > lamp.len) return;
    var head = pointAt(lamp, Math.min(p.s, lamp.len), tmp), k = head[4];
    var tail = 30 + 150 * k, steps = 7, segLen = tail / steps;
    for (var line = -1; line <= 1; line += 2) {
      var px = 0, py = 0, has = false;
      for (var j = 0; j <= steps; j++) {
        var s = p.s - tail + j * segLen;
        if (s < 0 || s > lamp.len) continue;
        var q = pointAt(lamp, s, tmp), g = gapAt(q[4]) * 0.5 * line;
        var x = q[0] + q[2] * g, y = q[1] - scrollY + q[3] * g;
        if (has) {
          var a = (j / steps) * (0.25 + 0.75 * q[4]) * clamp((endY - q[1]) / 260, 0, 1);
          ctx.strokeStyle = "rgba(150,205,255," + (a * 0.35).toFixed(3) + ")";
          ctx.lineWidth = 3 + 8 * q[4];
          ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(x, y); ctx.stroke();
          ctx.strokeStyle = "rgba(255,255,255," + a.toFixed(3) + ")";
          ctx.lineWidth = 0.8 + 1.6 * q[4];
          ctx.stroke();
        }
        px = x; py = y; has = true;
      }
    }
  }

  function drawSource(sx, sy, t) {
    if (sy < -80 || sy > H + 80) return;
    var pulse = reduced ? 1 : 0.85 + 0.15 * Math.sin(t / 900);
    var g = ctx.createRadialGradient(sx, sy, 0, sx, sy, 140);
    g.addColorStop(0, "rgba(200,225,255," + (0.22 * pulse).toFixed(3) + ")");
    g.addColorStop(0.35, "rgba(110,160,255,0.06)");
    g.addColorStop(1, "rgba(110,160,255,0)");
    ctx.fillStyle = g; ctx.fillRect(sx - 140, sy - 140, 280, 280);
    // horizontal lens flare + core
    var hw = Math.min(220, W * 0.22);
    var fl = ctx.createLinearGradient(sx - hw, 0, sx + hw, 0);
    fl.addColorStop(0, "rgba(180,215,255,0)"); fl.addColorStop(0.5, "rgba(225,240,255," + (0.55 * pulse).toFixed(3) + ")"); fl.addColorStop(1, "rgba(180,215,255,0)");
    ctx.fillStyle = fl; ctx.fillRect(sx - hw, sy - 0.5, hw * 2, 1);
    ctx.fillStyle = "rgba(255,255,255," + (0.9 * pulse).toFixed(3) + ")";
    ctx.beginPath(); ctx.arc(sx, sy, 1.8, 0, 6.2832); ctx.fill();
  }

  function frame(now) {
    raf = 0;
    if (!alive) return;
    var dt = last ? Math.min(0.05, (now - last) / 1000) : 0.016;
    last = now;
    var hidden = root.classList.contains("intro-pending");
    if (!hidden && (dirty || !reduced)) render(now, reduced ? 0 : dt);
    if (!reduced || hidden) raf = requestAnimationFrame(frame);   // reduced: loop only until the intro is gone
  }

  function render(now, dt) {
    dirty = false;
    scrollY = window.pageYOffset || root.scrollTop || 0;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    var sx = vp.x, sy = vp.y - scrollY;
    var cy = clamp(sy, -H * 0.25, H * 0.6), cx = W / 2, f = Math.max(W, H) * 0.5;

    // starfield streaming out of the source
    for (var i = 0; i < stars.length; i++) {
      var st = stars[i];
      if (dt) { st.z -= dt * 0.035; if (st.z < 0.06) spawn(st, false); }
      var px = cx + (st.x / st.z) * f, py = cy + (st.y / st.z) * f;
      if (px < -4 || px > W + 4 || py < -4 || py > H + 4) { if (dt) spawn(st, false); continue; }
      var depth = 1 - st.z, a = clamp(depth * 1.15, 0.05, 0.9);
      if (dt) a *= 0.8 + 0.2 * Math.sin(now / 700 + st.tw);
      var size = 0.4 + depth * 1.6;
      ctx.fillStyle = st.w ? "rgba(255,214,170," + a.toFixed(3) + ")" : "rgba(214,228,255," + a.toFixed(3) + ")";
      ctx.fillRect(px - size / 2, py - size / 2, size, size);
    }

    ctx.globalCompositeOperation = "lighter";
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    drawSource(sx, sy, now);
    drawLamp(lamps[0]); drawLamp(lamps[1]);
    if (!reduced) {
      for (var p = 0; p < pulses.length; p++) {
        var pu = pulses[p], lamp = lamps[pu.lamp];
        var k = pu.s > 0 ? pointAt(lamp, Math.min(pu.s, lamp.len), tmp)[4] : 0;
        pu.s += dt * (70 + 820 * k * k + (pu.s > lamp.curveLen ? 160 : 0));
        if (pu.s - 200 > lamp.len) pu.s = -Math.random() * 900;
        if (pu.s <= 0) pu.s += dt * 260;   // waiting in deep space before launch
        drawPulse(pu);
      }
    }
    ctx.globalCompositeOperation = "source-over";
  }

  // ------------------------------------------------------------------ lifecycle
  function kick() { if (!raf && alive) raf = requestAnimationFrame(frame); }
  function onScroll() { if (reduced) { dirty = true; kick(); } }
  function onResize() {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(function () { build(); kick(); }, 120);
  }
  function onVisibility() {
    if (document.hidden) { if (raf) cancelAnimationFrame(raf); raf = 0; }
    else { last = 0; dirty = true; kick(); }
  }

  try {
    build();
  } catch (e) {
    if (window.console) console.warn("cosmos disabled:", e);
    canvas.parentNode && canvas.parentNode.removeChild(canvas);
    return;
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onResize);
  document.addEventListener("visibilitychange", onVisibility);
  if (mq && mq.addEventListener) mq.addEventListener("change", function (e) { reduced = e.matches; dirty = true; kick(); });
  // layout changes (images loading, flashes closing) move the footer: rebuild geometry
  var ro = null;
  if (window.ResizeObserver) { ro = new ResizeObserver(onResize); ro.observe(document.body); }
  window.addEventListener("pagehide", function () {
    alive = false; if (raf) cancelAnimationFrame(raf); raf = 0;
    if (ro) ro.disconnect();
  });
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    alive = true; last = 0; build(); kick();
    if (ro) ro.observe(document.body);
  });
  kick();
})();
