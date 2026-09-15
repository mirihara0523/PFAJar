var ipc = require("electron").ipcRenderer;
var branding = require("./branding");

var log = document.getElementById("log");

/** Max (br+span) pairs kept in the log DOM to bound renderer memory. */
var MAX_LOG_LINES = 8000;

/** Current app-instance session id (set by main on log window load). */
var activeLogSession = "";

function appendLogChunk(text) {
  var lines = text.split("\n");
  for (var i = 0; i < lines.length; i++) {
    var br = document.createElement("br");
    var span = document.createElement("span");
    span.textContent = lines[i];
    log.appendChild(br);
    log.appendChild(span);
  }
  trimLogDom();
  window.scrollTo(0, document.body.scrollHeight);
}

function trimLogDom() {
  var maxNodes = MAX_LOG_LINES * 2;
  while (log.childNodes.length > maxNodes) {
    log.removeChild(log.firstChild);
    log.removeChild(log.firstChild);
  }
}

function clearLogStorage() {
  try {
    localStorage.removeItem(branding.LOG_SESSION_KEY);
    localStorage.removeItem(branding.LEGACY_LOG_UI_KEY);
    localStorage.removeItem(branding.LEGACY_LOG_TIME_KEY);
  } catch (e) {
    // ignore quota / private mode
  }
}

/** Empty the log view and drop any persisted UI log from prior app instances. */
function resetLogView(sessionId) {
  activeLogSession = sessionId || "";
  if (log) {
    log.innerHTML = "";
  }
  clearLogStorage();
  if (activeLogSession) {
    try {
      localStorage.setItem(branding.LOG_SESSION_KEY, activeLogSession);
    } catch (e) {
      // ignore
    }
  }
}

function beginLogSession(sessionId) {
  if (!sessionId) {
    return;
  }
  var stored = "";
  try {
    stored = localStorage.getItem(branding.LOG_SESSION_KEY) || "";
  } catch (e) {
    stored = "";
  }
  if (stored !== sessionId) {
    resetLogView(sessionId);
    return;
  }
  activeLogSession = sessionId;
  if (log && log.childNodes.length) {
    return;
  }
  if (log) {
    log.innerHTML = "";
  }
}

window.onload = function () {
  if (log) {
    log.innerHTML = "";
  }
  clearLogStorage();
};

ipc.on("resetLogSession", function (event, sessionId) {
  beginLogSession(String(sessionId || ""));
});

ipc.on("savelogs", function () {
  // Log UI is ephemeral per app instance; do not persist to localStorage on quit.
});

ipc.on("log", function (event, response) {
  appendLogChunk(String(response || ""));
});
