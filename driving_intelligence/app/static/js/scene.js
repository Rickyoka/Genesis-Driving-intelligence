/* DIScene — realistic pseudo-3D night road for the HUD windshield (no libraries, no assets).
 *
 *   var scene = DIScene.create(canvas);
 *   scene.set({ speed: 55, curve: 0.6, cornerAt: 250, grade: -1 });  // km/h, -1..1, metres, -1/0/1
 *   scene.start(); scene.stop(); scene.destroy();
 *
 * Korean road conventions: right-hand traffic (camera in the right lane), double yellow centre line,
 * white edge lines, W-beam guardrails with reflectors. Lighting = headlight falloff + distance fog.
 * One rAF loop at most; ResizeObserver and loop are released by stop()/destroy() (no leaks).
 */
(function (global) {
  "use strict";

  var SEG = 4;            // metres per segment
  var N = 190;            // segments drawn (~760 m)
  var ROAD = 3.6;         // half road width (m)
  var SHOULDER = 5.0;
  var RAIL = 5.4;
  var CAM_H = 1.25;       // camera height (m)
  var CAM_X = 1.8;        // right lane centre
  var FOV = 72 * Math.PI / 180;
  var DEPTH = 1 / Math.tan(FOV / 2);
  var FOG = [9, 12, 19];

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function shade(rgb, light, fog) {
    var r = rgb[0] * light, g = rgb[1] * light, b = rgb[2] * light;
    r += (FOG[0] - r) * fog; g += (FOG[1] - g) * fog; b += (FOG[2] - b) * fog;
    return "rgb(" + (r | 0) + "," + (g | 0) + "," + (b | 0) + ")";
  }
  function rand(seed) { return function () { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }; }

  function create(canvas, opts) {
    opts = opts || {};
    var ctx = canvas.getContext("2d", { alpha: false });
    var st = { speed: 0, curve: 0, curveNow: 0, cornerAt: 9999, grade: 0, gradeNow: 0, pos: 0, sky: 0, track: !!opts.track,
               raf: 0, running: false, last: 0, W: 0, H: 0, dpr: 1 };
    var R = rand(opts.seed || 7);
    var stars = [];
    for (var i = 0; i < 140; i++) stars.push([R(), R() * 0.42, R() * 1.2 + 0.2, R()]);
    function ridge(n, amp, base) {
      var pts = [], h = 0;
      for (var k = 0; k <= n; k++) { h = clamp(h + (R() - 0.5) * amp, -amp * 2, amp * 2); pts.push(base - Math.abs(h) - R() * amp * 0.4); }
      return pts;
    }
    var far = ridge(64, 0.05, 0.0), near = ridge(48, 0.07, 0.0);
    var noise = makeNoise();

    function makeNoise() {
      var c = document.createElement("canvas"); c.width = c.height = 96;
      var g = c.getContext("2d"), img = g.createImageData(96, 96);
      for (var p = 0; p < img.data.length; p += 4) {
        var v = 128 + (R() - 0.5) * 60;
        img.data[p] = img.data[p + 1] = img.data[p + 2] = v; img.data[p + 3] = 22;
      }
      g.putImageData(img, 0, 0);
      return c;
    }

    function resize() {
      var r = canvas.getBoundingClientRect();
      st.dpr = Math.min(global.devicePixelRatio || 1, 2);
      st.W = Math.max(1, Math.round(r.width * st.dpr));
      st.H = Math.max(1, Math.round(r.height * st.dpr));
      if (canvas.width !== st.W || canvas.height !== st.H) { canvas.width = st.W; canvas.height = st.H; }
      if (!st.running) draw();
    }
    var ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    if (ro) ro.observe(canvas); else global.addEventListener("resize", resize);

    // ------------------------------------------------------------------ drawing
    function drawSky(W, H, horizon) {
      var g = ctx.createLinearGradient(0, 0, 0, horizon);
      g.addColorStop(0, "#03050a"); g.addColorStop(0.7, "#0b1120"); g.addColorStop(1, "#1a2335");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, horizon + 2);
      var mx = W * 0.78 - st.sky * W * 0.05, my = horizon * 0.32;
      var mg = ctx.createRadialGradient(mx, my, 0, mx, my, W * 0.12);
      mg.addColorStop(0, "rgba(220,228,255,.35)"); mg.addColorStop(0.08, "rgba(200,210,240,.18)"); mg.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = mg; ctx.fillRect(0, 0, W, horizon);
      ctx.fillStyle = "#e9eefc"; ctx.beginPath(); ctx.arc(mx, my, W * 0.008, 0, 6.283); ctx.fill();
      for (var i = 0; i < stars.length; i++) {
        var s = stars[i], x = ((s[0] * W - st.sky * W * 0.08) % W + W) % W;
        ctx.globalAlpha = 0.25 + s[3] * 0.6; ctx.fillStyle = "#cfd8ff";
        ctx.fillRect(x, s[1] * H, s[2] * st.dpr, s[2] * st.dpr);
      }
      ctx.globalAlpha = 1;
      layer(far, "#121826", 0.10, W, horizon, H * 1.3);
      layer(near, "#0b0f17", 0.22, W, horizon, H * 1.0);
      var hz = ctx.createLinearGradient(0, horizon - H * 0.05, 0, horizon + 2);
      hz.addColorStop(0, "rgba(40,52,74,0)"); hz.addColorStop(1, "rgba(40,52,74,.55)");
      ctx.fillStyle = hz; ctx.fillRect(0, horizon - H * 0.05, W, H * 0.05 + 2);
    }
    function layer(pts, color, par, W, horizon, scale) {
      // pts are <= 0 (height above horizon as a fraction); tiled twice for seamless parallax
      var n = pts.length - 1, span = W * 1.6, step = span / n, off = ((st.sky * W * par) % span + span) % span;
      ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(-off, horizon + 2);
      for (var rep = 0; rep < 2; rep++)
        for (var k = 0; k <= n; k++) ctx.lineTo(-off + rep * span + k * step, horizon + pts[k] * scale);
      ctx.lineTo(-off + 2 * span, horizon + 2); ctx.closePath(); ctx.fill();
    }

    function quad(x1, y1, w1, x2, y2, w2, color) {
      ctx.fillStyle = color; ctx.beginPath();
      ctx.moveTo(x1 - w1, y1); ctx.lineTo(x1 + w1, y1); ctx.lineTo(x2 + w2, y2); ctx.lineTo(x2 - w2, y2);
      ctx.closePath(); ctx.fill();
    }
    function band(p1, p2, a, b, color) { // lateral band between world offsets a..b
      var c1 = p1.X + (a + b) / 2 * p1.S, c2 = p2.X + (a + b) / 2 * p2.S;
      quad(c1, p1.Y, (b - a) / 2 * p1.S, c2, p2.Y, (b - a) / 2 * p2.S, color);
    }

    function draw() {
      var W = st.W, H = st.H;
      if (!W || !H) return;
      var horizon = H * 0.46, half = W / 2;
      drawSky(W, H, horizon);
      // base ground in fog colour: sub-pixel far segments are skipped below, so never leave rows unpainted
      ctx.fillStyle = "rgb(" + FOG.join(",") + ")";
      ctx.fillRect(0, horizon, W, H - horizon);

      var base = Math.floor(st.pos / SEG), frac = (st.pos % SEG) / SEG;
      var x = 0, dx = 0, P = [];
      for (var n = 0; n <= N; n++) {
        var d = (n - frac) * SEG;
        var z = Math.max(0.6, d + 1.5);
        var ramp = clamp((d - st.cornerAt) / 40, 0, 1);
        x += dx; dx += st.curveNow * 0.25 * ramp;
        var y = st.gradeNow * 0.045 * d - st.gradeNow * 0.00004 * d * d;
        var s = DEPTH / z * half;
        P.push({ X: half + (x - (st.track ? 0 : CAM_X)) * s, Y: horizon + (CAM_H - y) * s, S: s, z: z, n: base + n });
      }
      st.sky += (P[N].X - half) / W * 0.0004;

      var clipY = H;
      for (var k = N; k > 0; k--) {
        var p2 = P[k], p1 = P[k - 1];
        if (p2.Y >= p1.Y) continue;                  // hidden behind a crest (or sub-pixel)
        var light = clamp(1.25 - p1.z / 60, 0.07, 1);
        var retro = clamp(1.35 - p1.z / 170, 0.12, 1);   // retroreflective paint / steel stays visible further
        var fog = Math.pow(clamp(p1.z / (N * SEG * 0.85), 0, 1), 1.15);
        var alt = (p1.n >> 1) & 1;
        ctx.fillStyle = shade([24, 27, 24], light * 0.8, fog);
        ctx.fillRect(0, p2.Y, W, p1.Y - p2.Y + 1);                     // roadside / ground
        if (st.track) {                                               // circuit: wide track + kerbs
          var TW = 6.2, kerb = ((p1.n >> 1) & 1) ? [205, 30, 40] : [230, 230, 228];
          band(p1, p2, -TW - 3, TW + 3, shade([34, 46, 30], light, fog));             // run-off grass
          band(p1, p2, -TW, TW, shade(alt ? [52, 54, 58] : [47, 49, 53], light, fog));
          band(p1, p2, -TW - 0.9, -TW, shade(kerb, Math.max(light, retro * 0.6), fog * 0.8));
          band(p1, p2, TW, TW + 0.9, shade(kerb, Math.max(light, retro * 0.6), fog * 0.8));
          band(p1, p2, -TW + 0.15, -TW + 0.3, shade([225, 225, 220], retro, fog * 0.7));
          band(p1, p2, TW - 0.3, TW - 0.15, shade([225, 225, 220], retro, fog * 0.7));
        } else {
          band(p1, p2, -SHOULDER, SHOULDER, shade([46, 46, 46], light, fog));   // gravel shoulder
          band(p1, p2, -ROAD, ROAD, shade(alt ? [44, 46, 50] : [39, 41, 45], light, fog)); // asphalt
          band(p1, p2, -ROAD + 0.1, -ROAD + 0.25, shade([215, 215, 210], retro, fog * 0.7));  // edge lines
          band(p1, p2, ROAD - 0.25, ROAD - 0.1, shade([215, 215, 210], retro, fog * 0.7));
          band(p1, p2, -0.2, -0.08, shade([236, 176, 52], retro, fog * 0.7));                 // double yellow
          band(p1, p2, 0.08, 0.2, shade([236, 176, 52], retro, fog * 0.7));
        }
        var steel = shade([128, 134, 140], Math.max(light * 0.9, retro * 0.55), fog * 0.8);
        for (var side = -1; side <= 1; side += 2) {                    // W-beam guardrail
          var gx = side * (st.track ? 10.5 : RAIL);
          var a1 = p1.X + gx * p1.S, a2 = p2.X + gx * p2.S;
          var h1a = 0.55 * p1.S, h1b = 0.78 * p1.S, h2a = 0.55 * p2.S, h2b = 0.78 * p2.S;
          ctx.fillStyle = steel; ctx.beginPath();
          ctx.moveTo(a1, p1.Y - h1a); ctx.lineTo(a1, p1.Y - h1b); ctx.lineTo(a2, p2.Y - h2b); ctx.lineTo(a2, p2.Y - h2a);
          ctx.closePath(); ctx.fill();
          if (p1.n % 6 === 0) {
            var pw = Math.max(1, 0.12 * p1.S);
            ctx.fillStyle = shade([70, 74, 78], light, fog);
            ctx.fillRect(a1 - pw / 2, p1.Y - 0.8 * p1.S, pw, 0.8 * p1.S);
            var rb = clamp(1.4 - p1.z / 140, 0, 1);                      // reflectors catch headlights
            if (rb > 0.02) {
              ctx.fillStyle = side < 0 ? "rgba(255,170,60," + rb + ")" : "rgba(235,240,255," + rb + ")";
              var rs = Math.max(1.2, 0.1 * p1.S);
              ctx.fillRect(a1 - rs / 2, p1.Y - 0.72 * p1.S, rs, rs);
            }
          }
        }
        clipY = Math.min(clipY, p2.Y);
      }

      // asphalt grain + headlight pool
      ctx.save();
      ctx.globalAlpha = 0.6; ctx.fillStyle = ctx.createPattern(noise, "repeat");
      ctx.translate(0, (st.pos * 40) % 96); ctx.fillRect(0, horizon - 96, W, H - horizon + 192);
      ctx.restore();
      var hl = ctx.createRadialGradient(half, H * 1.02, H * 0.05, half, H * 0.9, W * 0.55);
      hl.addColorStop(0, "rgba(255,238,205,.20)"); hl.addColorStop(0.5, "rgba(255,238,205,.07)"); hl.addColorStop(1, "rgba(255,238,205,0)");
      ctx.fillStyle = hl; ctx.fillRect(0, horizon, W, H - horizon);

      drawCabin(W, H);
    }

    function drawCabin(W, H) {
      var v = ctx.createRadialGradient(W / 2, H * 0.55, H * 0.35, W / 2, H * 0.55, W * 0.75);
      v.addColorStop(0, "rgba(0,0,0,0)"); v.addColorStop(1, "rgba(0,0,0,.65)");
      ctx.fillStyle = v; ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = "rgba(255,255,255,.025)";                        // glass reflection streak
      ctx.beginPath(); ctx.moveTo(W * 0.18, 0); ctx.lineTo(W * 0.30, 0); ctx.lineTo(W * 0.12, H * 0.7); ctx.lineTo(W * 0.02, H * 0.7); ctx.fill();
      var pl = ctx.createLinearGradient(0, 0, W * 0.09, 0);             // A-pillars
      pl.addColorStop(0, "#020203"); pl.addColorStop(1, "rgba(2,2,3,0)");
      ctx.fillStyle = pl; ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(W * 0.1, 0); ctx.lineTo(W * 0.03, H); ctx.lineTo(0, H); ctx.fill();
      ctx.save(); ctx.translate(W, 0); ctx.scale(-1, 1); ctx.fillStyle = pl;
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(W * 0.1, 0); ctx.lineTo(W * 0.03, H); ctx.lineTo(0, H); ctx.fill(); ctx.restore();
      var top = H * 0.86;                                               // dashboard edge
      var dg = ctx.createLinearGradient(0, top - H * 0.04, 0, H);
      dg.addColorStop(0, "#15120f"); dg.addColorStop(0.25, "#0c0b0a"); dg.addColorStop(1, "#050505");
      ctx.fillStyle = dg; ctx.beginPath();
      ctx.moveTo(0, H); ctx.lineTo(0, top + H * 0.02);
      ctx.quadraticCurveTo(W / 2, top - H * 0.05, W, top + H * 0.02); ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = "rgba(196,154,108,.45)"; ctx.lineWidth = Math.max(1, st.dpr); ctx.setLineDash([6 * st.dpr, 5 * st.dpr]);
      ctx.beginPath(); ctx.moveTo(0, top + H * 0.045); ctx.quadraticCurveTo(W / 2, top - H * 0.025, W, top + H * 0.045); ctx.stroke();
      ctx.setLineDash([]);
    }

    // ------------------------------------------------------------------ loop
    function frame(t) {
      if (!st.running) return;
      var dt = Math.min(0.05, (t - st.last) / 1000); st.last = t;
      var mps = st.speed / 3.6;
      st.pos += mps * dt;
      if (st.cornerAt > 0) st.cornerAt = Math.max(0, st.cornerAt - mps * dt);
      st.curveNow += (st.curve - st.curveNow) * Math.min(1, dt * 2.5);
      st.gradeNow += (st.grade - st.gradeNow) * Math.min(1, dt * 1.5);
      draw();
      st.raf = global.requestAnimationFrame(frame);
    }

    resize();
    return {
      set: function (o) {
        if (o.speed !== undefined) st.speed = clamp(+o.speed || 0, 0, 900);  // may be time-compressed
        if (o.curve !== undefined) st.curve = clamp(+o.curve || 0, -1, 1);
        if (o.cornerAt !== undefined) st.cornerAt = Math.max(0, +o.cornerAt || 0);
        if (o.grade !== undefined) st.grade = clamp(+o.grade || 0, -1, 1);
        if (!st.running) { st.curveNow = st.curve; st.gradeNow = st.grade; draw(); }
      },
      start: function () { if (st.running) return; st.running = true; st.last = global.performance.now(); st.raf = global.requestAnimationFrame(frame); },
      stop: function () { st.running = false; if (st.raf) global.cancelAnimationFrame(st.raf); st.raf = 0; },
      destroy: function () { this.stop(); if (ro) ro.disconnect(); else global.removeEventListener("resize", resize); },
      draw: draw
    };
  }

  global.DIScene = { create: create };
})(window);
