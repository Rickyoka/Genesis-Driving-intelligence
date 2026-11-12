/* Route guide map: Leaflet (bundled locally) + /api/route-guide.
 * - All user-visible text is inserted with textContent (no innerHTML with data) → XSS-safe.
 * - Layers are cleared and rebuilt on each recalculation; one rAF loop at most for playback,
 *   cancelled on stop / page hide (no leaks). In-flight fetches are aborted when superseded.
 */
(function () {
  "use strict";

  var COLORS = { decel: "#ff9a3c", stop: "#ff5a6a", accel: "#3ddc97", cruise: "#7fb2ff" };
  var KIND_LABEL = { decel: "감속", stop: "정지", accel: "가속", cruise: "정속" };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function km(m) { return (m / 1000).toFixed(m < 10000 ? 2 : 1) + " km"; }

  function init() {
    var root = document.querySelector("[data-route]");
    var cfgEl = document.getElementById("route-config");
    if (!root || !cfgEl) return;
    var cfg;
    try { cfg = JSON.parse(cfgEl.textContent); } catch (e) { console.warn("route config", e); return; }
    var notice = root.querySelector("[data-notice]");
    function showNotice(text) { notice.textContent = text; notice.hidden = !text; }
    if (!window.L) { showNotice("지도 라이브러리를 불러오지 못했습니다."); return; }

    var state = { comfort: "standard", limit: cfg.limit, waypoints: cfg.waypoints.map(function (w) { return { label: w.label, lat: w.lat, lng: w.lng }; }),
                  data: null, ctrl: null, raf: 0, playing: false, firstFit: true };

    var map = L.map("map", { zoomControl: true, scrollWheelZoom: true, attributionControl: true });
    map.setView([cfg.waypoints[0].lat, cfg.waypoints[0].lng], 12);
    var tileFailed = false;
    L.tileLayer(cfg.tileUrl, { maxZoom: 18, attribution: cfg.tileAttr })
      .on("tileerror", function () {
        if (!tileFailed) { tileFailed = true; showNotice("지도 배경(타일)을 불러오지 못했습니다. 인터넷 연결을 확인하세요. 경로·구간 안내는 계속 표시됩니다."); }
      }).addTo(map);
    var zoneLayer = L.layerGroup().addTo(map);
    var guideLayer = L.layerGroup().addTo(map);
    var wpLayer = L.layerGroup().addTo(map);
    var car = null;

    // ------------------------------------------------------------ waypoints (draggable)
    function drawWaypoints() {
      wpLayer.clearLayers();
      state.waypoints.forEach(function (w, i) {
        var role = i === 0 ? "s" : (i === state.waypoints.length - 1 ? "e" : "c");
        var icon = L.divIcon({ className: "wp wp--" + role, html: role === "s" ? "S" : (role === "e" ? "E" : "●"),
                               iconSize: [28, 28], iconAnchor: [14, 14] });
        var m = L.marker([w.lat, w.lng], { icon: icon, draggable: true, keyboard: true, title: w.label + " (끌어서 이동)" });
        m.on("dragend", function () {
          var p = m.getLatLng();
          state.waypoints[i].lat = p.lat; state.waypoints[i].lng = p.lng;
          load();
        });
        m.bindTooltip(w.label, { direction: "top", offset: [0, -14] });
        wpLayer.addLayer(m);
      });
    }

    // ------------------------------------------------------------ fetch
    function load() {
      stopPlay();
      if (state.ctrl) state.ctrl.abort();
      var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
      state.ctrl = ctrl;
      var wp = state.waypoints.map(function (w) { return w.lat.toFixed(6) + "," + w.lng.toFixed(6); }).join(";");
      var url = cfg.api + "?" + new URLSearchParams({ course: cfg.course, comfort: state.comfort, limit: String(state.limit), wp: wp });
      root.classList.add("is-loading");
      fetch(url, { credentials: "same-origin", signal: ctrl ? ctrl.signal : undefined, headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok) { showNotice(res.j.error || "경로를 계산하지 못했습니다."); return; }
          state.data = res.j;
          if (!tileFailed) showNotice(res.j.fallback_reason || "");
          else if (res.j.fallback_reason) showNotice(res.j.fallback_reason);
          draw();
        })
        .catch(function (err) { if (!err || err.name !== "AbortError") showNotice("서버에 연결할 수 없습니다."); })
        .then(function () { root.classList.remove("is-loading"); });
    }

    // ------------------------------------------------------------ draw
    function draw() {
      var d = state.data;
      zoneLayer.clearLayers(); guideLayer.clearLayers();
      d.zones.forEach(function (z) {
        if (z.kind === "stop" || z.coords.length < 2) return;
        L.polyline(z.coords, { color: COLORS[z.kind], weight: 7, opacity: .92, lineCap: "round" }).addTo(zoneLayer);
      });
      d.guidance.forEach(function (g, i) {
        if (g.kind === "cruise") return;
        var mk = L.circleMarker([g.lat, g.lng], { radius: g.kind === "stop" ? 8 : 6, color: "#0b0c10", weight: 2,
                                                 fillColor: COLORS[g.kind], fillOpacity: 1 });
        var pop = el("div", "pop");
        pop.appendChild(el("b", null, g.title));
        pop.appendChild(el("div", "pop__d", g.detail));
        pop.appendChild(el("div", "pop__m", km(g.at_m) + " 지점"));
        mk.bindPopup(pop);
        mk.on("click", function () { highlight(i); });
        mk.addTo(guideLayer);
        g._marker = mk;
      });
      if (state.data.source === "synthetic") {
        // synthetic geometry doesn't follow real roads → hide the draggable via-points to avoid confusion
        wpLayer.clearLayers();
      } else {
        drawWaypoints();
      }
      if (state.firstFit || state.data.source === "synthetic") {
        map.fitBounds(L.latLngBounds(d.path), { padding: [30, 30] });
        state.firstFit = false;
      }
      renderSummary(); renderGuides(); renderProfile();
    }

    function renderSummary() {
      var d = state.data, box = root.querySelector("[data-summary]");
      while (box.firstChild) box.removeChild(box.firstChild);
      var stats = el("dl", "specs specs--wide");
      [["거리", km(d.distance_m)], ["권고 주행 시간", d.est_minutes + " 분"], ["경로 데이터", d.source === "osrm" ? "지도 API" : "시뮬레이션"]]
        .forEach(function (kv) { var w = el("div"); w.appendChild(el("dt", null, kv[0])); w.appendChild(el("dd", null, kv[1])); stats.appendChild(w); });
      box.appendChild(stats);
      var bars = el("div", "zbar");
      ["decel", "cruise", "accel"].forEach(function (k) {
        var seg = el("span", "zbar__" + k);
        seg.style.width = Math.max(0, d.shares[k]) + "%";
        seg.title = KIND_LABEL[k] + " " + d.shares[k] + "%";
        bars.appendChild(seg);
      });
      box.appendChild(bars);
      var cap = el("p", "muted small m0", "감속 " + d.shares.decel + "% · 정속 " + d.shares.cruise + "% · 가속 " + d.shares.accel + "% · 정지 " +
        d.guidance.filter(function (g) { return g.kind === "stop"; }).length + "곳");
      box.appendChild(cap);
    }

    function renderGuides() {
      var list = root.querySelector("[data-guides]");
      while (list.firstChild) list.removeChild(list.firstChild);
      state.data.guidance.forEach(function (g, i) {
        if (g.kind === "cruise") return;
        var li = el("li", "gd gd--" + g.kind);
        li.tabIndex = 0;
        li.dataset.idx = String(i);
        li.appendChild(el("span", "gd__k", KIND_LABEL[g.kind]));
        var body = el("span", "gd__b");
        body.appendChild(el("b", null, g.title));
        body.appendChild(el("span", "muted small", g.detail));
        li.appendChild(body);
        li.appendChild(el("span", "gd__m", km(g.at_m)));
        function pick() { highlight(i, true); }
        li.addEventListener("click", pick);
        li.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
        list.appendChild(li);
      });
    }

    function highlight(i, pan) {
      var g = state.data.guidance[i];
      root.querySelectorAll(".gd").forEach(function (li) { li.classList.toggle("is-on", li.dataset.idx === String(i)); });
      var li = root.querySelector('.gd[data-idx="' + i + '"]');
      if (li && li.scrollIntoView) li.scrollIntoView({ block: "nearest" });
      if (pan && g) { map.flyTo([g.lat, g.lng], Math.max(map.getZoom(), 15), { duration: .6 }); if (g._marker) g._marker.openPopup(); }
    }

    // ------------------------------------------------------------ profile chart (SVG)
    var NS = "http://www.w3.org/2000/svg";
    function svg(tag, attrs) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); return e; }
    function renderProfile() {
      var d = state.data, s = root.querySelector("[data-profile]");
      while (s.firstChild) s.removeChild(s.firstChild);
      var W = 1000, H = 220, top = 12, vmax = Math.max(d.limit_kmh * 1.1, 10), total = Math.max(d.distance_m, 1);
      function X(m) { return (m / total) * W; }
      function Y(v) { return H - (v / vmax) * (H - top); }
      d.zones.forEach(function (z) {
        if (z.kind === "cruise") return;
        s.appendChild(svg("rect", { x: X(z.from_m), y: 0, width: Math.max(1.5, X(z.to_m) - X(z.from_m)), height: H, "class": "pz pz--" + z.kind }));
      });
      s.appendChild(svg("line", { x1: 0, x2: W, y1: Y(d.limit_kmh), y2: Y(d.limit_kmh), "class": "pz-limit" }));
      s.appendChild(svg("polyline", { points: d.profile.map(function (p) { return X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1); }).join(" "), "class": "pz-line" }));
      state.cursor = svg("line", { x1: 0, x2: 0, y1: 0, y2: H, "class": "pz-cursor" });
      state.cursor.style.display = "none";
      s.appendChild(state.cursor);
      root.querySelector("[data-axis]").textContent = "0 – " + km(d.distance_m) + " · 상한 " + d.limit_kmh + " km/h · 횡가속 " + d.comfort_g + " g";
      root.querySelector("[data-source]").textContent = d.source === "osrm" ? "경로: 지도 API (OSRM / OpenStreetMap)" : "경로: 시뮬레이션 (오프라인)";
      s.X = X;
    }

    // ------------------------------------------------------------ playback with mini HUD
    var hud = root.querySelector("[data-minihud]");
    var hSpd = root.querySelector("[data-mh-spd]"), hZone = root.querySelector("[data-mh-zone]"), hNext = root.querySelector("[data-mh-next]");
    var playBtn = root.querySelector("[data-play]");

    function speedAt(m) {
      var p = state.data.profile, lo = 0, hi = p.length - 1;
      while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (p[mid][0] <= m) lo = mid; else hi = mid; }
      var a = p[lo], b = p[hi], t = b[0] === a[0] ? 0 : (m - a[0]) / (b[0] - a[0]);
      return a[1] + (b[1] - a[1]) * Math.max(0, Math.min(1, t));
    }
    function posAt(m) {
      var path = state.data.path, prof = state.data.profile;
      var f = Math.max(0, Math.min(1, m / Math.max(state.data.distance_m, 1))) * (path.length - 1);
      var i = Math.floor(f), t = f - i, a = path[i], b = path[Math.min(i + 1, path.length - 1)];
      void prof;
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
    function zoneAt(m) {
      var zs = state.data.zones;
      for (var i = 0; i < zs.length; i++) if (m >= zs[i].from_m && m <= zs[i].to_m) return zs[i].kind;
      return "cruise";
    }
    function nextGuide(m) {
      var gs = state.data.guidance;
      for (var i = 0; i < gs.length; i++) if (gs[i].kind !== "cruise" && gs[i].at_m > m + 5) return gs[i];
      return null;
    }

    function startPlay() {
      if (!state.data || state.playing) return;
      state.playing = true; state.m = 0; state.last = performance.now();
      hud.hidden = false;
      playBtn.textContent = "재생 정지";
      if (!car) car = L.circleMarker(state.data.path[0], { radius: 9, color: "#fff", weight: 3, fillColor: "#ff6a1f", fillOpacity: 1 }).addTo(map);
      state.cursor.style.display = "";
      state.raf = requestAnimationFrame(tick);
    }
    function stopPlay() {
      state.playing = false;
      if (state.raf) cancelAnimationFrame(state.raf);
      state.raf = 0;
      if (playBtn) playBtn.textContent = "주행 재생";
    }
    function tick(t) {
      if (!state.playing) return;
      var dt = Math.min(0.1, (t - state.last) / 1000); state.last = t;
      var v = speedAt(state.m);                              // km/h advisory
      var scale = Math.max(20, state.data.distance_m / 40); // whole route in ~40 s
      state.m += Math.max(v, 12) / 3.6 * dt * scale / 10;
      if (state.m >= state.data.distance_m) { state.m = state.data.distance_m; stopPlay(); }
      var p = posAt(state.m);
      car.setLatLng(p);
      if (!map.getBounds().pad(-0.15).contains(p)) map.panTo(p, { animate: true, duration: .4 });
      var z = zoneAt(state.m), g = nextGuide(state.m);
      hSpd.textContent = String(Math.round(v));
      hZone.textContent = KIND_LABEL[z];
      hZone.className = "minihud__zone mz--" + z;
      hNext.textContent = g ? "다음: " + g.title + " · " + Math.round(g.at_m - state.m) + " m" : "목적지 도착";
      var X = root.querySelector("[data-profile]").X;
      if (X) { var x = X(state.m); state.cursor.setAttribute("x1", x); state.cursor.setAttribute("x2", x); }
      if (state.playing) state.raf = requestAnimationFrame(tick);
    }

    // ------------------------------------------------------------ controls
    playBtn.addEventListener("click", function () { state.playing ? stopPlay() : startPlay(); });
    root.querySelectorAll("[data-comfort]").forEach(function (b) {
      b.addEventListener("click", function () {
        state.comfort = b.getAttribute("data-comfort");
        root.querySelectorAll("[data-comfort]").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        load();
      });
    });
    root.querySelector("[data-limit]").addEventListener("change", function (e) { state.limit = +e.target.value; load(); });
    root.querySelector("[data-reset-wp]").addEventListener("click", function () {
      state.waypoints = cfg.waypoints.map(function (w) { return { label: w.label, lat: w.lat, lng: w.lng }; });
      state.firstFit = true;
      load();
    });
    window.addEventListener("pagehide", function () { stopPlay(); if (state.ctrl) state.ctrl.abort(); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) stopPlay(); });

    drawWaypoints();
    load();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
