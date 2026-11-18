const i18n = window.OpenStudyI18n;
const t = (value) => i18n ? i18n.t(value) : value;
const currentLanguage = () => i18n ? i18n.getLanguage() : "pt-BR";
const currentLocale = () => i18n ? i18n.getIntlLocale() : "pt-BR";
const scrollBehavior = () => document.documentElement.dataset.reduceMotion === "true" ? "auto" : "smooth";

const state = {
  cards: [],
  cardIndex: 0,
  revealed: false,
  documents: [],
  quiz: [],
  answered: new Set(),
  correct: 0,
  activeView: "cards",
  llmSettings: null,
  selectedProvider: "ollama",
  reviewMode: "due",
  reviewedThisSession: 0,
  reviewQueueTotal: 0,
  activeSessionId: null,
  activeSessionType: "practice",
  sessionStartedAt: null,
  sessionTimer: null,
  learningDashboard: null,
  quizPurpose: "practice",
  quizXp: 0,
  sharedWorkspaceId: null,
  sharedWorkspaceSyncTimer: null,
  sharedWorkspaceSyncBusy: false,
  quizCompletionHandled: false,
  activePlanId: null,
  matchGame: null,
  whatsappStatus: null,
  tutorHistorySignature: "",
};

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
}[char]));

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const headers = new Headers(options.headers || {});
    headers.set("X-CSRF-Token", document.querySelector('meta[name="csrf-token"]')?.content || "");
    options = { ...options, headers };
  }
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("Sua sessão terminou. Entre novamente.");
  }
  if (!response.ok) {
    let message = `Erro ${response.status}`;
    try {
      const body = await response.json();
      if (Array.isArray(body.detail)) {
        message = body.detail.map((item) => item.msg || item.message || "Dados inválidos").join("; ");
      } else {
        message = body.detail || message;
      }
    } catch (_) {}
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message) {
  const element = $("toast");
  element.textContent = t(message);
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 3400);
}

function setView(view) {
  state.activeView = view;
  document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.id === `view-${view}`));
  document.querySelectorAll(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.view === view));
  const labels = {
    cards: "Flashcards", learning: "Minha jornada", exercises: "Exercícios", library: "Biblioteca",
    tutor: "Tutor IA", whatsapp: "WhatsApp", intelligence: "Inteligência", settings: "Configurar IA",
  };
  const titles = {
    cards: 'Aprenda de forma <span>inteligente.</span>',
    learning: 'Sua jornada de <span>aprendizagem.</span>',
    exercises: 'Transforme conhecimento em <span>prática.</span>',
    library: 'Sua biblioteca de <span>conhecimento.</span>',
    tutor: 'Aprenda conversando com a <span>IA.</span>',
    whatsapp: 'Seu assistente no <span>WhatsApp.</span>',
    intelligence: 'Evolua com <span>inteligência.</span>',
    settings: 'Sua IA, suas <span>escolhas.</span>',
  };
  const descriptions = {
    cards: "Transforme documentos em conhecimento e pratique até dominar cada assunto.",
    learning: "Aprenda brincando, supere dificuldades e acompanhe sua evolução.",
    exercises: "Teste sua compreensão com exercícios personalizados gerados pela IA.",
    library: "Organize seus materiais e deixe a IA preparar seu próximo ciclo de estudos.",
    tutor: "Faça perguntas e receba respostas fundamentadas nos seus próprios documentos.",
    whatsapp: "Converse, envie materiais, pratique exercícios e receba lembretes no seu telefone.",
    intelligence: "Use seus dados de aprendizagem para orientar revisões e planos personalizados.",
    settings: "Conecte suas chaves, escolha modelos e alterne entre provedores de IA quando quiser.",
  };
  $("breadcrumb-current").textContent = labels[view];
  $("page-title").innerHTML = titles[view];
  $("page-description").textContent = descriptions[view];
  if (view === "learning") {
    loadLearningDashboard().catch((error) => toast(error.message));
  }
  if (view === "tutor") {
    loadTutorHistory().catch((error) => toast(error.message));
  }
  if (view === "intelligence") {
    loadAnalytics().catch((error) => toast(error.message));
    refreshObservability().catch(() => {});
    loadLatestPlan().catch(() => {});
  }
  if (view === "settings") loadLLMSettings().catch((error) => toast(error.message));
  if (view === "whatsapp") loadWhatsAppDashboard().catch((error) => toast(error.message));
  if (view === "tutor") $("tutor-input").focus();
  i18n?.translatePage();
  window.scrollTo({ top: 0, behavior: scrollBehavior() });
}

async function loadStats() {
  const stats = await api("/api/stats");
  $("stat-documents").textContent = stats.documents;
  $("stat-cards").textContent = stats.cards;
  $("stat-exercises").textContent = stats.exercises;
  $("stat-reviews").textContent = stats.reviews;
  $("nav-card-count").textContent = stats.cards;
}

async function loadHealth() {
  const data = await api("/api/health");
  const ai = data.ai || {};
  const local = data.ollama || {};
  const configured = Boolean(ai.configured);
  const providerLabel = ai.provider_label || ai.provider || "Ollama";
  $("sidebar-model").textContent = `${providerLabel} · ${ai.model || "sem modelo"}`;
  $("top-status-text").textContent = configured ? `${providerLabel} configurado` : "Configure sua IA";
  $("tutor-llm-name").textContent = `${providerLabel} · ${ai.model || "—"}`;
  $("tutor-embedding-name").textContent = local.embedding_model || "Ollama indisponível";
  ["sidebar-status-dot", "top-status-dot"].forEach((id) => {
    $(id).classList.toggle("online", configured);
    $(id).classList.toggle("offline", !configured);
  });
  if (ai.provider === "ollama" && local.online && !local.model_installed) {
    toast(`Execute: ollama pull ${ai.model}`);
  }
  return data;
}

const providerIcons = { ollama: "◉", gemini: "✦", openai: "◎", claude: "✧", groq: "ϟ" };

async function loadLLMSettings() {
  const data = await api("/api/llm/settings");
  state.llmSettings = data;
  $("active-provider-pill").textContent = `Ativo: ${data.providers.find((item) => item.id === data.active_provider)?.label || data.active_provider}`;
  renderProviderList();
  const selectedExists = data.providers.some((item) => item.id === state.selectedProvider);
  selectProvider(selectedExists ? state.selectedProvider : data.active_provider);
  return data;
}

function renderProviderList() {
  const providers = state.llmSettings?.providers || [];
  $("provider-list").innerHTML = providers.map((provider) => `
    <button type="button" class="provider-list-item ${provider.id === state.selectedProvider ? "selected" : ""} ${provider.active ? "is-active" : ""}" data-provider-select="${provider.id}">
      <span class="provider-list-icon">${providerIcons[provider.id] || "✦"}</span>
      <span class="provider-list-copy"><strong>${escapeHtml(provider.label)}</strong><small>${provider.active ? "Em uso" : provider.configured ? "Configurado" : provider.key_required ? "Chave necessária" : "Disponível"}</small></span>
      <span class="provider-list-state ${provider.configured ? "ready" : ""}">${provider.active ? "●" : provider.configured ? "✓" : "＋"}</span>
    </button>`).join("");
  $("provider-list").querySelectorAll("[data-provider-select]").forEach((button) => {
    button.addEventListener("click", () => selectProvider(button.dataset.providerSelect));
  });
}

function selectProvider(providerId) {
  const provider = state.llmSettings?.providers?.find((item) => item.id === providerId);
  if (!provider) return;
  state.selectedProvider = providerId;
  renderProviderList();
  $("provider-editor-icon").textContent = providerIcons[providerId] || "✦";
  $("provider-editor-title").textContent = provider.label;
  $("provider-editor-description").textContent = provider.description;
  $("provider-model").value = provider.model || "";
  $("provider-api-key").value = "";
  $("provider-api-key").placeholder = provider.configured && provider.key_required
    ? "Chave salva. Deixe vazio para manter ou informe outra para substituir."
    : "Cole sua chave de API aqui";
  $("provider-key-field").classList.toggle("hidden", !provider.key_required);
  $("provider-key-status").textContent = provider.key_required
    ? (provider.configured ? "✓ Chave cadastrada e armazenada criptografada." : "Nenhuma chave cadastrada para este provedor.")
    : "O Ollama não exige chave de API. O serviço precisa estar ativo no computador.";
  $("provider-account-link").innerHTML = provider.key_required
    ? `<a href="${escapeHtml(provider.key_url)}" target="_blank" rel="noopener noreferrer">Obter chave de API em ${escapeHtml(provider.label)} ↗</a>`
    : `<span>Endpoint local: configurado em OLLAMA_BASE_URL no arquivo .env.</span>`;
  $("clear-provider-key").classList.toggle("hidden", !provider.key_required || !provider.configured);
  $("provider-active-note").classList.toggle("hidden", !provider.active);
  $("activate-provider").disabled = provider.active;
  $("activate-provider").textContent = provider.active ? "Provedor em uso" : "Usar este provedor";
  $("provider-model-options").innerHTML = "";
  setProviderFeedback("", "");
}

function setProviderFeedback(message, kind = "success") {
  const box = $("provider-config-feedback");
  box.textContent = message;
  box.classList.toggle("hidden", !message);
  box.classList.toggle("error", kind === "error");
  box.classList.toggle("success", kind !== "error");
}

async function saveProviderConfig(event) {
  event.preventDefault();
  const provider = state.selectedProvider;
  const payload = { model: $("provider-model").value.trim() };
  const apiKey = $("provider-api-key").value.trim();
  if (apiKey) payload.api_key = apiKey;
  const button = $("save-provider-config");
  button.disabled = true;
  try {
    const result = await api(`/api/llm/providers/${provider}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    $("provider-api-key").value = "";
    await loadLLMSettings();
    setProviderFeedback(result.message || "Configuração salva com sucesso.");
    toast(`${result.model} salvo para ${provider}.`);
  } catch (error) {
    setProviderFeedback(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function activateProvider() {
  const provider = state.selectedProvider;
  try {
    const result = await api("/api/llm/active", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    await Promise.all([loadLLMSettings(), loadHealth()]);
    toast(`${provider} ativado · ${result.model}`);
  } catch (error) {
    setProviderFeedback(error.message, "error");
  }
}

async function testProvider() {
  const provider = state.selectedProvider;
  const button = $("test-provider");
  button.disabled = true;
  setProviderFeedback("Testando a conexão com o provedor...");
  try {
    const result = await api(`/api/llm/providers/${provider}/test`, { method: "POST" });
    setProviderFeedback(`Conexão validada! Modelo: ${result.model}. Resposta: ${result.message}`);
    toast("Conexão validada com sucesso.");
  } catch (error) {
    setProviderFeedback(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function loadProviderModels() {
  const provider = state.selectedProvider;
  const button = $("load-provider-models");
  button.disabled = true;
  button.textContent = "Buscando...";
  try {
    const result = await api(`/api/llm/providers/${provider}/models`);
    $("provider-model-options").innerHTML = result.models.map((model) => `<option value="${escapeHtml(model)}"></option>`).join("");
    setProviderFeedback(`${result.models.length} modelos encontrados. Digite para filtrar e selecione o desejado.`);
    if (!result.models.length) setProviderFeedback("Nenhum modelo foi retornado pela conta.", "error");
  } catch (error) {
    setProviderFeedback(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Buscar modelos ↻";
  }
}

async function clearProviderKey() {
  const provider = state.selectedProvider;
  if (!window.confirm(t(`Remover a chave de API salva para ${provider}?`))) return;
  try {
    await api(`/api/llm/providers/${provider}/key`, { method: "DELETE" });
    await Promise.all([loadLLMSettings(), loadHealth()]);
    setProviderFeedback("Chave removida. Se este provedor estava ativo, o sistema voltou para Ollama.");
  } catch (error) {
    setProviderFeedback(error.message, "error");
  }
}

async function loadDocuments() {
  state.documents = await api("/api/documents");
  renderDocuments();
  const select = $("card-document-filter");
  const selected = select.value;
  select.innerHTML = '<option value="">Todos os materiais</option>' + state.documents.map((doc) =>
    `<option value="${doc.id}">${escapeHtml(doc.original_filename)}</option>`).join("");
  select.value = selected;
  $("document-count").textContent = `${state.documents.length} materiais`;
}

function renderDocuments() {
  const list = $("document-list");
  $("library-empty").classList.toggle("hidden", state.documents.length > 0);
  list.innerHTML = state.documents.map((doc) => {
    const statusText = t(({ uploaded: "Na fila", processing: "Processando", ready: "Concluído", error: "Erro" }[doc.status]) || doc.status);
    const ragText = t(({ ready: "RAG semântico", partial: "RAG parcial", lexical_only: "Busca textual", indexing: "Indexando", pending: "Aguardando índice" }[doc.rag_status]) || "");
    const date = parseUtcDate(doc.created_at).toLocaleDateString(currentLocale());
    return `<div class="document-row">
      <div class="file-icon">${escapeHtml(doc.file_type.replace(".", "").toUpperCase())}</div>
      <div class="document-info"><strong title="${escapeHtml(doc.original_filename)}">${escapeHtml(doc.original_filename)}</strong>
      <small>${date} · ${doc.extracted_chars ? `${doc.extracted_chars.toLocaleString(currentLocale())} caracteres` : t("Aguardando extração")}${ragText ? ` · ${ragText}` : ""}${doc.error_message ? ` · ${escapeHtml(doc.error_message)}` : ""}</small></div>
      <span class="status-pill ${doc.status}">${statusText}</span>
      <div class="document-actions">
        ${(doc.status === "error" || (doc.status === "ready" && doc.rag_status !== "ready")) ? `<button class="icon-button" title="${t("Reprocessar e indexar embeddings")}" data-reprocess="${doc.id}">↻</button>` : ""}
        <button class="icon-button" title="${t("Excluir material")}" data-delete-doc="${doc.id}">×</button>
      </div>
    </div>`;
  }).join("");
  list.querySelectorAll("[data-delete-doc]").forEach((button) => button.addEventListener("click", () => deleteDocument(button.dataset.deleteDoc)));
  list.querySelectorAll("[data-reprocess]").forEach((button) => button.addEventListener("click", () => reprocessDocument(button.dataset.reprocess)));
}

async function loadCards() {
  const documentId = $("card-document-filter").value;
  if (state.reviewMode === "due") {
    const suffix = documentId ? `&document_id=${encodeURIComponent(documentId)}` : "";
    const rows = await api(`/api/reviews/queue?limit=200${suffix}`);
    state.cards = rows.map((row) => ({ ...row.card, due_at: row.due_at, review_state: row.state, retrievability: row.retrievability }));
  } else {
    const query = documentId ? `?document_id=${encodeURIComponent(documentId)}` : "";
    state.cards = await api(`/api/cards${query}`);
  }
  state.cardIndex = 0;
  state.revealed = false;
  state.reviewedThisSession = 0;
  state.reviewQueueTotal = state.cards.length;
  $("review-complete").classList.add("hidden");
  renderCard();
  const totalCards = Number($("stat-cards").textContent || 0);
  $("cards-empty").classList.toggle("hidden", state.cards.length > 0 || (state.reviewMode === "due" && totalCards > 0));
  $("study-layout").classList.toggle("hidden", state.cards.length === 0);
  if (state.reviewMode === "due" && state.cards.length === 0 && totalCards > 0) $("review-complete").classList.remove("hidden");
  $("review-mode-toggle").textContent = t(state.reviewMode === "due" ? "Ver todos os cards" : "Revisões de hoje");
  i18n?.translatePage();
}

function renderCard() {
  const card = state.cards[state.cardIndex];
  if (!card) {
    $("card-position").textContent = state.reviewMode === "due" && state.reviewQueueTotal ? "Concluído" : "0 / 0";
    $("study-layout").classList.add("hidden");
    if (state.reviewMode === "due" && Number($("stat-cards").textContent) > 0) $("review-complete").classList.remove("hidden");
    $("card-front").textContent = "Seus flashcards aparecerão aqui.";
    $("card-back").textContent = "";
    $("card-explanation").textContent = "";
    $("card-tags").innerHTML = "";
    $("answer-area").classList.add("hidden");
    $("rating-actions").classList.add("hidden");
    $("reveal-card").classList.remove("hidden");
    $("prev-card").disabled = true;
    $("next-card").disabled = true;
    return;
  }
  $("card-position").textContent = `${state.cardIndex + 1} / ${state.cards.length}`;
  $("card-front").textContent = card.front;
  $("card-back").textContent = card.back;
  $("card-explanation").textContent = card.explanation || "";
  $("card-difficulty").textContent = t(({ facil: "FÁCIL", intermediario: "INTERMEDIÁRIO", dificil: "DIFÍCIL" })[card.difficulty] || "INTERMEDIÁRIO");
  $("card-tags").innerHTML = (card.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  $("answer-area").classList.toggle("hidden", !state.revealed);
  $("rating-actions").classList.toggle("hidden", !state.revealed);
  $("reveal-card").classList.toggle("hidden", state.revealed);
  $("card-hint").textContent = t(state.revealed ? "Avalie com honestidade para ajustar sua próxima revisão" : "Tente responder mentalmente");
  $("prev-card").disabled = state.cardIndex === 0;
  $("next-card").disabled = state.cardIndex >= state.cards.length - 1;
  updateReviewProgress();
}

function updateReviewProgress() {
  const total = state.reviewMode === "due" ? state.reviewQueueTotal : state.cards.length;
  const reviewed = state.reviewMode === "due" ? state.reviewedThisSession : state.cards.filter((card) => card.review_count > 0).length;
  const percent = total ? Math.round(reviewed / total * 100) : 0;
  $("review-progress").style.width = `${percent}%`;
  $("reviewed-count").textContent = `${reviewed} revisados`;
  $("review-percent").textContent = `${percent}%`;
}

async function revealCard() {
  if (!state.cards[state.cardIndex]) return;
  state.revealed = true;
  renderCard();
}

async function rateCurrentCard(rating) {
  const card = state.cards[state.cardIndex];
  if (!card) return;
  document.querySelectorAll(".rating-button").forEach((button) => { button.disabled = true; });
  try {
    const result = await api(`/api/cards/${card.id}/review`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating }),
    });
    card.review_count += 1;
    state.reviewedThisSession += 1;
    toast(`+${result.reward?.xp_awarded || 0} XP · Próxima revisão em ${formatInterval(result.due_at)}.`);
    if (state.reviewMode === "due") {
      state.cards.splice(state.cardIndex, 1);
      if (state.cardIndex >= state.cards.length) state.cardIndex = Math.max(0, state.cards.length - 1);
    } else if (state.cardIndex < state.cards.length - 1) {
      state.cardIndex += 1;
    }
    state.revealed = false;
    renderCard();
    if (!state.cards.length && state.reviewMode === "due" && state.activeSessionId) await finishActiveSession("Fila de revisão concluída.");
    await Promise.all([loadStats(), loadAnalytics(), loadLearningDashboard()]);
  } catch (error) { toast(error.message); }
  finally { document.querySelectorAll(".rating-button").forEach((button) => { button.disabled = false; }); }
}

function parseUtcDate(value) {
  if (typeof value !== "string") return new Date(value);
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

function formatInterval(value) {
  const date = parseUtcDate(value);
  const diff = Math.max(0, date.getTime() - Date.now());
  const minutes = Math.round(diff / 60000);
  if (minutes < 60) return `${Math.max(1, minutes)} min`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h`;
  return currentLanguage() === "es" ? `${Math.round(minutes / 1440)} día(s)` : currentLanguage() === "en" ? `${Math.round(minutes / 1440)} day(s)` : `${Math.round(minutes / 1440)} dia(s)`;
}

async function uploadFile(file) {
  if (!file) return;
  const extension = file.name.split(".").pop().toLowerCase();
  if (!["pdf", "docx", "md"].includes(extension)) {
    toast("Formato não suportado. Envie PDF, DOCX ou MD.");
    return;
  }
  const form = new FormData();
  form.append("file", file);
  form.append("language", currentLanguage());
  $("upload-progress").classList.remove("hidden");
  $("upload-status").textContent = `Enviando ${file.name}...`;
  $("upload-progress-fill").style.width = "35%";
  try {
    const doc = await api("/api/documents/upload", { method: "POST", body: form });
    $("upload-progress-fill").style.width = "65%";
    $("upload-status").textContent = "Arquivo recebido. Extraindo, indexando e gerando cards...";
    toast("Material enviado. A análise começou.");
    await pollDocument(doc.id);
  } catch (error) {
    toast(error.message);
    $("upload-status").textContent = error.message;
  } finally {
    $("upload-progress-fill").style.width = "100%";
    setTimeout(() => $("upload-progress").classList.add("hidden"), 1800);
    $("file-input").value = "";
  }
}

async function pollDocument(id) {
  let attempts = 0;
  while (attempts < 240) {
    const doc = await api(`/api/documents/${id}`);
    if (doc.status === "ready") {
      $("upload-status").textContent = "Flashcards e índice de conhecimento processados.";
      toast(doc.rag_status === "ready" ? "Material indexado com embeddings e cards prontos!" : "Cards prontos. Busca semântica indisponível; confira o modelo de embeddings.");
      await Promise.all([loadDocuments(), loadCards(), loadStats()]);
      setView("cards");
      return;
    }
    if (doc.status === "error") {
      $("upload-status").textContent = doc.error_message || "Falha no processamento.";
      toast(doc.error_message || "Não foi possível processar o material.");
      await loadDocuments();
      return;
    }
    await loadDocuments();
    await new Promise((resolve) => setTimeout(resolve, 2500));
    attempts += 1;
  }
  toast("O processamento continua em segundo plano. Atualize a biblioteca em instantes.");
}

async function deleteDocument(id) {
  if (!confirm(t("Excluir este material, seus chunks e todos os flashcards associados?"))) return;
  try {
    await api(`/api/documents/${id}`, { method: "DELETE" });
    toast("Material excluído.");
    await Promise.all([loadDocuments(), loadCards(), loadStats()]);
  } catch (error) { toast(error.message); }
}

async function reprocessDocument(id) {
  try {
    await api(`/api/documents/${id}/reprocess?language=${encodeURIComponent(currentLanguage())}`, { method: "POST" });
    toast("Processamento reiniciado.");
    await pollDocument(Number(id));
  } catch (error) { toast(error.message); }
}

async function generateQuiz() {
  const count = Number($("exercise-count").value);
  const difficulty = $("exercise-difficulty").value;
  if (Number($("stat-cards").textContent) < 3) {
    toast("Adicione materiais e gere pelo menos 3 flashcards primeiro.");
    return;
  }
  const button = $("generate-exercises");
  button.disabled = true;
  button.textContent = "Gerando com IA...";
  try {
    const exercises = await api("/api/exercises/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count, difficulty, language: currentLanguage(), bloom_level: $("exercise-bloom").value, purpose: "practice" }),
    });
    await prepareQuiz(exercises, "practice");
    await loadStats();
  } catch (error) { toast(error.message); }
  finally {
    button.disabled = false;
    button.innerHTML = 'Gerar sessão <span>✦</span>';
  }
}

async function prepareQuiz(exercises, purpose = "practice") {
  const workspace = await api("/api/exercises/workspace", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      exercise_ids: exercises.map((item) => item.id),
      purpose,
      source: "desktop",
      focus_session_id: state.activeSessionId,
    }),
  });
  state.sharedWorkspaceId = workspace.id;
  state.quiz = exercises.map((item) => ({ ...item, selected: "", result: null, hintLevel: 0, hintText: "", startedAt: Date.now(), confidence: 3 }));
  state.answered = new Set();
  state.correct = 0;
  state.quizXp = 0;
  state.quizPurpose = purpose;
  state.quizCompletionHandled = false;
  renderQuiz();
  $("quiz-area").classList.remove("hidden");
  $("quiz-area").scrollIntoView({ behavior: scrollBehavior(), block: "start" });
  startSharedWorkspacePolling();
}

function startSharedWorkspacePolling() {
  if (state.sharedWorkspaceSyncTimer) clearInterval(state.sharedWorkspaceSyncTimer);
  state.sharedWorkspaceSyncTimer = setInterval(() => syncSharedWorkspace().catch(() => {}), 2500);
}

async function syncSharedWorkspace() {
  if (state.sharedWorkspaceSyncBusy) return;
  state.sharedWorkspaceSyncBusy = true;
  try {
    const workspace = await api("/api/exercises/workspace/active");
    if (!workspace.id || !["active", "completed"].includes(workspace.status)) return;
    const incomingIds = (workspace.exercise_ids || []).map(Number);
    const localIds = state.quiz.map((item) => Number(item.id));
    if (!state.sharedWorkspaceId || !state.quiz.length) {
      if (workspace.status === "active") restoreSharedWorkspace(workspace);
      return;
    }
    if (workspace.id !== state.sharedWorkspaceId || incomingIds.join(",") !== localIds.join(",")) {
      restoreSharedWorkspace(workspace);
      return;
    }
    let changed = false;
    for (const item of state.quiz) {
      const result = workspace.results?.[String(item.id)];
      if (result && !item.result) {
        item.result = result;
        item.selected = result.submitted_answer || "";
        item.result.correct_answer = item.result.correct_answer || "";
        state.answered.add(item.id);
        if (item.result.correct) state.correct += 1;
        state.quizXp += item.result.reward?.xp_awarded || 0;
        changed = true;
      }
    }
    if (changed) {
      for (const item of state.quiz) {
        if (item.kind === "short_answer") {
          const input = $(`answer-${item.id}`);
          if (input && !item.result) item.selected = input.value;
        }
      }
      renderQuiz();
      await Promise.all([loadAnalytics(), loadLearningDashboard()]);
    }
    if (workspace.status === "completed" || state.answered.size === state.quiz.length) {
      await completeQuizIfNeeded();
    }
  } finally {
    state.sharedWorkspaceSyncBusy = false;
  }
}

function restoreSharedWorkspace(workspace) {
  state.sharedWorkspaceId = workspace.id;
  state.activeSessionId = workspace.focus_session_id || null;
  state.quizPurpose = workspace.purpose || "practice";
  state.quiz = (workspace.exercises || []).map((item) => ({
    ...item,
    selected: item.result?.submitted_answer || "",
    result: item.result || null,
    hintLevel: 0,
    hintText: "",
    startedAt: Date.now(),
    confidence: 3,
  }));
  state.answered = new Set(state.quiz.filter((item) => item.result).map((item) => item.id));
  state.correct = state.quiz.filter((item) => item.result?.correct).length;
  state.quizXp = state.quiz.reduce((total, item) => total + (item.result?.reward?.xp_awarded || 0), 0);
  state.quizCompletionHandled = false;
  if (!state.quiz.length) return;
  renderQuiz();
  $("quiz-area").classList.remove("hidden");
  startSharedWorkspacePolling();
  toast("Sessão compartilhada restaurada. Você pode continuar pelo computador ou pelo WhatsApp.");
  if (state.answered.size === state.quiz.length) completeQuizIfNeeded().catch(() => {});
}

function renderQuiz() {
  $("quiz-completion").classList.add("hidden");
  $("quiz-questions").classList.remove("hidden");
  $("quiz-score").textContent = `${state.correct} acertos · ${state.quizXp} XP`;
  const total = state.quiz.length;
  $("quiz-progress").style.width = `${total ? state.answered.size / total * 100 : 0}%`;
  $("quiz-questions").innerHTML = state.quiz.map((item, index) => {
    const typeLabel = t(({ multiple_choice: "Múltipla escolha", true_false: "Verdadeiro ou falso", short_answer: "Resposta curta" })[item.kind]);
    const controls = item.kind === "short_answer"
      ? `<input class="short-answer-input" id="answer-${item.id}" placeholder="Digite sua resposta..." ${item.result ? "disabled" : ""} value="${escapeHtml(item.selected)}">`
      : `<div class="option-list">${item.options.map((option) => `<button class="option-button ${item.selected === option ? "selected" : ""}" data-select="${item.id}" data-option="${escapeHtml(option)}" ${item.result ? "disabled" : ""}>${escapeHtml(option)}</button>`).join("")}</div>`;
    const remediation = item.result?.remediation_progress
      ? `<div class="error-intervention"><strong>${item.result.remediation_progress.resolved ? "Dificuldade superada!" : "Boa recuperação — continue consolidando."}</strong><p>${item.result.remediation_progress.successful_rechecks}/${item.result.remediation_progress.required_rechecks} verificações corretas. ${item.result.remediation_progress.resolved ? "O sistema marcou esta dificuldade como resolvida." : "Faça mais uma atividade de reforço para confirmar que o aprendizado se manteve."}</p></div>`
      : "";
    const feedback = item.result ? `<div class="answer-feedback ${item.result.correct ? "correct" : "incorrect"}"><strong>${item.result.correct ? "Muito bem!" : "Vamos aprender com esse erro."} · ${Math.round((item.result.score ?? (item.result.correct ? 1 : 0)) * 100)}%</strong> ${escapeHtml(item.result.feedback)}<small>Resposta: ${escapeHtml(item.result.correct_answer)} · ${escapeHtml(item.result.explanation)}</small>${item.result.error_diagnosis ? `<div class="error-intervention"><strong>Como melhorar</strong><p>${escapeHtml(item.result.error_diagnosis.intervention)}</p><small>${escapeHtml(item.result.error_diagnosis.practice_prompt)}</small></div>` : ""}${remediation}<div class="reward-inline">+${item.result.reward?.xp_awarded || 0} XP</div></div>` : "";
    const hint = item.hintText ? `<div class="hint-box"><strong>Dica ${item.hintLevel}/3</strong><p>${escapeHtml(item.hintText)}</p></div>` : "";
    return `<article class="question-card">
      <div class="question-top"><span class="question-number">MISSÃO ${index + 1}</span><span class="question-type">${typeLabel} · ${escapeHtml(item.bloom_level || "understand")}</span></div>
      <h4>${escapeHtml(item.question)}</h4>${controls}${hint}
      ${item.result ? feedback : `<div class="question-actions-row"><label class="confidence-control">Minha confiança<select data-confidence="${item.id}" class="select-control"><option value="1">1 · Chute</option><option value="2">2 · Baixa</option><option value="3" selected>3 · Média</option><option value="4">4 · Alta</option><option value="5">5 · Certeza</option></select></label><button class="text-button hint-button" data-hint="${item.id}" ${item.hintLevel >= 3 ? "disabled" : ""}>💡 Pedir dica ${item.hintLevel ? `(${item.hintLevel}/3)` : ""}</button><button class="primary-button answer-submit" data-submit="${item.id}">Confirmar resposta</button></div>`}
    </article>`;
  }).join("");

  $("quiz-questions").querySelectorAll("[data-select]").forEach((button) => button.addEventListener("click", () => {
    const item = state.quiz.find((q) => q.id === Number(button.dataset.select));
    if (item && !item.result) { item.selected = button.dataset.option; renderQuiz(); }
  }));
  $("quiz-questions").querySelectorAll("[data-submit]").forEach((button) => button.addEventListener("click", () => submitAnswer(Number(button.dataset.submit))));
  $("quiz-questions").querySelectorAll("[data-hint]").forEach((button) => button.addEventListener("click", () => requestExerciseHint(Number(button.dataset.hint))));
  i18n?.translatePage();
}

async function submitAnswer(id) {
  const item = state.quiz.find((question) => question.id === id);
  if (!item || item.result) return;
  const input = item.kind === "short_answer" ? $(`answer-${id}`) : null;
  const answer = input ? input.value.trim() : item.selected;
  if (!answer) { toast("Selecione ou digite uma resposta antes de confirmar."); return; }
  const button = document.querySelector(`[data-submit="${id}"]`);
  if (button) { button.disabled = true; button.textContent = "Corrigindo..."; }
  try {
    item.result = await api(`/api/exercises/${id}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        answer, language: currentLanguage(),
        confidence: Number(document.querySelector(`[data-confidence="${id}"]`)?.value || item.confidence || 3),
        response_time_seconds: Math.max(0, (Date.now() - item.startedAt) / 1000),
        session_id: state.activeSessionId,
        workspace_id: state.sharedWorkspaceId,
      }),
    });
    item.selected = answer;
    state.answered.add(id);
    if (item.result.correct) state.correct += 1;
    state.quizXp += item.result.reward?.xp_awarded || 0;
    renderQuiz();
    await Promise.all([loadAnalytics(), loadLearningDashboard()]);
    if (state.answered.size === state.quiz.length) await completeQuizIfNeeded();
  } catch (error) {
    toast(error.message);
    if (button) { button.disabled = false; button.textContent = "Confirmar resposta"; }
  }
}

async function completeQuizIfNeeded() {
  if (state.quizCompletionHandled || !state.quiz.length || state.answered.size !== state.quiz.length) return;
  state.quizCompletionHandled = true;
  if (state.sharedWorkspaceSyncTimer) clearInterval(state.sharedWorkspaceSyncTimer);
  state.sharedWorkspaceSyncTimer = null;
  $("quiz-questions").classList.add("hidden");
  $("quiz-completion").classList.remove("hidden");
  $("diagnostic-report").classList.add("hidden");
  $("completion-message").textContent = `Você acertou ${state.correct} de ${state.quiz.length} questões (${Math.round(state.correct / state.quiz.length * 100)}%). Ganhou ${state.quizXp} XP. Revise as intervenções abaixo para transformar os erros em progresso.`;
  const completedSessionId = state.activeSessionId;
  if (state.quizPurpose === "diagnostic" && completedSessionId) {
    try {
      const report = await api(`/api/learning/diagnostic/report?session_id=${completedSessionId}`);
      renderDiagnosticReport(report);
    } catch (reportError) {
      toast(`Não foi possível montar o relatório: ${reportError.message}`);
    }
  }
  if (state.activeSessionId) await finishActiveSession("Sessão de aprendizagem concluída.");
  await loadLearningDashboard();
}

function renderDiagnosticReport(report) {
  const container = $("diagnostic-report");
  container.classList.remove("hidden");
  const rows = (report.topics || []).map((item) => `
    <div class="diagnostic-topic ${item.status}">
      <span>${escapeHtml(item.topic)} · ${item.correct}/${item.attempts} ${t("corretas")}</span>
      <span>${Math.round(item.score * 100)}% ${item.status === "strong" ? "✓" : item.status === "needs_attention" ? "Reforçar" : "Em evolução"}</span>
    </div>`).join("");
  container.innerHTML = `<h4>${t("Seu mapa de diagnóstico")}</h4><p>${escapeHtml(t(report.summary))}</p>${rows || `<p>${t("Responda mais questões para formar seu diagnóstico.")}</p>`}`;
  if (report.priority_topics?.length) {
    container.innerHTML += `<p><strong>${t("Prioridade para estudar:")}</strong> ${report.priority_topics.map(escapeHtml).join(", ")}</p>`;
  }
  i18n?.translatePage();
}

async function requestExerciseHint(id) {
  const item = state.quiz.find((question) => question.id === id);
  if (!item || item.result || item.hintLevel >= 3) return;
  const button = document.querySelector(`[data-hint="${id}"]`);
  if (button) button.disabled = true;
  try {
    const level = item.hintLevel + 1;
    const result = await api(`/api/exercises/${id}/hint`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level, language: currentLanguage() }),
    });
    item.hintLevel = level;
    item.hintText = result.hint;
    renderQuiz();
  } catch (error) { toast(error.message); if (button) button.disabled = false; }
}

function appendTutorMessage(role, content, sources = []) {
  const container = $("tutor-messages");
  const wrapper = document.createElement("div");
  wrapper.className = `tutor-message ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = role === "assistant" ? "✧" : "S";
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  const text = document.createElement("div");
  text.className = "message-text";
  text.textContent = content;
  bubble.appendChild(text);
  if (sources.length) {
    const sourceWrap = document.createElement("div");
    sourceWrap.className = "source-list";
    const label = document.createElement("strong");
    label.textContent = "Fontes recuperadas";
    sourceWrap.appendChild(label);
    sources.forEach((source) => {
      const item = document.createElement("div");
      item.className = "source-item";
      item.textContent = `[${source.citation}] ${source.filename}${source.page_number ? ` · página ${source.page_number}` : ""} · relevância ${(source.score * 100).toFixed(0)}%`;
      sourceWrap.appendChild(item);
    });
    bubble.appendChild(sourceWrap);
  }
  wrapper.append(avatar, bubble);
  container.appendChild(wrapper);
  container.scrollTop = container.scrollHeight;
}

async function loadTutorHistory() {
  const history = await api("/api/tutor/history?limit=50");
  const signature = history.map((message) => `${message.created_at}|${message.role}|${message.content}`).join("\n");
  if (signature === state.tutorHistorySignature) return;
  state.tutorHistorySignature = signature;
  $("tutor-messages").innerHTML = "";
  history.forEach((message) => appendTutorMessage(message.role, message.content, message.sources || []));
}

async function sendTutorMessage(event) {
  event.preventDefault();
  const input = $("tutor-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  appendTutorMessage("user", message);
  const send = $("tutor-send");
  send.disabled = true;
  send.textContent = "Pensando...";
  try {
    const result = await api("/api/tutor/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, language: currentLanguage(), mode: $("tutor-mode").value }),
    });
    appendTutorMessage("assistant", result.answer, result.sources);
    if (!result.retrieval_count) toast("Nenhum trecho foi recuperado. Confira se seus materiais foram indexados.");
  } catch (error) {
    appendTutorMessage("assistant", `Não consegui responder: ${error.message}`);
  } finally {
    send.disabled = false;
    send.innerHTML = 'Enviar <span>↑</span>';
    input.focus();
  }
}

async function clearTutorHistory() {
  if (!confirm(t("Limpar todo o histórico de conversa com o tutor?"))) return;
  try {
    await api("/api/tutor/history", { method: "DELETE" });
    $("tutor-messages").innerHTML = "";
    appendTutorMessage("assistant", "Conversa limpa. Faça uma nova pergunta sobre seus materiais.");
  } catch (error) { toast(error.message); }
}

function renderMastery(topics) {
  const container = $("mastery-list");
  if (!topics.length) {
    container.innerHTML = '<span class="muted-note">Ainda não há dados suficientes. Revise cards e responda exercícios para formar seu mapa de conhecimento.</span>';
    return;
  }
  container.innerHTML = topics.slice(0, 12).map((topic) => {
    const percent = Math.round(topic.mastery * 100);
    const level = percent >= 75 ? "strong" : percent < 50 ? "weak" : "";
    return `<div class="mastery-row"><div class="mastery-label"><strong>${escapeHtml(topic.topic)}</strong><span>${percent}% · ${topic.attempts} tentativas</span></div><div class="progress-track"><div class="progress-fill ${level}" style="width:${percent}%"></div></div></div>`;
  }).join("");
}

async function loadAnalytics() {
  const data = await api("/api/analytics/overview");
  renderMastery(data.topics || []);
  $("kpi-due").textContent = data.due_cards;
  $("kpi-accuracy").textContent = `${Math.round(data.exercise_accuracy * 100)}%`;
  $("kpi-attempts").textContent = data.exercise_attempts;
  $("kpi-topics").textContent = data.topics.length;
  const retention = data.retention || {};
  for (const [key, id] of [["24h", "retention-24h"], ["7d", "retention-7d"], ["30d", "retention-30d"]]) {
    const metric = retention[key] || {};
    $(id).textContent = metric.accuracy == null ? "—" : `${Math.round(metric.accuracy * 100)}%`;
    $(`${id}-n`).textContent = metric.reviews ? `${metric.reviews} revisões observadas` : "Aguardando amostras";
  }
  $("analytics-errors").textContent = data.unresolved_errors ?? 0;
  return data;
}

async function loadLatestPlan() {
  const plans = await api("/api/agent/plans");
  if (plans.length) renderPlan(plans[0]);
}

function renderPlan(plan) {
  state.activePlanId = plan.id;
  $("plan-output").classList.remove("hidden");
  $("plan-title").textContent = plan.title;
  $("plan-summary").textContent = plan.summary;
  $("plan-tasks").innerHTML = plan.tasks.map((task) => `
    <label class="plan-task ${task.completed ? "completed" : ""}">
      <input type="checkbox" data-task-id="${task.id}" ${task.completed ? "checked" : ""}>
      <span class="task-day">DIA ${task.day_number}</span>
      <span class="task-copy"><strong>${escapeHtml(task.title)}</strong><small>${escapeHtml(task.description)} · ${task.estimated_minutes} min${task.topic ? ` · ${escapeHtml(task.topic)}` : ""}</small></span>
      <span class="task-check">${task.completed ? "✓" : "○"}</span>
    </label>`).join("");
  updatePlanProgress();
  $("plan-tasks").querySelectorAll("[data-task-id]").forEach((checkbox) => checkbox.addEventListener("change", async () => {
    try {
      await api(`/api/agent/tasks/${checkbox.dataset.taskId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ completed: checkbox.checked }),
      });
      checkbox.closest(".plan-task").classList.toggle("completed", checkbox.checked);
      checkbox.closest(".plan-task").querySelector(".task-check").textContent = checkbox.checked ? "✓" : "○";
      updatePlanProgress();
    } catch (error) { toast(error.message); checkbox.checked = !checkbox.checked; }
  }));
}

function updatePlanProgress() {
  const tasks = [...document.querySelectorAll("#plan-tasks [data-task-id]")];
  const completed = tasks.filter((item) => item.checked).length;
  $("plan-progress").textContent = `${completed} / ${tasks.length} tarefas`;
}

async function adaptPlan() {
  if (!state.activePlanId) return;
  const button = $("adapt-plan");
  button.disabled = true;
  button.textContent = "Analisando seu progresso...";
  try {
    const plan = await api(`/api/agent/plans/${state.activePlanId}/adapt?language=${encodeURIComponent(currentLanguage())}`, { method: "POST" });
    renderPlan(plan);
    toast("Plano atualizado com base nas suas dificuldades e revisões pendentes.");
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = "Adaptar plano ao meu progresso ↻"; i18n?.translatePage(); }
}

async function createPlan(event) {
  event.preventDefault();
  const button = $("generate-plan");
  button.disabled = true;
  button.textContent = "Agente planejando...";
  try {
    const plan = await api("/api/agent/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        language: currentLanguage(),
        objective: $("plan-objective").value,
        days: Number($("plan-days").value),
        minutes_per_day: Number($("plan-minutes").value),
      }),
    });
    renderPlan(plan);
    toast("Seu plano personalizado foi criado.");
    $("plan-output").scrollIntoView({ behavior: scrollBehavior(), block: "start" });
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.innerHTML = 'Criar plano com IA <span>✦</span>'; }
}

async function generateInsights() {
  const button = $("generate-insights");
  button.disabled = true;
  button.textContent = "Analisando...";
  try {
    const result = await api(`/api/agent/insights?language=${encodeURIComponent(currentLanguage())}`, { method: "POST" });
    const insights = result.insights;
    const container = $("insights-result");
    container.classList.remove("hidden");
    container.innerHTML = `
      <p>${escapeHtml(insights.summary)}</p>
      ${insights.strengths.length ? `<strong>Pontos fortes</strong><ul>${insights.strengths.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
      ${insights.risks.length ? `<strong>Atenção</strong><ul>${insights.risks.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
      ${insights.recommendations.length ? `<strong>Próximas ações</strong><ul>${insights.recommendations.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
      ${insights.confidence_note ? `<small>${escapeHtml(insights.confidence_note)}</small>` : ""}`;
    renderMastery(result.analytics.topics || []);
    await refreshObservability();
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.innerHTML = 'Analisar meu aprendizado <span>↗</span>'; }
}

async function evaluateCards() {
  const button = $("evaluate-cards");
  button.disabled = true;
  button.textContent = "Avaliando cards...";
  try {
    const result = await api("/api/evaluations/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 10, language: currentLanguage() }),
    });
    const container = $("evaluation-result");
    container.classList.remove("hidden");
    container.innerHTML = `<div class="evaluation-summary">Nota média: <strong>${result.average_score.toFixed(2)} / 5</strong> · ${result.evaluated} cards avaliados</div>` +
      result.results.map((item) => `<div class="evaluation-item"><strong>${escapeHtml(item.front)}</strong><span>${item.overall_score.toFixed(1)} / 5</span><small>${escapeHtml(item.issues.join(" · ") || "Nenhum problema apontado")}</small></div>`).join("");
    await refreshObservability();
    toast("Avaliação concluída.");
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.innerHTML = 'Avaliar últimos 10 cards <span>↗</span>'; }
}

const achievementLabels = {
  first_100_xp: "Primeiros 100 XP", knowledge_explorer: "Explorador do conhecimento",
  three_day_streak: "3 dias de consistência", ten_correct: "10 respostas corretas",
};
const errorLabels = {
  knowledge_gap: "Lacuna de conhecimento", misconception: "Conceito confundido",
  misinterpretation: "Interpretação", application_error: "Aplicação",
  careless_error: "Atenção", incomplete_answer: "Resposta incompleta",
  retrieval_failure: "Dificuldade de lembrar",
};

async function loadLearningDashboard() {
  const data = await api("/api/learning/dashboard");
  state.learningDashboard = data;
  const progress = data.progress;
  $("journey-level-title").textContent = `Nível ${progress.level} · ${progress.level >= 5 ? "Mestre do conhecimento" : progress.level >= 3 ? "Aprendiz avançado" : "Explorador"}`;
  $("journey-level-copy").textContent = `${progress.xp} XP acumulados. Continue praticando para desbloquear o próximo nível.`;
  $("journey-xp-fill").style.width = `${progress.level_progress}%`;
  $("journey-xp-label").textContent = `${progress.xp % 250} / 250 XP · faltam ${progress.xp_to_next_level} XP`;
  $("journey-streak").textContent = progress.streak_days;
  $("journey-due").textContent = data.due_count;
  $("journey-achievements").textContent = (progress.achievements || []).length;
  $("nav-due-count").textContent = data.due_count;
  renderLearningRecommendations(data.recommendations || []);
  renderDailyQuests(data.daily_quests || { available: false, quests: [] });
  renderLearningConcepts(data.concepts || []);
  renderLearningErrors(data.recent_errors || []);
  renderAchievements(progress.achievements || []);
  await loadConceptOptions(data.concepts || []);
  i18n?.translatePage();
  return data;
}

function renderDailyQuests(data) {
  const container = $("daily-quests-grid");
  const claimButton = $("claim-daily-quests");
  const status = $("daily-quests-status");
  if (!data.available || !data.quests?.length) {
    container.innerHTML = `<div class="recommendation-empty"><strong>Suas missões começam com seu primeiro material.</strong><p>Adicione um documento e transforme o estudo em pequenas conquistas diárias.</p><button class="text-button" data-go-library>Adicionar material ↗</button></div>`;
    claimButton.classList.add("hidden");
    status.textContent = "Cada missão representa uma ação real de aprendizagem.";
    container.querySelector("[data-go-library]")?.addEventListener("click", () => setView("library"));
    i18n?.translatePage();
    return;
  }
  container.innerHTML = data.quests.map((quest) => {
    const percent = Math.min(100, Math.round(quest.progress / Math.max(1, quest.target) * 100));
    const complete = quest.progress >= quest.target;
    return `<article class="daily-quest-card ${complete ? "complete" : ""}">
      <div class="daily-quest-top"><span class="daily-quest-icon">${escapeHtml(quest.icon)}</span><span class="daily-quest-state">${complete ? "Concluída ✓" : "Em andamento"}</span></div>
      <strong>${escapeHtml(quest.title)}</strong><p>${escapeHtml(quest.description)}</p>
      <div class="daily-quest-progress"><span>${quest.progress} / ${quest.target} ${escapeHtml(quest.unit)}</span><span>${percent}%</span></div>
      <div class="daily-quest-track"><span style="width:${percent}%"></span></div>
    </article>`;
  }).join("");
  claimButton.classList.toggle("hidden", !data.claimable);
  claimButton.disabled = Boolean(data.claimed);
  status.textContent = data.claimed
    ? "Recompensa de hoje resgatada. Amanhã teremos novas missões!"
    : data.claimable
      ? "Todas as missões concluídas. Resgate sua recompensa!"
      : "Complete as três missões no seu ritmo. Não há penalidade por pausas.";
  i18n?.translatePage();
}

async function claimDailyQuests() {
  const button = $("claim-daily-quests");
  button.disabled = true;
  try {
    const result = await api("/api/learning/quests/claim", { method: "POST" });
    toast(`${t("Missões concluídas!")} +${result.xp_awarded} XP`);
    await loadLearningDashboard();
  } catch (error) {
    toast(error.message);
    button.disabled = false;
  }
}

function renderLearningRecommendations(items) {
  const container = $("learning-recommendations");
  if (!items.length) {
    container.innerHTML = `<div class="recommendation-empty"><strong>Você está em dia! ✨</strong><p>Escolha um conceito para aprender algo novo ou faça um desafio para testar sua memória.</p></div>`;
    return;
  }
  container.innerHTML = items.map((item) => `<button class="recommendation-item" data-recommendation="${escapeHtml(item.action)}" data-topic="${escapeHtml(item.topic || "")}"><span class="recommendation-priority">${item.priority}</span><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.description)}</small></span><span class="recommendation-arrow">↗</span></button>`).join("");
  container.querySelectorAll("[data-recommendation]").forEach((button) => button.addEventListener("click", async () => {
    const action = button.dataset.recommendation;
    if (action === "cards") { state.reviewMode = "due"; setView("cards"); await loadCards(); }
    else if (action === "lesson") { $("concept-select").value = button.dataset.topic; await startAdaptiveLesson(); }
    else if (action === "errors") $("learning-errors").scrollIntoView({ behavior: scrollBehavior(), block: "center" });
  }));
  i18n?.translatePage();
}

function renderLearningConcepts(concepts) {
  const container = $("learning-concepts");
  if (!concepts.length) {
    container.innerHTML = `<div class="recommendation-empty"><strong>Seu mapa está esperando.</strong><p>Adicione materiais e atualize o mapa de conceitos para organizar o que precisa aprender.</p></div>`;
    return;
  }
  container.innerHTML = concepts.slice(0, 12).map((item) => {
    const percent = Math.round((item.mastery || 0) * 100);
    const stateLabel = percent >= 75 ? "Em consolidação" : percent >= 45 ? "Em desenvolvimento" : "Precisa de atenção";
    return `<button class="concept-node" data-concept="${escapeHtml(item.title)}"><span class="concept-node-dot ${percent >= 75 ? "mastered" : percent < 45 ? "weak" : "learning"}"></span><span><strong>${escapeHtml(item.title)}</strong><small>${stateLabel} · ${item.card_count || 0} cartões${item.prerequisites?.length ? ` · depende de: ${item.prerequisites.slice(0,2).join(", ")}` : ""}</small></span><span class="concept-percent">${percent}%</span></button>`;
  }).join("");
  container.querySelectorAll("[data-concept]").forEach((button) => button.addEventListener("click", () => {
    $("concept-select").value = button.dataset.concept;
    $("feynman-topic").value = button.dataset.concept;
    startAdaptiveLesson();
  }));
  i18n?.translatePage();
}

function renderLearningErrors(errors) {
  const container = $("learning-errors");
  if (!errors.length) {
    container.innerHTML = `<div class="recommendation-empty"><strong>Nenhuma dificuldade pendente! 🌱</strong><p>Quando um erro aparecer, vamos transformá-lo em um plano de melhoria.</p></div>`;
    return;
  }
  container.innerHTML = errors.map((item) => `<article class="error-coach-item"><div class="error-coach-top"><strong>${escapeHtml(item.topic)}</strong><span>${escapeHtml(errorLabels[item.error_type] || item.error_type)}</span></div><p>${escapeHtml(item.explanation)}</p><div class="error-coach-intervention"><strong>Próximo passo</strong><small>${escapeHtml(item.intervention)}</small><small>Verificações corretas: ${item.successful_rechecks || 0}/${item.required_rechecks || 2}</small></div><button class="primary-button remediation-button" data-practice-error="${item.id}">Praticar reforço ↗</button></article>`).join("");
  container.querySelectorAll("[data-practice-error]").forEach((button) => button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const exercise = await api(`/api/learning/errors/${button.dataset.practiceError}/practice?language=${encodeURIComponent(currentLanguage())}`, { method: "POST" });
      if (!state.activeSessionId) await beginTrackedSession("practice");
      await prepareQuiz([exercise], "remediation");
      setView("exercises");
      toast(t("Atividade de reforço criada. São necessárias duas verificações corretas em exercícios distintos para superar essa dificuldade."));
    } catch (error) { toast(error.message); button.disabled = false; }
  }));
  i18n?.translatePage();
}

function renderAchievements(items) {
  const container = $("achievement-list");
  container.innerHTML = items.length
    ? items.map((key) => `<span class="achievement-chip">🏅 ${escapeHtml(achievementLabels[key] || key)}</span>`).join("")
    : `<span class="achievement-empty">Sua primeira conquista está logo ali.</span>`;
  i18n?.translatePage();
}

async function loadConceptOptions(concepts) {
  const selects = [$("concept-select"), $("feynman-topic")];
  for (const select of selects) {
    const previous = select.value;
    select.innerHTML = `<option value="">Selecione um conceito</option>` + concepts.map((item) => `<option value="${escapeHtml(item.title)}">${escapeHtml(item.title)}</option>`).join("");
    if (concepts.some((item) => item.title === previous)) select.value = previous;
  }
}

async function syncLearningConcepts() {
  const button = $("sync-concepts");
  button.disabled = true;
  try {
    const result = await api(`/api/learning/concepts/sync?language=${encodeURIComponent(currentLanguage())}`, { method: "POST" });
    toast(result.fallback ? "Mapa básico atualizado pelas tags dos cards." : `${result.count} conceitos organizados no mapa.`);
    await loadLearningDashboard();
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}

async function startAdaptiveLesson() {
  const topic = $("concept-select").value;
  if (!topic) { toast("Escolha um conceito primeiro."); return; }
  const button = $("generate-lesson");
  button.disabled = true;
  button.textContent = "Preparando sua aula...";
  try {
    const lesson = await api("/api/learning/lesson", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, language: currentLanguage() }),
    });
    $("lesson-panel").classList.remove("hidden");
    $("lesson-title").textContent = lesson.title;
    $("lesson-goal").textContent = lesson.learning_goal;
    const sections = [
      ["Comece pelo simples", lesson.simple_explanation], ["Uma analogia", lesson.analogy],
      ["Agora, aprofunde", lesson.technical_explanation], ["Exemplo resolvido", lesson.worked_example],
      ["No mundo real", lesson.real_world_application],
    ].filter((item) => item[1]);
    $("lesson-sections").innerHTML = sections.map(([title, body]) => `<article class="lesson-section"><h4>${escapeHtml(title)}</h4><p>${escapeHtml(body)}</p></article>`).join("") + (lesson.common_mistakes?.length ? `<article class="lesson-section mistake-section"><h4>Armadilhas comuns</h4><ul>${lesson.common_mistakes.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></article>` : "");
    $("lesson-check-question").textContent = lesson.check_question;
    $("lesson-check-answer").value = "";
    $("lesson-check-result").classList.add("hidden");
    $("lesson-sources").innerHTML = (lesson.sources || []).length ? `<strong>Fontes consultadas</strong><span>${lesson.sources.map((source) => `${escapeHtml(source.citation)} · ${escapeHtml(source.filename)}${source.page_number ? ` · pág. ${source.page_number}` : ""}`).join(" | ")}</span>` : "";
    i18n?.translatePage();
    $("lesson-panel").scrollIntoView({ behavior: scrollBehavior(), block: "start" });
    toast("Sua microaula está pronta. Leia, pense e depois tente explicar o conceito.");
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.innerHTML = 'Começar microaula <span>✦</span>'; }
}

async function startMatchingGame() {
  const button = $("start-match-game");
  button.disabled = true;
  try {
    const result = await api("/api/learning/games/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic: $("concept-select").value || "", pair_count: 5 }),
    });
    state.matchGame = { ...result, matches: {}, selectedTerm: null, submitted: false };
    $("matching-game").classList.remove("hidden");
    $("matching-game-topic").textContent = `${t("Tema:")} ${result.topic} · ${result.pair_count} ${t("associações")}`;
    $("matching-game-result").classList.add("hidden");
    $("play-match-again").classList.add("hidden");
    $("submit-match-game").classList.remove("hidden");
    $("submit-match-game").disabled = true;
    renderMatchingGame();
    $("matching-game").scrollIntoView({ behavior: scrollBehavior(), block: "center" });
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

function renderMatchingGame() {
  const game = state.matchGame;
  if (!game) return;
  const terms = $("match-terms");
  const definitions = $("match-definitions");
  terms.innerHTML = game.terms.map((item, index) => {
    const matched = Object.prototype.hasOwnProperty.call(game.matches, item.id);
    const selected = game.selectedTerm === item.id;
    const resultClass = game.submitted
      ? (game.correctMatches?.[item.id] === game.matches[item.id] ? "matched" : "wrong")
      : matched ? "matched" : "";
    return `<button type="button" class="match-choice ${selected ? "selected" : ""} ${resultClass}" data-match-term="${item.id}" ${game.submitted ? "disabled" : ""}>
      <span class="match-choice-index">${index + 1}</span><span>${escapeHtml(item.text)}</span>
    </button>`;
  }).join("");
  definitions.innerHTML = game.definitions.map((item, index) => {
    const selectedFor = Object.entries(game.matches).find(([, definitionId]) => definitionId === item.id)?.[0];
    const isSelected = Boolean(selectedFor);
    const resultClass = game.submitted && selectedFor
      ? (game.correctMatches?.[selectedFor] === item.id ? "matched" : "wrong")
      : "";
    return `<button type="button" class="match-choice ${isSelected ? "selected" : ""} ${resultClass}" data-match-definition="${item.id}" ${game.submitted || isSelected ? "disabled" : ""}>
      <span class="match-choice-index">${String.fromCharCode(65 + index)}</span><span>${escapeHtml(item.text)}</span>
    </button>`;
  }).join("");
  const completed = Object.keys(game.matches).length;
  $("matching-game-progress").textContent = game.submitted
    ? `${game.correctPairs}/${game.pair_count} ${t("associações corretas.")}`
    : `${completed} ${t("de")} ${game.pair_count} ${t("associações feitas.")} ${t("Selecione um conceito e depois uma definição.")}`;
  i18n?.translatePage();
  $("submit-match-game").disabled = game.submitted || completed !== game.pair_count;
  terms.querySelectorAll("[data-match-term]").forEach((button) => button.addEventListener("click", () => {
    game.selectedTerm = game.selectedTerm === button.dataset.matchTerm ? null : button.dataset.matchTerm;
    renderMatchingGame();
  }));
  definitions.querySelectorAll("[data-match-definition]").forEach((button) => button.addEventListener("click", () => {
    if (!game.selectedTerm) {
      toast(t("Escolha primeiro um conceito."));
      return;
    }
    const definitionId = button.dataset.matchDefinition;
    for (const [termId, currentDefinition] of Object.entries(game.matches)) {
      if (currentDefinition === definitionId) delete game.matches[termId];
    }
    game.matches[game.selectedTerm] = definitionId;
    game.selectedTerm = null;
    renderMatchingGame();
  }));
}

async function submitMatchingGame() {
  const game = state.matchGame;
  if (!game || game.submitted) return;
  const button = $("submit-match-game");
  button.disabled = true;
  try {
    const result = await api(`/api/learning/games/${game.id}/submit`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ matches: game.matches }),
    });
    game.submitted = true;
    game.correctMatches = result.correct_matches;
    game.correctPairs = result.correct_pairs;
    renderMatchingGame();
    $("matching-game-result").classList.remove("hidden");
    $("matching-game-result").innerHTML = `<strong>${escapeHtml(t(result.message))} · ${Math.round(result.score * 100)}%</strong><span>${result.correct_pairs} ${t("de")} ${result.total_pairs} ${t("pares corretos.")} ${t("Você ganhou")} ${result.xp} XP. ${t("Revise as associações marcadas e tente outra rodada para consolidar.")}</span>`;
    $("play-match-again").classList.remove("hidden");
    $("submit-match-game").classList.add("hidden");
    await Promise.all([loadLearningDashboard(), loadAnalytics()]);
  } catch (error) {
    toast(error.message);
    button.disabled = false;
  }
}

async function submitLessonCheck(event) {
  event.preventDefault();
  const topic = $("concept-select").value;
  const answer = $("lesson-check-answer").value.trim();
  if (!topic || answer.length < 5) {
    toast(t("Escreva uma resposta com pelo menos cinco caracteres."));
    return;
  }
  const button = $("submit-lesson-check");
  button.disabled = true;
  button.textContent = "Analisando sua resposta...";
  try {
    const result = await api("/api/learning/lesson/check", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topic, answer, language: currentLanguage(),
        confidence: Number($("lesson-check-confidence").value || 3),
      }),
    });
    const box = $("lesson-check-result");
    box.classList.remove("hidden");
    box.classList.toggle("incorrect", !result.correct);
    const details = result.correct
      ? "Você demonstrou compreensão suficiente. Continue revisando para consolidar a memória."
      : `${escapeHtml(result.intervention || "Revise o conceito e tente novamente.")}${result.missing_concepts?.length ? ` Conceitos a completar: ${result.missing_concepts.map(escapeHtml).join(", ")}.` : ""}`;
    box.innerHTML = `<strong>${result.correct ? "Muito bem! Conceito compreendido." : "Encontramos um ponto para melhorar."} · ${Math.round(result.score * 100)}%</strong><span>${escapeHtml(result.feedback)}</span><p>${details}</p><small>+${result.reward?.xp_awarded || 0} XP</small>`;
    if (!result.correct) toast(t("Sua dificuldade foi registrada no Coach de Aprendizagem."));
    i18n?.translatePage();
    await loadLearningDashboard();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Verificar compreensão ↗";
  }
}

async function beginTrackedSession(type) {
  if (state.activeSessionId) return;
  const result = await api("/api/learning/sessions/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_type: type }),
  });
  state.activeSessionId = result.id;
  state.activeSessionType = type;
  state.sessionStartedAt = Date.now();
  $("session-button-title").textContent = "Sessão em andamento";
  $("session-button-copy").textContent = "Você está registrando seu tempo de foco.";
  $("session-button-action").textContent = "Encerrar sessão ✓";
  if (state.sessionTimer) clearInterval(state.sessionTimer);
  state.sessionTimer = setInterval(() => {
    const seconds = Math.floor((Date.now() - state.sessionStartedAt) / 1000);
    $("session-button-copy").textContent = `Tempo focado: ${Math.floor(seconds / 60)} min ${seconds % 60}s`;
  }, 1000);
}

async function finishActiveSession(summary = "Sessão finalizada pelo aluno.") {
  if (!state.activeSessionId) return;
  const id = state.activeSessionId;
  const result = await api(`/api/learning/sessions/${id}/finish`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ summary }),
  });
  state.activeSessionId = null;
  if (state.sessionTimer) clearInterval(state.sessionTimer);
  state.sessionTimer = null;
  $("session-button-title").textContent = "Iniciar sessão focada";
  $("session-button-copy").textContent = "Registre seu tempo de estudo sem pressão.";
  $("session-button-action").textContent = "Começar sessão ↗";
  toast(`Sessão encerrada · ${Math.round(result.duration_seconds / 60)} min · +${result.xp_earned} XP`);
  await loadLearningDashboard();
}

async function toggleStudySession() {
  try {
    if (state.activeSessionId) await finishActiveSession();
    else await beginTrackedSession("practice");
  } catch (error) { toast(error.message); }
}

async function startDiagnostic() {
  try {
    await beginTrackedSession("diagnostic");
    const result = await api("/api/learning/diagnostic", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic: $("concept-select").value, count: 8, language: currentLanguage() }),
    });
    if (!result.length) throw new Error("Não foi possível criar o diagnóstico. Atualize o mapa de conceitos.");
    $("diagnostic-report").classList.add("hidden");
    await prepareQuiz(result, "diagnostic");
    setView("exercises");
    toast("Responda sem consultar o material. O objetivo é descobrir o que merece atenção.");
  } catch (error) { toast(error.message); if (state.activeSessionId) await finishActiveSession("Diagnóstico não iniciado."); }
}

async function startChallenge() {
  try {
    await beginTrackedSession("challenge");
    const result = await api("/api/exercises/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count: 5, difficulty: "mista", language: currentLanguage(), purpose: "challenge", bloom_level: "mixed", topic: $("concept-select").value }),
    });
    await prepareQuiz(result, "challenge");
    setView("exercises");
    toast("Desafio iniciado! Pense antes de pedir uma dica.");
  } catch (error) { toast(error.message); if (state.activeSessionId) await finishActiveSession("Desafio não iniciado."); }
}

async function evaluateFeynman(event) {
  event.preventDefault();
  const topic = $("feynman-topic").value;
  const explanation = $("feynman-explanation").value.trim();
  if (!topic || explanation.length < 20) { toast("Selecione um conceito e escreva uma explicação com pelo menos 20 caracteres."); return; }
  const button = $("evaluate-feynman");
  button.disabled = true;
  button.textContent = "Analisando sua explicação...";
  try {
    const result = await api("/api/learning/feynman", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, explanation, language: currentLanguage() }),
    });
    $("feynman-result").classList.remove("hidden");
    $("feynman-result").innerHTML = `<div class="feynman-score">${Math.round(result.overall_score * 100)}<small>/100</small></div><div><strong>${escapeHtml(result.feedback)}</strong><p>${escapeHtml(result.priority_improvement)}</p>${result.missing_concepts?.length ? `<small>Faltou abordar: ${result.missing_concepts.map(escapeHtml).join(", ")}</small>` : ""}${result.misconceptions?.length ? `<small>Reveja: ${result.misconceptions.map(escapeHtml).join(", ")}</small>` : ""}<p class="feynman-retry">${escapeHtml(result.retry_prompt)}</p><span class="reward-inline">+${result.reward?.xp_awarded || 0} XP</span></div>`;
    if (result.duplicate_attempt) toast(t("Esta explicação já foi avaliada. Seu progresso não foi contado novamente."));
    i18n?.translatePage();
    await loadLearningDashboard();
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.innerHTML = 'Avaliar minha explicação <span>↗</span>'; i18n?.translatePage(); }
}

async function refreshObservability() {
  const [data, agentsData] = await Promise.all([
    api("/api/observability/summary"),
    api("/api/agents/status"),
  ]);
  $("agent-metrics").innerHTML = agentsData.agents.map(agent => {
    const m = agent.metrics;
    const latency = m.p95_ms == null ? "Sem amostras" : `p95: ${m.p95_ms} ms`;
    return `<div class="agent-metric"><strong>${escapeHtml(agent.name)}</strong>`
      + `<small>${m.calls} chamada(s) · ${latency} · ${m.errors} erro(s)</small></div>`;
  }).join("");
  $("observability-metrics").innerHTML = `
    <div><strong>${data.calls}</strong><small>Chamadas</small></div>
    <div><strong>${data.average_latency_ms} ms</strong><small>Latência média</small></div>
    <div><strong>${data.generated_tokens.toLocaleString(currentLocale())}</strong><small>Tokens gerados</small></div>`;
  return data;
}

async function loadWhatsAppDashboard() {
  const status = await api("/api/whatsapp/status");
  state.whatsappStatus = status;
  if (!status.enabled) {
    $("whatsapp-unavailable-notice").classList.remove("hidden");
    $("whatsapp-overall-status").textContent = "Indisponível sem Docker";
    $("whatsapp-bot-status").textContent = "Requer Docker Compose";
    $("whatsapp-instance-name").textContent = status.instance_name || "openstudy-assistant";
    $("whatsapp-bot-dot").classList.remove("online");
    $("whatsapp-bot-dot").classList.add("offline");
    $("whatsapp-config-warning").textContent = "O modo local com uv não inicia Evolution API, PostgreSQL/Redis da integração nem o webhook do WhatsApp.";
    $("whatsapp-link-feedback").textContent = "O painel, a IA local e os recursos de aprendizagem continuam disponíveis. Para habilitar o WhatsApp, execute o OpenStudy com Docker Compose.";
    $("whatsapp-message-history").innerHTML = '<span class="muted-note">Histórico do WhatsApp indisponível no modo sem Docker.</span>';
    $("whatsapp-qr-panel").classList.add("hidden");
    document.querySelectorAll("#view-whatsapp button, #view-whatsapp input, #view-whatsapp select, #view-whatsapp textarea").forEach((element) => {
      element.disabled = true;
    });
    i18n?.translatePage();
    return status;
  }

  $("whatsapp-unavailable-notice").classList.add("hidden");
  const profile = status.profile || {};
  const connected = status.bot_connection?.state === "open";
  $("whatsapp-instance-name").textContent = status.instance_name || "openstudy-assistant";
  $("whatsapp-bot-status").textContent = connected ? "Conectado" : status.bot_connection?.state || "Desconectado";
  $("whatsapp-overall-status").textContent = connected ? "Assistente conectado" : "Assistente desconectado";
  $("whatsapp-bot-dot").classList.toggle("online", connected);
  $("whatsapp-bot-dot").classList.toggle("offline", !connected);
  $("whatsapp-link-state").textContent = profile.verified ? "Número verificado" : "Não vinculado";
  $("whatsapp-link-state").classList.toggle("verified", Boolean(profile.verified));
  $("whatsapp-phone").value = profile.phone || "";
  $("whatsapp-phone").disabled = Boolean(profile.verified);
  $("whatsapp-send-code").textContent = profile.verified ? "Número vinculado" : "Enviar código";
  $("whatsapp-send-code").disabled = Boolean(profile.verified);
  $("whatsapp-linked-actions").classList.toggle("hidden", !profile.verified);
  $("whatsapp-linked-phone").textContent = profile.phone || "";
  $("whatsapp-reminders-enabled").checked = Boolean(profile.reminders_enabled);
  $("whatsapp-reminders-enabled").disabled = !profile.verified;
  $("whatsapp-reminder-times").value = (profile.times || ["19:00"]).join(", ");
  $("whatsapp-timezone").value = profile.timezone || "America/Sao_Paulo";
  $("whatsapp-daily-goal").value = profile.daily_goal_minutes || 30;
  $("whatsapp-quiet-start").value = profile.quiet_start || "22:00";
  $("whatsapp-quiet-end").value = profile.quiet_end || "08:00";
  $("whatsapp-reminder-template").value = profile.template || "📚 Hora de estudar! Sua meta hoje é {daily_goal_minutes} min e você tem {due_count} revisões pendentes. Vamos avançar um pouco hoje?";
  document.querySelectorAll(".whatsapp-days input").forEach((input) => {
    input.checked = (profile.days || [0,1,2,3,4,5,6]).includes(Number(input.value));
    input.disabled = !profile.verified;
  });
  ["whatsapp-reminder-times","whatsapp-daily-goal","whatsapp-timezone","whatsapp-quiet-start","whatsapp-quiet-end","whatsapp-reminder-template"].forEach((id) => {
    $(id).disabled = !profile.verified;
  });
  $("whatsapp-save-reminders").disabled = !profile.verified;
  $("whatsapp-test-reminder").disabled = !profile.verified;
  $("whatsapp-unlink").disabled = !profile.verified;

  const warnings = [];
  if (!status.configured) warnings.push("Configure EVOLUTION_API_URL, EVOLUTION_GLOBAL_API_KEY e EVOLUTION_INSTANCE_TOKEN no arquivo .env.");
  if (!status.webhook_ready) warnings.push("Configure EVOLUTION_WEBHOOK_SECRET e confira EVOLUTION_WEBHOOK_BASE_URL. No Docker Compose, o endereço interno padrão é http://app:8000.");
  if (status.bot_error) warnings.push(status.bot_error);
  $("whatsapp-config-warning").textContent = warnings.join(" ");
  $("whatsapp-link-feedback").textContent = profile.verified
    ? "Seu número está verificado. Envie mensagens para o número oficial do OpenStudy para começar."
    : "Depois de enviar o código, responda à mensagem do OpenStudy com VINCULAR e os seis dígitos.";

  if (profile.verified) {
    try {
      const messages = await api("/api/whatsapp/messages?limit=30");
      renderWhatsAppMessages(messages);
    } catch (_) {
      $("whatsapp-message-history").innerHTML = '<span class="muted-note">Não foi possível carregar as mensagens.</span>';
    }
  } else {
    $("whatsapp-message-history").innerHTML = '<span class="muted-note">Vincule seu número para visualizar as mensagens.</span>';
  }
  i18n?.translatePage();
  return status;
}

function showWhatsAppQr(base64) {
  if (!base64) {
    $("whatsapp-qr-panel").classList.add("hidden");
    toast("A Evolution API não retornou o QR Code. Verifique o status e tente novamente.");
    return;
  }
  const value = String(base64);
  $("whatsapp-qr-image").src = value.startsWith("data:")
    ? value
    : `data:image/png;base64,${value}`;
  $("whatsapp-qr-panel").classList.remove("hidden");
}

async function initializeWhatsApp() {
  const button = $("whatsapp-initialize");
  button.disabled = true;
  button.textContent = "Configurando instância...";
  try {
    const result = await api("/api/whatsapp/instance/initialize", { method: "POST" });
    if (result.qrcode) {
      showWhatsAppQr(result.qrcode);
      toast("QR Code gerado. Escaneie com o número oficial do OpenStudy.");
    } else if (result.connected) {
      $("whatsapp-qr-panel").classList.add("hidden");
      toast("O número oficial do OpenStudy já está conectado.");
    } else {
      toast("A Evolution API não retornou um QR Code. Verifique os logs da instância.");
    }
    await loadWhatsAppDashboard();
  } catch (error) {
    $("whatsapp-config-warning").textContent = error.message;
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Configurar instância e gerar QR ↗";
  }
}

async function reconnectWhatsApp() {
  const button = $("whatsapp-reconnect");
  button.disabled = true;
  try {
    const result = await api("/api/whatsapp/instance/connect", { method: "POST" });
    if (result.qrcode) showWhatsAppQr(result.qrcode);
    else if (result.connected) {
      $("whatsapp-qr-panel").classList.add("hidden");
      toast("O número oficial do OpenStudy já está conectado.");
    } else {
      toast("A Evolution API não retornou um QR Code. Verifique o status e tente novamente.");
    }
    await loadWhatsAppDashboard();
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}

async function requestWhatsAppLink(event) {
  event.preventDefault();
  const phone = $("whatsapp-phone").value.trim();
  const button = $("whatsapp-send-code");
  button.disabled = true;
  try {
    const result = await api("/api/whatsapp/link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone }),
    });
    $("whatsapp-link-feedback").textContent = result.verified
      ? result.message
      : "Código enviado! Abra a conversa do número oficial do OpenStudy e responda: VINCULAR seguido do código de seis dígitos.";
    toast(result.message);
    await loadWhatsAppDashboard();
  } catch (error) {
    $("whatsapp-link-feedback").textContent = error.message;
    toast(error.message);
  } finally {
    button.disabled = false;
    if (state.whatsappStatus?.profile?.verified) button.disabled = true;
  }
}

async function saveWhatsAppReminders(event) {
  event.preventDefault();
  const times = $("whatsapp-reminder-times").value.split(/[,\s]+/).map((item) => item.trim()).filter(Boolean);
  const days = [...document.querySelectorAll(".whatsapp-days input:checked")].map((input) => Number(input.value));
  const button = $("whatsapp-save-reminders");
  button.disabled = true;
  try {
    const result = await api("/api/whatsapp/reminders", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: $("whatsapp-reminders-enabled").checked,
        days,
        times,
        daily_goal_minutes: Number($("whatsapp-daily-goal").value || 30),
        timezone: $("whatsapp-timezone").value,
        quiet_start: $("whatsapp-quiet-start").value,
        quiet_end: $("whatsapp-quiet-end").value,
        template: $("whatsapp-reminder-template").value,
      }),
    });
    $("whatsapp-reminder-feedback").textContent = result.enabled
      ? "Rotina salva. Os lembretes serão enviados nos horários escolhidos."
      : "Preferências salvas. Os lembretes permanecem desativados.";
    toast("Rotina de lembretes salva.");
    await loadWhatsAppDashboard();
  } catch (error) {
    $("whatsapp-reminder-feedback").textContent = error.message;
    toast(error.message);
  } finally { button.disabled = false; }
}

async function testWhatsAppReminder() {
  const button = $("whatsapp-test-reminder");
  button.disabled = true;
  try {
    await api("/api/whatsapp/reminders/test", { method: "POST" });
    $("whatsapp-reminder-feedback").textContent = "Mensagem de teste enviada para seu WhatsApp.";
    toast("Lembrete de teste enviado.");
    await loadWhatsAppDashboard();
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}

async function unlinkWhatsApp() {
  if (!window.confirm("Deseja realmente desvincular este número e desativar os lembretes?")) return;
  try {
    await api("/api/whatsapp/unlink", { method: "POST" });
    $("whatsapp-qr-panel").classList.add("hidden");
    toast("Número desvinculado.");
    await loadWhatsAppDashboard();
  } catch (error) { toast(error.message); }
}

function renderWhatsAppMessages(messages) {
  const container = $("whatsapp-message-history");
  if (!messages.length) {
    container.innerHTML = '<span class="muted-note">Ainda não há mensagens registradas.</span>';
    return;
  }
  container.innerHTML = messages.map((item) => {
    const inbound = item.direction === "inbound";
    const date = item.created_at ? new Date(item.created_at).toLocaleString(currentLocale(), { dateStyle: "short", timeStyle: "short" }) : "";
    return `<article class="whatsapp-message-item"><span class="whatsapp-message-direction">${inbound ? "↙" : "↗"}</span><div class="whatsapp-message-content"><p>${escapeHtml(item.text || `[${item.type}]`)}</p><small>${inbound ? "Recebida" : "Enviada"} · ${escapeHtml(item.status)} · ${escapeHtml(date)}</small></div></article>`;
  }).join("");
}

function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  document.querySelectorAll("[data-go-library]").forEach((button) => button.addEventListener("click", () => setView("library")));
  $("quick-upload").addEventListener("click", () => setView("library"));
  $("browse-files").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (event) => uploadFile(event.target.files[0]));
  $("drop-zone").addEventListener("dragover", (event) => { event.preventDefault(); $("drop-zone").classList.add("dragover"); });
  $("drop-zone").addEventListener("dragleave", () => $("drop-zone").classList.remove("dragover"));
  $("drop-zone").addEventListener("drop", (event) => {
    event.preventDefault(); $("drop-zone").classList.remove("dragover"); uploadFile(event.dataTransfer.files[0]);
  });
  $("card-document-filter").addEventListener("change", loadCards);
  $("review-mode-toggle").addEventListener("click", () => { state.reviewMode = state.reviewMode === "due" ? "all" : "due"; loadCards(); });
  $("review-all-cards").addEventListener("click", () => { state.reviewMode = "all"; loadCards(); });
  $("reveal-card").addEventListener("click", revealCard);
  document.querySelectorAll("[data-rating]").forEach((button) => button.addEventListener("click", () => rateCurrentCard(button.dataset.rating)));
  $("prev-card").addEventListener("click", () => { state.cardIndex = Math.max(0, state.cardIndex - 1); state.revealed = false; renderCard(); });
  $("next-card").addEventListener("click", () => { state.cardIndex = Math.min(state.cards.length - 1, state.cardIndex + 1); state.revealed = false; renderCard(); });
  $("shuffle-cards").addEventListener("click", () => {
    for (let i = state.cards.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [state.cards[i], state.cards[j]] = [state.cards[j], state.cards[i]];
    }
    state.cardIndex = 0; state.revealed = false; renderCard(); toast("Flashcards embaralhados.");
  });
  $("generate-exercises").addEventListener("click", generateQuiz);
  $("refresh-learning").addEventListener("click", () => loadLearningDashboard().catch((error) => toast(error.message)));
  $("claim-daily-quests").addEventListener("click", claimDailyQuests);
  $("start-match-game").addEventListener("click", startMatchingGame);
  $("submit-match-game").addEventListener("click", submitMatchingGame);
  $("play-match-again").addEventListener("click", startMatchingGame);
  $("lesson-check-form").addEventListener("submit", submitLessonCheck);
  $("sync-concepts").addEventListener("click", syncLearningConcepts);
  $("generate-lesson").addEventListener("click", startAdaptiveLesson);
  $("start-diagnostic").addEventListener("click", startDiagnostic);
  $("start-challenge").addEventListener("click", startChallenge);
  $("start-study-session").addEventListener("click", toggleStudySession);
  $("feynman-form").addEventListener("submit", evaluateFeynman);
  $("new-quiz").addEventListener("click", () => { $("quiz-area").classList.add("hidden"); generateQuiz(); });
  $("check-connection").addEventListener("click", async () => {
    try {
      const current = await api("/api/llm/settings");
      const result = await api(`/api/llm/providers/${current.active_provider}/test`, { method: "POST" });
      toast(`Conexão validada: ${result.model}`);
      await loadHealth();
    } catch (error) { toast(error.message); }
  });
  $("tutor-form").addEventListener("submit", sendTutorMessage);
  $("clear-tutor").addEventListener("click", clearTutorHistory);
  $("plan-form").addEventListener("submit", createPlan);
  $("adapt-plan").addEventListener("click", adaptPlan);
  $("generate-insights").addEventListener("click", generateInsights);
  $("evaluate-cards").addEventListener("click", evaluateCards);
  $("refresh-observability").addEventListener("click", () => refreshObservability().then(() => toast("Métricas atualizadas.")).catch((error) => toast(error.message)));
  $("provider-config-form").addEventListener("submit", saveProviderConfig);
  $("activate-provider").addEventListener("click", activateProvider);
  $("test-provider").addEventListener("click", testProvider);
  $("load-provider-models").addEventListener("click", loadProviderModels);
  $("clear-provider-key").addEventListener("click", clearProviderKey);
  $("whatsapp-initialize").addEventListener("click", initializeWhatsApp);
  $("whatsapp-reconnect").addEventListener("click", reconnectWhatsApp);
  $("whatsapp-refresh").addEventListener("click", () => loadWhatsAppDashboard().catch((error) => toast(error.message)));
  $("whatsapp-link-form").addEventListener("submit", requestWhatsAppLink);
  $("whatsapp-reminder-form").addEventListener("submit", saveWhatsAppReminders);
  $("whatsapp-test-reminder").addEventListener("click", testWhatsAppReminder);
  $("whatsapp-unlink").addEventListener("click", unlinkWhatsApp);
  $("whatsapp-refresh-messages").addEventListener("click", () => loadWhatsAppDashboard().catch((error) => toast(error.message)));
  $("whatsapp-reminders-enabled").addEventListener("change", () => {
    $("whatsapp-reminder-feedback").textContent = $("whatsapp-reminders-enabled").checked
      ? "Clique em Salvar rotina para ativar os lembretes."
      : "Clique em Salvar rotina para pausar os lembretes.";
  });
}

async function init() {
  bindEvents();
  try {
    await Promise.all([loadStats(), loadDocuments(), loadHealth()]);
    await Promise.all([loadCards(), loadTutorHistory()]);
    await loadLearningDashboard();
    const workspace = await api("/api/exercises/workspace/active");
    if (workspace.id && ["active", "completed"].includes(workspace.status)) restoreSharedWorkspace(workspace);
    startSharedWorkspacePolling();
    setInterval(() => {
      if (state.activeView === "tutor" && document.activeElement !== $("tutor-input")) {
        loadTutorHistory().catch(() => {});
      }
    }, 7000);
  } catch (error) {
    toast(error.message);
  }
}

document.addEventListener("DOMContentLoaded", init);


document.getElementById("logout-button")?.addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); }
  finally { window.location.assign("/login"); }
});
