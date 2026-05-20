let toastTimer = null;

function showToast(message, isError = false) {
  const el = document.getElementById("toast");
  if (!el) {
    alert(message);
    return;
  }
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 5200);
}

const api = (path, opts = {}) =>
  fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  }).then(async (r) => {
    if (!r.ok) {
      let msg = r.statusText;
      try {
        const j = await r.json();
        msg = j.detail ?? JSON.stringify(j);
      } catch {
        const t = await r.text();
        if (t) msg = t;
      }
      if (typeof msg === "object" && msg !== null && Array.isArray(msg)) {
        msg = msg.map((e) => e.msg || JSON.stringify(e)).join("; ");
      }
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    const ct = r.headers.get("content-type") || "";
    if (ct.includes("application/json")) return r.json();
    return r.text();
  });

function debounce(fn, wait) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(null, args), wait);
  };
}

const SURVEY_MOODS = [
  { label: "Литература / характер", tag: "literary" },
  { label: "Фентъзи и мит", tag: "fantasy" },
  { label: "Напрежение и мистерия", tag: "thriller" },
  { label: "Романтика", tag: "romance" },
  { label: "История и епос", tag: "history" },
  { label: "Наука и идеи", tag: "science" },
];

const SURVEY_FORMATS = [
  { label: "Роман", tag: "novel" },
  { label: "Разкази", tag: "stories" },
  { label: "Нехудожествена", tag: "nonfiction" },
  { label: "Поезия", tag: "poetry" },
  { label: "Серии и епоси", tag: "series" },
];

function setupRegSurveyChips() {
  const mEl = document.getElementById("regMoods");
  const fEl = document.getElementById("regFormats");
  if (!mEl || !fEl) return;
  const fill = (el, items) => {
    items.forEach(({ label, tag }) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip survey-chip";
      b.textContent = label;
      b.dataset.tag = tag;
      b.addEventListener("click", () => b.classList.toggle("on"));
      el.appendChild(b);
    });
  };
  fill(mEl, SURVEY_MOODS);
  fill(fEl, SURVEY_FORMATS);
}

function collectRegSurvey() {
  const paceEl = document.querySelector('input[name="regPace"]:checked');
  const reading_pace = paceEl ? paceEl.value : "steady";
  const moods = [...document.querySelectorAll("#regMoods .survey-chip.on")].map((b) => b.dataset.tag);
  const formats = [...document.querySelectorAll("#regFormats .survey-chip.on")].map((b) => b.dataset.tag);
  const noteRaw = document.getElementById("regSurveyNote");
  const note = noteRaw && noteRaw.value ? noteRaw.value.trim() : "";
  const out = { reading_pace };
  if (moods.length) out.moods = moods;
  if (formats.length) out.formats = formats;
  if (note) out.note = note;
  return out;
}

let currentMe = null;
let tasteState = [];
let onboardingQuizStep = 0;
let browseQuery = "";
let browseOffset = 0;
let evalLoadedOnce = false;

const QUIZ_STEP_LABELS = [
  "Стъпка 1 от 4",
  "Стъпка 2 от 4",
  "Стъпка 3 от 4",
  "Стъпка 4 от 4",
];

let bookSignals = { books: {} };

async function loadBookSignals() {
  if (!currentMe) {
    bookSignals = { books: {} };
    return;
  }
  bookSignals = await api("/api/me/book-signals");
}

function getBookSignal(bookId) {
  const b = bookSignals.books[String(bookId)];
  return b || { last_rating: null, last_event: null };
}

function interactionToastMessage(eventType, rating) {
  if (eventType === "rating" && rating != null) return `Оценка ${rating}/5 — записано.`;
  const map = {
    like: "Харесано.",
    dislike: "Отбелязано: не харесваш.",
    to_read: "В списъка „За четене“.",
    finished: "Отбелязано като прочетено.",
  };
  return map[eventType] || "Записано.";
}

function hoverStars(container, upTo) {
  [...container.querySelectorAll(".star")].forEach((s) => {
    const v = Number(s.dataset.value);
    s.classList.toggle("hover", v <= upTo);
  });
}

function syncStarHover(container, bookId) {
  const sig = getBookSignal(bookId);
  const current = sig.last_rating != null ? Math.round(Number(sig.last_rating)) : 0;
  [...container.querySelectorAll(".star")].forEach((s) => {
    const v = Number(s.dataset.value);
    s.classList.toggle("on", current > 0 && v <= current);
    s.classList.remove("hover");
  });
}

function renderStarRating(container, bookId) {
  container.innerHTML = "";
  container.className = "star-rating";
  container.dataset.bookId = String(bookId);
  const sig = getBookSignal(bookId);
  const current = sig.last_rating != null ? Math.round(Number(sig.last_rating)) : 0;
  for (let i = 1; i <= 5; i++) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "star" + (current > 0 && i <= current ? " on" : "");
    btn.dataset.value = String(i);
    btn.setAttribute("aria-label", `Оценка ${i} от 5`);
    btn.textContent = "★";
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      sendInteraction(bookId, "rating", i).catch(() => {});
    });
    btn.addEventListener("mouseenter", () => hoverStars(container, i));
    container.appendChild(btn);
  }
  container.onmouseleave = () => syncStarHover(container, bookId);
}

const QUICK_EVENTS = [
  { type: "like", label: "Харесвам" },
  { type: "dislike", label: "Не харесвам" },
  { type: "to_read", label: "За четене" },
  { type: "finished", label: "Прочетох" },
];

function renderQuickActions(container, bookId) {
  container.innerHTML = "";
  container.className = "quick-actions";
  container.dataset.bookId = String(bookId);
  const sig = getBookSignal(bookId);
  const active = sig.last_event;
  QUICK_EVENTS.forEach(({ type, label }) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className =
      "btn small quick-act" +
      (type === "dislike" ? " danger" : "") +
      (active === type ? " is-active" : "");
    b.dataset.eventType = type;
    b.textContent = label;
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      sendInteraction(bookId, type).catch(() => {});
    });
    container.appendChild(b);
  });
}

function refreshCardActions(bookId) {
  const card = document.querySelector(`.card[data-book-id="${bookId}"]`);
  if (!card) return;
  const stars = card.querySelector(".star-rating");
  const quick = card.querySelector(".quick-actions");
  if (stars) renderStarRating(stars, bookId);
  if (quick) renderQuickActions(quick, bookId);
}

function refreshDrawerActionsIfOpen(bookId) {
  const drawer = document.getElementById("drawer");
  const drawerBody = document.getElementById("drawerBody");
  if (!drawer || !drawerBody || !drawer.classList.contains("on")) return;
  const inner = drawerBody.querySelector(".drawer-inner");
  if (!inner || Number(inner.dataset.drawerBookId) !== bookId) return;
  const stars = drawerBody.querySelector(".star-rating");
  const quick = drawerBody.querySelector(".quick-actions");
  if (stars) renderStarRating(stars, bookId);
  if (quick) renderQuickActions(quick, bookId);
}

function coverSrc(book) {
  return book.cover_url || "/static/placeholder-cover.svg";
}

function bookCard(book, extra = {}, options = {}) {
  const interactive = options.interactive !== false;
  const wrap = document.createElement("article");
  wrap.className = "card";
  wrap.dataset.bookId = String(book.id);
  const cover = document.createElement("img");
  cover.className = "cover";
  cover.alt = "";
  cover.loading = "lazy";
  cover.src = coverSrc(book);
  cover.onerror = () => {
    cover.src = "/static/placeholder-cover.svg";
  };
  wrap.appendChild(cover);
  const body = document.createElement("div");
  body.className = "card-body";
  body.innerHTML = `
    <h3>${escapeHtml(book.title)}</h3>
    <div class="meta">${escapeHtml(book.authors)} · ${escapeHtml(book.language)}${book.year ? " · " + book.year : ""}</div>
    <div class="tags">${escapeHtml(book.tags)}</div>
  `;
  if (extra.why) {
    const w = document.createElement("div");
    w.className = "why";
    w.textContent = extra.why;
    body.appendChild(w);
  }
  if (interactive) {
    const foot = document.createElement("div");
    foot.className = "card-actions-block";

    const rowStars = document.createElement("div");
    rowStars.className = "action-row";
    const ls = document.createElement("span");
    ls.className = "action-label";
    ls.textContent = "Оценка";
    const starsHost = document.createElement("div");
    rowStars.appendChild(ls);
    rowStars.appendChild(starsHost);

    const rowQuick = document.createElement("div");
    rowQuick.className = "action-row";
    const lq = document.createElement("span");
    lq.className = "action-label";
    lq.textContent = "Бързи действия";
    const quickHost = document.createElement("div");
    rowQuick.appendChild(lq);
    rowQuick.appendChild(quickHost);

    const hint = document.createElement("p");
    hint.className = "card-interact-hint";
    hint.textContent = "Натисни звезди или бутоните — виж съобщение горе. Празно място отваря детайли.";

    foot.appendChild(rowStars);
    foot.appendChild(rowQuick);
    foot.appendChild(hint);

    renderStarRating(starsHost, book.id);
    renderQuickActions(quickHost, book.id);

    body.appendChild(foot);
  }
  wrap.appendChild(body);
  wrap.style.cursor = "pointer";
  wrap.addEventListener("click", (e) => {
    if (e.target.closest("button")) return;
    openBookDrawer(book.id);
  });
  return wrap;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function sendInteraction(bookId, eventType, rating = null, { silent = false } = {}) {
  requireSession();
  const body = { book_id: bookId, event_type: eventType };
  if (rating != null) body.rating = rating;
  try {
    await api("/api/interactions", { method: "POST", body: JSON.stringify(body) });
    await loadBookSignals();
    refreshCardActions(bookId);
    refreshDrawerActionsIfOpen(bookId);
    if (!silent) showToast(interactionToastMessage(eventType, rating));
  } catch (e) {
    showToast(String(e.message || e), true);
    throw e;
  }
}

function requireSession() {
  if (!currentMe) throw new Error("Нужен е вход");
}

async function openBookDrawer(bookId) {
  if (currentMe) await loadBookSignals().catch(() => {});
  const [book, sim, stats] = await Promise.all([
    api(`/api/books/${bookId}`),
    api(`/api/similar/${bookId}?k=8`),
    api(`/api/books/${bookId}/stats`).catch(() => null),
  ]);
  let social = { explanation: null };
  if (currentMe) {
    try {
      social = await api(`/api/book-social/${bookId}`);
    } catch (_) {}
  }
  const statsHtml =
    stats && (stats.rating_count || stats.like_count || stats.finished_count || stats.to_read_count)
      ? `
      <div class="book-stats">
        ${stats.avg_rating != null ? `<span>⭐ ${stats.avg_rating}</span>` : ""}
        ${stats.rating_count ? `<span>(${stats.rating_count} оценки)</span>` : ""}
        ${stats.like_count ? `<span>👍 ${stats.like_count}</span>` : ""}
        ${stats.finished_count ? `<span>✅ ${stats.finished_count}</span>` : ""}
        ${stats.to_read_count ? `<span>📌 ${stats.to_read_count}</span>` : ""}
      </div>
      `
      : "";
  const drawer = document.getElementById("drawerBody");
  const mineBlock =
    currentMe &&
    `
    <h3 class="drawer-section-title">Твоята оценка</h3>
    <div id="drawerStarsHost"></div>
    <h3 class="drawer-section-title">Бързи действия</h3>
    <div id="drawerQuickHost"></div>
    `;
  drawer.innerHTML = `
    <div class="drawer-inner" data-drawer-book-id="${bookId}">
      <img class="drawer-cover" src="${escapeHtml(coverSrc(book))}" alt="" />
      <h2>${escapeHtml(book.title)}</h2>
      <p class="meta">${escapeHtml(book.authors)}</p>
      ${statsHtml}
      <p>${escapeHtml(book.description)}</p>
      <p class="tags">${escapeHtml(book.tags)}</p>
      ${social.explanation ? `<p class="why">${escapeHtml(social.explanation)}</p>` : ""}
      ${mineBlock || ""}
      <h3>Подобни</h3>
      <div class="mini-list" id="simList"></div>
      <h3>Коментар</h3>
      <p class="hint drawer-comment-hint">Избери звездите по-горе, после запази текста.</p>
      <textarea id="cmt" rows="3" style="width:100%" placeholder="Мнение по книгата…" ${
        currentMe ? "" : "disabled"
      }></textarea>
      <div class="row drawer-save-row">
        <button type="button" class="btn primary" id="saveCmt" ${currentMe ? "" : "disabled"}>Запази коментара</button>
      </div>
    </div>
  `;
  const dimg = drawer.querySelector(".drawer-cover");
  if (dimg)
    dimg.onerror = () => {
      dimg.src = "/static/placeholder-cover.svg";
    };
  const list = drawer.querySelector("#simList");
  sim.forEach((b) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = b.title;
    btn.addEventListener("click", () => openBookDrawer(b.id));
    list.appendChild(btn);
  });
  if (currentMe) {
    const starsHost = drawer.querySelector("#drawerStarsHost");
    const quickHost = drawer.querySelector("#drawerQuickHost");
    if (starsHost) renderStarRating(starsHost, bookId);
    if (quickHost) renderQuickActions(quickHost, bookId);
    const saveBtn = drawer.querySelector("#saveCmt");
    if (saveBtn) {
      saveBtn.addEventListener("click", async () => {
        const txt = drawer.querySelector("#cmt").value.trim();
        const sig = getBookSignal(bookId);
        if (sig.last_rating == null) {
          showToast("Първо избери оценка със звездите.", true);
          return;
        }
        try {
          await api("/api/interactions", {
            method: "POST",
            body: JSON.stringify({
              book_id: bookId,
              event_type: "rating",
              rating: Math.round(Number(sig.last_rating)),
              comment: txt || null,
            }),
          });
          await loadBookSignals();
          refreshCardActions(bookId);
          refreshDrawerActionsIfOpen(bookId);
          showToast("Коментарът е записан.");
        } catch (e) {
          showToast(String(e.message || e), true);
        }
      });
    }
  }
  document.getElementById("drawer").classList.add("on");
  document.getElementById("backdrop").classList.add("on");
  document.getElementById("drawer").setAttribute("aria-hidden", "false");
}

function closeDrawer() {
  document.getElementById("drawer").classList.remove("on");
  document.getElementById("backdrop").classList.remove("on");
  document.getElementById("drawer").setAttribute("aria-hidden", "true");
}

function setAuthUI() {
  const landing = document.getElementById("landingScreen");
  const shell = document.getElementById("appShell");
  if (!landing || !shell) return;

  if (currentMe) {
    landing.classList.add("hidden");
    shell.classList.remove("hidden");
    const who = document.getElementById("whoami");
    if (who) {
      who.textContent =
        currentMe.username + (currentMe.onboarding_completed ? "" : " · попълни анкетата");
    }
  } else {
    landing.classList.remove("hidden");
    shell.classList.add("hidden");
    closeOnboardingModal();
  }
}

function updateMetaLabels(meta) {
  const bk = typeof meta.book_count === "number" ? ` · ${meta.book_count} кн.` : "";
  const suffix = `${meta.database_ok ? "" : " · БД offline"}`;
  const line = `v${meta.version} · ${meta.environment}${bk}${suffix}`;
  const v = document.getElementById("verLabel");
  const vl = document.getElementById("verLabelLanding");
  if (v) v.textContent = line;
  if (vl) vl.textContent = line;
  const tw = document.getElementById("olTokenWrap");
  if (tw && meta.catalog_token_required) tw.classList.remove("hidden");
}

async function refreshMe() {
  currentMe = await api("/api/auth/me");
  setAuthUI();
  if (currentMe) await loadBookSignals().catch(() => {});
  return currentMe;
}

async function loadRecommendations() {
  const btn = document.getElementById("btnRefreshRec");
  const oldText = btn ? btn.textContent : "";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Зареждане…";
  }
  try {
    requireSession();
    await loadBookSignals().catch(() => {});
    const k = 12;
    const data = await api(`/api/recommendations?k=${k}`);
    const root = document.getElementById("recList");
    root.innerHTML = "";
    data.forEach((item) => {
      root.appendChild(bookCard(item.book, { why: item.explanation }));
    });
  } catch (e) {
    const msg = String(e.message || e);
    showToast(msg, true);
    throw e;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = oldText || "Обнови препоръките";
    }
  }
}

async function loadFriendRecommendations() {
  const btn = document.getElementById("btnRefreshFriendsRec");
  const oldText = btn ? btn.textContent : "";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Зареждане…";
  }
  try {
    requireSession();
    const data = await api("/api/friends/recommendations?limit=12");
    const root = document.getElementById("friendsRecList");
    if (!root) return;
    root.innerHTML = "";
    if (!data || !data.length) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "Още нямаш приятели или няма нови предложения от тях.";
      root.appendChild(p);
      return;
    }
    data.forEach((item) => {
      root.appendChild(bookCard(item.book, { why: item.explanation }, { interactive: false }));
    });
  } catch (e) {
    const msg = String(e.message || e);
    showToast(msg, true);
    throw e;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = oldText || "Обнови";
    }
  }
}

async function loadBooks({ append = false } = {}) {
  const root = document.getElementById("bookList");
  if (!root) return;

  const capEl = document.getElementById("browseCapHint");
  const moreBtn = document.getElementById("btnLoadMore");

  const q = (document.getElementById("bookFilter")?.value || "").trim();
  const qNorm = q.toLowerCase();
  if (!append || qNorm !== browseQuery) {
    browseQuery = qNorm;
    browseOffset = 0;
    append = false;
  }

  if (moreBtn) {
    moreBtn.disabled = true;
    moreBtn.textContent = "Зареждане…";
  }
  if (!append && capEl) {
    capEl.textContent = "Зареждане…";
    capEl.classList.remove("hidden");
  }

  const limit = 80;
  let hasMore = false;
  let ok = false;
  try {
    const res = await api(
      `/api/books?limit=${limit}&offset=${browseOffset}&q=${encodeURIComponent(qNorm)}`
    );

    const items = Array.isArray(res?.items) ? res.items : [];
    hasMore = !!res?.has_more;

    if (!append) root.innerHTML = "";
    items.forEach((b) => root.appendChild(bookCard(b, {}, { interactive: false })));

    browseOffset += items.length;
    ok = true;
  } catch (e) {
    const msg = String(e.message || e);
    showToast(msg, true);
    if (capEl) {
      capEl.textContent = msg;
      capEl.classList.remove("hidden");
    }
    hasMore = false;
  } finally {
    if (ok && capEl) {
      if (!browseOffset) {
        capEl.textContent = qNorm ? "Няма резултати." : "Няма книги в каталога.";
        capEl.classList.remove("hidden");
      } else if (hasMore) {
        capEl.textContent = `Показани са първите ${browseOffset} резултата. Можеш да уточниш търсенето или да заредиш още.`;
        capEl.classList.remove("hidden");
      } else {
        capEl.textContent = "";
        capEl.classList.add("hidden");
      }
    }

    if (moreBtn) {
      if (ok && hasMore) {
        moreBtn.classList.remove("hidden");
        moreBtn.disabled = false;
        moreBtn.textContent = "Покажи още";
      } else {
        moreBtn.classList.add("hidden");
        moreBtn.disabled = false;
        moreBtn.textContent = "Покажи още";
      }
    }
  }
}

async function renderBookFilter() {
  await loadBooks({ append: false });
}

async function loadPopular() {
  const data = await api("/api/popular?limit=16");
  const root = document.getElementById("popularList");
  root.innerHTML = "";
  data.forEach((b) => root.appendChild(bookCard(b, {}, { interactive: false })));
}

async function loadLibrary() {
  requireSession();
  const lib = await api("/api/library");
  const fill = (id, arr) => {
    const el = document.getElementById(id);
    el.innerHTML = "";
    arr.forEach((b) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = b.title;
      btn.addEventListener("click", () => openBookDrawer(b.id));
      el.appendChild(btn);
    });
  };
  fill("libToRead", lib.to_read);
  fill("libFinished", lib.finished);
  fill("libFav", lib.favorites);
}

async function loadFriends() {
  requireSession();
  const data = await api("/api/friends");
  const root = document.getElementById("friendsList");
  root.innerHTML = "";
  data.forEach((f) => {
    const span = document.createElement("span");
    span.className = "chip";
    span.textContent = f.username;
    root.appendChild(span);
  });
}

async function loadFriendRequests() {
  requireSession();
  const inRoot = document.getElementById("incomingReqs");
  const outRoot = document.getElementById("outgoingReqs");
  if (!inRoot || !outRoot) return;

  inRoot.innerHTML = '<p class="hint">Зареждане…</p>';
  outRoot.innerHTML = '<p class="hint">Зареждане…</p>';
  const res = await api("/api/friends/requests");
  const incoming = Array.isArray(res?.incoming) ? res.incoming : [];
  const outgoing = Array.isArray(res?.outgoing) ? res.outgoing : [];

  inRoot.innerHTML = "";
  if (!incoming.length) {
    inRoot.innerHTML = '<p class="hint">Няма входящи покани.</p>';
  } else {
    incoming.forEach((r) => {
      const row = document.createElement("div");
      row.className = "req-row";
      row.innerHTML = `
        <div class="req-row__main">
          <strong>${escapeHtml(r.from_user.username)}</strong>
          <div class="req-row__meta">иска да те добави</div>
        </div>
        <div class="req-row__actions">
          <button type="button" class="btn small primary">Приеми</button>
          <button type="button" class="btn small danger">Откажи</button>
        </div>
      `;
      const [btnOk, btnNo] = row.querySelectorAll("button");
      btnOk.addEventListener("click", async (e) => {
        e.stopPropagation();
        await api(`/api/friends/requests/${r.id}/accept`, { method: "POST" });
        showToast("Поканата е приета.");
        await Promise.allSettled([loadFriendRequests(), loadFriends(), loadFriendRecommendations()]);
      });
      btnNo.addEventListener("click", async (e) => {
        e.stopPropagation();
        await api(`/api/friends/requests/${r.id}/decline`, { method: "POST" });
        showToast("Поканата е отказана.");
        await loadFriendRequests();
      });
      inRoot.appendChild(row);
    });
  }

  outRoot.innerHTML = "";
  if (!outgoing.length) {
    outRoot.innerHTML = '<p class="hint">Няма изпратени покани.</p>';
  } else {
    outgoing.forEach((r) => {
      const row = document.createElement("div");
      row.className = "req-row";
      row.innerHTML = `
        <div class="req-row__main">
          <strong>${escapeHtml(r.to_user.username)}</strong>
          <div class="req-row__meta">изпратена покана</div>
        </div>
        <div class="req-row__actions">
          <button type="button" class="btn small danger">Отмени</button>
        </div>
      `;
      row.querySelector("button").addEventListener("click", async (e) => {
        e.stopPropagation();
        await api(`/api/friends/requests/${r.id}/cancel`, { method: "POST" });
        showToast("Поканата е отменена.");
        await loadFriendRequests();
      });
      outRoot.appendChild(row);
    });
  }
}

async function sendFriendRequest(username) {
  try {
    requireSession();
    const res = await api("/api/friends/requests", {
      method: "POST",
      body: JSON.stringify({ friend_username: username }),
    });
    if (res?.already_friends) showToast("Вече сте приятели.");
    else if (res?.duplicate) showToast("Вече имаш изпратена покана.");
    else showToast("Поканата е изпратена.");
    await loadFriendRequests().catch(() => {});
  } catch (e) {
    showToast(String(e.message || e), true);
  }
}

async function searchUsers(raw) {
  const root = document.getElementById("userSearchResults");
  if (!root) return;
  const q = String(raw || "").trim();
  if (q.length < 2) {
    root.innerHTML = "";
    return;
  }
  try {
    requireSession();
    root.innerHTML = '<p class="hint">Търсене…</p>';
    const data = await api(`/api/users/search?q=${encodeURIComponent(q)}&limit=12`);
    root.innerHTML = "";
    if (!data || !data.length) {
      root.innerHTML = '<p class="hint">Няма резултати.</p>';
      return;
    }
    data.forEach((u) => {
      const row = document.createElement("div");
      row.className = "user-row";
      row.innerHTML = `
        <div class="user-row__name">${escapeHtml(u.username)}</div>
        <div class="user-row__actions">
          <button type="button" class="btn small">Покани</button>
        </div>
      `;
      row.querySelector("button").addEventListener("click", (e) => {
        e.stopPropagation();
        sendFriendRequest(u.username);
      });
      root.appendChild(row);
    });
  } catch (e) {
    root.innerHTML = "";
    showToast(String(e.message || e), true);
  }
}

async function loadFriendsPage() {
  await Promise.allSettled([loadFriends(), loadFriendRequests()]);
}

async function loadEval() {
  const kSel = document.getElementById("evalK");
  const btn = document.getElementById("btnRunEval");
  const noteEl = document.getElementById("evalNote");
  const repEl = document.getElementById("evalReport");
  const tb = document.querySelector("#evalTable tbody");
  if (!tb) return;

  const k = kSel ? parseInt(kSel.value || "10", 10) : 10;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Изчисляване…";
  }
  if (noteEl) noteEl.textContent = "Изчисляване на метриките…";

  try {
    const rep = await api(`/api/evaluation?k=${k}`);
    tb.innerHTML = "";
    rep.rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${escapeHtml(r.method)}</td><td>${r.precision_at_k}</td><td>${r.recall_at_k}</td><td>${r.ndcg_at_k}</td>`;
      tb.appendChild(tr);
    });
    evalLoadedOnce = true;
    if (noteEl) {
      const fold = rep.users_in_fold ? ` · fold users: ${rep.users_in_fold}` : "";
      noteEl.textContent = (rep.note || "").trim() ? `${rep.note}${fold}` : (fold ? fold.slice(3) : "");
    }
    if (repEl) {
      repEl.innerHTML = `
        <p><strong>Проблем:</strong> ${escapeHtml(rep.problem_statement || "")}</p>
        <p><strong>Хипотеза:</strong> ${escapeHtml(rep.hypothesis || "")}</p>
        <p><strong>Методология:</strong> ${escapeHtml(rep.methodology || "")}</p>
        <p><strong>Ограничения:</strong> ${escapeHtml(rep.limitations || "")}</p>
      `;
    }
    showToast("Оценката е готова.");
  } catch (e) {
    const msg = String(e.message || e);
    showToast(msg, true);
    if (noteEl) noteEl.textContent = msg;
    throw e;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Изчисли";
    }
  }
}

function setupEvalControls() {
  const kSel = document.getElementById("evalK");
  const btn = document.getElementById("btnRunEval");
  if (btn) btn.addEventListener("click", () => loadEval().catch(() => {}));
  if (kSel) {
    kSel.addEventListener("change", () => {
      if (evalLoadedOnce) loadEval().catch(() => {});
    });
  }
}

function setupTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.dataset.tab;
      const needAuth = ["feed", "browse", "library", "friends", "eval"].includes(name);
      if (!currentMe && needAuth) {
        showToast("Влез или се регистрирай.", true);
        return;
      }
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      document.getElementById("tab-" + name).classList.add("active");
      if (name === "browse") renderBookFilter().catch(() => {});
      if (name === "library") loadLibrary().catch(() => {});
      if (name === "friends") loadFriendsPage().catch(() => {});
      if (name === "eval" && !evalLoadedOnce) loadEval().catch(() => {});
    });
  });
}

const GENRES = [
  "фентъзи",
  "sci-fi",
  "трилър",
  "романс",
  "класика",
  "нехудожествена",
  "българска литература",
  "young adult",
  "история",
  "дистопия",
];

async function loadTasteDeck() {
  const host = document.getElementById("tasteDeck");
  if (!host || !currentMe) return;
  host.innerHTML = "<p class=\"hint\">Зареждане…</p>";
  try {
    const books = await api("/api/onboarding/taste-deck?n=12");
    tasteState = [];
    host.innerHTML = "";
    books.forEach((b) => {
      tasteState.push({ book_id: b.id, reaction: null });
      const row = document.createElement("div");
      row.className = "taste-row";
      row.innerHTML = `
        <img src="${escapeHtml(coverSrc(b))}" alt="" class="taste-cover" />
        <div class="taste-info">
          <strong>${escapeHtml(b.title)}</strong>
          <div class="meta">${escapeHtml(b.authors)}</div>
        </div>
        <div class="taste-actions" data-bid="${b.id}">
          <button type="button" class="btn small" data-r="like">Харесвам</button>
          <button type="button" class="btn small danger" data-r="dislike">Не</button>
          <button type="button" class="btn small" data-r="finished">Чел(а) съм</button>
        </div>
      `;
      const img = row.querySelector(".taste-cover");
      img.onerror = () => {
        img.src = "/static/placeholder-cover.svg";
      };
      row.querySelectorAll("[data-r]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const bid = Number(btn.closest(".taste-actions").dataset.bid);
          const r = btn.getAttribute("data-r");
          const entry = tasteState.find((x) => x.book_id === bid);
          if (entry) entry.reaction = r;
          row.querySelectorAll("[data-r]").forEach((x) => x.classList.remove("taste-picked"));
          btn.classList.add("taste-picked");
        });
      });
      host.appendChild(row);
    });
  } catch (e) {
    host.innerHTML = `<p class="hint">${escapeHtml(String(e.message || e))}</p>`;
  }
}

function collectTasteReactions() {
  return tasteState
    .filter((x) => x.reaction)
    .map((x) => ({ book_id: x.book_id, reaction: x.reaction }));
}

function collectSelectedGenres() {
  return [...document.querySelectorAll("#genrePick .chip.on")]
    .map((el) => el.textContent.trim())
    .filter(Boolean);
}

function activateMainTab(name) {
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === name);
  });
  document.querySelectorAll(".panel").forEach((p) => {
    p.classList.toggle("active", p.id === "tab-" + name);
  });
}

function openOnboardingModal() {
  closeDrawer();
  const backdropEl = document.getElementById("backdrop");
  if (backdropEl) backdropEl.classList.remove("on");
  const dr = document.getElementById("drawer");
  if (dr) {
    dr.classList.remove("on");
    dr.setAttribute("aria-hidden", "true");
  }

  const m = document.getElementById("onboardingModal");
  if (!m) return;
  m.classList.remove("hidden");
  m.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-quiz-open");
  const sheet = m.querySelector(".onboarding-modal-sheet");
  if (sheet) sheet.focus({ preventScroll: true });
}

function closeOnboardingModal() {
  const m = document.getElementById("onboardingModal");
  if (!m) return;
  m.classList.add("hidden");
  m.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-quiz-open");
}

function showOnboardingQuizStep(step) {
  onboardingQuizStep = Math.max(0, Math.min(3, step));
  document.querySelectorAll(".quiz-pane").forEach((pane) => {
    const s = Number(pane.dataset.quizStep);
    const on = s === onboardingQuizStep;
    pane.classList.toggle("hidden", !on);
    pane.setAttribute("aria-hidden", on ? "false" : "true");
  });

  const bar = document.getElementById("quizProgressBar");
  const meta = document.getElementById("quizStepMeta");
  const pctRounded = Math.round(((onboardingQuizStep + 1) / 4) * 100);
  if (bar) bar.style.width = `${pctRounded}%`;
  if (meta) meta.textContent = QUIZ_STEP_LABELS[onboardingQuizStep];
  const progWrap = document.getElementById("quizProgressWrap");
  if (progWrap) progWrap.setAttribute("aria-valuenow", String(pctRounded));

  const prev = document.getElementById("quizPrev");
  const next = document.getElementById("quizNext");
  const fin = document.getElementById("quizFinish");
  if (prev) prev.classList.toggle("hidden", onboardingQuizStep === 0);
  if (next) next.classList.toggle("hidden", onboardingQuizStep === 3);
  if (fin) fin.classList.toggle("hidden", onboardingQuizStep !== 3);

  if (onboardingQuizStep === 3) loadTasteDeck();
}

function launchColdStartQuiz() {
  onboardingQuizStep = 0;
  activateMainTab("feed");
  showOnboardingQuizStep(0);
  openOnboardingModal();
}

async function submitOnboardingQuiz() {
  try {
    requireSession();
  } catch (e) {
    showToast(e.message, true);
    return;
  }
  const genres = collectSelectedGenres();
  if (!genres.length) {
    showToast("Избери поне един жанр.", true);
    showOnboardingQuizStep(1);
    return;
  }
  const authorsRaw = document.getElementById("onbAuthors").value;
  const authors = authorsRaw
    .split(/[,;\n]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const quick = collectTasteReactions();
  await api("/api/onboarding/complete", {
    method: "POST",
    body: JSON.stringify({
      genres,
      language: document.getElementById("onbLang").value,
      authors,
      quick_reactions: quick,
    }),
  });
  await refreshMe();
  closeOnboardingModal();
  activateMainTab("feed");
  showToast("Профилът е записан.");
  loadRecommendations();
}

function setupOnboarding() {
  const root = document.getElementById("genrePick");
  if (!root) return;
  root.innerHTML = "";
  GENRES.forEach((g) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.textContent = g;
    btn.addEventListener("click", () => btn.classList.toggle("on"));
    root.appendChild(btn);
  });

  const reload = document.getElementById("btnReloadTaste");
  if (reload) reload.addEventListener("click", loadTasteDeck);

  const prev = document.getElementById("quizPrev");
  const next = document.getElementById("quizNext");
  const fin = document.getElementById("quizFinish");
  if (prev)
    prev.addEventListener("click", () => {
      if (onboardingQuizStep > 0) showOnboardingQuizStep(onboardingQuizStep - 1);
    });
  if (next)
    next.addEventListener("click", () => {
      if (onboardingQuizStep === 1) {
        const genres = collectSelectedGenres();
        if (!genres.length) {
          showToast("Избери поне един жанр, за да продължиш.", true);
          return;
        }
      }
      if (onboardingQuizStep < 3) showOnboardingQuizStep(onboardingQuizStep + 1);
    });
  if (fin) fin.addEventListener("click", () => submitOnboardingQuiz().catch((e) => showToast(String(e.message || e), true)));
}

async function refreshAll() {
  if (!currentMe) return;
  await loadBookSignals().catch(() => {});
  await Promise.allSettled([loadRecommendations(), loadFriendRecommendations(), loadPopular()]);
}

function setupLandingTabs() {
  const loginTab = document.getElementById("landingTabLogin");
  const regTab = document.getElementById("landingTabReg");
  const loginPane = document.getElementById("landingLoginPane");
  const regPane = document.getElementById("landingRegisterPane");
  if (!loginTab || !regTab || !loginPane || !regPane) return;

  loginTab.addEventListener("click", () => {
    loginTab.classList.add("active");
    regTab.classList.remove("active");
    loginTab.setAttribute("aria-selected", "true");
    regTab.setAttribute("aria-selected", "false");
    loginPane.classList.remove("hidden");
    regPane.classList.add("hidden");
  });

  regTab.addEventListener("click", () => {
    regTab.classList.add("active");
    loginTab.classList.remove("active");
    regTab.setAttribute("aria-selected", "true");
    loginTab.setAttribute("aria-selected", "false");
    regPane.classList.remove("hidden");
    loginPane.classList.add("hidden");
  });
}

async function init() {
  try {
    const meta = await api("/api/meta");
    updateMetaLabels(meta);
  } catch (_) {
    const v = document.getElementById("verLabel");
    const vl = document.getElementById("verLabelLanding");
    if (v) v.textContent = "";
    if (vl) vl.textContent = "";
  }

  await refreshMe().catch(() => {
    currentMe = null;
    setAuthUI();
  });

  setupRegSurveyChips();
  setupLandingTabs();
  setupTabs();
  setupOnboarding();
  setupEvalControls();

  document.getElementById("btnRefreshRec").addEventListener("click", () => loadRecommendations().catch(() => {}));
  const btnFR = document.getElementById("btnRefreshFriendsRec");
  if (btnFR) btnFR.addEventListener("click", () => loadFriendRecommendations().catch(() => {}));
  const bf = document.getElementById("bookFilter");
  if (bf) {
    const run = debounce(() => renderBookFilter().catch(() => {}), 160);
    bf.addEventListener("input", run);
  }
  const more = document.getElementById("btnLoadMore");
  if (more) {
    more.addEventListener("click", () => loadBooks({ append: true }).catch(() => {}));
  }
  const us = document.getElementById("userSearch");
  if (us) {
    const runSearch = debounce(() => searchUsers(us.value), 180);
    us.addEventListener("input", runSearch);
  }
  document.getElementById("drawerClose").addEventListener("click", closeDrawer);
  document.getElementById("backdrop").addEventListener("click", () => {
    if (document.body.classList.contains("modal-quiz-open")) return;
    closeDrawer();
  });

  document.getElementById("btnLogin").addEventListener("click", async () => {
    const u = document.getElementById("loginUser").value.trim();
    const p = document.getElementById("loginPass").value;
    try {
      currentMe = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: u, password: p }),
      });
      setAuthUI();
      await refreshAll();
      if (!currentMe.onboarding_completed) {
        launchColdStartQuiz();
      }
    } catch (e) {
      showToast(String(e.message || e), true);
    }
  });
  document.getElementById("btnReg").addEventListener("click", async () => {
    const u = document.getElementById("regUser").value.trim();
    const p = document.getElementById("regPass").value;
    const e = document.getElementById("regEmail").value.trim();
    try {
      const survey = collectRegSurvey();
      currentMe = await api("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({ username: u, password: p, email: e || null, survey }),
      });
      setAuthUI();
      await refreshAll();
      if (!currentMe.onboarding_completed) {
        launchColdStartQuiz();
      }
    } catch (err) {
      showToast(String(err.message || err), true);
    }
  });
  const btnOl = document.getElementById("btnOlFetch");
  if (btnOl) {
    btnOl.addEventListener("click", async () => {
      const q = document.getElementById("olQuery").value.trim();
      if (!q) {
        showToast("Въведи търсене.", true);
        return;
      }
      requireSession();
      const lim = Math.min(80, Math.max(1, parseInt(document.getElementById("olLimit").value || "25", 10)));
      const fd = document.getElementById("olDetails").checked;
      const headers = { "Content-Type": "application/json" };
      const tokWrap = document.getElementById("olTokenWrap");
      const needsTok = tokWrap && !tokWrap.classList.contains("hidden");
      const tok = document.getElementById("olToken")?.value?.trim() || "";
      if (needsTok && !tok) {
        showToast("Липсва catalog token.", true);
        return;
      }
      if (tok) headers["X-Catalog-Token"] = tok;
      try {
        const res = await api("/api/catalog/fetch-openlibrary", {
          method: "POST",
          headers,
          body: JSON.stringify({ query: q, limit: lim, fetch_descriptions: fd }),
        });
        showToast(`Готово: ${res.inserted} нови, ${res.updated} актуализирани.`);
        await loadBooks();
        try {
          const meta = await api("/api/meta");
          updateMetaLabels(meta);
        } catch (_) {}
      } catch (err) {
        showToast(String(err.message || err), true);
      }
    });
  }
  document.getElementById("btnLogout").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    currentMe = null;
    onboardingQuizStep = 0;
    bookSignals = { books: {} };
    setAuthUI();
    document.getElementById("recList").innerHTML = "";
  });

  if (currentMe) {
    if (!currentMe.onboarding_completed) {
      launchColdStartQuiz();
    }
    await refreshAll();
  }
}

init().catch((e) => console.error(e));
