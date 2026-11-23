const el = {
  sidebar: document.getElementById("sidebar"),
  btnToggleMenu: document.getElementById("btnToggleMenu"),
  btnNovaConversa: document.getElementById("btnNovaConversa"),
  btnNovaConversaMobile: document.getElementById("btnNovaConversaMobile"),
  statusDot: document.getElementById("statusDot"),
  connectionStatus: document.getElementById("connectionStatus"),
  modoOperacao: document.getElementById("modoOperacao"),
  filtroCategoria: document.getElementById("filtroCategoria"),
  filtroMaquina: document.getElementById("filtroMaquina"),
  usuarioInput: document.getElementById("usuarioInput"),
  raciocinioProfundo: document.getElementById("raciocinioProfundo"),
  btnRecarregarFiltros: document.getElementById("btnRecarregarFiltros"),
  chatMessages: document.getElementById("chatMessages"),
  chatForm: document.getElementById("chatForm"),
  perguntaInput: document.getElementById("perguntaInput"),
  btnEnviar: document.getElementById("btnEnviar"),
  tabelaDocsBody: document.getElementById("tabelaDocsBody"),
  btnAtualizarDocs: document.getElementById("btnAtualizarDocs"),
  listaHistorico: document.getElementById("listaHistorico"),
  btnAtualizarHist: document.getElementById("btnAtualizarHist"),
  auditFilters: document.getElementById("auditFilters"),
};

let hub = null;
let hubConectado = false;
let respostaEmAndamento = null;
let bufferMarkdown = "";
let perguntaAtual = "";
let historicoConversa = [];
let cacheHistoricoAuditoria = [];
let filtroAuditoriaAtual = "todos";

function textoModoAtual() {
  return el.modoOperacao.options[el.modoOperacao.selectedIndex].text;
}

function formatarTempo(ms) {
  if (typeof ms !== "number" || isNaN(ms)) return "-";
  const totalSeg = ms / 1000;
  if (totalSeg < 60) {
    return totalSeg.toFixed(1).replace(".", ",") + "s";
  }
  const min = Math.floor(totalSeg / 60);
  const seg = Math.round(totalSeg % 60).toString().padStart(2, "0");
  return `${min}m ${seg}s`;
}

function limparPensamentoCliente(texto) {
  if (!texto) return "";
  let limpo = texto.replace(/<think>[\s\S]*?<\/think>/gi, "");
  const idxFechamento = limpo.toLowerCase().lastIndexOf("</think>");
  if (idxFechamento >= 0) {
    limpo = limpo.slice(idxFechamento + "</think>".length);
  }
  limpo = limpo.replace(/<think>[\s\S]*$/gi, "");
  return limpo.trim();
}

function limparConversa() {
  historicoConversa = [];
  el.chatMessages.innerHTML = `
    <div class="msg assistant">
      <div class="msg-header">Assistente T\u00e9cnico \u2022 Qwen3 Local</div>
      <div class="msg-body">
        Nova conversa iniciada. Como posso te ajudar agora?
      </div>
    </div>
  `;
  el.sidebar.classList.remove("open");
  el.perguntaInput.focus();
}

el.btnNovaConversa.addEventListener("click", limparConversa);
el.btnNovaConversaMobile.addEventListener("click", limparConversa);

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    el.sidebar.classList.remove("open");

    if (btn.dataset.tab === "documentos") carregarDocumentos();
    if (btn.dataset.tab === "historico") carregarHistorico();
  });
});

el.auditFilters.addEventListener("click", (e) => {
  const btn = e.target.closest(".filter-chip");
  if (!btn) return;
  el.auditFilters.querySelectorAll(".filter-chip").forEach((c) => c.classList.remove("active"));
  btn.classList.add("active");
  filtroAuditoriaAtual = btn.dataset.filter;
  renderizarListaHistorico();
});

el.btnToggleMenu.addEventListener("click", () => el.sidebar.classList.toggle("open"));

function escapeHtml(str) {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderMarkdown(md) {
  const safe = escapeHtml(limparPensamentoCliente(md));
  const lines = safe.split(/\r?\n/);
  let html = "";
  let listTag = null;
  let inTable = false;
  let isHeaderRow = false;

  const fecharLista = () => {
    if (listTag) {
      html += `</${listTag}>`;
      listTag = null;
    }
  };

  const formatInline = (txt) =>
    txt
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>");

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();

    if (line.startsWith("|") && line.endsWith("|")) {
      const cells = line.slice(1, -1).split("|").map((c) => c.trim());
      if (cells.every((c) => /^[-:]+$/.test(c))) {
        isHeaderRow = false;
        continue;
      }
      if (!inTable) {
        fecharLista();
        html += "<table><thead>";
        inTable = true;
        isHeaderRow = true;
      }
      if (isHeaderRow) {
        html += "<tr>" + cells.map((c) => `<th>${formatInline(c)}</th>`).join("") + "</tr></thead><tbody>";
        isHeaderRow = false;
      } else {
        html += "<tr>" + cells.map((c) => `<td>${formatInline(c)}</td>`).join("") + "</tr>";
      }
      continue;
    } else if (inTable) {
      html += "</tbody></table>";
      inTable = false;
    }

    if (/^[-*]\s+/.test(line)) {
      if (listTag !== "ul") { fecharLista(); html += "<ul>"; listTag = "ul"; }
      html += `<li>${formatInline(line.replace(/^[-*]\s+/, ""))}</li>`;
      continue;
    }

    if (/^\d+\.\s+/.test(line)) {
      if (listTag !== "ol") { fecharLista(); html += "<ol>"; listTag = "ol"; }
      html += `<li>${formatInline(line.replace(/^\d+\.\s+/, ""))}</li>`;
      continue;
    }

    fecharLista();

    if (!line) continue;

    if (/^---+$/.test(line)) {
      html += "<hr />";
    } else if (line.startsWith("### ")) {
      html += `<h4>${formatInline(line.slice(4))}</h4>`;
    } else if (line.startsWith("## ")) {
      html += `<h3>${formatInline(line.slice(3))}</h3>`;
    } else {
      html += `<p>${formatInline(line)}</p>`;
    }
  }

  fecharLista();
  if (inTable) html += "</tbody></table>";
  return html;
}

async function iniciarSignalR() {
  if (typeof signalR === "undefined") {
    el.statusDot.className = "brand-dot online";
    el.connectionStatus.textContent = "Online (REST)";
    return;
  }

  hub = new signalR.HubConnectionBuilder()
    .withUrl("/hubs/chat")
    .withAutomaticReconnect()
    .build();

  hub.on("StatusAtualizado", () => {
    if (respostaEmAndamento) {
      respostaEmAndamento.statusEl.textContent = "Gerando resposta...";
    }
  });

  hub.on("ReceberToken", (token) => {
    if (!respostaEmAndamento) return;
    bufferMarkdown += token;
  });

  hub.on("FinalizarResposta", (info) => {
    if (respostaEmAndamento) {
      const textoFinal = limparPensamentoCliente(info.resposta || bufferMarkdown || "");
      exibirRespostaFinal(
        respostaEmAndamento,
        textoFinal,
        info.fontes,
        info.casosValidados,
        info.tempoMs,
        info.modelo,
        info.historicoId
      );
      if (perguntaAtual && textoFinal) {
        registrarTurnoNaMemoria(perguntaAtual, textoFinal);
      }
    }
    finalizarEstadoEnvio();
  });

  hub.on("ErroConsulta", (erro) => {
    if (respostaEmAndamento) {
      respostaEmAndamento.statusEl.style.display = "none";
      respostaEmAndamento.bodyEl.innerHTML = `<p style="color:#fca5a5">${escapeHtml(erro)}</p>`;
    }
    finalizarEstadoEnvio();
  });

  hub.onreconnecting(() => {
    hubConectado = false;
    el.statusDot.className = "brand-dot";
    el.connectionStatus.textContent = "Reconectando...";
  });

  hub.onreconnected(() => {
    hubConectado = true;
    el.statusDot.className = "brand-dot online";
    el.connectionStatus.textContent = "Online";
  });

  try {
    await hub.start();
    hubConectado = true;
    el.statusDot.className = "brand-dot online";
    el.connectionStatus.textContent = "Online";
  } catch (err) {
    console.warn("Falha ao conectar SignalR, usando REST:", err);
    el.statusDot.className = "brand-dot online";
    el.connectionStatus.textContent = "Online (REST)";
  }
}

function exibirRespostaFinal(bolha, textoFinal, fontes, casosValidados, tempoMs, modelo, historicoId) {
  bolha.statusEl.style.display = "none";
  bolha.bodyEl.innerHTML = renderMarkdown(textoFinal);

  if (fontes && fontes.length > 0) {
    renderizarFontes(bolha.sourcesEl, fontes);
  }

  if (casosValidados && casosValidados.length > 0) {
    renderizarCasosValidados(bolha.learnedEl, casosValidados);
  }

  bolha.metaEl.style.display = "block";
  bolha.metaEl.textContent = `Tempo de gera\u00e7\u00e3o: ${formatarTempo(tempoMs)} \u2022 Modelo: ${modelo}`;

  if (historicoId) {
    renderizarControlesFeedback(bolha.feedbackEl, historicoId);
  }

  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
}

function registrarTurnoNaMemoria(pergunta, resposta) {
  historicoConversa.push({ role: "user", content: pergunta });
  historicoConversa.push({ role: "assistant", content: resposta });
  if (historicoConversa.length > 12) {
    historicoConversa = historicoConversa.slice(-12);
  }
}

function criarBolhaUsuario(texto) {
  const div = document.createElement("div");
  div.className = "msg user";
  div.innerHTML = `<div class="msg-header">${escapeHtml(el.usuarioInput.value || "operador_local")}</div>
                   <div class="msg-body">${escapeHtml(texto).replace(/\n/g, "<br>")}</div>`;
  el.chatMessages.appendChild(div);
  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
}

function criarBolhaAssistente() {
  const div = document.createElement("div");
  div.className = "msg assistant";
  div.innerHTML = `
    <div class="msg-header">
      <span>Assistente T\u00e9cnico \u2022 ${escapeHtml(textoModoAtual())}</span>
    </div>
    <div class="msg-status">Gerando resposta...</div>
    <div class="msg-body"></div>
    <div class="sources-box" style="display:none"></div>
    <div class="learned-box" style="display:none"></div>
    <div class="msg-meta"></div>
    <div class="feedback-bar"></div>
  `;
  el.chatMessages.appendChild(div);
  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;

  return {
    statusEl: div.querySelector(".msg-status"),
    bodyEl: div.querySelector(".msg-body"),
    sourcesEl: div.querySelector(".sources-box"),
    learnedEl: div.querySelector(".learned-box"),
    metaEl: div.querySelector(".msg-meta"),
    feedbackEl: div.querySelector(".feedback-bar"),
  };
}

function renderizarFontes(container, fontes) {
  if (!fontes || fontes.length === 0) return;
  container.style.display = "block";
  container.innerHTML = `<strong>Fontes da biblioteca (${fontes.length}):</strong>` +
    fontes.map((f, idx) => {
      const nome = f.nome_arquivo || f.nomeArquivo || "documento";
      const pag = f.pagina_inicio ?? f.paginaInicio ?? "-";
      const cat = f.categoria || "-";
      const ver = f.versao ?? 1;
      const score = (f.score_rrf ?? f.scoreRrf ?? 0).toFixed(4);
      const trecho = f.conteudo_markdown || f.conteudoMarkdown || "";
      return `
        <details>
          <summary>[${idx + 1}] ${escapeHtml(nome)} (v${ver} \u2022 p\u00e1g. ${pag} \u2022 ${escapeHtml(cat)} \u2022 score ${score})</summary>
          <div class="source-excerpt">${escapeHtml(trecho)}</div>
        </details>
      `;
    }).join("");
}

function renderizarCasosValidados(container, casos) {
  if (!casos || casos.length === 0) return;
  container.style.display = "block";
  container.innerHTML = `<strong>Aprendizado operacional aplicado (${casos.length}):</strong>` +
    casos.map((c, idx) => {
      const dataFmt = new Date(c.criadoEm).toLocaleDateString("pt-BR");
      const corpo = c.correcaoOperador
        ? `Corre\u00e7\u00e3o validada: ${c.correcaoOperador}`
        : `Resposta validada anteriormente: ${c.resposta}`;
      return `
        <details>
          <summary>\u2605 [Hist\u00f3rico Validado ${idx + 1}] ${escapeHtml(c.pergunta)} (${escapeHtml(dataFmt)})</summary>
          <div class="source-excerpt">${escapeHtml(corpo)}</div>
        </details>
      `;
    }).join("");
}

function renderizarControlesFeedback(container, historicoId) {
  container.style.display = "flex";
  container.innerHTML = `
    <div class="feedback-actions">
      <button type="button" class="btn-fb btn-util">\ud83d\udc4d \u00datil (Validar)</button>
      <button type="button" class="btn-fb btn-ruim">\ud83d\udc4e Corrigir / Melhorar</button>
      <span class="fb-status"></span>
    </div>
    <div class="correction-form">
      <textarea placeholder="Descreva a corre\u00e7\u00e3o t\u00e9cnica ou par\u00e2metro correto (ex: Na Linha 2, o setpoint ideal \u00e9 185 \u00b0C, n\u00e3o 170 \u00b0C)..."></textarea>
      <div class="correction-footer">
        <label class="checkbox-row" style="margin:0">
          <input type="checkbox" class="chk-aprovar" checked />
          <span>Usar esta corre\u00e7\u00e3o nas pr\u00f3ximas consultas</span>
        </label>
        <button type="button" class="btn-primary btn-salvar-correcao" style="padding:6px 12px;font-size:0.82rem">Salvar corre\u00e7\u00e3o</button>
      </div>
    </div>
  `;

  const btnUtil = container.querySelector(".btn-util");
  const btnRuim = container.querySelector(".btn-ruim");
  const statusSpan = container.querySelector(".fb-status");
  const form = container.querySelector(".correction-form");
  const txtCorrecao = form.querySelector("textarea");
  const chkAprovar = form.querySelector(".chk-aprovar");
  const btnSalvar = form.querySelector(".btn-salvar-correcao");

  btnUtil.addEventListener("click", async () => {
    btnUtil.disabled = true;
    try {
      const resp = await fetch("/api/avaliacao", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          historicoId,
          avaliacao: "UTIL",
          correcaoOperador: null,
          aprovarConhecimento: true,
        }),
      });
      if (!resp.ok) throw new Error("Falha ao salvar");
      btnUtil.classList.add("active-ok");
      btnRuim.classList.remove("active-bad");
      form.style.display = "none";
      statusSpan.textContent = "\u2605 Validado e adicionado ao aprendizado operacional.";
    } catch {
      statusSpan.textContent = "Erro ao registrar avalia\u00e7\u00e3o.";
    } finally {
      btnUtil.disabled = false;
    }
  });

  btnRuim.addEventListener("click", () => {
    form.style.display = form.style.display === "flex" ? "none" : "flex";
    if (form.style.display === "flex") txtCorrecao.focus();
  });

  btnSalvar.addEventListener("click", async () => {
    const correcao = txtCorrecao.value.trim();
    const aprovar = chkAprovar.checked && correcao.length > 0;
    btnSalvar.disabled = true;
    try {
      const resp = await fetch("/api/avaliacao", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          historicoId,
          avaliacao: "RUIM",
          correcaoOperador: correcao || null,
          aprovarConhecimento: aprovar,
        }),
      });
      if (!resp.ok) throw new Error("Falha ao salvar");
      btnRuim.classList.add("active-bad");
      btnUtil.classList.remove("active-ok");
      form.style.display = "none";
      statusSpan.textContent = aprovar
        ? "\u2605 Corre\u00e7\u00e3o salva! A IA usar\u00e1 esta orienta\u00e7\u00e3o nas pr\u00f3ximas consultas."
        : "Feedback registrado na Auditoria para revis\u00e3o.";
    } catch {
      statusSpan.textContent = "Erro ao salvar corre\u00e7\u00e3o.";
    } finally {
      btnSalvar.disabled = false;
    }
  });
}

function finalizarEstadoEnvio() {
  respostaEmAndamento = null;
  bufferMarkdown = "";
  perguntaAtual = "";
  el.btnEnviar.disabled = false;
  el.perguntaInput.focus();
}

el.chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const pergunta = el.perguntaInput.value.trim();
  if (!pergunta) return;

  const payload = {
    pergunta,
    modoOperacao: el.modoOperacao.value,
    categoria: el.filtroCategoria.value || null,
    maquina: el.filtroMaquina.value || null,
    usuario: el.usuarioInput.value.trim() || "operador_local",
    raciocinioProfundo: el.raciocinioProfundo.checked,
    topK: 5,
    historico: historicoConversa.slice(-6),
  };

  perguntaAtual = pergunta;
  el.perguntaInput.value = "";
  el.perguntaInput.style.height = "auto";
  el.btnEnviar.disabled = true;
  criarBolhaUsuario(pergunta);
  respostaEmAndamento = criarBolhaAssistente();
  bufferMarkdown = "";

  if (hub && hubConectado) {
    try {
      await hub.invoke("EnviarPergunta", payload);
      return;
    } catch (err) {
      console.warn("SignalR falhou no envio, tentando via REST:", err);
    }
  }

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    const textoFinal = limparPensamentoCliente(data.resposta);
    exibirRespostaFinal(
      respostaEmAndamento,
      textoFinal,
      data.fontes,
      data.casosValidados,
      data.tempoRespostaMs,
      data.modeloLlm,
      data.historicoId
    );
    registrarTurnoNaMemoria(pergunta, textoFinal);
  } catch (err) {
    respostaEmAndamento.statusEl.style.display = "none";
    respostaEmAndamento.bodyEl.innerHTML = `<p style="color:#fca5a5">Erro na consulta: ${escapeHtml(err.message)}</p>`;
  } finally {
    finalizarEstadoEnvio();
  }
});

el.perguntaInput.addEventListener("input", () => {
  el.perguntaInput.style.height = "auto";
  el.perguntaInput.style.height = Math.min(el.perguntaInput.scrollHeight, 140) + "px";
});

el.perguntaInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    el.chatForm.requestSubmit();
  }
});

async function carregarFiltros() {
  try {
    const resp = await fetch("/api/filtros");
    const data = await resp.json();

    el.filtroCategoria.innerHTML = `<option value="">Todas as categorias</option>` +
      (data.categorias || []).map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");

    el.filtroMaquina.innerHTML = `<option value="">Todas as m\u00e1quinas</option>` +
      (data.maquinas || []).map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
  } catch (err) {
    console.error("Erro ao carregar filtros:", err);
  }
}

async function carregarDocumentos() {
  el.tabelaDocsBody.innerHTML = `<tr><td colspan="6">Atualizando...</td></tr>`;
  try {
    const resp = await fetch("/api/documentos");
    const docs = await resp.json();
    if (!docs.length) {
      el.tabelaDocsBody.innerHTML = `<tr><td colspan="6">Nenhum documento indexado ainda.</td></tr>`;
      return;
    }
    el.tabelaDocsBody.innerHTML = docs.map((d) => `
      <tr>
        <td title="${escapeHtml(d.caminhoRelativo)}">${escapeHtml(d.nomeArquivo)}</td>
        <td>${escapeHtml(d.categoria)}${d.subcategoria ? " / " + escapeHtml(d.subcategoria) : ""}</td>
        <td>${escapeHtml(d.maquina || "-")}</td>
        <td>v${d.versao}</td>
        <td>${d.totalPaginas ?? "-"}</td>
        <td><span class="pill ${d.status === "ATIVO" ? "ativo" : "obsoleto"}">${escapeHtml(d.status)}</span></td>
      </tr>
    `).join("");
  } catch (err) {
    el.tabelaDocsBody.innerHTML = `<tr><td colspan="6">Erro ao carregar: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function carregarHistorico() {
  el.listaHistorico.innerHTML = "Carregando...";
  try {
    const resp = await fetch("/api/historico");
    cacheHistoricoAuditoria = await resp.json();
    renderizarListaHistorico();
  } catch (err) {
    el.listaHistorico.innerHTML = `Erro ao carregar hist\u00f3rico: ${escapeHtml(err.message)}`;
  }
}

function renderizarListaHistorico() {
  const filtrados = cacheHistoricoAuditoria.filter((h) => {
    if (filtroAuditoriaAtual === "aprovados") return !!h.aprovadoConhecimento;
    if (filtroAuditoriaAtual === "uteis") return h.avaliacao === "UTIL";
    if (filtroAuditoriaAtual === "ruins") return h.avaliacao === "RUIM";
    return true;
  });

  if (!filtrados.length) {
    el.listaHistorico.innerHTML = "Nenhum registro encontrado para este filtro.";
    return;
  }

  el.listaHistorico.innerHTML = "";
  filtrados.forEach((h) => {
    const dataFmt = new Date(h.criadoEm).toLocaleString("pt-BR");
    const card = document.createElement("div");
    card.className = "history-card";

    const badges = [];
    if (h.avaliacao === "UTIL") badges.push(`<span class="pill ativo">\ud83d\udc4d \u00datil</span>`);
    if (h.avaliacao === "RUIM") badges.push(`<span class="pill warn">\ud83d\udc4e Corrigido / Ruim</span>`);
    if (h.aprovadoConhecimento) badges.push(`<span class="pill info">\u2605 Ativo no Aprendizado</span>`);

    const correcaoHtml = h.correcaoOperador
      ? `<div class="correction-note"><strong>Corre\u00e7\u00e3o / Orienta\u00e7\u00e3o da Equipe:</strong> ${escapeHtml(h.correcaoOperador)}</div>`
      : "";

    card.innerHTML = `
      <div class="history-card-top">
        <h3>${escapeHtml(h.pergunta)}</h3>
        <button type="button" class="btn-fb btn-toggle-aprovacao">
          ${h.aprovadoConhecimento ? "Remover do Aprendizado" : "Ativar no Aprendizado"}
        </button>
      </div>
      <div class="meta">
        <span>${escapeHtml(dataFmt)} \u2022 ${escapeHtml(h.usuario)} \u2022 Modo: ${escapeHtml(h.modoOperacao)} \u2022 Tempo: ${formatarTempo(h.tempoRespostaMs)}</span>
        ${badges.join(" ")}
      </div>
      <div class="msg-body">${renderMarkdown(h.resposta)}</div>
      ${correcaoHtml}
    `;

    const btnToggle = card.querySelector(".btn-toggle-aprovacao");
    btnToggle.addEventListener("click", async () => {
      const novoEstado = !h.aprovadoConhecimento;
      btnToggle.disabled = true;
      try {
        const resp = await fetch(`/api/historico/${encodeURIComponent(h.id)}/aprovacao`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ aprovado: novoEstado }),
        });
        if (!resp.ok) throw new Error("Erro ao atualizar");
        h.aprovadoConhecimento = novoEstado;
        renderizarListaHistorico();
      } catch {
        btnToggle.disabled = false;
      }
    });

    el.listaHistorico.appendChild(card);
  });
}

el.btnRecarregarFiltros.addEventListener("click", carregarFiltros);
el.btnAtualizarDocs.addEventListener("click", carregarDocumentos);
el.btnAtualizarHist.addEventListener("click", carregarHistorico);

carregarFiltros();
iniciarSignalR();