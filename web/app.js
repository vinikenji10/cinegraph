// CineGraph — interface de chat.
// No Firebase Hosting, a configuração do projeto vem de /__/firebase/init.json e o login usa
// Firebase Auth (Google). Rodando localmente pela API (modo de desenvolvimento), o login é pulado.

const FIREBASE_VERSION = "13.1.0";
const GENRES_PT = {
  Action: "ação", Adventure: "aventura", Animation: "animação", Children: "infantil", Comedy: "comédia",
  Crime: "crime", Documentary: "documentário", Drama: "drama", Fantasy: "fantasia", "Film-Noir": "film noir",
  Horror: "terror", IMAX: "IMAX", Musical: "musical", Mystery: "mistério", Romance: "romance",
  "Sci-Fi": "ficção científica", Thriller: "suspense", War: "guerra", Western: "faroeste",
};
const ROUTE_LABEL = { sql: "SQL", vector: "Semântica", hybrid: "Híbrida", outro: "Fora do escopo", llm_only: "Só LLM" };
const SORT_LABEL = { relevance: "relevância", rating: "nota", popularity: "popularidade", newest: "mais recentes", oldest: "mais antigos" };

const $ = (id) => document.getElementById(id);
const state = { auth: null, devMode: false, conversationId: null, busy: false };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

const fmt = (x, d = 1) => (x == null ? "—" : Number(x).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }));

// ---------------------------------------------------------------- autenticação

async function initAuth() {
  let config = null;
  try {
    const r = await fetch("/__/firebase/init.json");
    if (r.ok) config = await r.json();
  } catch { /* rodando fora do Hosting */ }

  if (!config) {
    const health = await fetch("/api/health").then((r) => r.json()).catch(() => null);
    if (health && health.auth === false) {
      state.devMode = true;
      $("dev-banner").hidden = false;
      showChat("Modo local");
      return;
    }
    showLogin("Não foi possível carregar a configuração do Firebase.");
    return;
  }

  const base = `https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}`;
  const { initializeApp } = await import(`${base}/firebase-app.js`);
  const { getAuth, GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signOut } = await import(`${base}/firebase-auth.js`);
  const app = initializeApp(config);
  state.auth = getAuth(app);
  state.auth.languageCode = "pt";
  const provider = new GoogleAuthProvider();

  const LOGIN_ERRORS = {
    "auth/configuration-not-found": "O login Google ainda não foi ativado no Firebase (Authentication → Sign-in method → Google).",
    "auth/operation-not-allowed": "O login Google está desativado no Firebase (Authentication → Sign-in method → Google).",
    "auth/unauthorized-domain": `O domínio ${location.hostname} não está autorizado no Firebase Authentication.`,
    "auth/popup-blocked": "O navegador bloqueou a janela de login. Permita pop-ups para este site e tente de novo.",
    "auth/network-request-failed": "Falha de rede ao falar com o Google. Verifique a conexão e tente de novo.",
  };
  $("sign-in").onclick = async () => {
    $("login-error").hidden = true;
    try { await signInWithPopup(state.auth, provider); }
    catch (e) {
      if (e.code === "auth/popup-closed-by-user" || e.code === "auth/cancelled-popup-request") return showLogin(null);
      console.error("login", e.code, e.message);
      showLogin(LOGIN_ERRORS[e.code] || `Não foi possível entrar (${e.code || "erro desconhecido"}). Tente de novo.`);
    }
  };
  $("sign-out").onclick = () => signOut(state.auth);
  onAuthStateChanged(state.auth, (user) => (user ? showChat(user.displayName || user.email) : showLogin()));
}

function showLogin(error) {
  $("login").hidden = false;
  $("chat").hidden = true;
  $("user-area").hidden = true;
  const box = $("login-error");
  box.hidden = !error;
  box.textContent = error || "";
}

function showChat(name) {
  $("login").hidden = true;
  $("chat").hidden = false;
  $("user-area").hidden = state.devMode;
  $("user-name").textContent = name || "";
  $("input").focus();
}

async function authHeaders() {
  const h = { "Content-Type": "application/json" };
  if (state.auth?.currentUser) h.Authorization = `Bearer ${await state.auth.currentUser.getIdToken()}`;
  return h;
}

// ---------------------------------------------------------------- renderização

function renderCard(movie, index, ranked) {
  const node = $("tpl-card").content.firstElementChild.cloneNode(true);
  const title = node.querySelector(".card-title");
  if (ranked) title.append(el("span", { class: "card-rank", text: `${index + 1}.` }));
  title.append(movie.title);
  node.querySelector(".card-year").textContent = movie.year ?? "";

  const rating = node.querySelector(".card-rating");
  if (movie.avg_rating != null) {
    rating.append(el("strong", { text: `★ ${fmt(movie.avg_rating)}` }),
      ` média em ${movie.n_ratings.toLocaleString("pt-BR")} avaliações`,
      movie.bayes_rating != null ? ` · ponderada ${fmt(movie.bayes_rating, 2)}` : "");
  } else rating.textContent = "Sem avaliações no MovieLens";

  const genres = node.querySelector(".genres");
  for (const g of movie.genres || []) genres.append(el("li", { text: GENRES_PT[g] || g }));
  const reason = node.querySelector(".card-reason");
  if (movie.reason) reason.textContent = movie.reason; else reason.remove();
  const tags = node.querySelector(".tags");
  for (const t of (movie.tags || []).slice(0, 6)) tags.append(el("li", { text: t }));
  if (!tags.children.length) tags.remove();

  const links = node.querySelector(".card-links");
  if (movie.imdb_id) links.append(el("a", { href: `https://www.imdb.com/title/${movie.imdb_id}/`, target: "_blank", rel: "noopener", text: "IMDb" }));
  if (movie.tmdb_id) links.append(el("a", { href: `https://www.themoviedb.org/movie/${movie.tmdb_id}`, target: "_blank", rel: "noopener", text: "TMDb" }));
  return node;
}

function describeFilters(f) {
  if (!f) return "";
  const parts = [];
  const g = (xs) => xs.map((x) => GENRES_PT[x] || x).join(", ");
  if (f.genres_all?.length) parts.push(`gêneros: ${g(f.genres_all)}`);
  if (f.genres_any?.length) parts.push(`algum de: ${g(f.genres_any)}`);
  if (f.genres_exclude?.length) parts.push(`sem: ${g(f.genres_exclude)}`);
  if (f.year_min != null || f.year_max != null) parts.push(`anos ${f.year_min ?? "…"}–${f.year_max ?? "…"}`);
  if (f.min_avg_rating != null) parts.push(`nota ≥ ${fmt(f.min_avg_rating)}`);
  if (f.min_ratings != null) parts.push(`≥ ${f.min_ratings} avaliações`);
  return parts.join(" · ");
}

function renderTrace(trace) {
  const route = trace.route || "outro";
  const summary = el("summary", {},
    el("span", { class: `route-badge route-${route}`, text: ROUTE_LABEL[route] || route }),
    `Como cheguei nisso · ${fmt(trace.latency_s)} s`);
  const dl = el("dl");
  const row = (k, v) => { if (v) dl.append(el("dt", { text: k }), el("dd", { text: v })); };
  const plan = trace.plan || {};
  row("Por que essa rota", plan.reason);
  row("Filtros exatos", describeFilters(plan.filters));
  row("Ordenação", plan.sort_by && plan.sort_by !== "relevance" ? SORT_LABEL[plan.sort_by] : "");
  row("Busca semântica", trace.retrieval?.query_text);
  if (trace.retrieval) {
    const r = trace.retrieval;
    row("Banco vetorial", `${r.backend} · ${r.used_previous ? "resposta anterior" : `top ${r.searched_k}`} · ${r.pool_after_filters} candidatos após filtros`);
  }
  row("O que o SQL faz", trace.sql_explanation);
  row("Erro", trace.sql_error || trace.error);
  row("Etapas", (trace.steps || []).map((s) => `${s.node} ${fmt(s.latency_s, 2)} s`).join(" → "));
  row("Tokens", trace.tokens ? `${trace.tokens.input.toLocaleString("pt-BR")} entrada · ${trace.tokens.output.toLocaleString("pt-BR")} saída · US$ ${fmt(trace.cost_usd, 4)}` : "");
  const body = el("div", { class: "trace-body" }, dl);
  if (trace.sql) body.append(el("pre", { text: trace.sql }));
  return el("details", { class: "trace" }, summary, body);
}

function addUserMessage(text) {
  $("empty").hidden = true;
  const li = el("li", { class: "msg-user", text });
  $("messages").append(li);
  li.scrollIntoView({ block: "end", behavior: "smooth" });
}

function addThinking() {
  const li = el("li", { class: "msg-bot" },
    el("p", { class: "thinking" }, el("span", { class: "dots" }, el("i"), el("i"), el("i")), "Escolhendo a rota e buscando no acervo…"));
  $("messages").append(li);
  li.scrollIntoView({ block: "end", behavior: "smooth" });
  return li;
}

function renderAnswer(li, data) {
  li.replaceChildren();
  const { answer, trace } = data;
  li.append(el("p", { class: "msg-text", text: answer.text }));
  if (answer.numeric_answer != null && !answer.movies.length) {
    li.append(el("p", { class: "big-number", text: fmt(answer.numeric_answer, Number.isInteger(answer.numeric_answer) ? 0 : 2) }));
  }
  if (answer.movies.length) {
    const ranked = trace.route === "sql" && trace.plan?.sort_by !== "relevance";
    const grid = el("div", { class: "cards" });
    answer.movies.forEach((m, i) => grid.append(renderCard(m, i, ranked)));
    li.append(grid);
  }
  li.append(renderTrace(trace));
}

function renderError(li, message) {
  li.replaceChildren(el("p", { class: "msg-text", text: message }));
  li.classList.add("msg-error");
}

// ---------------------------------------------------------------- envio

async function send(text) {
  text = text.trim();
  if (!text || state.busy) return;
  state.busy = true;
  $("send").disabled = true;
  $("input").value = "";
  autosize();
  addUserMessage(text);
  const li = addThinking();
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: await authHeaders(),
      body: JSON.stringify({ message: text, conversation_id: state.conversationId }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `Erro ${r.status}`);
    state.conversationId = data.conversation_id;
    $("quota").textContent = data.remaining_today != null ? `${data.remaining_today} perguntas restantes hoje` : "";
    renderAnswer(li, data);
  } catch (e) {
    renderError(li, typeof e.message === "string" ? e.message : "Algo deu errado. Tente de novo.");
  } finally {
    state.busy = false;
    $("send").disabled = false;
    $("input").focus();
  }
}

function autosize() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = `${Math.min(t.scrollHeight, 160)}px`;
}

$("composer").addEventListener("submit", (e) => { e.preventDefault(); send($("input").value); });
$("input").addEventListener("input", autosize);
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("input").value); }
});
document.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
$("new-chat").addEventListener("click", () => {
  state.conversationId = null;
  $("messages").replaceChildren();
  $("empty").hidden = false;
  $("input").focus();
});

initAuth();
