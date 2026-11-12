/* Cinematic engine-start intro (v2).
 *
 * Timeline after the START/STOP press (ms) — sound and picture share these numbers:
 *     0  click + button press, shockwave, light blade begins
 *   250  instrument cluster draws on, SYSTEM CHECK, needle self-test sweep (250–850)
 *   900  ICE: ignition catch → rev flare (peak 1320) → overrun crackle → idle (2600+)
 *        EV : power-up swell → READY
 *  1400  vehicle reveal + light sweep across the body
 *  2300  title letters track in
 *  4300  (ICE) / 3800 (EV) zoom-through exit to the page
 *
 * - Audio is synthesised with Web Audio (no files). Browsers only allow sound after a
 *   gesture, which the push-button start provides.
 * - The RPM needle follows the same rpm curve that drives the synthesised engine.
 * - One rAF loop; AudioContext closed after playback; listeners removed on finish (no leaks).
 * - prefers-reduced-motion: static READY state, short hold, no particles.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var SOUND_KEY = "di_sound";
  var TL = { checkStart: 250, sweepUp: 550, sweepEnd: 850, catchAt: 900, peak: 1320, settle: 2000, idle: 2600,
             reveal: 1400, title: 2300, exitIce: 4300, exitEv: 3800, exitDur: 950 };

  function sget(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function sset(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
  function markSeen() { try { window.sessionStorage.setItem("di_intro_seen", "1"); } catch (e) { /* ignore */ } }
  function soundOn() { return sget(SOUND_KEY) !== "off"; }
  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function easeOut(x) { return 1 - Math.pow(1 - x, 3); }
  function easeInOut(x) { return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function seg(t, a, b) { return clamp((t - a) / (b - a), 0, 1); }

  // ======================================================================= engine rpm model
  function rpmAt(t) {
    if (t < TL.catchAt) return 250 + 70 * Math.sin(t / 1000 * Math.PI * 18);              // cranking
    if (t < 1000) return 250 + 1150 * seg(t, TL.catchAt, 1000);                            // catch
    if (t < TL.peak) return 1400 + 3000 * easeOut(seg(t, 1000, TL.peak));                   // flare
    if (t < TL.settle) return 4400 - 3300 * easeInOut(seg(t, TL.peak, TL.settle));          // fall
    if (t < TL.idle) return 1100 - 280 * easeOut(seg(t, TL.settle, TL.idle));               // settle
    return 820 + 22 * Math.sin(t * 0.011) + 10 * Math.sin(t * 0.037);                       // idle lope
  }
  function selfTest(t, lag) {
    var a = TL.checkStart + lag, m = TL.sweepUp + lag, e = TL.sweepEnd + lag;
    if (t < a) return 0;
    if (t < m) return easeOut(seg(t, a, m));
    if (t < e) return 1 - easeInOut(seg(t, m, e));
    return null;
  }

  // ======================================================================= audio
  function noiseBuffer(ctx, sec) {
    var len = Math.max(1, Math.floor(ctx.sampleRate * sec)), buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
    for (var i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    return buf;
  }
  function noise(ctx, sec) { var s = ctx.createBufferSource(); s.buffer = noiseBuffer(ctx, sec); return s; }
  function impulse(ctx, sec, decay) {
    var len = Math.floor(ctx.sampleRate * sec), buf = ctx.createBuffer(2, len, ctx.sampleRate);
    for (var c = 0; c < 2; c++) {
      var d = buf.getChannelData(c);
      for (var i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, decay);
    }
    return buf;
  }
  function shaperCurve(k) {
    var n = 2048, c = new Float32Array(n);
    for (var i = 0; i < n; i++) { var x = i * 2 / n - 1; c[i] = (1 + k) * x / (1 + k * Math.abs(x)); }
    return c;
  }

  function click(ctx, out, t) {
    var n = noise(ctx, 0.05), bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = 3200; bp.Q.value = 2;
    var g = ctx.createGain(); g.gain.setValueAtTime(0.5, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.045);
    n.connect(bp).connect(g).connect(out); n.start(t); n.stop(t + 0.06);
    var o = ctx.createOscillator(); o.frequency.setValueAtTime(140, t); o.frequency.exponentialRampToValueAtTime(60, t + 0.08);
    var og = ctx.createGain(); og.gain.setValueAtTime(0.35, t); og.gain.exponentialRampToValueAtTime(0.001, t + 0.1);
    o.connect(og).connect(out); o.start(t); o.stop(t + 0.12);
  }

  function engine(ctx, out, t0) {
    var s = function (ms) { return t0 + ms / 1000; }, nodes = [], timed = [];  // timed: [bufferSource, startMs]
    // starter motor: chopped band-passed noise + whine
    var st = noise(ctx, 1.0), bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = 480; bp.Q.value = 1.4;
    var chop = ctx.createGain(); chop.gain.value = 0.3;
    var lfo = ctx.createOscillator(); lfo.type = "square"; lfo.frequency.value = 9.5;
    var lg = ctx.createGain(); lg.gain.value = 0.26; lfo.connect(lg).connect(chop.gain);
    var se = ctx.createGain(); se.gain.setValueAtTime(0, s(0)); se.gain.linearRampToValueAtTime(2.6, s(60));
    se.gain.setValueAtTime(2.6, s(820)); se.gain.linearRampToValueAtTime(0, s(940));
    st.connect(bp).connect(chop).connect(se).connect(out);
    var wh = ctx.createOscillator(); wh.type = "sawtooth"; wh.frequency.setValueAtTime(150, s(0)); wh.frequency.linearRampToValueAtTime(210, s(880));
    var wg = ctx.createGain(); wg.gain.setValueAtTime(0.06, s(0)); wg.gain.linearRampToValueAtTime(0, s(940));
    wh.connect(wg).connect(out);
    nodes.push(lfo, wh); timed.push([st, 0]);

    // combustion: firing frequency = rpm/60 * 3 (V6). Saws + sub square -> waveshaper -> tracking low-pass
    var shaper = ctx.createWaveShaper(); shaper.curve = shaperCurve(18); shaper.oversample = "4x";
    var lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.Q.value = 3;
    var body = ctx.createGain();
    var env = ctx.createGain(); env.gain.setValueAtTime(0, t0); env.gain.setValueAtTime(0, s(TL.catchAt));
    env.gain.linearRampToValueAtTime(0.5, s(TL.catchAt + 60)); env.gain.setValueAtTime(0.5, s(TL.peak));
    env.gain.linearRampToValueAtTime(0.32, s(TL.settle)); env.gain.setValueAtTime(0.3, s(TL.exitIce - 300));
    env.gain.linearRampToValueAtTime(0, s(TL.exitIce + 500));
    function f(rpm) { return rpm / 60 * 3; }
    var keys = [[TL.catchAt, 250], [1000, 1400], [1160, 3300], [TL.peak, 4400], [1600, 2200], [TL.settle, 1100], [TL.idle, 820], [TL.exitIce + 500, 810]];
    [[0, "sawtooth", 1], [8, "sawtooth", 1], [0, "square", 0.5]].forEach(function (spec) {
      var o = ctx.createOscillator(); o.type = spec[1]; o.detune.value = spec[0];
      var g = ctx.createGain(); g.gain.value = spec[1] === "square" ? 0.35 : 0.5;
      o.frequency.setValueAtTime(f(keys[0][1]) * spec[2], t0);
      o.frequency.setValueAtTime(f(keys[0][1]) * spec[2], s(keys[0][0]));
      for (var k = 1; k < keys.length; k++) o.frequency.exponentialRampToValueAtTime(f(keys[k][1]) * spec[2], s(keys[k][0]));
      o.connect(g).connect(shaper); nodes.push(o);
    });
    lp.frequency.setValueAtTime(380, t0); lp.frequency.setValueAtTime(380, s(TL.catchAt)); lp.frequency.exponentialRampToValueAtTime(2600, s(TL.peak));
    lp.frequency.exponentialRampToValueAtTime(700, s(TL.settle)); lp.frequency.exponentialRampToValueAtTime(520, s(TL.idle));
    var lope = ctx.createOscillator(); lope.frequency.value = 6.5; var ld = ctx.createGain(); ld.gain.value = 0.16;
    lope.connect(ld).connect(body.gain); nodes.push(lope);
    shaper.connect(lp).connect(body).connect(env).connect(out);

    // catch: sub thump + burst
    var th = ctx.createOscillator(); th.frequency.setValueAtTime(70, t0); th.frequency.setValueAtTime(70, s(TL.catchAt)); th.frequency.exponentialRampToValueAtTime(38, s(TL.catchAt + 260));
    var tg = ctx.createGain(); tg.gain.setValueAtTime(0, t0); tg.gain.setValueAtTime(0, s(TL.catchAt)); tg.gain.linearRampToValueAtTime(0.7, s(TL.catchAt + 15));
    tg.gain.exponentialRampToValueAtTime(0.001, s(TL.catchAt + 320)); th.connect(tg).connect(out); nodes.push(th);
    var cb = noise(ctx, 0.3), clp = ctx.createBiquadFilter(); clp.type = "lowpass"; clp.frequency.value = 1100;
    var cg = ctx.createGain(); cg.gain.setValueAtTime(0, s(TL.catchAt)); cg.gain.linearRampToValueAtTime(0.45, s(TL.catchAt + 12));
    cg.gain.exponentialRampToValueAtTime(0.001, s(TL.catchAt + 260)); cb.connect(clp).connect(cg).connect(out); timed.push([cb, TL.catchAt]);

    // overrun crackle after the flare
    [1440, 1530, 1585, 1700, 1810].forEach(function (ms, i) {
      var p = noise(ctx, 0.06), pb = ctx.createBiquadFilter(); pb.type = "bandpass"; pb.frequency.value = 900 + i * 260; pb.Q.value = 1.2;
      var pg = ctx.createGain(); pg.gain.setValueAtTime(0, s(ms)); pg.gain.linearRampToValueAtTime(0.32 - i * 0.04, s(ms + 4));
      pg.gain.exponentialRampToValueAtTime(0.001, s(ms + 55)); p.connect(pb).connect(pg).connect(out); timed.push([p, ms]);
    });
    var end = s(TL.exitIce + 600);
    nodes.forEach(function (n) { n.start(t0); n.stop(end); });
    timed.forEach(function (p) { p[0].start(s(p[1])); });   // buffer sources stop on their own
    return (end - ctx.currentTime) * 1000;
  }

  function evPower(ctx, out, t0) {
    var s = function (ms) { return t0 + ms / 1000; }, nodes = [];
    var lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.Q.value = 6;
    lp.frequency.setValueAtTime(260, s(0)); lp.frequency.exponentialRampToValueAtTime(2800, s(1300)); lp.frequency.exponentialRampToValueAtTime(900, s(3200));
    var pad = ctx.createGain(); pad.gain.setValueAtTime(0, s(0)); pad.gain.linearRampToValueAtTime(0.22, s(900));
    pad.gain.linearRampToValueAtTime(0.14, s(2200)); pad.gain.linearRampToValueAtTime(0, s(TL.exitEv + 400));
    [110, 164.81, 220, 277.18].forEach(function (hz, i) {
      [-7, 7].forEach(function (dt) {
        var o = ctx.createOscillator(); o.type = "sawtooth"; o.frequency.value = hz; o.detune.value = dt + i;
        var g = ctx.createGain(); g.gain.value = 0.12; o.connect(g).connect(lp); nodes.push(o);
      });
    });
    lp.connect(pad).connect(out);
    var tb = ctx.createOscillator(); tb.type = "sine"; tb.frequency.setValueAtTime(110, t0); tb.frequency.exponentialRampToValueAtTime(980, s(1500));
    var tg = ctx.createGain(); tg.gain.setValueAtTime(0, t0); tg.gain.setValueAtTime(0, s(100)); tg.gain.linearRampToValueAtTime(0.07, s(700)); tg.gain.linearRampToValueAtTime(0, s(1700));
    tb.connect(tg).connect(out); nodes.push(tb);
    var sub = ctx.createOscillator(); sub.frequency.setValueAtTime(42, s(0)); sub.frequency.linearRampToValueAtTime(55, s(1200));
    var sg = ctx.createGain(); sg.gain.setValueAtTime(0, s(0)); sg.gain.linearRampToValueAtTime(0.4, s(800)); sg.gain.linearRampToValueAtTime(0, s(2600));
    sub.connect(sg).connect(out); nodes.push(sub);
    var air = noise(ctx, 2.0), ab = ctx.createBiquadFilter(); ab.type = "bandpass"; ab.Q.value = 0.9;
    ab.frequency.setValueAtTime(400, s(0)); ab.frequency.exponentialRampToValueAtTime(4200, s(1300));
    var ag = ctx.createGain(); ag.gain.setValueAtTime(0, s(0)); ag.gain.linearRampToValueAtTime(0.09, s(900)); ag.gain.linearRampToValueAtTime(0, s(1600));
    air.connect(ab).connect(ag).connect(out); nodes.push(air);
    [[659.25, 1150], [987.77, 1330], [1318.5, 1510]].forEach(function (n) {
      var o = ctx.createOscillator(); o.frequency.value = n[0]; var g = ctx.createGain();
      g.gain.setValueAtTime(0, t0); g.gain.setValueAtTime(0, s(n[1])); g.gain.linearRampToValueAtTime(0.13, s(n[1] + 20)); g.gain.exponentialRampToValueAtTime(0.001, s(n[1] + 1600));
      o.connect(g).connect(out); nodes.push(o);
    });
    var end = s(TL.exitEv + 600);
    nodes.forEach(function (n) { n.start(t0); n.stop(end); });
    return (end - ctx.currentTime) * 1000;
  }

  function playSound(powertrain) {
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC || !soundOn()) return null;
    var ctx;
    try {
      ctx = new AC();
      var master = ctx.createGain(); master.gain.value = 0.75;
      var comp = ctx.createDynamicsCompressor(); comp.threshold.value = -14; comp.ratio.value = 4;
      var dry = ctx.createGain(), wet = ctx.createGain(); wet.gain.value = 0.2;
      var verb = ctx.createConvolver(); verb.buffer = impulse(ctx, 1.8, 2.6);
      dry.connect(comp); dry.connect(verb); verb.connect(wet).connect(comp);
      comp.connect(master).connect(ctx.destination);
      var t0 = ctx.currentTime + 0.04;
      click(ctx, dry, t0);
      var ms = powertrain === "ev" ? evPower(ctx, dry, t0) : engine(ctx, dry, t0);
      var timer = window.setTimeout(function () { ctx.close().catch(function () {}); }, ms + 2200);
      return { stop: function () { window.clearTimeout(timer); ctx.close().catch(function () {}); } };
    } catch (err) {
      console.warn("intro audio unavailable", err);
      if (ctx) ctx.close().catch(function () {});
      return null;
    }
  }

  // ======================================================================= visuals (canvas)
  function Renderer(canvas, opts) {
    var ctx = canvas.getContext("2d");
    var W = 0, H = 0, dpr = 1;
    var parts = [], grain = makeGrain();
    for (var i = 0; i < 80; i++) parts.push({ x: Math.random(), y: Math.random(), z: Math.random(), vx: (Math.random() - 0.5) * 0.004, vy: -0.002 - Math.random() * 0.004 });

    function makeGrain() {
      var c = document.createElement("canvas"); c.width = c.height = 128;
      var g = c.getContext("2d"), img = g.createImageData(128, 128);
      for (var p = 0; p < img.data.length; p += 4) { var v = Math.random() * 255; img.data[p] = img.data[p + 1] = img.data[p + 2] = v; img.data[p + 3] = 14; }
      g.putImageData(img, 0, 0); return c;
    }
    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = Math.round(canvas.clientWidth * dpr); H = Math.round(canvas.clientHeight * dpr);
      if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
    }
    // Landscape: [gauge] [panel] [gauge] on one line.
    // Portrait: the two gauges sit side by side and the panel moves underneath,
    // so the cluster never overflows a narrow phone screen.
    function layout() {
      if (W < H * 0.9) {
        var pr = Math.min(W * 0.215, H * 0.13), pcy = H * 0.38;
        return { r: pr, cy: pcy, lx: W / 2 - pr * 1.1, rx: W / 2 + pr * 1.1, blade: pcy + pr * 2.3, portrait: true };
      }
      var r = Math.min(W * 0.14, H * 0.19, 240 * dpr);
      return { r: r, cy: H * 0.42, lx: W / 2 - r * 1.78, rx: W / 2 + r * 1.78, blade: H * 0.585, portrait: false };
    }

    function gauge(cx, cy, r, prog, val, o) {
      if (prog <= 0) return;
      var a0 = Math.PI * 0.75, sweep = Math.PI * 1.5, aN = a0 + sweep * prog;
      ctx.save(); ctx.globalAlpha = prog;
      var face = ctx.createRadialGradient(cx, cy - r * 0.3, r * 0.1, cx, cy, r);
      face.addColorStop(0, "#14171d"); face.addColorStop(1, "#050608");
      ctx.fillStyle = face; ctx.beginPath(); ctx.arc(cx, cy, r * 0.98, 0, 6.2832); ctx.fill();
      var bz = ctx.createLinearGradient(cx, cy - r, cx, cy + r);
      bz.addColorStop(0, "rgba(220,214,204,.55)"); bz.addColorStop(0.5, "rgba(90,86,80,.35)"); bz.addColorStop(1, "rgba(30,30,32,.6)");
      ctx.strokeStyle = bz; ctx.lineWidth = r * 0.035; ctx.beginPath(); ctx.arc(cx, cy, r, 0, 6.2832); ctx.stroke();
      ctx.restore();
      ctx.save();
      ctx.lineCap = "round";
      ctx.strokeStyle = "rgba(255,255,255,.07)"; ctx.lineWidth = r * 0.05;
      ctx.beginPath(); ctx.arc(cx, cy, r * 0.85, a0, aN); ctx.stroke();
      if (o.red != null && prog > o.red) {
        ctx.strokeStyle = "rgba(255,106,31,.85)"; ctx.lineWidth = r * 0.025;
        ctx.beginPath(); ctx.arc(cx, cy, r * 0.93, a0 + sweep * o.red, aN); ctx.stroke();
      }
      var majors = o.majors, minor = 5;
      for (var k = 0; k <= majors * minor; k++) {
        var f = k / (majors * minor); if (f > prog) break;
        var a = a0 + sweep * f, major = k % minor === 0, red = o.red != null && f >= o.red;
        var r1 = r * (major ? 0.74 : 0.78), r2 = r * 0.84;
        ctx.strokeStyle = red ? "rgba(255,120,60,.95)" : (major ? "rgba(240,240,236,.85)" : "rgba(240,240,236,.32)");
        ctx.lineWidth = major ? r * 0.018 : r * 0.009;
        ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1); ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2); ctx.stroke();
        if (major) {
          ctx.fillStyle = red ? "rgba(255,140,80,.95)" : "rgba(230,230,226,.8)";
          ctx.font = "600 " + Math.round(Math.max(r * 0.105, 9 * dpr)) + "px -apple-system, 'Segoe UI', sans-serif";
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          var rn = r * 0.6; ctx.fillText(String(o.labels[k / minor]), cx + Math.cos(a) * rn, cy + Math.sin(a) * rn);
        }
      }
      if (prog >= 1) {
        var av = a0 + sweep * clamp(val, 0, 1);
        var tr = ctx.createLinearGradient(cx - r, cy, cx + r, cy);
        tr.addColorStop(0, "rgba(196,154,108,.15)"); tr.addColorStop(1, "rgba(255,106,31,.85)");
        ctx.strokeStyle = tr; ctx.lineWidth = r * 0.05; ctx.shadowColor = "rgba(255,120,50,.8)"; ctx.shadowBlur = r * 0.12;
        ctx.beginPath(); ctx.arc(cx, cy, r * 0.85, a0, av); ctx.stroke();
        ctx.shadowBlur = r * 0.1; ctx.strokeStyle = "#ffb27a"; ctx.lineWidth = r * 0.024;
        ctx.beginPath(); ctx.moveTo(cx - Math.cos(av) * r * 0.14, cy - Math.sin(av) * r * 0.14);
        ctx.lineTo(cx + Math.cos(av) * r * 0.8, cy + Math.sin(av) * r * 0.8); ctx.stroke();
        ctx.shadowBlur = 0;
        var hub = ctx.createRadialGradient(cx - r * 0.02, cy - r * 0.02, 0, cx, cy, r * 0.08);
        hub.addColorStop(0, "#d9d3c8"); hub.addColorStop(1, "#3b342c");
        ctx.fillStyle = hub; ctx.beginPath(); ctx.arc(cx, cy, r * 0.075, 0, 6.2832); ctx.fill();
        ctx.strokeStyle = "rgba(196,154,108,.9)"; ctx.lineWidth = r * 0.012; ctx.stroke();
      }
      ctx.globalAlpha = prog;
      ctx.fillStyle = "#f3efe8"; ctx.font = "300 " + Math.round(r * 0.2) + "px -apple-system, 'Segoe UI', sans-serif";
      ctx.textAlign = "center"; ctx.fillText(o.readout, cx, cy + r * 0.42);
      ctx.fillStyle = "rgba(200,196,188,.7)"; ctx.font = "600 " + Math.round(Math.max(r * 0.07, 8 * dpr)) + "px -apple-system, 'Segoe UI', sans-serif";
      ctx.fillText(o.label, cx, cy + r * 0.62);
      ctx.restore();
    }

    function panel(L, t, st) {
      var p = seg(t, 200, 520); if (p <= 0) return;
      var k = L.portrait ? 1.45 : 1;
      var w = L.portrait ? L.r * 2.6 : L.r * 1.25, h = L.portrait ? L.r * 0.98 : L.r * 1.05;
      var x = W / 2 - w / 2, y = L.portrait ? L.cy + L.r * 1.14 : L.cy - h / 2 - L.r * 0.05, rad = L.r * 0.08;
      ctx.save(); ctx.globalAlpha = easeOut(p);
      ctx.fillStyle = "rgba(14,16,22,.85)"; ctx.strokeStyle = "rgba(196,154,108,.35)"; ctx.lineWidth = Math.max(1, dpr);
      ctx.beginPath(); ctx.moveTo(x + rad, y); ctx.arcTo(x + w, y, x + w, y + h, rad); ctx.arcTo(x + w, y + h, x, y + h, rad);
      ctx.arcTo(x, y + h, x, y, rad); ctx.arcTo(x, y, x + w, y, rad); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      var cx = W / 2;
      if (t < st.readyAt) {
        ctx.fillStyle = "rgba(230,226,218,.85)"; ctx.font = "700 " + Math.round(L.r * 0.085 * k) + "px -apple-system, 'Segoe UI', sans-serif";
        ctx.fillText("SYSTEM CHECK", cx, y + h * 0.4);
        var bw = w * 0.62, bx = cx - bw / 2, by = y + h * 0.58, pr = seg(t, TL.checkStart, st.readyAt);
        ctx.fillStyle = "rgba(255,255,255,.1)"; ctx.fillRect(bx, by, bw, L.r * 0.02 * k);
        ctx.fillStyle = "#e3c49f"; ctx.fillRect(bx, by, bw * pr, L.r * 0.02 * k);
      } else {
        var q = easeOut(seg(t, st.readyAt, st.readyAt + 400));
        ctx.globalAlpha = q;
        ctx.fillStyle = "#3ddc97"; ctx.font = "800 " + Math.round(L.r * 0.11 * k) + "px -apple-system, 'Segoe UI', sans-serif";
        ctx.shadowColor = "rgba(61,220,151,.6)"; ctx.shadowBlur = L.r * 0.08;
        ctx.fillText(opts.ev ? "POWER ON" : "READY", cx, y + h * 0.36); ctx.shadowBlur = 0;
        ctx.fillStyle = "#f3efe8"; ctx.font = "600 " + Math.round(L.r * 0.085 * k) + "px -apple-system, 'Segoe UI', sans-serif";
        ctx.fillText(fit(opts.vehicle, w * 0.88, L.r * 0.085 * k), cx, y + h * 0.58);
        ctx.fillStyle = "rgba(200,196,188,.7)"; ctx.font = "500 " + Math.round(L.r * 0.06 * k) + "px -apple-system, 'Segoe UI', sans-serif";
        ctx.fillText(fit(opts.engine, w * 0.88, L.r * 0.06), cx, y + h * 0.75);
      }
      ctx.restore();
    }
    function fit(text, maxW, size) {
      if (!text) return "";
      var s = text; while (s.length > 3 && ctx.measureText(s).width > maxW) s = s.slice(0, -2);
      return s === text ? s : s + "…";
    }

    function draw(t, idle) {
      resize();
      var L = layout();
      ctx.clearRect(0, 0, W, H);
      var bg = ctx.createRadialGradient(W / 2, H * 0.55, 0, W / 2, H * 0.55, Math.max(W, H) * 0.75);
      bg.addColorStop(0, "#17130f"); bg.addColorStop(0.55, "#0a0908"); bg.addColorStop(1, "#030303");
      ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
      // dust motes
      if (!opts.reduced) {
        var boost = idle ? 1 : 1 + 4 * (1 - seg(t, 0, 900));
        for (var i = 0; i < parts.length; i++) {
          var p = parts[i];
          p.x += p.vx * 0.016 * boost * (0.5 + p.z); p.y += p.vy * 0.016 * boost * (0.5 + p.z);
          if (p.y < -0.05) { p.y = 1.05; p.x = Math.random(); } if (p.x < -0.05) p.x = 1.05; if (p.x > 1.05) p.x = -0.05;
          ctx.fillStyle = "rgba(227,196,159," + (0.08 + p.z * 0.25) + ")";
          ctx.beginPath(); ctx.arc(p.x * W, p.y * H, (0.6 + p.z * 1.8) * dpr, 0, 6.2832); ctx.fill();
        }
      }
      // light blade
      var bw = idle ? 0.08 + 0.02 * Math.sin(performance.now() / 600) : easeOut(seg(t, 120, 900));
      if (bw > 0) {
        var half = W * 0.46 * bw, by = L.blade, inten = idle ? 0.5 : (1 - 0.45 * seg(t, 1400, 2600));
        var g = ctx.createLinearGradient(W / 2 - half, 0, W / 2 + half, 0);
        g.addColorStop(0, "rgba(255,226,190,0)"); g.addColorStop(0.5, "rgba(255,236,210," + inten + ")"); g.addColorStop(1, "rgba(255,226,190,0)");
        ctx.save(); ctx.globalCompositeOperation = "lighter";
        // double light line (twin-line lamp motif, same as the site background)
        ctx.fillStyle = g; ctx.fillRect(W / 2 - half, by - 4 * dpr, half * 2, 1.6 * dpr);
        ctx.fillRect(W / 2 - half * 0.94, by + 2.4 * dpr, half * 1.88, 1.6 * dpr);
        var glow = ctx.createRadialGradient(W / 2, by, 0, W / 2, by, half * 0.9);
        glow.addColorStop(0, "rgba(255,170,100," + 0.22 * inten + ")"); glow.addColorStop(1, "rgba(255,170,100,0)");
        ctx.fillStyle = glow; ctx.save(); ctx.translate(W / 2, by); ctx.scale(1, 0.12); ctx.translate(-W / 2, -by);
        ctx.beginPath(); ctx.arc(W / 2, by, half * 0.9, 0, 6.2832); ctx.fill(); ctx.restore();
        ctx.restore();
      }
      if (!idle) {
        // shockwave from the button
        var sw = seg(t, 0, 900);
        if (sw < 1) {
          ctx.strokeStyle = "rgba(227,196,159," + (0.5 * (1 - sw)) + ")"; ctx.lineWidth = 2 * dpr * (1 - sw) + dpr;
          ctx.beginPath(); ctx.arc(W / 2, L.cy, L.r * 0.4 + easeOut(sw) * Math.max(W, H) * 0.55, 0, 6.2832); ctx.stroke();
        }
        var draw1 = easeOut(seg(t, TL.checkStart, TL.checkStart + 650)), draw2 = easeOut(seg(t, TL.checkStart + 60, TL.checkStart + 710));
        var st = opts.state(t);
        gauge(L.lx, L.cy, L.r, draw1, st.left, st.leftOpts);
        gauge(L.rx, L.cy, L.r, draw2, st.right, st.rightOpts);
        panel(L, t, st);
      }
      // film grain + vignette
      ctx.save(); ctx.globalAlpha = 0.6; ctx.fillStyle = ctx.createPattern(grain, "repeat");
      ctx.translate((Math.random() * 128) | 0, (Math.random() * 128) | 0); ctx.fillRect(-128, -128, W + 256, H + 256); ctx.restore();
      var vg = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.35, W / 2, H / 2, Math.max(W, H) * 0.75);
      vg.addColorStop(0, "rgba(0,0,0,0)"); vg.addColorStop(1, "rgba(0,0,0,.7)");
      ctx.fillStyle = vg; ctx.fillRect(0, 0, W, H);
    }
    return { draw: draw };
  }

  // ======================================================================= controller
  function init() {
    var intro = document.getElementById("intro");
    if (!intro) return;
    if (!root.classList.contains("intro-pending")) { intro.parentNode.removeChild(intro); return; }
    intro.hidden = false;

    var ev = intro.getAttribute("data-powertrain") === "ev";
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var startBtn = intro.querySelector("[data-start]"), skipBtn = intro.querySelector("[data-skip]"), soundBtn = intro.querySelector("[data-sound]");
    var canvas = intro.querySelector("[data-fx]"), carWrap = intro.querySelector("[data-car-wrap]"), title = intro.querySelector("[data-title]");
    var vehicle = intro.getAttribute("data-vehicle") || "Driving Intelligence";
    var engineTxt = intro.getAttribute("data-engine") || (ev ? "Electric" : "");
    var readyAt = ev ? 1100 : TL.catchAt + 80;
    var exitAt = ev ? TL.exitEv : TL.exitIce;

    // title letters (textContent only)
    var text = title.getAttribute("data-text") || "DRIVING INTELLIGENCE";
    for (var i = 0; i < text.length; i++) {
      var sp = document.createElement("span");
      sp.textContent = text[i] === " " ? " " : text[i];
      sp.style.setProperty("--i", String(i));
      title.appendChild(sp);
    }
    var img = carWrap.querySelector("img");
    if (img) carWrap.style.setProperty("--car", 'url("' + img.getAttribute("src").replace(/"/g, "") + '")');

    function state(t) {
      var stL = selfTest(t, 0), stR = selfTest(t, 40);
      var rpm = ev ? 0 : rpmAt(t);
      return {
        readyAt: readyAt,
        left: stL != null ? stL : (ev ? 0.02 + 0.01 * Math.sin(t * 0.004) : rpm / 8000),
        right: stR != null ? stR : 0,
        leftOpts: ev ? { majors: 5, labels: [0, 20, 40, 60, 80, 100], red: null, label: "POWER %", readout: t < readyAt ? "–" : "0" }
                     : { majors: 8, labels: [0, 1, 2, 3, 4, 5, 6, 7, 8], red: 0.8125, label: "r/min · ×1000",
                         readout: t < TL.catchAt ? "–" : String(Math.round(rpm / 10) * 10) },
        rightOpts: { majors: 7, labels: [0, 40, 80, 120, 160, 200, 240, 280], red: null, label: "km/h", readout: t < readyAt ? "–" : "0" }
      };
    }
    var R = Renderer(canvas, { ev: ev, reduced: reduced, vehicle: vehicle, engine: engineTxt, state: state });

    var raf = 0, t0 = 0, started = false, done = false, sound = null, timers = [];
    function loop(now) {
      if (done) return;
      var t = started ? now - t0 : 0;
      if (reduced && started) t = Math.max(t, 2700);     // jump to the settled READY frame
      R.draw(t, !started);
      raf = requestAnimationFrame(loop);
    }
    raf = requestAnimationFrame(loop);

    function syncSound() { if (soundBtn) soundBtn.textContent = soundOn() ? "사운드 켜짐" : "사운드 꺼짐"; }
    syncSound();
    function onKey(e) { if (e.key === "Escape") finish(true); }

    function finish(immediate) {
      if (done) return;
      timers.forEach(window.clearTimeout); timers = [];
      document.removeEventListener("keydown", onKey);
      markSeen();
      if (immediate && sound) sound.stop();
      intro.classList.add("is-leaving");
      root.classList.remove("intro-pending");
      window.setTimeout(function () {
        done = true; cancelAnimationFrame(raf);
        if (intro.parentNode) intro.parentNode.removeChild(intro);
        var main = document.getElementById("main"); if (main) main.focus({ preventScroll: true });
      }, (immediate || reduced) ? 400 : TL.exitDur);
      if (/[?&]intro=1/.test(window.location.search) && window.history.replaceState) {
        var url = window.location.pathname + window.location.search.replace(/([?&])intro=1(&|$)/, "$1").replace(/[?&]$/, "");
        window.history.replaceState(null, "", url + window.location.hash);
      }
    }

    startBtn.addEventListener("click", function () {
      if (started) return;
      started = true; t0 = performance.now();
      intro.classList.add("is-on");
      if (ev) intro.classList.add("is-ev");
      sound = playSound(ev ? "ev" : "ice");
      timers.push(window.setTimeout(function () { intro.classList.add("show-car"); }, reduced ? 0 : TL.reveal));
      timers.push(window.setTimeout(function () { intro.classList.add("show-title"); }, reduced ? 0 : TL.title));
      timers.push(window.setTimeout(function () { finish(false); }, reduced ? 1400 : exitAt));
    }, { once: true });
    skipBtn.addEventListener("click", function () { finish(true); });
    if (soundBtn) soundBtn.addEventListener("click", function () { sset(SOUND_KEY, soundOn() ? "off" : "on"); syncSound(); });
    document.addEventListener("keydown", onKey);
    startBtn.focus({ preventScroll: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
