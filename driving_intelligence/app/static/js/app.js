/* Driving Intelligence – progressive enhancement only (every page works without JS).
   No inline scripts (CSP: script-src 'self'). Listeners are attached once per page load
   to elements that live for the whole page, so nothing accumulates (no leaks). */
(function () {
  "use strict";

  function clampPct(v) {
    var n = Number(v);
    if (!isFinite(n)) return 0;
    return Math.max(0, Math.min(100, n));
  }

  // Animate score rings, bars and timeline columns (CSSOM writes are allowed by CSP).
  function paintScores() {
    var rings = document.querySelectorAll(".ring[data-score]");
    var bars = document.querySelectorAll(".bar span[data-w]");
    var cols = document.querySelectorAll(".timeline__bar span[data-h]");
    requestAnimationFrame(function () {
      rings.forEach(function (el) { el.style.setProperty("--p", clampPct(el.dataset.score)); });
      bars.forEach(function (el) { el.style.width = clampPct(el.dataset.w) + "%"; });
      cols.forEach(function (el) { el.style.height = clampPct(el.dataset.h) + "%"; });
    });
  }

  function initNav() {
    var btn = document.querySelector("[data-nav-toggle]");
    var nav = document.querySelector("[data-nav]");
    if (!btn || !nav) return;
    btn.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // Course filters submit on change.
  function initAutosubmit() {
    document.querySelectorAll("form[data-autosubmit]").forEach(function (form) {
      form.addEventListener("change", function () { form.submit(); });
    });
  }

  // Confirmation for destructive actions.
  function initConfirm() {
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        if (!window.confirm(form.getAttribute("data-confirm") || "계속할까요?")) e.preventDefault();
      });
    });
  }

  // Prevent double submissions on slow uploads.
  function initBusy() {
    document.querySelectorAll("form[data-busy]").forEach(function (form) {
      form.addEventListener("submit", function () {
        var b = form.querySelector("button[type=submit]");
        if (b) { b.setAttribute("aria-busy", "true"); b.textContent = "분석 중…"; }
      });
    });
  }

  // Vehicle preset loader (data comes from a non-executable JSON block).
  function initPresets() {
    var select = document.querySelector("[data-preset-select]");
    var form = document.querySelector("[data-vehicle-form]");
    var dataEl = document.getElementById("preset-data");
    if (!select || !form || !dataEl) return;
    var presets = {};
    try {
      JSON.parse(dataEl.textContent || "[]").forEach(function (p) { presets[p.key] = p; });
    } catch (err) {
      console.warn("preset data parse failed", err);
      return;
    }
    var fields = ["maker", "model", "engine", "power_ps", "torque_nm", "drivetrain", "transmission", "weight_kg", "size_class"];
    select.addEventListener("change", function () {
      var p = presets[select.value];
      if (!p) return;
      fields.forEach(function (f) {
        var input = form.elements.namedItem(f);
        if (input && p[f] !== undefined) input.value = String(p[f]);
      });
      var nick = form.elements.namedItem("nickname");
      if (nick && !nick.value) nick.value = p.model;
    });
  }

  // File picker label + drag & drop highlight.
  function initDrop() {
    var drop = document.querySelector("[data-drop]");
    var input = document.querySelector("[data-file-input]");
    var label = document.querySelector("[data-file-label]");
    if (!drop || !input || !label) return;
    input.addEventListener("change", function () {
      label.textContent = input.files && input.files.length ? input.files[0].name : "CSV 파일을 선택하거나 끌어다 놓으세요";
    });
    ["dragenter", "dragover"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("is-over"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      drop.addEventListener(ev, function () { drop.classList.remove("is-over"); });
    });
    drop.addEventListener("drop", function (e) {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files.length) {
        input.files = e.dataTransfer.files;
        input.dispatchEvent(new Event("change"));
      }
    });
  }


  // HUD simulation player. One interval at most; cleared on pause and on page hide (no leaks).
  function initHud() {
    var wrap = document.querySelector("[data-hud]");
    var dataEl = document.getElementById("hud-data");
    if (!wrap || !dataEl) return;
    var segs;
    try { segs = JSON.parse(dataEl.textContent || "[]"); } catch (err) { console.warn("hud data", err); return; }
    if (!segs.length) return;
    var q = function (k) { return wrap.querySelector('[data-h="' + k + '"]'); };
    var el = { speed: q("speed"), arrow: q("arrow"), dist: q("dist"), sev: q("sev"), limit: q("limit"), msg: q("msg"), hint: q("hint") };
    var shield = wrap.querySelector("[data-windshield]");
    var canvas = wrap.querySelector("[data-scene]");
    var scene = canvas && window.DIScene ? window.DIScene.create(canvas) : null;  // realistic 3D road
    var CURVE = [0, 0.25, 0.55, 1.0];
    var items = wrap.querySelectorAll("[data-seglist] li");
    var pos = wrap.parentNode.querySelector("[data-hud-pos]") || document.querySelector("[data-hud-pos]");
    var playBtn = document.querySelector("[data-hud-play]");
    var i = 0, timer = null;

    function render() {
      var s = segs[i];
      var sign = s.direction === "left" ? -1 : 1;
      el.speed.textContent = s.speed;
      el.dist.textContent = s.distance_m + " m";
      el.sev.textContent = (s.direction === "left" ? "좌" : "우") + " · " + s.severity_label;
      el.sev.className = "hud__sev sev-" + s.severity;
      el.limit.textContent = s.limit;
      el.msg.textContent = s.message;
      el.hint.textContent = s.hint || "";
      el.arrow.classList.toggle("is-left", s.direction === "left");
      el.arrow.classList.toggle("is-hidden", !!s.checkpoint);
      if (s.checkpoint) {
        el.dist.textContent = "도착";
        el.sev.textContent = "📍 체크포인트";
        el.sev.className = "hud__sev sev-cp";
      }
      if (scene) {
        // segments advance every 2.6 s, so the bend is brought close enough to be visible in that time
        scene.set({ speed: s.speed, curve: s.checkpoint ? 0 : sign * CURVE[s.severity],
                    cornerAt: Math.min(80, s.distance_m * 0.2),
                    grade: s.gradient === "down" ? -1 : (s.gradient === "up" ? 1 : 0) });
      }
      items.forEach(function (li, k) { li.classList.toggle("is-current", k === i); });
      if (pos) pos.textContent = (i + 1) + " / " + segs.length;
    }
    function go(n) { i = (n + segs.length) % segs.length; render(); }
    function stop() {
      if (timer) { window.clearInterval(timer); timer = null; }
      if (shield) shield.classList.add("is-paused");
      if (scene) scene.stop();
      if (playBtn) { playBtn.textContent = "재생"; playBtn.setAttribute("aria-pressed", "false"); }
    }
    function start() {
      if (timer) return;
      timer = window.setInterval(function () { go(i + 1); }, 2600);
      if (shield) shield.classList.remove("is-paused");
      if (scene) scene.start();
      if (playBtn) { playBtn.textContent = "일시정지"; playBtn.setAttribute("aria-pressed", "true"); }
    }
    if (playBtn) playBtn.addEventListener("click", function () { timer ? stop() : start(); });
    var prev = document.querySelector("[data-hud-prev]"), next = document.querySelector("[data-hud-next]");
    if (prev) prev.addEventListener("click", function () { stop(); go(i - 1); });
    if (next) next.addEventListener("click", function () { stop(); go(i + 1); });
    items.forEach(function (li, k) {
      function pick() { stop(); go(k); }
      li.addEventListener("click", pick);
      li.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    });
    window.addEventListener("pagehide", stop);
    document.addEventListener("visibilitychange", function () { if (document.hidden) stop(); });
    render();
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) stop(); else start();
  }


  // ------------------------------------------------------------------ toasts (textContent only: XSS-safe)
  function showToast(title, lines, warn) {
    var box = document.querySelector("[data-toasts]");
    if (!box) return;
    var t = document.createElement("div");
    t.className = "toast" + (warn ? " toast--warn" : "");
    t.setAttribute("role", "status");
    var b = document.createElement("b");
    b.textContent = title;
    t.appendChild(b);
    (lines || []).forEach(function (line) {
      var d = document.createElement("div");
      d.className = "small";
      d.textContent = line;
      t.appendChild(d);
    });
    box.appendChild(t);
    window.setTimeout(function () {
      t.classList.add("is-out");
      window.setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 450);
    }, 6500);
  }

  function haversineM(lat1, lng1, lat2, lng2) {
    var R = 6371008.8, rad = Math.PI / 180;
    var dp = (lat2 - lat1) * rad, dl = (lng2 - lng1) * rad;
    var a = Math.sin(dp / 2) * Math.sin(dp / 2) +
            Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  // ------------------------------------------------------------------ auto stamp (geofence check-in)
  // Client pre-filters by distance so the server is only called near a checkpoint;
  // the server re-verifies everything. One watch at most; cleared on stop and page hide.
  function initAutoStamp() {
    var box = document.querySelector("[data-autostamp]");
    var dataEl = document.getElementById("checkpoint-data");
    if (!box || !dataEl) return;
    var btn = box.querySelector("[data-as-toggle]");
    var statusEl = box.querySelector("[data-as-status]");
    var pointsEl = document.querySelector("[data-points]");
    var url = box.getAttribute("data-url");
    var csrfToken = box.getAttribute("data-csrf");
    var KEY = "di_autostamp";
    var COOLDOWN_MS = 30000, MAX_ACC = 300;
    var cps;
    try { cps = JSON.parse(dataEl.textContent || "[]"); } catch (err) { console.warn("checkpoint data", err); return; }
    var watchId = null, pending = false, lastPost = 0;

    function setStatus(text) { statusEl.textContent = text; }
    function store(v) { try { window.localStorage.setItem(KEY, v); } catch (e) { /* ignore */ } }

    if (!("geolocation" in navigator)) {
      setStatus("이 브라우저는 위치 기능을 지원하지 않습니다.");
      btn.disabled = true;
      return;
    }
    if (!window.isSecureContext) {
      setStatus("위치 기능은 HTTPS 또는 localhost 주소에서만 사용할 수 있습니다.");
      btn.disabled = true;
      return;
    }

    function setUi(on) {
      box.classList.toggle("is-on", on);
      btn.textContent = on ? "자동 스탬프 끄기" : "자동 스탬프 켜기";
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.className = on ? "btn btn--ghost" : "btn btn--primary";
    }

    function handle(res) {
      var j = res.j || {};
      if (!res.ok) {
        showToast("스탬프 보류", [j.message || "요청을 처리하지 못했습니다. 페이지를 새로고침하세요."], true);
        setStatus("보류: " + (j.message || "오류"));
        return;
      }
      if (pointsEl && typeof j.balance === "number") {
        pointsEl.firstChild.nodeValue = j.balance.toLocaleString("ko-KR");
      }
      if (j.status === "stamped") {
        var lines = (j.awarded || []).map(function (a) { return a.reason + "  +" + a.amount.toLocaleString("ko-KR") + "P"; });
        showToast("📍 " + j.course + " 스탬프 획득!", lines.concat(["보유 " + j.balance.toLocaleString("ko-KR") + "P · 스탬프 " + j.stamps + "/" + j.total]));
        setStatus("적립 완료 · " + j.course);
        window.setTimeout(function () { window.location.reload(); }, 3500);  // refresh stamp book
      } else if (j.status === "revisit") {
        setStatus(j.course + " 재방문 기록 · 이미 스탬프 보유");
      } else if (j.status === "already") {
        setStatus(j.course + " · 이미 스탬프 보유 (방금 확인됨)");
      } else if (j.status === "outside") {
        setStatus("체크포인트 반경 밖 · 가장 가까운 곳: " + j.nearest + " " + j.distance_km + " km");
      }
    }

    function onPos(pos) {
      var c = pos.coords, best = null, bestD = Infinity;
      cps.forEach(function (cp) {
        var d = haversineM(c.latitude, c.longitude, cp.lat, cp.lng);
        if (d < bestD) { bestD = d; best = cp; }
      });
      if (!best) return;
      if (c.accuracy > MAX_ACC) {
        setStatus("위치 정확도가 낮습니다 (±" + Math.round(c.accuracy) + " m) · 계속 확인 중");
        return;
      }
      if (bestD > best.radius) {
        setStatus("켜짐 · 가장 가까운 체크포인트: " + best.name + " (" + best.label + ") " + (bestD / 1000).toFixed(1) + " km");
        return;
      }
      if (pending || Date.now() - lastPost < COOLDOWN_MS) return;
      pending = true;
      lastPost = Date.now();
      setStatus(best.label + " 도착 · 스탬프 확인 중…");
      var body = new URLSearchParams({ csrf_token: csrfToken, lat: String(c.latitude),
                                       lng: String(c.longitude), accuracy: String(c.accuracy) });
      fetch(url, { method: "POST", body: body, credentials: "same-origin",
                   headers: { "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json" } })
        .then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; });
        })
        .then(handle)
        .catch(function () { showToast("네트워크 오류", ["연결이 복구되면 다시 확인합니다."], true); })
        .then(function () { pending = false; });
    }

    function onErr(err) {
      if (err.code === 1) {  // PERMISSION_DENIED
        stop(false);
        setStatus("위치 권한이 거부되었습니다. 브라우저 설정에서 허용한 뒤 다시 켜세요.");
      } else {
        setStatus("위치를 확인하지 못했습니다 · 재시도 중");
      }
    }

    function start() {
      if (watchId !== null) return;
      watchId = navigator.geolocation.watchPosition(onPos, onErr,
        { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 });
      store("on");
      setUi(true);
      setStatus("켜짐 · 위치 확인 중…");
    }
    function stop(remember) {
      if (watchId !== null) navigator.geolocation.clearWatch(watchId);
      watchId = null;
      if (remember !== false) store("off");
      setUi(false);
      setStatus("꺼짐 · 켜면 위치를 확인해 체크포인트 반경 안에서 자동으로 적립합니다.");
    }

    btn.addEventListener("click", function () { watchId === null ? start() : stop(); });
    window.addEventListener("pagehide", function () {
      if (watchId !== null) navigator.geolocation.clearWatch(watchId);
      watchId = null;
    });
    var saved = null;
    try { saved = window.localStorage.getItem(KEY); } catch (e) { /* ignore */ }
    if (saved === "on") start();
  }

  document.addEventListener("DOMContentLoaded", function () {
    paintScores();
    initNav();
    initAutosubmit();
    initConfirm();
    initBusy();
    initPresets();
    initDrop();
    initHud();
    initAutoStamp();
  });
})();
