const STORAGE = {
  sessionId: "amma_voice_session_id",
  userId: "amma_voice_user_id",
  apiBase: "amma_voice_api_base",
  messages: "amma_voice_messages",
  workspaceName: "amma_voice_workspace",
  autoPlay: "amma_voice_auto_play",
  currentPage: "amma_voice_page",
};

const PAGE_META = {
  dashboard: {
    title: "Dashboard",
    subtitle: "Overview of your voice workspace",
  },
  library: {
    title: "Voice Library",
    subtitle: "Upload samples and manage your voice clone",
  },
  speakers: {
    title: "Speaker Split",
    subtitle: "Separate two speakers from a single recording",
  },
  chat: {
    title: "Conversations",
    subtitle: "Chat with Amma using her preserved voice",
  },
  settings: {
    title: "Settings",
    subtitle: "Workspace, integrations, and preferences",
  },
};

let lastVoiceStatus = null;
let platformInfo = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function getApiBase() {
  const input = $("#api-base")?.value?.trim().replace(/\/$/, "");
  return input || window.location.origin;
}

function audioUrl(path) {
  return path.startsWith("http") ? path : `${getApiBase()}${path}`;
}

async function api(path, options = {}) {
  const url = `${getApiBase()}${path}`;
  const res = await fetch(url, options);
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text || res.statusText };
  }
  if (!res.ok) {
    const detail = data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
          : `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

let toastTimer;
function toast(message, type = "info") {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast show ${type}`;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => {
      el.hidden = true;
    }, 350);
  }, 4200);
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

function setConnection(ok, label) {
  const pill = $("#connection-pill");
  pill.classList.toggle("ok", ok);
  pill.classList.toggle("error", !ok);
  $("#connection-label").textContent = label;
}

/* ——— Session & messages ——— */

function getSessionId() {
  let id = localStorage.getItem(STORAGE.sessionId);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(STORAGE.sessionId, id);
  }
  return id;
}

function getUserId() {
  let id = localStorage.getItem(STORAGE.userId);
  if (!id) {
    id = "user";
    localStorage.setItem(STORAGE.userId, id);
  }
  return id;
}

function newSession() {
  localStorage.setItem(STORAGE.sessionId, crypto.randomUUID());
  localStorage.removeItem(STORAGE.messages);
  renderMessages([]);
  toast("Started a new conversation", "success");
}

function loadMessages() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE.messages) || "[]");
  } catch {
    return [];
  }
}

function saveMessages(messages) {
  localStorage.setItem(STORAGE.messages, JSON.stringify(messages));
}

function renderMessages(messages) {
  const thread = $("#chat-thread");
  thread.innerHTML = "";
  if (!messages.length) {
    thread.innerHTML =
      '<p class="chat-empty">Your conversation with Amma will appear here.</p>';
    return;
  }
  for (const msg of messages) {
    thread.appendChild(createBubble(msg));
  }
  thread.scrollTop = thread.scrollHeight;
}

function createBubble(msg) {
  const wrap = document.createElement("div");
  wrap.className = `bubble ${msg.role}`;
  const role = document.createElement("span");
  role.className = "bubble-role";
  role.textContent = msg.role === "user" ? "You" : "Amma";
  wrap.appendChild(role);
  const text = document.createElement("p");
  text.style.margin = "0";
  text.textContent = msg.text;
  wrap.appendChild(text);
  if (msg.role === "amma" && msg.audioUrl) {
    const actions = document.createElement("div");
    actions.className = "bubble-actions";
    const playBtn = document.createElement("button");
    playBtn.type = "button";
    playBtn.textContent = "Play again";
    playBtn.addEventListener("click", () => playAudio(msg.audioUrl));
    actions.appendChild(playBtn);
    wrap.appendChild(actions);
  }
  return wrap;
}

function playAudio(url) {
  const player = $("#reply-audio");
  player.src = audioUrl(url);
  player.play().catch(() => toast("Could not play audio", "error"));
}

function getAutoPlay() {
  const s = localStorage.getItem(STORAGE.autoPlay);
  return s === null ? true : s === "true";
}

function setAutoPlay(value) {
  localStorage.setItem(STORAGE.autoPlay, String(value));
  const main = $("#auto-play");
  const settings = $("#auto-play-settings");
  if (main) main.checked = value;
  if (settings) settings.checked = value;
}

/* ——— Navigation ——— */

function navigateTo(page) {
  const meta = PAGE_META[page] || PAGE_META.dashboard;
  $("#page-title").textContent = meta.title;
  $("#page-subtitle").textContent = meta.subtitle;

  $$(".nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.page === page);
  });

  $$(".page").forEach((el) => {
    const show = el.dataset.page === page;
    el.classList.toggle("active", show);
    el.hidden = !show;
  });

  localStorage.setItem(STORAGE.currentPage, page);
  window.location.hash = page;
  $("#sidebar")?.classList.remove("open");

  if (page === "chat") renderMessages(loadMessages());
}

function initNavigation() {
  $$(".nav-item").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      navigateTo(el.dataset.page);
    });
  });

  $$("[data-goto]").forEach((el) => {
    el.addEventListener("click", () => navigateTo(el.dataset.goto));
  });

  $("#btn-menu")?.addEventListener("click", () => {
    $("#sidebar").classList.toggle("open");
  });

  const hash = window.location.hash.replace("#", "");
  const saved = localStorage.getItem(STORAGE.currentPage);
  const initial = PAGE_META[hash] ? hash : PAGE_META[saved] ? saved : "dashboard";
  navigateTo(initial);

  window.addEventListener("hashchange", () => {
    const p = window.location.hash.replace("#", "");
    if (PAGE_META[p]) navigateTo(p);
  });
}

function initSettingsTabs() {
  $$(".settings-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const id = tab.dataset.settings;
      $$(".settings-tab").forEach((t) => t.classList.toggle("active", t === tab));
      $$(".settings-panel").forEach((p) => {
        const show = p.id === `settings-${id}`;
        p.classList.toggle("active", show);
        p.hidden = !show;
      });
    });
  });
}

/* ——— Status & dashboard ——— */

function updateCloneButton(status) {
  const btn = $("#btn-clone");
  const hint = $("#clone-hint");
  if (!btn) return;

  const samples = status.sample_count ?? 0;
  const hasClone = status.has_clone;
  const elevenlabsOk = status.elevenlabs_verify?.can_clone ?? false;
  const elevenlabsMsg = status.elevenlabs_verify?.message;
  const canClone = status.can_clone ?? samples > 0;

  btn.disabled = !canClone;
  if (hasClone) btn.textContent = "Re-create voice clone";
  else btn.textContent = "Create voice clone";

  if (!canClone) {
    btn.title = "Add voice samples first";
    if (hint) hint.textContent = "Upload at least one voice note to enable cloning.";
  } else if (!elevenlabsOk) {
    btn.title = elevenlabsMsg || "Fix ElevenLabs API key";
    if (hint)
      hint.textContent =
        elevenlabsMsg ||
        "Enable Instant Voice Cloning on your ElevenLabs API key.";
  } else {
    btn.title = "";
    if (hint)
      hint.textContent = hasClone
        ? "A voice clone exists. Re-create to refresh, or open Conversations."
        : "Ready to create your voice clone from uploaded samples.";
  }
}

function renderOnboarding(status) {
  const steps = [
    {
      id: "samples",
      label: "Add voice samples",
      detail: "Upload WhatsApp voice notes or use Speaker Split",
      done: (status.sample_count ?? 0) > 0,
    },
    {
      id: "elevenlabs",
      label: "Configure ElevenLabs",
      detail: "API key with Instant Voice Cloning permission",
      done: status.elevenlabs_verify?.can_clone ?? false,
    },
    {
      id: "clone",
      label: "Create voice clone",
      detail: "Generate the preserved voice profile",
      done: status.has_clone,
    },
    {
      id: "chat",
      label: "Start a conversation",
      detail: "Talk to Amma with voice replies",
      done: status.has_clone,
    },
  ];

  const doneCount = steps.filter((s) => s.done).length;
  $("#onboarding-progress").textContent = `${doneCount}/${steps.length}`;

  const list = $("#onboarding-checklist");
  list.innerHTML = steps
    .map(
      (s, i) => `
    <li class="${s.done ? "done" : ""}">
      <span class="check-step">${s.done ? "✓" : i + 1}</span>
      <div class="check-body">
        <strong>${escapeHtml(s.label)}</strong>
        <span>${escapeHtml(s.detail)}</span>
      </div>
    </li>`
    )
    .join("");
}

function renderMetrics(status) {
  const samples = status.sample_count ?? 0;
  const hasClone = status.has_clone;
  const elOk = status.elevenlabs_verify?.can_clone;
  const elSet = status.elevenlabs_configured;
  const ffmpeg = status.ffmpeg_available;

  const setMetric = (id, text, cls, hintId, hint) => {
    const el = $(`#metric-${id}`);
    if (el) {
      el.textContent = text;
      el.className = `metric-value ${cls || ""}`;
    }
    if (hintId && hint) {
      const h = $(`#metric-${hintId}`);
      if (h) h.textContent = hint;
    }
  };

  setMetric(
    "samples",
    String(samples),
    samples > 0 ? "ok" : "warn",
    "samples-hint",
    samples > 0 ? `~${status.estimated_audio_seconds || "?"}s estimated` : "No samples yet"
  );
  setMetric(
    "clone",
    hasClone ? "Active" : "None",
    hasClone ? "ok" : "bad",
    "clone-hint",
    hasClone ? "Ready for conversations" : "Create after uploading samples"
  );
  setMetric(
    "elevenlabs",
    elOk ? "Connected" : elSet ? "Limited" : "Missing",
    elOk ? "ok" : elSet ? "warn" : "bad"
  );

  const sysParts = [];
  if (ffmpeg) sysParts.push("ffmpeg");
  const diar = status.diarization_ready;
  if (diar?.pyannote_installed) sysParts.push("pyannote");
  else if (platformInfo?.settings?.diarization_backend === "local")
    sysParts.push("local split");
  setMetric(
    "system",
    sysParts.length ? "Ready" : "Partial",
    sysParts.length >= 1 ? "ok" : "warn"
  );
}

function renderFeatureGrid() {
  const grid = $("#feature-grid");
  const features = platformInfo?.features || [
    { name: "Voice Library", description: "Manage voice samples" },
    { name: "Voice Clone", description: "ElevenLabs instant clone" },
    { name: "Speaker Split", description: "Two-speaker separation" },
    { name: "Conversations", description: "AI chat with voice replies" },
  ];
  grid.innerHTML = features
    .map(
      (f) => `
    <article class="feature-card">
      <h3>${escapeHtml(f.name)}</h3>
      <p>${escapeHtml(f.description)}</p>
    </article>`
    )
    .join("");
}

function renderIntegrations(status) {
  const tbody = $("#integrations-table tbody");
  const el = status.elevenlabs_verify || {};
  const diar = status.diarization_ready || {};

  const rows = [
    {
      name: "ElevenLabs",
      status: el.can_clone ? "ok" : status.elevenlabs_configured ? "warn" : "bad",
      label: el.can_clone ? "Connected" : status.elevenlabs_configured ? "Limited" : "Not configured",
      notes: el.message || "Instant Voice Cloning required",
    },
    {
      name: "Google Gemini",
      status: "ok",
      label: "Server-side",
      notes: "Powers Amma conversations (GEMINI_API_KEY on server)",
    },
    {
      name: "Hugging Face",
      status: diar.hf_token_set || diar.hf_gated_access?.ok ? "ok" : "warn",
      label: diar.hf_token_set ? "Token set" : "Optional",
      notes: diar.hf_gated_access?.skipped
        ? "Using local diarization on this server"
        : "For pyannote speaker separation",
    },
    {
      name: "ffmpeg",
      status: status.ffmpeg_available ? "ok" : "warn",
      label: status.ffmpeg_available ? "Available" : "Missing",
      notes: "Required for audio processing",
    },
  ];

  tbody.innerHTML = rows
    .map(
      (r) => `
    <tr>
      <td>${escapeHtml(r.name)}</td>
      <td><span class="status-chip ${r.status}">${escapeHtml(r.label)}</span></td>
      <td>${escapeHtml(r.notes)}</td>
    </tr>`
    )
    .join("");
}

function renderServerConfig() {
  const s = platformInfo?.settings || {};
  const items = [
    ["Reply language", s.mother_reply_language || "auto"],
    ["Diarization", s.diarization_backend || "—"],
    ["TTS model", s.elevenlabs_model || "—"],
  ];
  $("#server-config-list").innerHTML = items
    .map(
      ([k, v]) => `
    <div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd></div>`
    )
    .join("");

  const profile = lastVoiceStatus?.profile;
  $("#audio-config-list").innerHTML = [
    ["Voice profile", profile?.voice_id ? `${profile.name || "mother"} (${profile.voice_id.slice(0, 8)}…)` : "Not created"],
    ["Samples", String(lastVoiceStatus?.sample_count ?? 0)],
  ]
    .map(
      ([k, v]) => `
    <div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd></div>`
    )
    .join("");
}

function updateChatReady(ready) {
  const banner = $("#chat-banner");
  const bannerText = $("#chat-banner-text");
  $("#chat-input").disabled = !ready;
  $("#btn-send").disabled = !ready;
  if (ready) {
    banner.classList.add("ready");
    bannerText.textContent = "Amma is ready. Write a message to begin.";
  } else {
    banner.classList.remove("ready");
    bannerText.textContent =
      "Complete voice setup: upload samples and create a clone.";
  }
}

async function refreshStatus() {
  try {
    const [status, platform] = await Promise.all([
      api("/voice/status"),
      api("/voice/platform").catch(() => null),
    ]);
    lastVoiceStatus = status;
    if (platform) platformInfo = platform;

    setConnection(true, "Connected");
    renderMetrics(status);
    renderOnboarding(status);
    renderFeatureGrid();
    renderIntegrations(status);
    renderServerConfig();
    updateCloneButton(status);
    updateChatReady(status.has_clone);

    const rec = $("#dashboard-recommendation");
    if (status.recommendation) {
      rec.textContent = status.recommendation;
      rec.hidden = false;
    } else {
      rec.hidden = true;
    }

    $("#library-count").textContent = `${status.sample_count ?? 0} file${status.sample_count === 1 ? "" : "s"}`;
    await renderSampleList(status.samples || []);
    return status;
  } catch (err) {
    setConnection(false, "Disconnected");
    toast(err.message, "error");
    throw err;
  }
}

async function renderSampleList(samples) {
  const list = $("#sample-list");
  list.innerHTML = "";
  for (const s of samples) {
    const li = document.createElement("li");
    li.innerHTML = `
      <div>
        <span class="sample-name">${escapeHtml(s.filename)}</span>
        <span class="sample-size">${formatBytes(s.size_bytes)}</span>
      </div>`;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn-icon";
    del.title = "Remove";
    del.textContent = "×";
    del.addEventListener("click", () => deleteSample(s.id));
    li.appendChild(del);
    list.appendChild(li);
  }
}

async function deleteSample(id) {
  if (!confirm("Remove this voice sample?")) return;
  try {
    await api(`/voice/samples/${id}`, { method: "DELETE" });
    toast("Sample removed", "success");
    await refreshStatus();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function uploadFiles(files) {
  if (!files.length) return;
  let ok = 0;
  for (const file of files) {
    const form = new FormData();
    form.append("file", file);
    try {
      await api("/voice/samples", { method: "POST", body: form });
      ok += 1;
    } catch (err) {
      toast(`${file.name}: ${err.message}`, "error");
    }
  }
  if (ok) {
    toast(`Uploaded ${ok} file${ok > 1 ? "s" : ""}`, "success");
    await refreshStatus();
  }
}

async function createClone() {
  const status = lastVoiceStatus || (await api("/voice/status"));
  if (!(status.sample_count > 0)) {
    toast("Add voice samples first.", "error");
    navigateTo("library");
    return;
  }
  if (!status.elevenlabs_verify?.can_clone) {
    toast(
      status.elevenlabs_verify?.message ||
        "Fix ElevenLabs API key permissions.",
      "error"
    );
    navigateTo("settings");
    return;
  }

  const btn = $("#btn-clone");
  btn.disabled = true;
  btn.textContent = "Creating clone…";
  try {
    await api("/voice/clone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "mother" }),
    });
    toast("Voice clone created successfully.", "success");
    await refreshStatus();
    navigateTo("chat");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    await refreshStatus();
  }
}

async function sendMessage(text) {
  const messages = loadMessages();
  messages.push({ role: "user", text, audioUrl: null });
  saveMessages(messages);
  renderMessages(messages);

  const thread = $("#chat-thread");
  const thinking = document.createElement("div");
  thinking.className = "bubble amma thinking";
  thinking.innerHTML =
    '<span class="bubble-role">Amma</span><p style="margin:0">Thinking…</p>';
  thread.appendChild(thinking);
  thread.scrollTop = thread.scrollHeight;

  const sendBtn = $("#btn-send");
  sendBtn.classList.add("loading");
  sendBtn.disabled = true;

  try {
    const data = await api("/voice/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        user_id: getUserId(),
        session_id: getSessionId(),
      }),
    });
    if (data.session_id) localStorage.setItem(STORAGE.sessionId, data.session_id);
    thinking.remove();
    const reply = { role: "amma", text: data.text, audioUrl: data.audio_url };
    messages.push(reply);
    saveMessages(messages);
    renderMessages(messages);
    if (getAutoPlay() && data.audio_url) playAudio(data.audio_url);
  } catch (err) {
    thinking.remove();
    toast(err.message, "error");
  } finally {
    sendBtn.classList.remove("loading");
    sendBtn.disabled = $("#chat-input").disabled;
  }
}

function renderSpeakerPick(job) {
  const container = $("#speaker-pick");
  container.hidden = false;
  container.innerHTML =
    '<p class="panel-desc">Listen to each preview, then select which speaker is Amma.</p>';
  const grid = document.createElement("div");
  grid.className = "speaker-grid";
  for (const sp of job.speakers || []) {
    const card = document.createElement("article");
    card.className = "speaker-card";
    card.innerHTML = `
      <h3>${escapeHtml(sp.label)}</h3>
      <p class="speaker-meta">${sp.speech_seconds}s speech · ${sp.preview_seconds}s preview</p>
      <audio controls preload="metadata" src="${audioUrl(sp.preview_url)}"></audio>`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-primary btn-sm";
    btn.textContent = "Use this voice";
    btn.addEventListener("click", () => selectSpeaker(job.job_id, sp.id, card));
    card.appendChild(btn);
    grid.appendChild(card);
  }
  container.appendChild(grid);
}

async function selectSpeaker(jobId, speakerId, cardEl) {
  try {
    await api(`/voice/speakers/jobs/${jobId}/select`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speaker_id: speakerId }),
    });
    $$(".speaker-card").forEach((c) => c.classList.remove("selected"));
    cardEl.classList.add("selected");
    toast("Added to voice library.", "success");
    await refreshStatus();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function processOgg(file) {
  const name = file.name.toLowerCase();
  if (!name.endsWith(".ogg") && !name.endsWith(".opus")) {
    toast("Upload an .ogg or .opus file", "error");
    return;
  }
  const processing = $("#ogg-processing");
  const pick = $("#speaker-pick");
  pick.hidden = true;
  processing.hidden = false;
  const form = new FormData();
  form.append("file", file);
  try {
    const data = await api("/voice/speakers/separate", { method: "POST", body: form });
    renderSpeakerPick(data.job);
    toast("Speakers separated successfully.", "success");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    processing.hidden = true;
  }
}

async function runHfCheck() {
  const el = $("#hf-check-result");
  el.hidden = false;
  el.textContent = "Checking Hugging Face access…";
  try {
    const data = await api("/voice/speakers/hf-check");
    if (data.skipped) {
      el.textContent = data.message || "Using local diarization on this server.";
    } else if (data.ok) {
      el.textContent = "HF access OK — pyannote models reachable.";
    } else {
      el.textContent = data.help || data.error || "HF access incomplete.";
    }
  } catch (err) {
    el.textContent = err.message;
  }
}

function initUpload() {
  const zone = $("#drop-zone");
  const input = $("#file-input");
  if (!zone) return;

  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.click();
    }
  });
  input.addEventListener("change", () => {
    uploadFiles([...input.files]);
    input.value = "";
  });
  ["dragenter", "dragover"].forEach((ev) => {
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((ev) => {
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
    });
  });
  zone.addEventListener("drop", (e) => {
    const files = [...e.dataTransfer.files].filter(
      (f) =>
        f.type.startsWith("audio/") ||
        /\.(ogg|opus|mp3|wav|m4a|webm|flac)$/i.test(f.name)
    );
    uploadFiles(files);
  });
}

function initOggSpeakerSplit() {
  const zone = $("#ogg-drop-zone");
  const input = $("#ogg-input");
  if (!zone) return;

  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.click();
    }
  });
  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) processOgg(file);
    input.value = "";
  });
  ["dragenter", "dragover"].forEach((ev) => {
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((ev) => {
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
    });
  });
  zone.addEventListener("drop", (e) => {
    const file = [...e.dataTransfer.files].find((f) => {
      const n = f.name.toLowerCase();
      return n.endsWith(".ogg") || n.endsWith(".opus");
    });
    if (file) processOgg(file);
    else toast("Drop a single .ogg or .opus file", "error");
  });
}

function initChat() {
  $("#chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#chat-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    await sendMessage(text);
  });
  $("#chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });
  $("#btn-new-session").addEventListener("click", newSession);
}

function initSettings() {
  const savedApi = localStorage.getItem(STORAGE.apiBase);
  if (savedApi) $("#api-base").value = savedApi;
  const ws = localStorage.getItem(STORAGE.workspaceName);
  if (ws) $("#workspace-name").value = ws;

  $("#api-base").addEventListener("change", () => {
    localStorage.setItem(STORAGE.apiBase, $("#api-base").value.trim());
    refreshStatus();
  });
  $("#workspace-name").addEventListener("change", () => {
    localStorage.setItem(STORAGE.workspaceName, $("#workspace-name").value.trim());
  });

  setAutoPlay(getAutoPlay());
  const syncAutoPlay = (e) => setAutoPlay(e.target.checked);
  $("#auto-play")?.addEventListener("change", syncAutoPlay);
  $("#auto-play-settings")?.addEventListener("change", syncAutoPlay);

  $("#btn-export-chat").addEventListener("click", () => {
    const data = JSON.stringify(loadMessages(), null, 2);
    const blob = new Blob([data], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `amma-conversation-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("Conversation exported", "success");
  });

  $("#btn-clear-chat").addEventListener("click", () => {
    if (!confirm("Clear all conversation history from this browser?")) return;
    localStorage.removeItem(STORAGE.messages);
    renderMessages([]);
    toast("Conversation cleared", "success");
  });
}

function init() {
  initNavigation();
  initSettingsTabs();
  initSettings();
  initUpload();
  initOggSpeakerSplit();
  initChat();

  $("#btn-refresh").addEventListener("click", () => refreshStatus());
  $("#btn-clone").addEventListener("click", createClone);
  $("#btn-hf-check")?.addEventListener("click", runHfCheck);

  renderMessages(loadMessages());
  refreshStatus();
}

init();
