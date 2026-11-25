const el = {
  authOverlay: document.getElementById("authOverlay"),
  authForm: document.getElementById("authForm"),
  authUsername: document.getElementById("authUsername"),
  authSenha: document.getElementById("authSenha"),
  authError: document.getElementById("authError"),
  appShell: document.getElementById("appShell"),
  sidebar: document.getElementById("sidebar"),
  statusDot: document.getElementById("statusDot"),
  connectionStatus: document.getElementById("connectionStatus"),
  userAvatar: document.getElementById("userAvatar"),
  userNameDisplay: document.getElementById("userNameDisplay"),
  btnLogout: document.getElementById("btnLogout"),
  sessionsList: document.getElementById("sessionsList"),
  btnNovaConversa: document.getElementById("btnNovaConversa"),
  btnNovaConversaMobile: document.getElementById("btnNovaConversaMobile"),
  btnToggleMenu: document.getElementById("btnToggleMenu"),
  btnToggleFiltros: document.getElementById("btnToggleFiltros"),
  filtersDrawer: document.getElementById("filtersDrawer"),
  modoOperacao: document.getElementById("modoOperacao"),
  filtroCategoria: document.getElementById("filtroCategoria"),
  filtroMaquina: document.getElementById("filtroMaquina"),
  raciocinioProfundo: document.getElementById("raciocinioProfundo"),
  modeloSelect: document.getElementById("modeloSelect"),
  btnAtualizarModelos: document.getElementById("btnAtualizarModelos"),
  chatMessages: document.getElementById("chatMessages"),
  chatForm: document.getElementById("chatForm"),
  perguntaInput: document.getElementById("perguntaInput"),
  btnEnviar: document.getElementById("btnEnviar"),
  tabelaDocsBody: document.getElementById("tabelaDocsBody"),
  listaHistorico: document.getElementById("listaHistorico"),
};

let authToken = localStorage.getItem("rag_auth_token") || "";
let usuarioAtual = null;
let sessaoAtualId = null;
let historicoConversa = [];
let hub = null, hubConectado = false, respostaEmAndamento = null, perguntaAtual = "";

function authHeaders() { return { "Content-Type": "application/json", "Authorization": `Bearer ${authToken}` }; }
function escapeHtml(s) { return (s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function formatarTempo(ms) { return typeof ms === "number" ? (ms/1000).toFixed(1).replace(".",",") + "s" : "-"; }

function renderMarkdown(md) {
  return escapeHtml(md || "")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>");
}

async function verificarSessaoAuth() {
  if (!authToken) { mostrarLogin(); return; }
  try {
    const r = await fetch("/api/auth/me", { headers: authHeaders() });
    if (!r.ok) throw new Error();
    usuarioAtual = await r.json();
    iniciarAppLogado();
  } catch {
    localStorage.removeItem("rag_auth_token");
    authToken = "";
    mostrarLogin();
  }
}

function mostrarLogin() {
  el.authOverlay.style.display = "flex";
  el.appShell.style.display = "none";
}

function iniciarAppLogado() {
  el.authOverlay.style.display = "none";
  el.appShell.style.display = "flex";
  el.userNameDisplay.textContent = usuarioAtual.nomeExibicao || usuarioAtual.username;
  el.userAvatar.textContent = (usuarioAtual.nomeExibicao || "U")[0].toUpperCase();
  novaConversa();
  carregarSessoes();
  carregarModelos();
  carregarFiltros();
  iniciarSignalR();
}

el.authForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  el.authError.textContent = "";
  try {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: el.authUsername.value.trim(), senha: el.authSenha.value }),
    });
    const d = await r.json();
    if (!r.ok) { el.authError.textContent = d.erro || "Usu\u00e1rio ou senha inv\u00e1lidos."; return; }
    authToken = d.token;
    usuarioAtual = d.usuario;
    localStorage.setItem("rag_auth_token", authToken);
    el.authSenha.value = "";
    iniciarAppLogado();
  } catch { el.authError.textContent = "Erro ao conectar com a API."; }
});

el.btnLogout.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST", headers: authHeaders() }).catch(() => {});
  localStorage.removeItem("rag_auth_token");
  authToken = ""; usuarioAtual = null;
  mostrarLogin();
});

function novaConversa() {
  sessaoAtualId = null;
  historicoConversa = [];
  el.chatMessages.innerHTML = `<div class="msg assistant"><div class="msg-body"><strong>Ol\u00e1, ${escapeHtml(usuarioAtual?.nomeExibicao || "")}!</strong><br>Sou seu agente t\u00e9cnico industrial. Como posso ajudar hoje?</div></div>`;
  document.querySelectorAll(".session-item").forEach(i => i.classList.remove("active"));
  el.sidebar.classList.remove("open");
}

async function carregarSessoes() {
  const r = await fetch("/api/sessoes", { headers: authHeaders() });
  if (!r.ok) return;
  const sessoes = await r.json();
  if (!sessoes.length) { el.sessionsList.innerHTML = `<div style="padding:8px 10px;font-size:0.8rem;color:var(--muted)">Sem conversas recentes</div>`; return; }
  el.sessionsList.innerHTML = "";
  sessoes.forEach(s => {
    const div = document.createElement("div");
    div.className = "session-item" + (s.id === sessaoAtualId ? " active" : "");
    div.innerHTML = `<span class="session-title">${escapeHtml(s.titulo)}</span><button class="btn-del-sess" title="Excluir">&#10005;</button>`;
    div.querySelector(".session-title").addEventListener("click", () => abrirSessao(s.id));
    div.querySelector(".btn-del-sess").addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await fetch(`/api/sessoes/${s.id}`, { method: "DELETE", headers: authHeaders() });
      if (sessaoAtualId === s.id) novaConversa();
      carregarSessoes();
    });
    el.sessionsList.appendChild(div);
  });
}

async function abrirSessao(id) {
  const r = await fetch(`/api/sessoes/${id}/mensagens`, { headers: authHeaders() });
  if (!r.ok) return;
  const msgs = await r.json();
  sessaoAtualId = id;
  historicoConversa = [];
  el.chatMessages.innerHTML = "";
  msgs.forEach(m => {
    criarBolhaUsuario(m.pergunta);
    const b = criarBolhaAssistente();
    exibirRespostaFinal(b, m.resposta, [], [], [], m.tempoRespostaMs, m.modeloLlm || "-", m.historicoId);
    historicoConversa.push({ role: "user", content: m.pergunta }, { role: "assistant", content: m.resposta });
  });
  carregarSessoes();
  el.sidebar.classList.remove("open");
}

async function carregarModelos() {
  const r = await fetch("/api/modelos");
  const modelos = await r.json();
  const salvo = localStorage.getItem("rag_modelo_preferido");
  el.modeloSelect.innerHTML = modelos.map(m => `<option value="${escapeHtml(m.nome)}">${escapeHtml(m.nome)}</option>`).join("");
  if (salvo && modelos.some(m => m.nome === salvo)) el.modeloSelect.value = salvo;
}

async function carregarFiltros() {
  const r = await fetch("/api/filtros");
  const d = await r.json();
  el.filtroCategoria.innerHTML = `<option value="">Todas</option>` + (d.categorias || []).map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
  el.filtroMaquina.innerHTML = `<option value="">Todas</option>` + (d.maquinas || []).map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
}

function criarBolhaUsuario(txt) {
  const d = document.createElement("div");
  d.className = "msg user";
  d.innerHTML = escapeHtml(txt).replace(/\n/g, "<br>");
  el.chatMessages.appendChild(d);
  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
}

function criarBolhaAssistente() {
  const d = document.createElement("div");
  d.className = "msg assistant";
  d.innerHTML = `<div class="msg-status">Agente pensando...</div><div class="tools-wrap"></div><div class="msg-body"></div><div class="sources-box"></div><div class="msg-meta"></div><div class="feedback-bar"></div>`;
  el.chatMessages.appendChild(d);
  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
  return {
    statusEl: d.querySelector(".msg-status"),
    toolsEl: d.querySelector(".tools-wrap"),
    bodyEl: d.querySelector(".msg-body"),
    sourcesEl: d.querySelector(".sources-box"),
    metaEl: d.querySelector(".msg-meta"),
    feedbackEl: d.querySelector(".feedback-bar"),
  };
}

function exibirRespostaFinal(b, texto, fontes, casos, ferramentas, tempoMs, modelo, historicoId) {
  b.statusEl.style.display = "none";
  if (ferramentas && ferramentas.length) {
    b.toolsEl.innerHTML = `<span class="tools-badge">&#9889; Agente usou: ${escapeHtml([...new Set(ferramentas)].join(", "))}</span>`;
  }
  b.bodyEl.innerHTML = renderMarkdown(texto);
  if (fontes && fontes.length) {
    b.sourcesEl.innerHTML = fontes.map((f, i) => `<details><summary>[${i+1}] ${escapeHtml(f.nome_arquivo || f.nomeArquivo)} (p\u00e1g. ${f.pagina_inicio ?? f.paginaInicio ?? "-"})</summary><div class="source-excerpt">${escapeHtml(f.conteudo_markdown || f.conteudoMarkdown)}</div></details>`).join("");
  }
  b.metaEl.textContent = `Tempo: ${formatarTempo(tempoMs)} \u2022 Modelo: ${modelo}`;
  if (historicoId) {
    b.feedbackEl.style.display = "flex";
    b.feedbackEl.innerHTML = `<div class="feedback-actions"><button type="button" class="btn-fb btn-u">\ud83d\udc4d \u00datil</button><button type="button" class="btn-fb btn-r">\ud83d\udc4e Corrigir</button><span class="fb-st" style="font-size:0.75rem;color:#86efac"></span></div><div class="correction-form"><textarea placeholder="Informe a corre\u00e7\u00e3o t\u00e9cnica..."></textarea><button type="button" class="btn-primary btn-sv" style="align-self:flex-end">Salvar</button></div>`;
    const st = b.feedbackEl.querySelector(".fb-st"), cf = b.feedbackEl.querySelector(".correction-form");
    b.feedbackEl.querySelector(".btn-u").onclick = async () => {
      await fetch("/api/avaliacao", { method: "POST", headers: authHeaders(), body: JSON.stringify({ historicoId, avaliacao: "UTIL", aprovarConhecimento: true }) });
      st.textContent = "\u2605 Validado no aprendizado.";
    };
    b.feedbackEl.querySelector(".btn-r").onclick = () => { cf.style.display = cf.style.display === "flex" ? "none" : "flex"; };
    b.feedbackEl.querySelector(".btn-sv").onclick = async () => {
      await fetch("/api/avaliacao", { method: "POST", headers: authHeaders(), body: JSON.stringify({ historicoId, avaliacao: "RUIM", correcaoOperador: cf.querySelector("textarea").value, aprovarConhecimento: true }) });
      cf.style.display = "none"; st.textContent = "\u2605 Corre\u00e7\u00e3o salva.";
    };
  }
  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
}

async function iniciarSignalR() {
  if (hub || typeof signalR === "undefined") return;
  hub = new signalR.HubConnectionBuilder().withUrl("/hubs/chat").withAutomaticReconnect().build();
  hub.on("StatusAtualizado", (s) => { if (respostaEmAndamento) respostaEmAndamento.statusEl.textContent = s; });
  hub.on("FinalizarResposta", (info) => {
    if (respostaEmAndamento) {
      sessaoAtualId = info.sessaoId || sessaoAtualId;
      exibirRespostaFinal(respostaEmAndamento, info.resposta, info.fontes, info.casosValidados, info.ferramentasUsadas, info.tempoMs, info.modelo, info.historicoId);
      historicoConversa.push({ role: "user", content: perguntaAtual }, { role: "assistant", content: info.resposta });
      carregarSessoes();
    }
    respostaEmAndamento = null; el.btnEnviar.disabled = false;
  });
  hub.on("ErroConsulta", (err) => {
    if (respostaEmAndamento) { respostaEmAndamento.statusEl.style.display = "none"; respostaEmAndamento.bodyEl.innerHTML = `<span style="color:#fca5a5">${escapeHtml(err)}</span>`; }
    respostaEmAndamento = null; el.btnEnviar.disabled = false;
  });
  await hub.start().then(() => { hubConectado = true; el.statusDot.className = "brand-dot online"; el.connectionStatus.textContent = "Online"; }).catch(() => {});
}

el.chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const pergunta = el.perguntaInput.value.trim();
  if (!pergunta) return;
  const payload = {
    pergunta, modoOperacao: el.modoOperacao.value, categoria: el.filtroCategoria.value || null,
    maquina: el.filtroMaquina.value || null, modeloLlm: el.modeloSelect.value || null,
    sessaoId: sessaoAtualId, authToken, raciocinioProfundo: el.raciocinioProfundo.checked,
    historico: historicoConversa.slice(-6)
  };
  perguntaAtual = pergunta; el.perguntaInput.value = ""; el.btnEnviar.disabled = true;
  criarBolhaUsuario(pergunta);
  respostaEmAndamento = criarBolhaAssistente();
  if (hub && hubConectado) { await hub.invoke("EnviarPergunta", payload); return; }
  const r = await fetch("/api/chat", { method: "POST", headers: authHeaders(), body: JSON.stringify(payload) });
  const d = await r.json();
  sessaoAtualId = d.sessaoId || sessaoAtualId;
  exibirRespostaFinal(respostaEmAndamento, d.resposta, d.fontes, d.casosValidados, d.ferramentasUsadas, d.tempoRespostaMs, d.modeloLlm, d.historicoId);
  historicoConversa.push({ role: "user", content: pergunta }, { role: "assistant", content: d.resposta });
  carregarSessoes(); respostaEmAndamento = null; el.btnEnviar.disabled = false;
});

el.perguntaInput.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); el.chatForm.requestSubmit(); } });
el.modeloSelect.addEventListener("change", () => localStorage.setItem("rag_modelo_preferido", el.modeloSelect.value));
el.btnAtualizarModelos.addEventListener("click", carregarModelos);
el.btnNovaConversa.addEventListener("click", novaConversa);
el.btnNovaConversaMobile.addEventListener("click", novaConversa);
el.btnToggleMenu.addEventListener("click", () => el.sidebar.classList.toggle("open"));
el.btnToggleFiltros.addEventListener("click", () => { el.filtersDrawer.style.display = el.filtersDrawer.style.display === "none" ? "block" : "none"; });

document.querySelectorAll(".nav-item[data-tab]").forEach(btn => {
  btn.addEventListener("click", async () => {
    document.querySelectorAll(".nav-item").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "documentos") {
      const docs = await (await fetch("/api/documentos")).json();
      el.tabelaDocsBody.innerHTML = docs.map(d => `<tr><td>${escapeHtml(d.nomeArquivo)}</td><td>${escapeHtml(d.categoria)}</td><td>${escapeHtml(d.maquina||"-")}</td><td>v${d.versao}</td><td>${d.totalPaginas??"-"}</td><td>${escapeHtml(d.status)}</td></tr>`).join("");
    }
    if (btn.dataset.tab === "historico") {
      const hist = await (await fetch("/api/historico")).json();
      el.listaHistorico.innerHTML = hist.map(h => `<div class="history-card"><strong>${escapeHtml(h.pergunta)}</strong><div style="font-size:0.75rem;color:var(--muted);margin:4px 0">${escapeHtml(h.usuario)} \u2022 ${formatarTempo(h.tempoRespostaMs)}</div><div>${renderMarkdown(h.resposta)}</div></div>`).join("");
    }
  });
});

verificarSessaoAuth();