"use strict";

(() => {
  const roomInfo = {
    main: {label: "Main", role: "MAIN / 감독관"},
    literature: {label: "문헌", role: "LITERATURE / 문헌"},
    work: {label: "계산·분석", role: "WORK / 계산·분석"},
    output: {label: "결과", role: "OUTPUT / 결과"},
    discussion: {label: "해석", role: "DISCUSSION / 해석"}
  };
  const statuses = {unassigned: "미배정", idle: "대기", working: "작업 중", waiting: "확인 대기", failed: "오류", unknown: "상태 확인 필요"};
  const $ = id => document.getElementById(id);
  const seats = [...document.querySelectorAll("[data-room]")];
  let token = "", storageAvailable = true, selected = "main", state = null;
  let reachable = false, sending = false, lastSync = null, fileSequence = 0, pollSequence = 0;
  let detailSignature = "";
  let modelSaving = false, modelSelection = null, effortSelection = null, fastSelection = null;
  let modelOptionsSignature = "", effortOptionsSignature = "";
  let modelFeedback = "", modelFeedbackError = false;
  const requestCards = new Map();
  const storageKey = "house.web.token";
  try { token = sessionStorage.getItem(storageKey) || ""; } catch { storageAvailable = false; }
  const fragmentToken = new URLSearchParams(location.hash.slice(1)).get("token");
  if (fragmentToken) {
    token = fragmentToken;
    try { sessionStorage.setItem(storageKey, token); } catch { storageAvailable = false; }
  }
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function valueText(value) {
    if (typeof value === "string") return value;
    return value === undefined || value === null ? "" : JSON.stringify(value, null, 2);
  }
  function timeText(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("ko-KR", {hour12: false});
  }
  function connected() { return reachable && state?.connected === true; }
  function roomState(id) {
    return state?.rooms?.find(room => room.id === id) || {id, status: "unknown", task: "", messages: [], events: [], files: []};
  }
  function roomStatus(room) {
    return connected() && Object.hasOwn(statuses, room.status) ? room.status : "unknown";
  }
  function setStatus(element, status) {
    element.dataset.status = status;
    element.textContent = statuses[status];
  }
  function feedback(text, error = false) {
    $("feedback").textContent = text;
    $("feedback").classList.toggle("is-error", error);
  }
  function updateSend() {
    $("send").disabled = sending || modelSaving || !connected() || state?.ready !== true || !token || !$("message").value.trim();
    $("send").textContent = sending ? "전달 중…" : "Main에게 전달";
    renderModels();
  }
  async function api(path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers: {Authorization: "Bearer " + token, ...(body === undefined ? {} : {"Content-Type": "application/json"})},
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer", signal: controller.signal
      });
      let result;
      try { result = await response.json(); } catch { throw new Error("서버 응답 형식을 확인할 수 없습니다."); }
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) throw new Error("인증을 확인할 수 없습니다. CLI에 표시된 연구실 주소를 다시 열어 주세요.");
        throw new Error(typeof result?.error === "string" ? result.error : result?.error?.message || "요청을 처리하지 못했습니다 (" + response.status + ").");
      }
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("서버 응답 시간이 초과됐습니다. 전송 결과를 확인한 뒤 다시 시도해 주세요.");
      throw error;
    } finally { clearTimeout(timer); }
  }

  function renderSession() {
    const session = state?.session;
    const percent = session?.context_percent;
    $("health-context").textContent = typeof percent === "number" && Number.isFinite(percent) && percent >= 0 && percent <= 100 ? percent.toFixed(1) + "% (최근 관측)" : "알 수 없음";
    const count = session?.auto_compactions;
    $("health-compactions").textContent = Number.isInteger(count) && count >= 0 ? count + "회" + (session.count_scope === "observed_since_attach" ? " 이상" : "") : "알 수 없음";
    const notes = [];
    if (session?.count_scope === "observed_since_attach") notes.push("연동 이후 관측한 횟수입니다.");
    if (session?.review_required === true) notes.push("자동 압축 후 인계 검토가 필요합니다.");
    if (session?.recommended_at) notes.push("인계가 저장되어 새 채팅을 권고한 상태입니다.");
    for (const alert of session?.alerts || []) {
      if (!alert.resolved_at && typeof alert.message === "string") notes.push(alert.message);
    }
    $("health-note").textContent = notes.join(" ") || (session ? "최근 저장된 관측이며 현재 문맥과 차이가 있을 수 있습니다." : "측정값이 제공되지 않아 현재 사용량을 알 수 없습니다.");
  }
  function modelChoices() {
    return Array.isArray(state?.models) ? state.models.filter(item => item && typeof item.model === "string" && item.model.trim()) : [];
  }
  function modelRow(model) {
    return modelChoices().find(item => item.model === (model || state?.current_model));
  }
  function effortChoices(model) {
    const row = modelRow(model);
    return Array.isArray(row?.supported_efforts) ? row.supported_efforts.filter(item => item && typeof item.effort === "string" && item.effort.trim()) : [];
  }
  function serviceTiers(model) {
    const row = modelRow(model);
    return Array.isArray(row?.service_tiers) ? row.service_tiers.filter(item => item && typeof item.id === "string") : [];
  }
  function fastTier(model) {
    return serviceTiers(model).find(tier => (typeof tier.name === "string" && tier.name.toLowerCase() === "fast") || ["fast", "priority"].includes(tier.id.toLowerCase()));
  }
  function selectedFast() {
    return typeof state?.selected_fast === "boolean" ? state.selected_fast : null;
  }
  function effortLabel(effort) {
    const labels = {none: "없음", minimal: "최소", low: "낮음", medium: "보통", high: "높음", xhigh: "매우 높음", max: "최대", ultra: "최상"};
    return (Object.hasOwn(labels, effort) ? labels[effort] : "추론 강도") + " (" + effort + ")";
  }
  function renderModels() {
    const models = modelChoices(), select = $("model-select");
    const current = state?.current_model;
    const currentLabel = models.find(item => item.model === current)?.display_name || current;
    $("current-model").textContent = "현재 Main 설정 · " + (typeof current === "string" && current ? currentLabel : "관측 없음");
    $("current-effort").textContent = "현재 Main 추론 · " + (typeof state?.current_effort === "string" && state.current_effort ? effortLabel(state.current_effort) : "관측 없음");
    const currentTier = state?.current_service_tier;
    const currentSpeed = serviceTiers(current).find(tier => tier.id === currentTier);
    $("current-speed").textContent = "현재 Main 속도 · " + (currentTier === "default" ? "표준" : typeof currentTier === "string" && currentTier ? (currentSpeed?.name || currentTier) : "관측 없음");
    const signature = JSON.stringify(models);
    if (signature !== modelOptionsSignature) {
      modelOptionsSignature = signature;
      const keep = node("option", "", "현재 설정 유지"); keep.value = "";
      select.replaceChildren(keep, ...models.map(model => {
        const option = node("option", "", model.display_name || model.model);
        option.value = model.model;
        return option;
      }));
    }
    const chosenModel = (modelSaving ? modelSelection : state?.selected_model) || null;
    select.value = chosenModel || "";
    const disabled = sending || modelSaving || !connected() || state?.ready !== true || !models.length || Boolean(state?.model_error);
    select.disabled = disabled;
    select.setAttribute("aria-busy", String(modelSaving));
    const efforts = effortChoices(chosenModel), effortSelect = $("effort-select");
    const effortSignature = JSON.stringify([chosenModel || current, efforts]);
    if (effortSignature !== effortOptionsSignature) {
      effortOptionsSignature = effortSignature;
      const auto = node("option", "", "자동 (현재 설정·모델 기본)"); auto.value = "";
      effortSelect.replaceChildren(auto, ...efforts.map(item => {
        const option = node("option", "", effortLabel(item.effort));
        option.value = item.effort;
        if (typeof item.description === "string") option.title = item.description;
        return option;
      }));
    }
    effortSelect.value = (modelSaving ? effortSelection : state?.selected_effort) || "";
    effortSelect.disabled = disabled || !efforts.length;
    effortSelect.setAttribute("aria-busy", String(modelSaving));
    const row = models.find(item => item.model === (chosenModel || current));
    $("effort-note").textContent = !efforts.length ? "지원 목록을 확인할 수 없어 추론 강도를 선택할 수 없습니다." : (typeof row?.default_effort === "string" ? "모델 기본값: " + effortLabel(row.default_effort) + ". " : "") + "자동은 같은 모델의 관측 설정 또는 모델 기본값을 사용합니다.";
    const fastSelect = $("fast-select"), advertisedFast = fastTier(chosenModel);
    const chosenFast = modelSaving ? fastSelection : selectedFast();
    fastSelect.value = chosenFast === true ? "on" : chosenFast === false ? "off" : "";
    fastSelect.disabled = disabled;
    fastSelect.setAttribute("aria-busy", String(modelSaving));
    const fastOption = fastSelect.querySelector('option[value="on"]');
    fastOption.disabled = !advertisedFast;
    fastOption.title = typeof advertisedFast?.description === "string" ? advertisedFast.description : "";
    const defaultTier = serviceTiers(chosenModel).find(tier => tier.id === row?.default_service_tier);
    fastSelect.querySelector('option[value=""]').title = defaultTier ? "모델 기본값: " + (defaultTier.name || defaultTier.id) : "";
    $("fast-note").textContent = (advertisedFast ? "" : "Fast 지원 정보가 없어 선택할 수 없습니다. ") + "Fast는 추론 강도와 별개이며 사용량이 늘 수 있습니다.";
    const error = Boolean(state?.model_error) || (!modelSaving && modelFeedbackError);
    const message = modelSaving ? "다음 요청의 모델·추론 강도·속도를 저장하고 있습니다…" : state?.model_error || (!models.length ? "모델 목록을 아직 확인할 수 없습니다." : modelFeedback);
    if ($("model-feedback").textContent !== message) $("model-feedback").textContent = message;
    $("model-feedback").classList.toggle("is-error", error);
  }
  function renderDetail() {
    const room = roomState(selected), status = roomStatus(room);
    $("detail-role").textContent = roomInfo[selected].role;
    $("detail-title").textContent = roomInfo[selected].label;
    setStatus($("detail-status"), status);
    $("conversation-title").textContent = roomInfo[selected].label + " · 작업 결과 대화";
    setStatus($("conversation-status"), status);
    $("agent-id").textContent = room.agent_id ? "담당 ID · " + room.agent_id : "아직 확인된 담당 ID가 없습니다.";
    $("detail-work").textContent = room.task || (status === "unknown" ? "현재 작업을 확인할 수 없습니다." : "아직 배정된 작업이 없습니다.");
    $("composer-title").textContent = selected === "main" ? "Main에게 작업 요청" : roomInfo[selected].label + " 담당에게 요청";
    $("route").textContent = selected === "main" ? "Main이 기존 방 담당에게 전달합니다." : "Main을 통해 기존 " + roomInfo[selected].label + " 담당에게 전달합니다.";
    const signature = JSON.stringify([selected, room.messages, room.files, room.events]);
    if (detailSignature === signature) return;
    detailSignature = signature;
    const messages = Array.isArray(room.messages) ? room.messages.slice(-60) : [];
    const atEnd = $("messages").scrollHeight - $("messages").scrollTop - $("messages").clientHeight < 35;
    $("messages").replaceChildren(...messages.map(message => {
      const element = node("article", "message");
      element.dataset.role = message.role || "unknown";
      element.append(node("span", "message-label", {user: "연구자", assistant: "담당자", agent: "담당자", system: "시스템"}[message.role] || message.role || "메시지"), node("p", "", message.text || ""));
      return element;
    }));
    if (!messages.length) $("messages").append(node("p", "small", "아직 받은 메시지가 없습니다."));
    if (atEnd) $("messages").scrollTop = $("messages").scrollHeight;
    const files = Array.isArray(room.files) ? room.files : [];
    $("files").replaceChildren(...files.map(file => {
      const button = node("button", "file", "▧ " + (file.label || file.path));
      button.type = "button";
      button.title = file.path;
      button.addEventListener("click", () => openFile(file.path));
      return button;
    }));
    if (!files.length) $("files").append(node("p", "small", "아직 확인된 파일이 없습니다."));
    const events = Array.isArray(room.events) ? room.events.slice(-30).reverse() : [];
    $("events").replaceChildren(...events.map(event => {
      const element = node("li");
      element.append(node("b", "", event.text || ""));
      if (event.at) element.append(node("time", "", timeText(event.at)));
      return element;
    }));
    if (!events.length) $("events").append(node("li", "small", "아직 관측된 활동이 없습니다."));
  }
  function render() {
    const online = connected();
    $("connection").dataset.state = online ? "connected" : "disconnected";
    const connectionText = online ? "Codex 연결됨" : "연결 확인 필요";
    if ($("connection").textContent !== connectionText) $("connection").textContent = connectionText;
    $("project-path").textContent = valueText(state?.project) || "프로젝트 확인 필요";
    $("project-path").title = $("project-path").textContent;
    $("last-sync").textContent = lastSync ? "마지막 상태 수신 · " + timeText(lastSync) : "아직 상태를 받지 못했습니다.";
    const queue = state?.queue;
    $("queue").textContent = "전달 대기 · " + (Array.isArray(queue) ? queue.filter(item => item?.status === "queued").length + "건" : "알 수 없음");
    const tasks = [];
    seats.forEach(seat => {
      const room = roomState(seat.dataset.room), status = roomStatus(room);
      seat.dataset.status = status;
      seat.setAttribute("aria-pressed", String(selected === room.id));
      seat.querySelector(".desk").classList.toggle("is-working", status === "working");
      setStatus(seat.querySelector(".pill"), status);
      seat.querySelector(".caption").textContent = room.task || (status === "unknown" ? "현재 상태를 확인할 수 없습니다." : "아직 배정된 작업이 없습니다.");
      seat.title = room.task || statuses[status];
      if (room.task) {
        const item = node("li");
        item.append(node("b", "", roomInfo[room.id].label + " · " + statuses[status]), node("span", "", room.task));
        tasks.push(item);
      }
    });
    $("task-board").replaceChildren(...(tasks.length ? tasks : [node("li", "small", "아직 관측된 작업이 없습니다.")]));
    renderSession(); renderModels(); renderDetail(); renderRequests(); updateSend();
  }
  function notice(text) {
    if ($("notice").textContent !== text) $("notice").textContent = text;
    $("notice").hidden = !text;
  }
  async function poll() {
    const sequence = ++pollSequence;
    if (!token) {
      reachable = false;
      notice("인증 주소가 필요합니다. house web을 실행한 CLI에 표시된 연구실 주소를 열어 주세요.");
      render();
      return;
    }
    try {
      const next = await api("/api/state");
      if (sequence !== pollSequence) return;
      if (!next || typeof next !== "object" || !Array.isArray(next.rooms)) throw new Error("연구실 상태 응답 형식을 확인할 수 없습니다.");
      state = next; reachable = true; lastSync = new Date().toISOString();
      notice(next.error ? valueText(next.error) : !next.connected ? "Codex 연결을 확인하고 있습니다. 표시된 기록은 마지막 관측일 수 있습니다." : next.ready !== true ? "작업 시작을 준비하고 있습니다. 확인과 승인에 대기 중인 요청이 있는지 확인해 주세요." : !storageAvailable ? "이 브라우저에서 탭 인증을 보관할 수 없습니다. 새로고침할 때 CLI의 주소를 다시 열어 주세요." : "");
    } catch (error) {
      if (sequence !== pollSequence) return;
      reachable = false;
      notice("상태를 갱신하지 못했습니다. " + error.message + (state ? " 표시된 기록은 마지막으로 받은 내용입니다." : " CLI 서버 실행 상태를 확인해 주세요."));
    }
    render();
  }
  function selectRoom(id) {
    if (!Object.hasOwn(roomInfo, id)) return;
    selected = id;
    render();
  }
  seats.forEach((seat, index) => {
    seat.addEventListener("click", () => selectRoom(seat.dataset.room));
    seat.addEventListener("keydown", event => {
      let next;
      if (["ArrowRight", "ArrowDown"].includes(event.key)) next = (index + 1) % seats.length;
      if (["ArrowLeft", "ArrowUp"].includes(event.key)) next = (index + seats.length - 1) % seats.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = seats.length - 1;
      if (next !== undefined) { event.preventDefault(); seats[next].focus(); selectRoom(seats[next].dataset.room); }
    });
  });
  $("message").addEventListener("input", updateSend);
  async function saveModelSettings(nextModel, nextEffort, nextFast, resetNotice = "") {
    const previousModel = state?.selected_model || null, previousEffort = state?.selected_effort || null;
    const previousFast = selectedFast();
    if (sending || modelSaving || !connected() || state?.ready !== true || !modelChoices().length || state?.model_error) { renderModels(); return; }
    if (nextModel !== null && !modelChoices().some(model => model.model === nextModel)) { renderModels(); return; }
    if (nextEffort !== null && !effortChoices(nextModel).some(item => item.effort === nextEffort)) { renderModels(); return; }
    if (nextFast === true && !fastTier(nextModel)) { renderModels(); return; }
    if (nextModel === previousModel && nextEffort === previousEffort && nextFast === previousFast) return;
    modelSaving = true; modelSelection = nextModel; effortSelection = nextEffort; fastSelection = nextFast; modelFeedback = ""; modelFeedbackError = false;
    renderModels(); updateSend();
    try {
      const result = await api("/api/model", {model: nextModel, effort: nextEffort, fast: nextFast});
      if (!result || !(result.selected_model === null || typeof result.selected_model === "string") || !(result.selected_effort === null || typeof result.selected_effort === "string") || !(result.selected_fast === null || typeof result.selected_fast === "boolean")) throw new Error("모델·추론 강도·속도 선택 응답을 확인할 수 없습니다.");
      pollSequence += 1;
      modelSelection = result.selected_model; effortSelection = result.selected_effort; fastSelection = result.selected_fast;
      state.selected_model = result.selected_model;
      state.selected_effort = result.selected_effort;
      state.selected_fast = result.selected_fast;
      modelFeedback = resetNotice + "다음 새 Main 요청부터 모델·추론 강도·속도 선택을 적용합니다.";
      await poll();
    } catch (error) {
      pollSequence += 1;
      modelSelection = previousModel; effortSelection = previousEffort; fastSelection = previousFast;
      if (state) { state.selected_model = previousModel; state.selected_effort = previousEffort; state.selected_fast = previousFast; }
      modelFeedback = "모델·추론 강도·속도를 저장하지 못했습니다. " + error.message;
      modelFeedbackError = true;
    } finally { modelSaving = false; renderModels(); updateSend(); }
  }
  $("model-select").addEventListener("change", () => {
    const nextModel = $("model-select").value || null, previousEffort = state?.selected_effort || null;
    const keepEffort = previousEffort !== null && effortChoices(nextModel).some(item => item.effort === previousEffort);
    const previousFast = selectedFast(), resetFast = previousFast === true && !fastTier(nextModel);
    const resets = [];
    if (previousEffort !== null && !keepEffort) resets.push("추론 강도");
    if (resetFast) resets.push("속도");
    saveModelSettings(nextModel, keepEffort ? previousEffort : null, resetFast ? null : previousFast, resets.length ? "모델 변경에 맞춰 " + resets.join("·") + "를 자동으로 전환했습니다. " : "");
  });
  $("effort-select").addEventListener("change", () => {
    const model = state?.selected_model || null;
    if (!effortChoices(model).length) { renderModels(); return; }
    saveModelSettings(model, $("effort-select").value || null, selectedFast());
  });
  $("fast-select").addEventListener("change", () => {
    const model = state?.selected_model || null, value = $("fast-select").value;
    if (!["", "on", "off"].includes(value)) { renderModels(); return; }
    saveModelSettings(model, state?.selected_effort || null, value === "on" ? true : value === "off" ? false : null);
  });
  $("message").addEventListener("keydown", event => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !$("send").disabled) { event.preventDefault(); $("instruction").requestSubmit(); }
  });
  $("instruction").addEventListener("submit", async event => {
    event.preventDefault();
    const draft = $("message").value, text = draft.trim(), room = selected;
    if (!text || sending || modelSaving || !connected() || state?.ready !== true) return;
    sending = true; updateSend(); feedback("Main에게 전달하고 있습니다…");
    try {
      await api("/api/message", {room, text});
      if ($("message").value === draft) $("message").value = "";
      feedback("Main의 전달 대기열에 등록했습니다. 작업 결과는 실제 활동에서 확인하세요.");
      await poll();
    } catch (error) { feedback("전달하지 못했습니다. " + error.message, true); }
    finally { sending = false; updateSend(); }
  });

  async function openFile(path) {
    const sequence = ++fileSequence;
    $("file-title").textContent = path;
    $("file-status").textContent = "파일을 불러오고 있습니다…";
    $("file-content").textContent = "";
    if (!$("file-dialog").open) $("file-dialog").showModal();
    try {
      const result = await api("/api/file?path=" + encodeURIComponent(path));
      if (sequence !== fileSequence) return;
      if (typeof result.text !== "string") throw new Error("텍스트 파일 응답을 확인할 수 없습니다.");
      $("file-content").textContent = result.text;
      $("file-status").textContent = "서버가 허용한 범위의 텍스트 미리보기입니다.";
    } catch (error) { if (sequence === fileSequence) $("file-status").textContent = error.message; }
  }
  $("file-close").addEventListener("click", () => $("file-dialog").close());
  $("file-dialog").addEventListener("close", () => { fileSequence += 1; });

  function detail(card, label, value) {
    if (value === undefined || value === null) return;
    const list = node("dl", "request-detail"), term = node("dt", "", label), description = node("dd");
    description.append(node("pre", "", valueText(value)));
    list.append(term, description); card.append(list);
  }
  async function respond(request, body, card) {
    if (card.dataset.responding === "true" || card.dataset.answered === "true") return;
    card.dataset.responding = "true";
    const errorLine = card.querySelector(".feedback");
    const controls = [...card.querySelectorAll("button,input,textarea")];
    controls.forEach(control => { control.disabled = true; });
    errorLine.textContent = "응답 전달 중…";
    try {
      await api("/api/respond", {id: request.id, ...body});
      card.dataset.answered = "true";
      errorLine.textContent = "응답을 전달했습니다. native 상태 갱신을 기다립니다.";
      await poll();
    } catch (error) {
      card.dataset.responding = "false";
      errorLine.textContent = error.message;
      controls.forEach(control => { control.disabled = !reachable || (!state?.connected && request.method !== "house/hooks/trust"); });
    }
  }
  function requestCard(request) {
    const card = node("article", "request-card");
    card.dataset.requestKey = JSON.stringify(request.id);
    const params = request.params || {};
    const roomLabel = roomInfo[request.room]?.label || request.room || "담당 확인 필요";
    const approval = ["item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/permissions/requestApproval"].includes(request.method);
    const trust = request.method === "house/hooks/trust";
    const approvalLabel = request.method === "item/fileChange/requestApproval" ? "파일 변경 승인" : request.method === "item/permissions/requestApproval" ? "권한 승인" : "명령 실행 승인";
    card.append(node("h3", "", roomLabel + " · " + (trust ? "세션 훅 검토" : approval ? approvalLabel : request.method === "item/tool/requestUserInput" ? "연구자 답변" : "지원하지 않는 요청")));
    if (trust) {
      card.append(node("p", "question-text", params.message || "이번 실행에 적용할 훅을 검토해 주세요."));
      if (Array.isArray(params.hooks)) params.hooks.forEach(hook => {
        const item = node("details"), summary = node("summary", "", (hook.eventName || "이벤트 확인 필요") + " · " + (hook.trustStatus || "신뢰 상태 확인 필요"));
        item.open = true;
        item.append(summary, node("pre", "", hook.command || "명령 정보 없음"));
        detail(item, "출처", hook.sourcePath);
        detail(item, "현재 해시", hook.currentHash);
        card.append(item);
      });
      const actions = node("div", "request-actions");
      [["이번 실행에 허용", "accept", "primary"], ["거절", "decline", "secondary decline"]].forEach(([label, decision, className]) => {
        const button = node("button", className, label); button.type = "button";
        button.addEventListener("click", () => respond(request, {decision}, card)); actions.append(button);
      });
      card.append(actions);
    } else if (approval) {
      detail(card, "이유", params.reason);
      detail(card, "명령", params.command);
      detail(card, "파일 변경", params.changes);
      detail(card, "작업 폴더", params.cwd);
      detail(card, "요청 권한", params.permissions);
      if (request.method === "item/permissions/requestApproval") card.append(node("p", "small", "허용한 권한은 이번 turn에만 적용합니다."));
      const all = node("details"), summary = node("summary", "", "native 요청 원문");
      all.append(summary, node("pre", "", valueText(params))); card.append(all);
      const actions = node("div", "request-actions");
      [["승인", "accept", "primary"], ["거절", "decline", "secondary decline"], ["취소", "cancel", "secondary"]].forEach(([label, decision, className]) => {
        const button = node("button", className, label); button.type = "button";
        button.addEventListener("click", () => respond(request, {decision}, card)); actions.append(button);
      });
      card.append(actions);
    } else if (request.method === "item/tool/requestUserInput" && Array.isArray(params.questions) && params.questions.length) {
      const form = node("form"), inputs = [];
      params.questions.forEach((question, index) => {
        const fieldset = node("fieldset"), legend = node("legend", "", question.header || "질문 " + (index + 1));
        fieldset.append(legend, node("p", "question-text", question.question || ""));
        const options = Array.isArray(question.options) ? question.options : [];
        options.forEach((option, optionIndex) => {
          const label = node("label", "option"), input = node("input");
          input.type = "radio"; input.name = "question-" + index; input.value = option.label;
          input.id = "option-" + requestCards.size + "-" + index + "-" + optionIndex;
          const text = node("span"); text.append(node("b", "", option.label || ""));
          if (option.description) text.append(node("small", "", option.description));
          label.append(input, text); fieldset.append(label);
        });
        const freeLabel = node("label", "free-label", options.length ? "직접 답변 (입력하면 선택 대신 전달)" : "답변");
        const free = node(question.isSecret ? "input" : "textarea");
        if (question.isSecret) free.type = "password";
        free.maxLength = 8000; free.rows = 2;
        freeLabel.append(free); fieldset.append(freeLabel); form.append(fieldset);
        inputs.push({question, fieldset, free});
      });
      const send = node("button", "primary", "답변 전달"); send.type = "submit"; form.append(send);
      form.addEventListener("submit", event => {
        event.preventDefault();
        const answers = Object.create(null);
        for (const {question, fieldset, free} of inputs) {
          const answer = free.value.trim() || fieldset.querySelector("input[type=radio]:checked")?.value;
          if (!answer) { card.querySelector(".feedback").textContent = "각 질문에 답변하거나 항목을 선택해 주세요."; (fieldset.querySelector("input") || free).focus(); return; }
          if (typeof question.id !== "string") { card.querySelector(".feedback").textContent = "질문 ID를 확인할 수 없어 응답하지 않았습니다."; return; }
          answers[question.id] = [answer];
        }
        respond(request, {answers}, card);
      });
      card.append(form);
    } else {
      card.append(node("p", "small", "지원하지 않는 native 요청입니다. 이 화면에서는 응답할 수 없습니다."), node("pre", "", request.method || "method 없음"));
      detail(card, "native 요청 내용", params);
    }
    card.append(node("p", "feedback", ""));
    return card;
  }
  function renderRequests() {
    const requests = Array.isArray(state?.requests) ? state.requests : [];
    $("request-count").textContent = state ? requests.length + "건" : "확인 필요";
    const keys = new Set(requests.map(request => JSON.stringify(request.id)));
    for (const [key, value] of requestCards) {
      if (!keys.has(key)) { value.card.remove(); requestCards.delete(key); }
    }
    if (!requests.length) {
      $("requests").replaceChildren(node("p", "small", connected() ? "대기 중인 확인 요청이 없습니다." : "연결 후 대기 중인 확인 요청을 표시합니다."));
      return;
    }
    for (const request of requests) {
      const key = JSON.stringify(request.id), signature = JSON.stringify([request.method, request.room, request.params]);
      let entry = requestCards.get(key);
      if (!entry || entry.signature !== signature) {
        const card = requestCard(request);
        if (entry) entry.card.replaceWith(card);
        entry = {card, signature}; requestCards.set(key, entry);
      }
      if (!entry.card.isConnected) $("requests").append(entry.card);
      entry.card.querySelectorAll("button,input,textarea").forEach(control => { control.disabled = !reachable || entry.card.dataset.responding === "true" || entry.card.dataset.answered === "true" || (!state?.connected && request.method !== "house/hooks/trust"); });
    }
    [...$("requests").children].filter(child => !child.classList.contains("request-card")).forEach(child => child.remove());
  }
  render();
  (async function refresh() { await poll(); setTimeout(refresh, 1000); })();
})();
