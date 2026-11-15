(function () {
  "use strict";

  var DEFAULT_KEY = "koya-clinic-api-key-2026-x9f3m7";
  var EMAIL_RE = /^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$/;
  var PAGE_SIZE = 5;

  var state = {
    slots: [],
    slotMap: {},
    provider: "all",
    day: null,
    selected: null,
    loaded: false,
    attemptSig: "",
    attemptKey: "",
    rateTimer: null,
    lookupEmail: "",
    nextCursor: null,
    active: null,
    cancelArmed: false
  };

  var $ = function (id) { return document.getElementById(id); };
  var tz = "";
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) {}

  function apiKey() {
    try { return localStorage.getItem("clinic_api_key") || DEFAULT_KEY; } catch (e) { return DEFAULT_KEY; }
  }

  function api(path, opts) {
    opts = opts || {};
    var headers = { "X-API-Key": apiKey() };
    if (opts.body) headers["Content-Type"] = "application/json";
    if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
    return fetch(path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) {}
        return { status: res.status, ok: res.ok, data: data, retryAfter: res.headers.get("Retry-After") };
      });
    });
  }

  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") n.textContent = attrs[k];
      else if (k === "class") n.className = attrs[k];
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), attrs[k]);
      else n.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }

  function toast(msg, kind) {
    var t = el("div", { class: "toast" + (kind ? " " + kind : ""), text: msg });
    $("toasts").appendChild(t);
    setTimeout(function () { t.remove(); }, 5000);
  }

  function titleCase(s) {
    return String(s).replace(/\S+/g, function (w) { return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase(); });
  }

  var fmtTime = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
  var fmtDayLong = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" });
  var fmtWeekday = new Intl.DateTimeFormat(undefined, { weekday: "short" });
  var fmtMonth = new Intl.DateTimeFormat(undefined, { month: "short" });

  function dayKey(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  function slotInfo(slotId) {
    var s = state.slotMap[slotId];
    if (!s) return null;
    return { provider: s.provider, start: new Date(s.start), end: new Date(s.end) };
  }

  /* Slots */

  function loadSlots(quiet) {
    var wake = null;
    if (!quiet) {
      renderSkeleton();
      wake = setTimeout(function () {
        $("tzNote").textContent = "The server is waking up. This can take up to a minute on the first visit.";
      }, 4000);
    }
    return api("/slots").then(function (r) {
      if (wake) clearTimeout(wake);
      if (!r.ok) {
        if (r.status === 401) toast("This page isn't authorised to load appointments.", "error");
        else toast("Couldn't load appointments. Try again.", "error");
        if (!state.loaded) renderEmpty("Couldn't load appointments", "Check your connection and press Refresh.");
        return;
      }
      state.loaded = true;
      state.slots = (r.data && r.data.slots) || [];
      state.slotMap = {};
      state.slots.forEach(function (s) { state.slotMap[s.id] = s; });
      if (state.selected && !isOpen(state.slotMap[state.selected])) state.selected = null;
      renderAll();
    }).catch(function () {
      if (wake) clearTimeout(wake);
      toast("Couldn't reach the server. Check your connection.", "error");
      if (!state.loaded) renderEmpty("Couldn't load appointments", "Check your connection and press Refresh.");
    });
  }

  function isFuture(s) { return s && new Date(s.start).getTime() > Date.now(); }
  function isOpen(s) { return isFuture(s) && s.available; }

  function visibleSlots() {
    return state.slots.filter(function (s) {
      return isFuture(s) && (state.provider === "all" || s.provider === state.provider);
    });
  }

  function renderAll() {
    renderProviders();
    renderDays();
    renderTimes();
    renderSummary();
    $("tzNote").textContent = tz ? "Times are shown in your local time (" + tz.replace(/_/g, " ") + ")." : "";
  }

  function renderSkeleton() {
    var row = el("div", { class: "skel-row" });
    for (var i = 0; i < 6; i++) row.appendChild(el("div", { class: "skeleton" }));
    var t = $("times");
    t.replaceChildren(row);
    $("days").replaceChildren();
    $("providers").replaceChildren();
  }

  function renderEmpty(title, body) {
    $("times").replaceChildren(el("div", { class: "empty" }, [
      el("h3", { text: title }),
      el("p", { text: body })
    ]));
  }

  function renderProviders() {
    var names = [];
    state.slots.forEach(function (s) {
      if (isFuture(s) && names.indexOf(s.provider) === -1) names.push(s.provider);
    });
    if (state.provider !== "all" && names.indexOf(state.provider) === -1) state.provider = "all";
    var box = $("providers");
    box.replaceChildren();
    ["all"].concat(names).forEach(function (n) {
      box.appendChild(el("button", {
        class: "chip", type: "button", "aria-pressed": String(state.provider === n),
        text: n === "all" ? "All doctors" : n,
        onclick: function () { state.provider = n; state.day = null; renderAll(); }
      }));
    });
  }

  function groupByDay() {
    var map = {};
    var order = [];
    visibleSlots().forEach(function (s) {
      var k = dayKey(new Date(s.start));
      if (!map[k]) { map[k] = []; order.push(k); }
      map[k].push(s);
    });
    order.sort();
    return { map: map, order: order };
  }

  function renderDays() {
    var g = groupByDay();
    var box = $("days");
    box.replaceChildren();
    if (!g.order.length) return;
    if (!state.day || !g.map[state.day]) {
      var firstOpen = g.order.filter(function (k) { return g.map[k].some(function (s) { return s.available; }); })[0];
      state.day = firstOpen || g.order[0];
    }
    g.order.forEach(function (k) {
      var d = new Date(g.map[k][0].start);
      var anyOpen = g.map[k].some(function (s) { return s.available; });
      var b = el("button", {
        class: "day", type: "button", "aria-pressed": String(state.day === k), "data-full": String(!anyOpen),
        "aria-label": fmtDayLong.format(d) + (anyOpen ? "" : ", fully booked"),
        onclick: function () { state.day = k; renderDays(); renderTimes(); }
      }, [
        el("small", { text: fmtWeekday.format(d) }),
        el("strong", { text: String(d.getDate()) }),
        el("small", { text: fmtMonth.format(d) })
      ]);
      box.appendChild(b);
    });
  }

  function renderTimes() {
    var g = groupByDay();
    var box = $("times");
    if (!g.order.length) {
      renderEmpty("No open times right now", "Check back soon, or press Refresh to look again.");
      return;
    }
    var list = (g.map[state.day] || []).slice().sort(function (a, b) {
      return new Date(a.start) - new Date(b.start) || a.provider.localeCompare(b.provider);
    });
    var wrap = el("div", { class: "times", role: "group", "aria-label": "Times" });
    list.forEach(function (s) {
      var start = new Date(s.start);
      var sub = state.provider === "all" ? s.provider : "";
      if (!s.available) sub = sub ? sub + " · Booked" : "Booked";
      var b = el("button", {
        class: "time", type: "button", "aria-pressed": String(state.selected === s.id),
        onclick: function () {
          state.selected = s.id;
          renderTimes();
          renderSummary();
          if (window.matchMedia("(max-width: 860px)").matches) $("bookingForm").scrollIntoView({ behavior: "smooth", block: "start" });
        }
      }, [
        el("span", { class: "t", text: fmtTime.format(start) }),
        sub ? el("small", { text: sub }) : null
      ]);
      if (!s.available) b.disabled = true;
      wrap.appendChild(b);
    });
    box.replaceChildren(wrap);
  }

  function renderSummary() {
    var box = $("summary");
    var s = state.selected ? state.slotMap[state.selected] : null;
    if (!s) {
      box.className = "summary muted";
      $("summaryText").textContent = "Pick a time to continue.";
      return;
    }
    var d = new Date(s.start);
    box.className = "summary";
    $("summaryText").textContent = fmtDayLong.format(d) + ", " + fmtTime.format(d) + " with " + s.provider;
  }

  /* Booking */

  function setErr(inputId, errId, msg) {
    $(errId).textContent = msg || "";
    $(inputId).setAttribute("aria-invalid", msg ? "true" : "false");
  }

  function setBusy(btn, busy, label) {
    if (busy) {
      btn.dataset.label = btn.textContent;
      btn.setAttribute("aria-busy", "true");
      btn.replaceChildren(el("span", { class: "spinner", "aria-hidden": "true" }), document.createTextNode(label || "Working"));
    } else {
      btn.removeAttribute("aria-busy");
      btn.textContent = btn.dataset.label || label || "";
    }
  }

  function startRateCountdown(seconds) {
    var btn = $("submitBtn");
    clearInterval(state.rateTimer);
    var left = Math.max(1, parseInt(seconds, 10) || 30);
    btn.setAttribute("aria-disabled", "true");
    function tick() {
      if (left <= 0) {
        clearInterval(state.rateTimer);
        btn.removeAttribute("aria-disabled");
        btn.textContent = "Confirm booking";
        return;
      }
      btn.textContent = "Too many tries. Wait " + left + "s";
      left -= 1;
    }
    tick();
    state.rateTimer = setInterval(tick, 1000);
  }

  function onSubmit(e) {
    e.preventDefault();
    var btn = $("submitBtn");
    if (btn.getAttribute("aria-disabled") === "true" || btn.getAttribute("aria-busy") === "true") return;

    var name = $("name").value.trim();
    var email = $("email").value.trim().toLowerCase();
    var ok = true;

    setErr("name", "nameErr", name ? "" : "Enter your full name");
    if (!name) ok = false;
    if (!email) { setErr("email", "emailErr", "Enter your email address"); ok = false; }
    else if (!EMAIL_RE.test(email)) { setErr("email", "emailErr", "That email doesn't look right"); ok = false; }
    else setErr("email", "emailErr", "");

    if (!state.selected) {
      toast("Pick a day and time first.", "error");
      $("times").scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    if (!ok) return;

    var sig = [state.selected, name, email].join("|");
    if (sig !== state.attemptSig) {
      state.attemptSig = sig;
      state.attemptKey = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random();
    }

    setBusy(btn, true, "Booking");
    api("/bookings", {
      method: "POST",
      body: { slot_id: state.selected, name: name, email: email },
      idempotencyKey: state.attemptKey
    }).then(function (r) {
      setBusy(btn, false, "Confirm booking");
      if (r.status === 201 || r.status === 200) {
        state.attemptSig = "";
        try { localStorage.setItem("clinic_email", email); } catch (x) {}
        showConfirmation(r.data);
        state.selected = null;
        $("lookupEmail").value = email;
        loadSlots(true);
        runLookup(email, false);
        return;
      }
      var msg = (r.data && r.data.error) || "";
      if (r.status === 409) {
        toast("Someone just took that time. Pick another one.", "error");
        state.selected = null;
        loadSlots(true);
      } else if (r.status === 429) {
        startRateCountdown(r.retryAfter);
      } else if (r.status === 400 && /started/i.test(msg)) {
        toast("That time has already started. Pick another one.", "error");
        state.selected = null;
        loadSlots(true);
      } else if (r.status === 404) {
        toast("That time is no longer listed. Pick another one.", "error");
        state.selected = null;
        loadSlots(true);
      } else if (r.status === 400) {
        toast(msg ? msg + "." : "Check your details and try again.", "error");
      } else if (r.status === 401) {
        toast("This page isn't authorised to book. Contact the clinic.", "error");
      } else {
        toast("Something went wrong on our side. Try again in a moment.", "error");
      }
    }).catch(function () {
      setBusy(btn, false, "Confirm booking");
      toast("Couldn't reach the server. Your booking wasn't made. Try again.", "error");
    });
  }

  /* Confirmation dialog */

  function showConfirmation(b) {
    state.active = b;
    state.cancelArmed = false;
    var info = slotInfo(b.slot_id);
    $("confSub").textContent = "A confirmation is saved under " + b.email + ".";
    $("cDoctor").textContent = info ? info.provider : "Your doctor";
    $("cDay").textContent = info ? fmtDayLong.format(info.start) : "";
    $("cTime").textContent = info ? fmtTime.format(info.start) + " to " + fmtTime.format(info.end) : "";
    $("cName").textContent = titleCase(b.name);
    $("cRef").textContent = b.id.slice(0, 8).toUpperCase();
    $("cFull").textContent = b.id + "  (slot " + b.slot_id + ")";
    $("cancelBtn").textContent = "Cancel booking";
    $("copyBtn").textContent = "Copy booking ID";
    var d = $("confirmDialog");
    if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
  }

  function closeConfirmation() {
    var d = $("confirmDialog");
    if (typeof d.close === "function") d.close(); else d.removeAttribute("open");
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = el("textarea", { "aria-hidden": "true" });
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy") ? resolve() : reject(); } catch (e) { reject(e); }
      ta.remove();
    });
  }

  function cancelBooking(id) {
    return api("/bookings/" + encodeURIComponent(id), { method: "DELETE" }).then(function (r) {
      if (r.ok) { toast("Booking cancelled. The time is free again.", "ok"); return true; }
      if (r.status === 409) { toast("That booking was already cancelled.", "error"); return true; }
      toast("Couldn't cancel. Try again.", "error");
      return false;
    }).catch(function () {
      toast("Couldn't reach the server. Try again.", "error");
      return false;
    });
  }

  /* Lookup */

  function bookingRow(b) {
    var info = slotInfo(b.slot_id);
    var past = info && info.start.getTime() <= Date.now();
    var when = info ? fmtDayLong.format(info.start) + ", " + fmtTime.format(info.start) : "Appointment";
    var badgeClass = b.status === "cancelled" ? "cancelled" : (past ? "past" : "confirmed");
    var badgeText = b.status === "cancelled" ? "Cancelled" : (past ? "Past" : "Confirmed");

    var right = el("div", { class: "right" }, [el("span", { class: "badge " + badgeClass, text: badgeText })]);
    if (b.status === "confirmed" && !past) {
      var armed = false;
      var btn = el("button", { class: "btn small danger", type: "button", text: "Cancel" });
      btn.addEventListener("click", function () {
        if (!armed) {
          armed = true; btn.textContent = "Sure?";
          setTimeout(function () { armed = false; btn.textContent = "Cancel"; }, 4000);
          return;
        }
        btn.setAttribute("aria-busy", "true");
        cancelBooking(b.id).then(function (done) {
          btn.removeAttribute("aria-busy");
          if (done) { loadSlots(true); runLookup(state.lookupEmail, false); }
        });
      });
      right.appendChild(btn);
    }

    return el("li", { class: "booking" }, [
      el("div", {}, [
        el("div", { class: "who", text: info ? info.provider : "Appointment" }),
        el("div", { class: "when", text: when }),
        el("div", { class: "ref", text: "Ref " + b.id.slice(0, 8).toUpperCase() })
      ]),
      right
    ]);
  }

  function runLookup(email, append) {
    var btn = $("lookupBtn");
    var list = $("bookingList");
    $("lookupErr").textContent = "";
    if (!append) { state.nextCursor = null; state.lookupEmail = email; }
    var url = "/bookings?email=" + encodeURIComponent(email) + "&limit=" + PAGE_SIZE +
      (append && state.nextCursor ? "&cursor=" + encodeURIComponent(state.nextCursor) : "");
    setBusy(btn, true, "Searching");
    return api(url).then(function (r) {
      setBusy(btn, false, "Search");
      if (!r.ok) {
        $("lookupErr").textContent = (r.data && r.data.error) ? r.data.error : "Couldn't load bookings. Try again.";
        return;
      }
      var items = (r.data && r.data.bookings) || [];
      if (!append) list.replaceChildren();
      if (!items.length && !append) {
        list.appendChild(el("li", { class: "empty" }, [
          el("h3", { text: "No bookings found" }),
          el("p", { text: "Check the spelling, or book your first visit above." })
        ]));
      }
      items.forEach(function (b) { list.appendChild(bookingRow(b)); });
      state.nextCursor = (r.data && r.data.next_cursor) || null;
      $("moreBtn").hidden = !state.nextCursor;
    }).catch(function () {
      setBusy(btn, false, "Search");
      $("lookupErr").textContent = "Couldn't reach the server. Try again.";
    });
  }

  /* Wire up */

  function init() {
    $("bookingForm").addEventListener("submit", onSubmit);
    $("name").addEventListener("input", function () { setErr("name", "nameErr", ""); });
    $("email").addEventListener("input", function () { setErr("email", "emailErr", ""); });
    $("refreshBtn").addEventListener("click", function () { loadSlots(false); });

    $("lookupForm").addEventListener("submit", function (e) {
      e.preventDefault();
      var email = $("lookupEmail").value.trim().toLowerCase();
      if (!EMAIL_RE.test(email)) { $("lookupErr").textContent = "Enter the email you booked with"; return; }
      runLookup(email, false);
    });
    $("moreBtn").addEventListener("click", function () { runLookup(state.lookupEmail, true); });

    $("doneBtn").addEventListener("click", closeConfirmation);
    $("copyBtn").addEventListener("click", function () {
      if (!state.active) return;
      copyText(state.active.id).then(function () { $("copyBtn").textContent = "Copied"; })
        .catch(function () { toast("Couldn't copy. Select the ID under Details instead.", "error"); });
    });
    $("cancelBtn").addEventListener("click", function () {
      if (!state.active) return;
      if (!state.cancelArmed) {
        state.cancelArmed = true;
        $("cancelBtn").textContent = "Tap again to cancel";
        return;
      }
      cancelBooking(state.active.id).then(function (done) {
        if (done) { closeConfirmation(); loadSlots(true); runLookup(state.lookupEmail || state.active.email, false); }
      });
    });

    $("themeBtn").addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", cur);
      try { localStorage.setItem("clinic_theme", cur); } catch (e) {}
    });

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && state.loaded) loadSlots(true);
    });

    var saved = "";
    try { saved = localStorage.getItem("clinic_email") || ""; } catch (e) {}
    if (saved) { $("email").value = saved; $("lookupEmail").value = saved; }

    loadSlots(false);
  }

  init();
})();
