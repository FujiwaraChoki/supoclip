const passwordDialog = document.querySelector("#password-dialog");
const passwordForm = document.querySelector("#password-form");
const passwordInput = document.querySelector("#app-password");
const passwordError = document.querySelector("#password-error");
const createForm = document.querySelector("#create-job");
const urlInput = document.querySelector("#video-url");
const createButton = createForm.querySelector("button");
const createError = document.querySelector("#create-error");
const jobsList = document.querySelector("#jobs-list");
const refreshButton = document.querySelector("#refresh-jobs");

let appPassword = "";
let refreshTimer;

function api(path, options = {}) {
  return fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-App-Password": appPassword,
      ...options.headers,
    },
  });
}

function showPasswordDialog(message = "") {
  passwordError.textContent = message;
  passwordError.hidden = !message;
  if (!passwordDialog.open) passwordDialog.showModal();
  passwordInput.focus();
}

async function validatePassword(password) {
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "X-App-Password": password },
  });
  if (!response.ok) throw new Error("That password did not unlock the workspace.");
}

function safeText(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = value;
  return element;
}

function formatTime(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}

function renderClip(clip) {
  const card = document.createElement("article");
  card.className = "clip-card";
  const video = document.createElement("video");
  video.controls = true;
  video.preload = "metadata";
  video.src = clip.url;
  video.setAttribute("aria-label", clip.title || "Generated short video clip");
  const meta = document.createElement("div");
  meta.className = "clip-meta";
  meta.append(safeText("b", "", `${formatTime(clip.start_seconds)} · Score ${clip.score}/100`));
  meta.append(safeText("p", "", clip.transcript));
  const link = document.createElement("a");
  link.href = clip.url;
  link.download = clip.filename;
  link.textContent = "DOWNLOAD CLIP ↓";
  meta.append(link);
  card.append(video, meta);
  return card;
}

function renderJob(job) {
  const card = document.createElement("article");
  card.className = "job-card";
  const topline = document.createElement("div");
  topline.className = "job-topline";
  topline.append(safeText("span", "job-source", `${job.source} · ${job.id.slice(0, 8)}`));
  topline.append(safeText("span", `job-status ${job.status}`, job.status));
  card.append(topline, safeText("p", "job-progress", job.progress));
  if (job.error) card.append(safeText("p", "job-error", job.error));
  if (job.clips?.length) {
    const clips = document.createElement("div");
    clips.className = "clip-grid";
    for (const clip of job.clips) clips.append(renderClip(clip));
    card.append(clips);
  }
  return card;
}

async function loadJobs() {
  const response = await api("/api/jobs");
  if (response.status === 401) {
    showPasswordDialog();
    return;
  }
  if (!response.ok) throw new Error("Could not load recent sessions.");
  const jobs = await response.json();
  jobsList.replaceChildren();
  if (!jobs.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.append(
      safeText("span", "empty-icon", "✧"),
      safeText("p", "", "Your next great clip starts with a link."),
    );
    jobsList.append(empty);
  } else {
    for (const job of jobs) jobsList.append(renderJob(job));
  }
  clearTimeout(refreshTimer);
  if (jobs.some((job) => job.status === "queued" || job.status === "processing")) {
    refreshTimer = setTimeout(() => loadJobs().catch(showLoadError), 3500);
  }
}

function showLoadError(error) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  jobsList.replaceChildren(safeText("p", "job-error", message));
}

passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = passwordInput.value;
  try {
    await validatePassword(password);
    appPassword = password;
    passwordDialog.close();
    passwordInput.value = "";
    await loadJobs();
  } catch (error) {
    showPasswordDialog(error instanceof Error ? error.message : "Could not unlock workspace.");
  }
});

createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  createError.hidden = true;
  createButton.disabled = true;
  createButton.querySelector("span").textContent = "STARTING…";
  try {
    const response = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ url: urlInput.value.trim() }),
    });
    const result = await response.json();
    if (response.status === 401) {
      showPasswordDialog();
      return;
    }
    if (!response.ok) throw new Error(result.detail || "Could not start this video.");
    urlInput.value = "";
    await loadJobs();
  } catch (error) {
    createError.textContent = error instanceof Error ? error.message : "Could not start this video.";
    createError.hidden = false;
  } finally {
    createButton.disabled = false;
    createButton.querySelector("span").textContent = "MAKE CLIPS";
  }
});

refreshButton.addEventListener("click", () => loadJobs().catch(showLoadError));

validatePassword("")
  .then(() => loadJobs())
  .catch(() => showPasswordDialog());
