const $ = (selector) => document.querySelector(selector);
const state = {
  ready: false, busy: false, question: "", token: null,
  response: null, lastRequest: null, pages: new Map(),
};
const strategyNames = {
  GRAPH: "Recherche relationnelle", VECTOR: "Recherche thématique",
  HYBRID_GRAPH_FIRST: "Relations + thèmes",
};
const statusMessages = {
  entity_not_found: "Une personne, un film ou son contexte est absent de cette collection. Précisez le nom ou essayez un autre film.",
  no_match_in_dataset: "Aucun résultat dans cette collection ne satisfait les relations demandées.",
  insufficient_evidence: "Les documents disponibles ne permettent pas de produire des recommandations sourcées.",
  unsupported: "Cette question ne peut pas encore être traitée. Essayez une collaboration, un genre ou un thème.",
  dependency_error: "Le service de recherche est momentanément indisponible. Vous pouvez réessayer dans un instant.",
};
const statusTitles = {
  entity_not_found: "Entité introuvable",
  no_match_in_dataset: "Aucun résultat à afficher",
  insufficient_evidence: "Sources insuffisantes",
  unsupported: "Question non interprétée",
  dependency_error: "Service indisponible",
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function status(text = "", error = false, quiet = false) {
  $("#status-message").textContent = text;
  $("#status-message").dataset.error = String(error);
  $("#status-message").classList.toggle("is-quiet", quiet);
}

function controls() {
  $("#question").disabled = state.busy;
  $("#limit").disabled = state.busy;
  $("#search-submit").disabled = state.busy || !state.ready;
  $("#submit-label").textContent = state.busy ? "Recherche…" : "Explorer";
  $("#search-spinner").hidden = !state.busy;
  $(".button-arrow").hidden = state.busy;
  $("#results-section").setAttribute("aria-busy", String(state.busy));
  document.querySelectorAll(".example-button, #clarification-form input, #clarification-form button, #restart-search, #new-search").forEach((node) => { node.disabled = state.busy; });
  const coverage = state.response?.coverage;
  const canPage = state.response?.status === "ok" && state.response.operation === "list";
  $("#previous-page").disabled = state.busy || !canPage || !coverage || coverage.offset === 0;
  $("#next-page").disabled = state.busy || !canPage || !coverage?.has_more;
}

function resetSearch() {
  state.question = "";
  state.token = null;
  state.response = null;
  state.lastRequest = null;
  state.pages.clear();
  $("#question").setCustomValidity("");
  $("#results-section").hidden = true;
  $("#clarification").hidden = true;
  $("#clarification-form").hidden = false;
  $("#clarification-fields").replaceChildren();
  $("#restart-search").hidden = true;
  $("#welcome").hidden = false;
  document.body.classList.remove("has-results");
  $("#search-details").open = false;
  resizeQuestion();
  status();
  controls();
}

function resizeQuestion() {
  const question = $("#question");
  question.style.height = "auto";
  question.style.height = `${Math.min(question.scrollHeight, 220)}px`;
}

async function jsonRequest(url, options = {}, timeout = 300000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, {
      ...options, headers: { Accept: "application/json", ...options.headers },
      signal: controller.signal, cache: "no-store",
    });
    let data;
    try { data = await response.json(); }
    catch { throw new Error("Le serveur a renvoyé une réponse illisible. Réessayez dans un instant."); }
    if (!response.ok) {
      const error = new Error(response.status === 422
        ? "La demande n’est plus valide. Relancez la question ou précisez votre recherche."
        : "Le serveur est indisponible. Réessayez dans un instant.");
      error.httpStatus = response.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("La recherche a dépassé le délai d’attente. Vous pouvez la relancer ; le serveur peut encore terminer la demande précédente.");
    if (error instanceof TypeError) throw new Error("Connexion au serveur impossible. Vérifiez que l’application Docker est démarrée.");
    throw error;
  } finally { clearTimeout(timer); }
}

let readinessTimer;
async function checkReadiness() {
  clearTimeout(readinessTimer);
  try {
    const response = await fetch("/ready", { cache: "no-store", signal: AbortSignal.timeout(10000) });
    const data = await response.json();
    state.ready = response.ok && data.ready === true;
    const unavailable = Boolean(data.reason) || data.llm_configured === false;
    $("#readiness").dataset.state = state.ready ? "ready" : unavailable ? "error" : "loading";
    $("#readiness-label").textContent = state.ready ? "Prêt à explorer" : unavailable ? "Service indisponible" : "Préparation en cours";
    $("#retry-ready").hidden = state.ready;
  } catch {
    state.ready = false;
    $("#readiness").dataset.state = "error";
    $("#readiness-label").textContent = "Service indisponible";
    $("#retry-ready").hidden = false;
  }
  controls();
  if (!state.ready && !document.hidden) readinessTimer = setTimeout(checkReadiness, 5000);
}

function showClarification(data) {
  $("#results-section").hidden = true;
  $("#clarification").hidden = false;
  $("#clarification-title").textContent = "De qui ou de quel film parlez-vous ?";
  $("#clarification-form").hidden = false;
  $("#restart-search").hidden = true;
  const fields = $("#clarification-fields");
  fields.replaceChildren();
  let groups = 0;
  for (const link of data.resolved_entities) {
    if (link.status !== "ambiguous") continue;
    const group = element("fieldset");
    group.append(element("legend", "", link.mention.text));
    if (link.truncated) {
      group.append(element("p", "clarification-note", `${link.total_candidates} candidats correspondent à ce nom. Précisez un nom complet, un film ou une année dans votre question.`));
    } else if (link.blocked_by_context) {
      const anchor = data.resolved_entities.find((item) => item.mention.ref === link.blocked_by_context);
      group.append(element("p", "clarification-note", `Choisissez d’abord le film « ${anchor?.mention.text || "cité"} ». Son contexte permettra de préciser cette personne.`));
    } else {
      groups += 1;
      for (const candidate of link.alternatives) {
        const label = element("label", "entity-choice");
        const input = element("input");
        input.type = "radio";
        input.name = link.mention.ref;
        input.value = String(candidate.id);
        input.required = true;
        const description = element("span");
        description.append(element("strong", "", candidate.name + (candidate.year ? ` (${candidate.year})` : "")));
        const roles = (candidate.roles || []).map((role) => role === "actor" ? "acteur / actrice" : "réalisateur / réalisatrice");
        if (roles.length) description.append(element("small", "", roles.join(" · ")));
        if (candidate.films?.length) description.append(element("small", "", candidate.films.join(" · ")));
        label.append(input, description);
        group.append(label);
      }
    }
    fields.append(group);
  }
  $("#clarification-form button").hidden = groups === 0;
  status("Une précision est nécessaire pour poursuivre la recherche.");
  $("#clarification-title").focus();
}

function identity(node) {
  const name = node.title || node.name || "Entrée TMDB";
  return node.title && node.release_year ? `${name} (${node.release_year})` : name;
}

function evidenceNode(proof, number) {
  const item = element("section", "evidence-item");
  item.id = `proof-${proof.id}`;
  item.dataset.kind = proof.kind;
  const labels = { relation: "Relation vérifiée", document: "Document source", aggregate: "Comptage exact" };
  item.append(element("p", "evidence-kind", labels[proof.kind] || "Source"));
  const source = proof.data;
  if (proof.kind === "relation") {
    const verbs = { ACTED_IN: "a joué dans", DIRECTED: "a réalisé", IN_GENRE: "a pour genre" };
    const seen = new Set();
    for (const edge of source.edges || []) {
      const sentence = `${identity(edge.source)} ${verbs[edge.type] || "est lié à"} ${identity(edge.target)}.`;
      if (!seen.has(sentence)) item.append(element("p", "", sentence));
      seen.add(sentence);
    }
    item.append(element("p", "evidence-note", "Relation issue des crédits ou des genres du dataset. Les films cités constituent les témoins de collaboration."));
  } else if (proof.kind === "document") {
    item.append(element("p", "", source.title));
    const quote = element("blockquote", "", source.overview || "Synopsis absent ; document fondé sur les métadonnées.");
    quote.lang = "en";
    item.append(quote);
    const keywords = element("ul", "keyword-list");
    for (const keyword of source.keywords || []) keywords.append(element("li", "", keyword.name));
    item.append(keywords, element("p", "evidence-note", "Sources du dataset. La pertinence thématique et la centralité ne sont pas certifiées par le classement."));
  } else if (proof.kind === "aggregate") {
    item.append(element("p", "", `${source.value} entité(s) distincte(s), calculées dans Neo4j sur tout l’ensemble correspondant.`));
    item.append(element("p", "evidence-note", `Motif : ${source.template_id}. Snapshot : ${source.snapshot_id}.`));
    for (const [name, hash] of Object.entries(source.source_hashes || {})) {
      item.append(element("p", "evidence-note", `${name} : ${hash}`));
    }
  }
  item.append(element("small", "proof-ref", `[${number}] · Référence ${proof.id}`));
  return item;
}

function movieDetails(result) {
  const details = element("details", "movie-details");
  const summary = element("summary", "", "Détails du film");
  const body = element("div", "movie-body");
  details.append(summary, body);
  let loaded = false;
  let loading = false;
  const load = async () => {
    if (loaded || loading) return;
    loading = true;
    body.replaceChildren(element("p", "", "Chargement du film…"));
    body.setAttribute("aria-busy", "true");
    try {
      const movie = await jsonRequest(`/movies/${result.id}`, {}, 20000);
      const meta = element("div", "movie-meta");
      if (movie.release_date) meta.append(element("span", "", `Sortie : ${movie.release_date}`));
      if (movie.genre_names?.length) meta.append(element("span", "", `Genres : ${movie.genre_names.join(", ")}`));
      if (movie.original_language) meta.append(element("span", "", `Langue originale : ${movie.original_language}`));
      const overview = element("p", "", movie.overview || "Synopsis absent de cette collection.");
      overview.lang = "en";
      body.replaceChildren(meta, overview);
      if (movie.original_title && movie.original_title !== movie.title) body.append(element("p", "evidence-note", `Titre original : ${movie.original_title}`));
      loaded = true;
    } catch (error) {
      const retry = element("button", "text-button", "Réessayer");
      retry.type = "button";
      retry.addEventListener("click", load);
      body.replaceChildren(element("p", "", error.message), retry);
    } finally { loading = false; body.setAttribute("aria-busy", "false"); }
  };
  details.addEventListener("toggle", () => { if (details.open) load(); });
  return details;
}

function renderAnswer(text, proofNumbers) {
  const container = $("#answer");
  container.replaceChildren();
  const pattern = /\[([^\]\n]+)\]/g;
  let end = 0;
  for (const match of text.matchAll(pattern)) {
    container.append(document.createTextNode(text.slice(end, match.index)));
    if (proofNumbers.has(match[1])) {
      const link = element("a", "citation", `[${proofNumbers.get(match[1])}]`);
      link.href = `#proof-${match[1]}`;
      link.setAttribute("aria-label", `Voir la preuve ${proofNumbers.get(match[1])}`);
      link.addEventListener("click", (event) => {
        const target = document.getElementById(`proof-${match[1]}`);
        if (!target) return;
        event.preventDefault();
        target.closest("details").open = true;
        target.tabIndex = -1;
        target.focus();
        target.scrollIntoView({ block: "center", behavior: "auto" });
      });
      container.append(link);
    } else container.append(document.createTextNode(match[0]));
    end = match.index + match[0].length;
  }
  container.append(document.createTextNode(text.slice(end)));
}

function renderResults(data) {
  $("#clarification").hidden = true;
  $("#results-section").hidden = false;
  $("#strategy-label").textContent = strategyNames[data.strategy] || "Recherche";
  $("#strategy-label").hidden = !data.strategy;
  const successful = data.status === "ok";
  const isCount = successful && data.operation === "count" && Number.isInteger(data.count);
  const answer = data.status === "dependency_error" ? statusMessages.dependency_error : data.answer || "";
  $("#results-title").textContent = successful
    ? isCount ? "Comptage exact" : data.results.length ? "Vos résultats" : "Aucun résultat à afficher"
    : statusTitles[data.status] || "Recherche interrompue";
  const warnings = $("#warnings");
  warnings.replaceChildren();
  for (const warning of data.warnings) warnings.append(element("p", "", warning));
  warnings.hidden = data.warnings.length === 0;
  const proofs = new Map(data.evidence.map((proof) => [proof.id, proof]));
  const proofNumbers = new Map(data.evidence.map((proof, index) => [proof.id, index + 1]));
  // A factual answer already appears in the cards or the count. Show prose only
  // when it adds a synthesis, or explains why the question could not be answered.
  $("#answer-panel").hidden = !answer || (successful && (data.operation !== "recommend"
    || data.trace?.generation_mode !== "llm_synthesis"));
  renderAnswer(answer, proofNumbers);
  const coverage = data.coverage;
  if (!successful) {
    $("#coverage").textContent = "";
  } else if (isCount) {
    $("#coverage").textContent = "Dans cette collection";
  } else if (data.operation === "list") {
    $("#coverage").textContent = data.results.length
      ? `${coverage.offset + 1}–${coverage.offset + data.results.length} sur ${coverage.total_candidates}`
      : "";
  } else if (data.strategy) {
    $("#coverage").textContent = `${data.results.length} suggestion${data.results.length > 1 ? "s" : ""} · par similarité`;
  } else $("#coverage").textContent = "";
  $("#technical-coverage").textContent = !successful ? "" : isCount
    ? "Compte distinct sur l’ensemble des résultats, sans limite d’affichage."
    : data.operation === "recommend"
      ? `${coverage.scored} films comparés, ${coverage.without_embedding} sans embedding. La similarité estime la pertinence du thème.`
      : `${coverage.total_candidates} résultats correspondent aux relations demandées.`;
  const results = $("#results");
  results.replaceChildren();
  if (isCount) {
    const card = element("article", "result-card count-card");
    card.append(element("h3", "count-value", new Intl.NumberFormat("fr-FR").format(data.count)));
    card.append(element("p", "result-type", "résultats dans cette collection"));
    const details = element("details", "evidence-details");
    details.append(element("summary", "", "Provenance du comptage"));
    for (const proof of data.evidence) details.append(evidenceNode(proof, proofNumbers.get(proof.id)));
    card.append(details);
    results.append(card);
  }
  data.results.forEach((result) => {
    const card = element("article", "result-card");
    card.dataset.entityId = String(result.id);
    card.dataset.entityType = result.type;
    const header = element("div", "result-header");
    const name = element("div");
    name.append(element("h3", "", result.name), element("p", "result-type", result.type === "Movie" ? "Film" : "Personne"));
    const symbol = element("span", "result-symbol");
    symbol.setAttribute("aria-hidden", "true");
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    const shape = document.createElementNS("http://www.w3.org/2000/svg", "path");
    shape.setAttribute("d", result.type === "Movie"
      ? "M4 4h16v16H4zM8 4v16M16 4v16M4 9h4M4 15h4M16 9h4M16 15h4"
      : "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM5 21v-2a7 7 0 0 1 14 0v2");
    icon.append(shape);
    symbol.append(icon);
    header.append(symbol, name);
    card.append(header);
    const actions = element("div", "result-actions");
    const details = element("details", "evidence-details");
    details.append(element("summary", "", "Pourquoi ce résultat ?"));
    if (result.score !== undefined) {
      details.append(element("p", "evidence-note", `Similarité : ${Number(result.score).toFixed(4)} · classement, pas probabilité de pertinence.`));
    }
    for (const ref of result.evidence_ids) {
      if (proofs.has(ref)) details.append(evidenceNode(proofs.get(ref), proofNumbers.get(ref)));
    }
    actions.append(details);
    if (result.type === "Movie") actions.append(movieDetails(result));
    card.append(actions);
    results.append(card);
  });
  $("#pagination").hidden = !successful || data.operation !== "list" || (!coverage.has_more && coverage.offset === 0);
  const limit = coverage.effective_limit || Number($("#limit").value);
  $("#page-label").textContent = `Page ${Math.floor(coverage.offset / limit) + 1}`;
  $("#trace-label").textContent = data.trace_id ? `Référence de recherche : ${data.trace_id}` : "";
  const message = isCount ? `Comptage terminé : ${data.count}.` : `${data.results.length} résultat${data.results.length > 1 ? "s" : ""} affiché${data.results.length > 1 ? "s" : ""}.`;
  status(data.status === "ok" ? message : answer || statusMessages[data.status], data.status === "dependency_error", true);
  $("#results-title").focus();
}

function render(data) {
  state.response = data;
  $("#welcome").hidden = true;
  document.body.classList.add("has-results");
  if (data.status === "needs_clarification") {
    state.token = data.clarification_token;
    showClarification(data);
  } else renderResults(data);
}

async function search(payload) {
  if (state.busy) return;
  state.busy = true;
  $("#welcome").hidden = true;
  document.body.classList.add("has-results");
  controls();
  status("Recherche en cours…");
  try {
    const data = await jsonRequest("/questions", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    state.lastRequest = payload;
    if (data.status === "ok" && data.operation === "list") {
      // Reuse the signed plan on subsequent pages: no new LLM interpretation.
      state.lastRequest = { ...payload, clarification_token: data.clarification_token, selections: {} };
      state.pages.set(payload.offset, { data, payload: state.lastRequest });
    }
    render(data);
  } catch (error) {
    status(error.message, true);
    if (error.httpStatus === 422 && payload.clarification_token) {
      state.token = null;
      state.response = null;
      state.lastRequest = null;
      state.pages.clear();
      $("#results-section").hidden = true;
      $("#pagination").hidden = true;
      $("#clarification").hidden = false;
      $("#clarification-title").textContent = "Cette recherche doit être relancée";
      $("#clarification-form").hidden = true;
      $("#clarification-fields").replaceChildren();
      $("#restart-search").hidden = false;
    }
  } finally {
    state.busy = false;
    controls();
  }
}

$("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (state.busy || !state.ready) return;
  const question = $("#question").value.trim();
  if (question.length < 3) {
    $("#question").setCustomValidity("Écrivez une question d’au moins trois caractères.");
    $("#question").reportValidity();
    return;
  }
  resetSearch();
  state.question = question;
  $("#question").value = question;
  $("#clarification-form").hidden = false;
  search({ question, language: "fr", limit: Number($("#limit").value), offset: 0 });
});

$("#question").addEventListener("input", resetSearch);
$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!state.busy && state.ready) $("#search-form").requestSubmit();
  }
});
$("#limit").addEventListener("change", resetSearch);
$("#new-search").addEventListener("click", () => {
  if (state.busy) return;
  $("#question").value = "";
  resetSearch();
  $("#question").focus();
});
document.querySelectorAll(".example-button").forEach((button) => {
  button.addEventListener("click", () => {
    $("#question").value = button.dataset.question;
    resetSearch();
    $("#question").focus();
  });
});

$("#clarification-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!state.token || state.busy) return;
  const selections = Object.fromEntries([...new FormData(event.currentTarget)].map(([ref, id]) => [ref, Number(id)]));
  search({ question: state.question, language: "fr", limit: Number($("#limit").value), offset: 0,
    clarification_token: state.token, selections });
});

function changePage(direction) {
  if (state.busy || state.response?.status !== "ok" || state.response.operation !== "list") return;
  const previous = state.lastRequest;
  const offset = Math.max(0, state.response.coverage.offset + direction * state.response.coverage.effective_limit);
  const cached = state.pages.get(offset);
  if (cached) {
    state.lastRequest = cached.payload;
    render(cached.data);
    controls();
  } else search({ ...previous, offset });
}
$("#previous-page").addEventListener("click", () => changePage(-1));
$("#next-page").addEventListener("click", () => changePage(1));
$("#restart-search").addEventListener("click", () => $("#search-form").requestSubmit());
$("#retry-ready").addEventListener("click", checkReadiness);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !state.ready) checkReadiness();
  else if (document.hidden) clearTimeout(readinessTimer);
});
checkReadiness();
