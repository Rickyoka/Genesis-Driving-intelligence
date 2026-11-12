/* Navigation-linked HUD.
 * Road mode   : /api/route-guide  -> advisory profile + corners + rhythm sections (comfort-limited).
 * Circuit mode: /api/circuit-guide -> grip/power-limited flying lap with braking boards and phases.
 * One rAF loop; text via textContent only; listeners on long-lived nodes; cleanup on pagehide.
 */
(function () {
  "use strict";

  var COL = { decel: "#ff9a3c", corner: "#8fd3ff", accel: "#3ddc97", stop: "#ff5a6a", rhythm: "#b48cff",
              brake: "#ff4d5e", turnin: "#ffb36b", apex: "#8fe9ff", throttle: "#3ddc97", full: "#7df0bd", board: "#ff4d5e" };

  function $(root, sel) { return root.querySelector(sel); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function fmtKm(m) { return m >= 1000 ? (m / 1000).toFixed(2) + " km" : Math.round(m) + " m"; }
  function fmtLap(s) { var m = Math.floor(s / 60); return m + ":" + (s - m * 60).toFixed(1).padStart(4, "0"); }
  function dirLabel(d) { return d > 0 ? "우" : "좌"; }

  function init() {
    var root = document.querySelector("[data-drive]");
    var cfgEl = document.getElementById("drive-config");
    if (!root || !cfgEl) return;
    var cfg; try { cfg = JSON.parse(cfgEl.textContent); } catch (e) { return; }
    var circuit = cfg.mode === "circuit";
    var ui = {
      spd: $(root, "[data-spd]"), phase: $(root, "[data-phase]"), sub: $(root, "[data-sub]"), lim: $(root, "[data-lim]"),
      arrow: $(root, "[data-arrow]"), prog: $(root, "[data-prog]"), meta: $(root, "[data-meta]"), hud: $(root, "[data-dhud]"),
      rhythm: $(root, "[data-rhythm]"), board: $(root, "[data-board]"), cues: $(root, "[data-cues]"), stats: $(root, "[data-stats]"),
      map: $(root, "[data-minimap]"), chart: $(root, "[data-profile]"), src: $(root, "[data-src]"), play: $(root, "[data-play]"),
      gate: $(root, "[data-gate]"), gateOk: $(root, "[data-gate-ok]")
    };
    var canvas = $(root, "[data-scene]");
    var scene = canvas && window.DIScene ? window.DIScene.create(canvas, { track: circuit }) : null;
    var st = { d: null, s: 0, ts: circuit ? 2 : 4, tyre: "street", running: false, raf: 0, last: 0, lap: 1, lapT: 0,
               bestLap: null, cueIdx: -1, ctrl: null };

    // ---------------------------------------------------------------- data
    function load() {
      stop();
      if (st.ctrl) st.ctrl.abort();
      var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
      st.ctrl = ctrl;
      var url = circuit ? cfg.circuitApi + "?" + new URLSearchParams({ tyre: st.tyre })
                        : cfg.routeApi + "?" + new URLSearchParams({ course: cfg.course, limit: String(cfg.limit) });
      fetch(url, { credentials: "same-origin", signal: ctrl ? ctrl.signal : undefined, headers: { Accept: "application/json" } })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok) { ui.sub.textContent = res.j.error || "데이터를 불러오지 못했습니다."; return; }
          st.d = normalise(res.j);
          st.s = 0; st.lap = 1; st.lapT = 0; st.cueIdx = -1;
          drawMap(); drawChart(); renderStats(); update(true);
        })
        .catch(function (err) { if (!err || err.name !== "AbortError") ui.sub.textContent = "서버에 연결할 수 없습니다."; });
    }

    function normalise(j) {
      var d = { raw: j, profile: j.profile, corners: (j.corners || []).slice().sort(function (a, b) { return a.turnin_m - b.turnin_m; }) };
      if (circuit) {
        d.total = j.length_m; d.loop = true; d.map = j.map; d.cues = j.cues.filter(function (q) { return q.kind !== "board"; });
        d.limit = null; d.rhythm = [];
      } else {
        d.total = j.distance_m; d.loop = false; d.limit = j.limit_kmh; d.rhythm = j.rhythm || [];
        d.map = projectPath(j.path);
        d.stops = (j.guidance || []).filter(function (g) { return g.kind === "stop"; });
        var cues = [];
        d.corners.forEach(function (c) {
          var sev = c.severity || "";
          if (c.brake_m != null) cues.push({ at_m: c.brake_m, kind: "decel", title: "감속 시작 → " + c.min_kmh + " km/h", detail: dirLabel(c.dir) + " " + sev + " · R " + c.radius_m + " m" });
          cues.push({ at_m: c.turnin_m, kind: "corner", title: "코너 · 일정한 속도", detail: dirLabel(c.dir) + " " + sev + " · 차로 안 유지" });
          if (c.throttle_m > c.apex_m || c.brake_m != null) cues.push({ at_m: c.throttle_m, kind: "accel", title: "출구 · 부드럽게 가속", detail: "제한속도 " + d.limit + " km/h까지" });
        });
        d.rhythm.forEach(function (r) { cues.push({ at_m: r.from_m, kind: "rhythm", title: r.title, detail: "권고 " + r.speed_band[0] + "–" + r.speed_band[1] + " km/h" }); });
        d.stops.forEach(function (g) { cues.push({ at_m: g.at_m, kind: "stop", title: g.title, detail: g.detail }); });
        cues.sort(function (a, b) { return a.at_m - b.at_m; });
        d.cues = cues;
        ui.src.textContent = j.source === "osrm" ? "경로: 지도 API" : "경로: 시뮬레이션" + (j.fallback_reason ? " · " + j.fallback_reason : "");
      }
      if (circuit) ui.src.textContent = j.name + " · " + j.vehicle + " · " + j.tyre_label;
      return d;
    }

    function projectPath(path) {
      var lat0 = path.reduce(function (a, p) { return a + p[0]; }, 0) / path.length, k = Math.cos(lat0 * Math.PI / 180);
      var xs = path.map(function (p) { return p[1] * k; }), ys = path.map(function (p) { return -p[0]; });
      var minx = Math.min.apply(null, xs), maxx = Math.max.apply(null, xs), miny = Math.min.apply(null, ys), maxy = Math.max.apply(null, ys);
      var span = Math.max(maxx - minx, maxy - miny) || 1;
      return xs.map(function (x, i) { return [(x - minx) / span, (ys[i] - miny) / span]; });
    }

    // ---------------------------------------------------------------- lookups
    function speedAt(m) {
      var p = st.d.profile, lo = 0, hi = p.length - 1;
      if (m <= p[0][0]) return p[0][1];
      if (m >= p[hi][0]) return p[hi][1];
      while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (p[mid][0] <= m) lo = mid; else hi = mid; }
      var t = (m - p[lo][0]) / ((p[hi][0] - p[lo][0]) || 1);
      return p[lo][1] + (p[hi][1] - p[lo][1]) * t;
    }
    function cornerAhead(m) {
      var cs = st.d.corners;
      for (var i = 0; i < cs.length; i++) if (cs[i].exit_m >= m) return { c: cs[i], off: 0 };
      return st.d.loop && cs.length ? { c: cs[0], off: st.d.total } : null;   // wrap to next lap
    }

    // ---------------------------------------------------------------- HUD update
    function setPhase(text, sub, color, arrowDir) {
      ui.phase.textContent = text; ui.sub.textContent = sub;
      ui.phase.style.color = color || ""; ui.hud.style.borderColor = color ? color + "66" : "";
      if (arrowDir == null) ui.arrow.classList.add("is-hidden");
      else { ui.arrow.classList.remove("is-hidden"); ui.arrow.classList.toggle("is-left", arrowDir < 0); }
    }

    function update(force) {
      var d = st.d; if (!d) return;
      var s = st.s, v = speedAt(s), a = cornerAhead(s);
      ui.spd.textContent = String(Math.round(v));
      ui.prog.style.width = (100 * s / d.total).toFixed(2) + "%";
      ui.meta.textContent = circuit ? "LAP " + st.lap + " · " + fmtLap(st.lapT) + (st.bestLap ? " · BEST " + fmtLap(st.bestLap) : "")
                                    : fmtKm(s) + " / " + fmtKm(d.total);
      if (!circuit) ui.lim.textContent = String(d.limit);

      // scene: bend direction/strength from the navigation corner data
      if (scene) {
        var curve = 0, cornerAt = 9999;
        if (a) {
          var c = a.c, tin = c.turnin_m + a.off, out = c.exit_m + a.off;
          curve = c.dir * clamp(28 / c.radius_m, 0.12, 1);
          cornerAt = s >= tin && s <= out ? 0 : Math.max(0, tin - s);
        }
        scene.set({ speed: v * st.ts, curve: curve, cornerAt: cornerAt, grade: 0 });
      }

      if (circuit) circuitPhase(s, a); else roadPhase(s, a, v);
      renderCues(s, force);
      moveDot(s);
      if (ui.chart.cursor) { var x = (s / d.total * 1000).toFixed(1); ui.chart.cursor.setAttribute("x1", x); ui.chart.cursor.setAttribute("x2", x); }
    }

    function roadPhase(s, a, v) {
      var d = st.d;
      var r = d.rhythm.filter(function (x) { return s >= x.from_m && s <= x.to_m; })[0];
      if (r) { ui.rhythm.hidden = false; ui.rhythm.textContent = "♪ " + r.title + " — " + r.tip; } else ui.rhythm.hidden = true;
      var stop = d.stops.filter(function (g) { return g.at_m >= s && g.at_m - s < 60; })[0];
      if (stop && s > 5) { setPhase("정지", stop.title.replace("정지 · ", "") + " · " + stop.detail, COL.stop, null); return; }
      if (!a) { setPhase("정속", "다음 코너 없음 · 일정한 속도 유지", "", null); return; }
      var c = a.c, sev = c.severity || "";
      if (c.brake_m != null && s < c.brake_m) {
        var dd = c.brake_m - s;
        if (dd <= 250) setPhase("감속 준비 " + Math.round(dd) + " m", "목표 " + c.min_kmh + " km/h · " + dirLabel(c.dir) + " " + sev + " · 직선에서 감속", COL.decel, c.dir);
        else setPhase("정속", "다음 " + dirLabel(c.dir) + " " + sev + "까지 " + fmtKm(c.turnin_m - s), "", c.dir);
      } else if (c.brake_m != null && s < c.turnin_m) {
        setPhase("감속 → " + c.min_kmh + " km/h", "턴인까지 " + Math.round(c.turnin_m - s) + " m · 감속은 직선에서 끝내기", COL.decel, c.dir);
      } else if (s < c.turnin_m) {
        setPhase("정속", "다음 " + dirLabel(c.dir) + " " + sev + "까지 " + Math.round(c.turnin_m - s) + " m · 속도 그대로", "", c.dir);
      } else if (s < c.throttle_m) {
        setPhase("코너 · 일정한 속도", dirLabel(c.dir) + " " + sev + " · 차로 안 유지, 시선은 출구로", COL.corner, c.dir);
      } else {
        setPhase("출구 · 부드럽게 가속", "제한속도 " + d.limit + " km/h까지 천천히 · 현재 권고 " + Math.round(v) + " km/h", COL.accel, c.dir);
      }
    }

    function circuitPhase(s, a) {
      if (!a) return;
      var c = a.c, off = a.off, nm = c.name || "";
      var brake = c.brake_m != null ? c.brake_m + off : null, tin = c.turnin_m + off, apex = c.apex_m + off,
          thr = c.throttle_m + off, out = c.exit_m + off;
      var boardTxt = null;
      if (brake != null && s < brake) {
        var dd = brake - s;
        if (dd <= 150) boardTxt = dd > 100 ? "150" : (dd > 50 ? "100" : "50");
        setPhase("FULL", nm + " 브레이킹까지 " + Math.round(dd) + " m · " + c.entry_kmh + " → " + c.min_kmh + " km/h", COL.full, c.dir);
      } else if (brake != null && s < tin) {
        setPhase("BRAKE", nm + " · " + c.min_kmh + " km/h까지 강하게, 직선에서", COL.brake, c.dir);
      } else if (s < tin) {
        setPhase("FULL", nm + " 턴인까지 " + Math.round(tin - s) + " m · 감속 없이 통과", COL.full, c.dir);
      } else if (s < apex) {
        setPhase("TURN IN", nm + " · " + dirLabel(c.dir) + " " + c.severity + " (R " + c.radius_m + " m)", COL.turnin, c.dir);
      } else if (s < thr) {
        setPhase("APEX", nm + " · 최저 " + c.min_kmh + " km/h · 클리핑 포인트", COL.apex, c.dir);
      } else if (s <= out) {
        setPhase("THROTTLE", nm + " · 스티어링을 풀면서 점진적으로 가속", COL.throttle, c.dir);
      } else {
        setPhase("FULL", "풀 스로틀", COL.full, null);
      }
      if (boardTxt) { ui.board.hidden = false; ui.board.textContent = boardTxt; } else ui.board.hidden = true;
    }

    function renderCues(s, force) {
      var cues = st.d.cues, idx = 0;
      while (idx < cues.length && cues[idx].at_m < s) idx++;
      if (!force && idx === st.cueIdx) return;
      st.cueIdx = idx;
      while (ui.cues.firstChild) ui.cues.removeChild(ui.cues.firstChild);
      var list = cues.slice(idx, idx + 6);
      if (st.d.loop && list.length < 6) list = list.concat(cues.slice(0, 6 - list.length).map(function (q) { return Object.assign({}, q, { wrap: true }); }));
      list.forEach(function (q, i) {
        var li = el("li", "cue cue--" + q.kind + (i === 0 ? " is-next" : ""));
        var k = el("span", "cue__k", q.title); k.style.color = COL[q.kind] || "";
        li.appendChild(k);
        li.appendChild(el("span", "cue__d muted small", q.detail));
        li.appendChild(el("span", "cue__m", q.wrap ? "다음 랩" : fmtKm(q.at_m - s)));
        ui.cues.appendChild(li);
      });
    }

    // ---------------------------------------------------------------- minimap & chart
    var NS = "http://www.w3.org/2000/svg";
    function svg(tag, attrs) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); return e; }
    var dot = null;
    function drawMap() {
      var m = ui.map; while (m.firstChild) m.removeChild(m.firstChild);
      var pts = st.d.map.map(function (p) { return [20 + p[0] * 260, 20 + p[1] * 260]; });
      var dstr = pts.map(function (p, i) { return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ") + (st.d.loop ? " Z" : "");
      m.appendChild(svg("path", { d: dstr, "class": "mm-track" }));
      m.appendChild(svg("path", { d: dstr, "class": "mm-line" }));
      st.d.corners.forEach(function (c) {
        var p = pointAt(c.apex_m);
        m.appendChild(svg("circle", { cx: p[0], cy: p[1], r: 3.5, "class": "mm-corner" }));
        if (circuit && c.name) { var t = svg("text", { x: p[0] + 6, y: p[1] - 6, "class": "mm-label" }); t.textContent = c.name.split(" ")[0]; m.appendChild(t); }
      });
      dot = svg("circle", { cx: pts[0][0], cy: pts[0][1], r: 6, "class": "mm-dot" });
      m.appendChild(dot);
      st.mm = pts;
    }
    function pointAt(m) {
      var pts = st.d.map, f = clamp(m / st.d.total, 0, 1) * (pts.length - 1), i = Math.floor(f), t = f - i;
      var a = pts[i], b = pts[Math.min(i + 1, pts.length - 1)];
      return [20 + (a[0] + (b[0] - a[0]) * t) * 260, 20 + (a[1] + (b[1] - a[1]) * t) * 260];
    }
    function moveDot(s) { if (dot) { var p = pointAt(s); dot.setAttribute("cx", p[0].toFixed(1)); dot.setAttribute("cy", p[1].toFixed(1)); } }

    function drawChart() {
      var c = ui.chart, d = st.d; while (c.firstChild) c.removeChild(c.firstChild);
      var vmax = Math.max.apply(null, d.profile.map(function (p) { return p[1]; })) * 1.12 || 10;
      function X(m) { return m / d.total * 1000; } function Y(v) { return 220 - v / vmax * 205; }
      d.rhythm.forEach(function (r) { c.appendChild(svg("rect", { x: X(r.from_m), y: 0, width: Math.max(2, X(r.to_m) - X(r.from_m)), height: 10, "class": "pc-rhythm" })); });
      d.corners.forEach(function (k) {
        if (k.brake_m != null && k.turnin_m > k.brake_m) c.appendChild(svg("rect", { x: X(k.brake_m), y: 12, width: Math.max(1.5, X(k.turnin_m) - X(k.brake_m)), height: 208, "class": "pc-decel" }));
        c.appendChild(svg("rect", { x: X(k.turnin_m), y: 12, width: Math.max(1.5, X(k.throttle_m) - X(k.turnin_m)), height: 208, "class": "pc-corner" }));
        var accEnd = Math.max(k.exit_m, k.throttle_m + 60);
        c.appendChild(svg("rect", { x: X(k.throttle_m), y: 12, width: Math.max(1.5, X(accEnd) - X(k.throttle_m)), height: 208, "class": "pc-accel" }));
      });
      if (d.limit) c.appendChild(svg("line", { x1: 0, x2: 1000, y1: Y(d.limit), y2: Y(d.limit), "class": "pz-limit" }));
      c.appendChild(svg("polyline", { points: d.profile.map(function (p) { return X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1); }).join(" "), "class": "pz-line" }));
      c.cursor = svg("line", { x1: 0, x2: 0, y1: 0, y2: 220, "class": "pz-cursor" });
      c.appendChild(c.cursor);
    }

    function renderStats() {
      var d = st.d, j = d.raw, box = ui.stats; while (box.firstChild) box.removeChild(box.firstChild);
      var rows = circuit
        ? [["랩 길이", fmtKm(j.length_m)], ["예상 랩타임", fmtLap(j.lap_time_s)], ["최고 / 최저", j.vmax_kmh + " / " + j.vmin_kmh + " km/h"], ["코너", d.corners.length + "개"]]
        : [["거리", fmtKm(j.distance_m)], ["코너", d.corners.length + "개"], ["리듬 구간", d.rhythm.length + "곳"], ["권고 주행", j.est_minutes + " 분"]];
      var dl = el("dl", "specs specs--wide");
      rows.forEach(function (r) { var w = el("div"); w.appendChild(el("dt", null, r[0])); w.appendChild(el("dd", null, r[1])); dl.appendChild(w); });
      box.appendChild(dl);
    }

    // ---------------------------------------------------------------- loop & controls
    function frame(t) {
      if (!st.running) return;
      var dt = Math.min(0.1, (t - st.last) / 1000); st.last = t;
      var v = speedAt(st.s), sim = dt * st.ts;
      st.s += Math.max(v, circuit ? 20 : 8) / 3.6 * sim;    // never stall at stop points
      st.lapT += sim;
      if (st.s >= st.d.total) {
        if (st.d.loop) {
          st.s -= st.d.total; st.bestLap = st.bestLap ? Math.min(st.bestLap, st.lapT) : st.lapT;
          st.lap += 1; st.lapT = 0; st.cueIdx = -1;
        } else { st.s = st.d.total; update(true); stop(); ui.play.textContent = "다시 재생"; return; }
      }
      update(false);
      st.raf = requestAnimationFrame(frame);
    }
    function start() {
      if (!st.d || st.running) return;
      if (circuit && ui.gateOk && !ui.gateOk.checked) return;
      if (!st.d.loop && st.s >= st.d.total) { st.s = 0; st.cueIdx = -1; }
      st.running = true; st.last = performance.now();
      ui.play.textContent = "일시정지";
      if (scene) scene.start();
      st.raf = requestAnimationFrame(frame);
    }
    function stop() {
      st.running = false;
      if (st.raf) cancelAnimationFrame(st.raf); st.raf = 0;
      if (scene) scene.stop();
      if (ui.play && ui.play.textContent === "일시정지") ui.play.textContent = "재생";
    }

    ui.play.addEventListener("click", function () { st.running ? stop() : start(); });
    $(root, "[data-restart]").addEventListener("click", function () {
      stop(); st.s = 0; st.lap = 1; st.lapT = 0; st.cueIdx = -1; update(true); start();
    });
    root.querySelectorAll("[data-ts]").forEach(function (b) {
      b.addEventListener("click", function () {
        st.ts = +b.getAttribute("data-ts");
        root.querySelectorAll("[data-ts]").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
      });
    });
    root.querySelectorAll("[data-tyre]").forEach(function (b) {
      b.addEventListener("click", function () {
        st.tyre = b.getAttribute("data-tyre");
        root.querySelectorAll("[data-tyre]").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        load();
      });
    });
    if (ui.gateOk) ui.gateOk.addEventListener("change", function () {
      ui.play.disabled = !ui.gateOk.checked;
      if (ui.gate) ui.gate.classList.toggle("is-open", ui.gateOk.checked);
      if (!ui.gateOk.checked) stop();
    });
    window.addEventListener("pagehide", function () { stop(); if (st.ctrl) st.ctrl.abort(); if (scene) scene.destroy(); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) stop(); });

    load();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
