"use strict";

var ipc = require("electron").ipcRenderer;
var pageInit = require("./page_init");
var navTrail = require("./nav_trail");

var LS_ALLOW_PRERELEASE = "masonjar.update.allowPrerelease";
var LS_LAST_CHECKED_AT = "masonjar.update.lastCheckedAt";
var UPDATE_REPOSITORY_URL = "https://github.com/mirihara0523-hue/masonjar";

var state = {
	cached: null,
	applyInfo: null,
	currentVersion: "",
	busy: false,
	mandatory: false,
};

function qs(id) {
	return document.getElementById(id);
}

function setFeedback(msg, isError) {
	var el = qs("updateFeedback");
	if (!el) {
		return;
	}
	el.textContent = msg || "";
	el.classList.toggle("text-danger", !!isError);
	el.classList.toggle("text-muted", !isError && !!msg);
}

function formatCheckedAt(value) {
	var date = new Date(value);
	if (isNaN(date.getTime())) {
		return "—";
	}
	function pad(number) {
		return String(number).padStart(2, "0");
	}
	return (
		date.getFullYear() +
		"-" +
		pad(date.getMonth() + 1) +
		"-" +
		pad(date.getDate()) +
		" " +
		pad(date.getHours()) +
		":" +
		pad(date.getMinutes()) +
		":" +
		pad(date.getSeconds())
	);
}

function renderLastChecked() {
	var el = qs("lastUpdateCheckedAt");
	if (!el) {
		return;
	}
	var stored = null;
	try {
		stored = localStorage.getItem(LS_LAST_CHECKED_AT);
	} catch (_e) {
		stored = null;
	}
	el.textContent = formatCheckedAt(stored);
}

function recordSuccessfulCheck() {
	try {
		localStorage.setItem(LS_LAST_CHECKED_AT, new Date().toISOString());
	} catch (_e) {
		// The current page can still show the time when local storage is unavailable.
	}
	renderLastChecked();
}

function setProgress(visible, percent, message) {
	var wrap = qs("updateProgressWrap");
	var bar = qs("updateProgressBar");
	var status = qs("updateProgressStatus");
	if (wrap) {
		wrap.classList.toggle("d-none", !visible);
	}
	if (bar) {
		var pct = Math.max(0, Math.min(100, Number(percent) || 0));
		bar.style.width = pct + "%";
		bar.setAttribute("aria-valuenow", String(pct));
	}
	if (status) {
		status.classList.toggle("d-none", !visible);
		status.textContent = message || "";
	}
}

function openedFromStartupPrompt() {
	try {
		var params = new URLSearchParams(window.location.search || "");
		return params.get("pending") === "1";
	} catch (_e) {
		return false;
	}
}

function openedFromMandatoryUpdate() {
	try {
		var params = new URLSearchParams(window.location.search || "");
		return params.get("mandatory") === "1";
	} catch (_e) {
		return false;
	}
}

function applyMandatoryLockUI() {
	state.mandatory = true;
	document.body.classList.add("mandatory-update-locked");
	var banner = qs("mandatoryUpdateBanner");
	if (banner) {
		banner.classList.remove("d-none");
		var latest = state.cached && state.cached.latest;
		banner.textContent =
			"Required update to version " +
			(latest || "…") +
			" — Mason Jar will download and install automatically.";
	}
	var advanced = qs("updateAdvancedPanel");
	if (advanced) {
		advanced.classList.add("d-none");
	}
	var footer = qs("updateFooterNav");
	if (footer) {
		footer.classList.add("d-none");
	}
	var nav = qs("navTrail");
	if (nav) {
		nav.classList.add("pe-none", "opacity-50");
	}
	var checkBtn = qs("checkAgainBtn");
	if (checkBtn) {
		checkBtn.classList.add("d-none");
	}
	var releaseBtn = qs("openReleaseBtn");
	if (releaseBtn) {
		releaseBtn.classList.add("d-none");
	}
	var macBtn = qs("macDownloadBtn");
	if (macBtn) {
		macBtn.classList.add("d-none");
	}
	var updateNowBtn = qs("updateNowBtn");
	if (updateNowBtn) {
		updateNowBtn.classList.add("d-none");
	}
	var logBtn = qs("openUpdateLogBtn");
	if (logBtn) {
		logBtn.classList.add("d-none");
	}
}

function renderVersionLabels() {
	var currentEl = qs("currentVersionLabel");
	var latestEl = qs("latestVersionLabel");
	var badge = qs("prereleaseBadge");
	var summary = qs("updateSummaryLine");
	var notesBlock = qs("releaseNotesBlock");
	var notesText = qs("releaseNotesText");

	if (currentEl) {
		currentEl.textContent = state.currentVersion || "—";
	}

	var cached = state.cached;
	var latest = cached && cached.latest;
	if (latestEl) {
		latestEl.textContent = latest || "—";
	}
	if (badge) {
		badge.classList.toggle("d-none", !(cached && cached.isPrerelease));
	}
	if (summary) {
		if (state.mandatory) {
			summary.textContent =
				"A required update to version " + (latest || "…") + " is installing.";
		} else if (!cached) {
			summary.textContent = "Could not load update information.";
		} else if (cached.error) {
			summary.textContent = cached.error;
		} else if (cached.updateAvailable) {
			summary.textContent =
				"A newer version (" + cached.latest + ") is available.";
		} else if (latest) {
			summary.textContent = "You're up to date.";
		} else {
			summary.textContent =
				"No published releases found in mirihara0523-hue/masonjar.";
		}
	}
	if (notesBlock && notesText) {
		var excerpt = (cached && cached.releaseNotesExcerpt) || "";
		var showNotes = !!excerpt && !!(cached && cached.updateAvailable);
		notesBlock.classList.toggle("d-none", !showNotes);
		notesText.textContent = excerpt;
	}
}

function renderActionButtons() {
	var checkBtn = qs("checkAgainBtn");
	var updateNowBtn = qs("updateNowBtn");
	var macBtn = qs("macDownloadBtn");
	var releaseBtn = qs("openReleaseBtn");
	var info = state.applyInfo || {};
	var cached = state.cached || {};
	var canWinApply = !!info.canApplyInApp;
	var isDarwin = info.platform === "darwin";
	var hasUpdate = !!cached.updateAvailable;

	if (state.mandatory) {
		return;
	}
	if (checkBtn) {
		checkBtn.disabled = state.busy;
	}
	if (updateNowBtn) {
		updateNowBtn.classList.toggle("d-none", !canWinApply || !hasUpdate);
		updateNowBtn.disabled = state.busy || !hasUpdate;
	}
	if (macBtn) {
		macBtn.classList.toggle("d-none", !isDarwin || !hasUpdate);
		macBtn.disabled = state.busy;
	}
	if (releaseBtn) {
		releaseBtn.classList.toggle("d-none", !cached.releaseUrl);
		releaseBtn.disabled = state.busy;
	}
}

function renderUpdateTestBanner() {
	var banner = qs("updateTestBanner");
	if (!banner) {
		return;
	}
	if (state.currentVersion === "6.0.5") {
		banner.textContent =
			"Pre-release build — validates Update Now from 6.0.4.";
		banner.classList.remove("d-none");
	} else {
		banner.textContent = "";
		banner.classList.add("d-none");
	}
}

function applyStatusPayload(payload) {
	if (!payload) {
		return;
	}
	state.cached = payload.cached || payload.result || state.cached;
	state.applyInfo = payload.applyInfo || state.applyInfo;
	if (payload.currentVersion) {
		state.currentVersion = payload.currentVersion;
	}
	if (payload.mandatoryUpdateActive) {
		state.mandatory = true;
	}
	if (payload.lockCleared) {
		setFeedback("Cleared a stale update lock from a previous attempt.");
	}
	renderVersionLabels();
	renderActionButtons();
	renderUpdateTestBanner();
}

function refreshFromMain(useCacheOnly) {
	if (useCacheOnly) {
		return ipc.invoke("getUpdateStatus").then(applyStatusPayload);
	}
	var allowEl = qs("allowPrerelease");
	var allowPrerelease = allowEl ? !!allowEl.checked : false;
	return ipc
		.invoke("checkForUpdatesDetailed", { allowPrerelease: allowPrerelease })
		.then(function (payload) {
			applyStatusPayload(payload);
			if (!(state.cached && state.cached.error)) {
				recordSuccessfulCheck();
			}
			return payload;
		});
}

function savePreferencesAndRefresh() {
	var allowEl = qs("allowPrerelease");
	var keepEl = qs("keepVersionBackups");
	var startupEl = qs("checkUpdatesOnStartup");
	var allow = allowEl ? !!allowEl.checked : false;
	var keepBackups = keepEl ? !!keepEl.checked : false;
	var checkOnStartup = false;
	try {
		localStorage.setItem(LS_ALLOW_PRERELEASE, allow ? "1" : "0");
	} catch (_e) {
		// ignore
	}
	setFeedback("");
	return ipc
		.invoke("saveUpdatePreferences", {
			allowPrerelease: allow,
			keepVersionBackups: keepBackups,
			checkOnStartup: checkOnStartup,
		})
		.then(function () {
			return refreshFromMain(false);
		});
}

function restoreKeepBackupsToggle(prefs) {
	var keepEl = qs("keepVersionBackups");
	if (!keepEl) {
		return;
	}
	keepEl.checked = !!(prefs && prefs.keep_version_backups);
}

function restoreCheckOnStartupToggle(prefs) {
	var el = qs("checkUpdatesOnStartup");
	if (!el) {
		return;
	}
	// Custom builds never enable background update checks.
	el.checked = false;
	el.disabled = true;
}

function refreshVersionBackupsStatus() {
	var statusEl = qs("versionBackupsStatus");
	var deleteBtn = qs("deleteVersionBackupsBtn");
	return ipc
		.invoke("listVersionBackups")
		.then(function (result) {
			var backups = (result && result.backups) || [];
			var n = backups.length;
			if (statusEl) {
				if (!result || !result.installRoot) {
					statusEl.textContent =
						"Backup cleanup is available in the packaged Windows app.";
				} else if (n === 0) {
					statusEl.textContent = "No version backup folders found.";
				} else {
					statusEl.textContent =
						n === 1
							? "1 backup folder found."
							: n + " backup folders found.";
				}
			}
			if (deleteBtn) {
				deleteBtn.disabled = !n || state.busy || state.mandatory;
			}
			return backups;
		})
		.catch(function () {
			if (statusEl) {
				statusEl.textContent = "Could not list version backups.";
			}
			if (deleteBtn) {
				deleteBtn.disabled = true;
			}
			return [];
		});
}

function onDeleteVersionBackupsClick() {
	if (state.busy || state.mandatory) {
		return;
	}
	ipc
		.invoke("listVersionBackups")
		.then(function (result) {
			var backups = (result && result.backups) || [];
			if (!backups.length) {
				setFeedback("No version backup folders to delete.");
				return refreshVersionBackupsStatus();
			}
			var preview = backups
				.slice(0, 5)
				.map(function (p) {
					return p;
				})
				.join("\n");
			var more =
				backups.length > 5 ? "\n…and " + (backups.length - 5) + " more" : "";
			var ok = window.confirm(
				"Delete " +
					backups.length +
					" version backup folder(s)?\n\n" +
					preview +
					more,
			);
			if (!ok) {
				return null;
			}
			state.busy = true;
			renderActionButtons();
			return ipc.invoke("deleteVersionBackups").then(function (delResult) {
				var deleted = (delResult && delResult.deleted) || [];
				var errors = (delResult && delResult.errors) || [];
				if (errors.length) {
					setFeedback(
						"Deleted " +
							deleted.length +
							"; errors: " +
							errors.join("; "),
						true,
					);
				} else {
					setFeedback(
						deleted.length
							? "Deleted " + deleted.length + " version backup folder(s)."
							: "No version backup folders to delete.",
					);
				}
				return refreshVersionBackupsStatus();
			});
		})
		.catch(function (err) {
			setFeedback(String(err && err.message ? err.message : err), true);
		})
		.finally(function () {
			state.busy = false;
			renderActionButtons();
		});
}

function confirmUpdateNow() {
	var cached = state.cached || {};
	var lines = [
		"Mason Jar will download the update, quit, and restart with version " +
			(cached.latest || "?") +
			".",
		"Finish or cancel any running pipeline jobs first.",
	];
	if (cached.isPrerelease) {
		lines.unshift("This is a pre-release build.");
	}
	return Promise.resolve(
		window.confirm("Update Now?\n\n" + lines.join("\n\n")),
	);
}

function runUpdateNowWithoutConfirm() {
	state.busy = true;
	setFeedback("");
	setProgress(true, 0, "Preparing required update…");
	renderActionButtons();
	return ipc
		.invoke("runWindowsUpdateNow")
		.then(function (result) {
			if (result && result.lockCleared) {
				setFeedback("Cleared a stale update lock from a previous attempt.");
			}
			if (!result || !result.ok) {
				setFeedback((result && result.error) || "Update failed.", true);
				setProgress(false, 0, "");
				state.busy = false;
				renderActionButtons();
				return ipc.invoke("getUpdateStatus");
			}
			setFeedback("Installing update… Mason Jar will restart.");
			setProgress(true, 100, "Installing update…");
		})
		.then(function (payload) {
			if (payload) {
				applyStatusPayload(payload);
			}
		})
		.catch(function (err) {
			setFeedback(String(err && err.message ? err.message : err), true);
			setProgress(false, 0, "");
			state.busy = false;
			renderActionButtons();
		});
}

function onUpdateNowClick() {
	if (state.busy) {
		return;
	}
	confirmUpdateNow().then(function (ok) {
		if (!ok) {
			return;
		}
		runUpdateNowWithoutConfirm();
	});
}

function runMandatoryUpdateFlow() {
	applyMandatoryLockUI();
	renderVersionLabels();
	var info = state.applyInfo || {};
	if (info.canApplyInApp && state.cached && state.cached.updateAvailable) {
		runUpdateNowWithoutConfirm();
		return;
	}
	state.busy = true;
	setProgress(true, 0, "Opening release download…");
	var url = state.cached && state.cached.releaseUrl;
	if (url) {
		ipc.invoke("openExternalUrl", url);
	}
	setFeedback(
		"Download and install the latest Mason Jar from GitHub, then reopen the app.",
		false,
	);
	setTimeout(function () {
		ipc.invoke("quitApp");
	}, 4000);
}

function openReleasePage() {
	var url = state.cached && state.cached.releaseUrl;
	if (!url) {
		return;
	}
	ipc.invoke("openExternalUrl", url);
}

function openUpdateRepository() {
	ipc.invoke("openExternalUrl", UPDATE_REPOSITORY_URL);
}

function openUpdateLog() {
	ipc
		.invoke("openUpdateLog")
		.then(function (result) {
			if (result && result.message) {
				setFeedback(result.message, false);
			}
		})
		.catch(function (err) {
			setFeedback(String(err && err.message ? err.message : err), true);
		});
}

function restorePrereleaseToggle(prefs) {
	var allowEl = qs("allowPrerelease");
	if (!allowEl) {
		return;
	}
	var fromPrefs = prefs && prefs.allow_prerelease;
	var stored = null;
	try {
		stored = localStorage.getItem(LS_ALLOW_PRERELEASE);
	} catch (_e) {
		stored = null;
	}
	if (stored === "1" || stored === "0") {
		allowEl.checked = stored === "1";
	} else if (fromPrefs != null) {
		allowEl.checked = !!fromPrefs;
	}
}

pageInit.onReady(function () {
	pageInit.installGlobalErrorHandler();
	renderLastChecked();
	navTrail.renderTrail(
		[
			{ label: "Start", href: "./menu.html" },
			{ label: "Settings", href: "./settings.html" },
			{ label: "Updates" },
		],
		"navTrail",
	);

	ipc.on("updateDownloadProgress", function (_event, data) {
		var pct = data && data[0];
		var msg = data && data[1];
		setProgress(true, pct, msg);
	});

	var checkBtn = qs("checkAgainBtn");
	var updateNowBtn = qs("updateNowBtn");
	var macBtn = qs("macDownloadBtn");
	var releaseBtn = qs("openReleaseBtn");
	var allowEl = qs("allowPrerelease");
	var keepEl = qs("keepVersionBackups");
	var deleteBackupsBtn = qs("deleteVersionBackupsBtn");
	var logBtn = qs("openUpdateLogBtn");
	var repositoryBtn = qs("openUpdateRepositoryBtn");

	if (checkBtn) {
		checkBtn.addEventListener("click", function () {
			setFeedback("Checking " + UPDATE_REPOSITORY_URL + "…");
			state.busy = true;
			renderActionButtons();
			refreshFromMain(false)
				.then(function () {
					var cached = state.cached || {};
					if (!cached.error) {
						setFeedback("Checked " + UPDATE_REPOSITORY_URL + ".");
					}
				})
				.catch(function (err) {
					setFeedback(String(err && err.message ? err.message : err), true);
				})
				.finally(function () {
					state.busy = false;
					renderActionButtons();
					refreshVersionBackupsStatus();
				});
		});
	}
	if (updateNowBtn) {
		updateNowBtn.addEventListener("click", onUpdateNowClick);
	}
	if (macBtn) {
		macBtn.addEventListener("click", openReleasePage);
	}
	if (releaseBtn) {
		releaseBtn.addEventListener("click", openReleasePage);
	}
	if (logBtn) {
		logBtn.addEventListener("click", openUpdateLog);
	}
	if (repositoryBtn) {
		repositoryBtn.addEventListener("click", openUpdateRepository);
	}
	if (deleteBackupsBtn) {
		deleteBackupsBtn.addEventListener("click", onDeleteVersionBackupsClick);
	}
	function onPrefToggleChange() {
		if (state.mandatory) {
			return;
		}
		state.busy = true;
		renderActionButtons();
		savePreferencesAndRefresh()
			.catch(function (err) {
				setFeedback(String(err && err.message ? err.message : err), true);
			})
			.finally(function () {
				state.busy = false;
				renderActionButtons();
			});
	}
	if (allowEl) {
		allowEl.addEventListener("change", onPrefToggleChange);
	}
	if (keepEl) {
		keepEl.addEventListener("change", onPrefToggleChange);
	}
	var startupEl = qs("checkUpdatesOnStartup");
	if (startupEl) {
		startupEl.addEventListener("change", onPrefToggleChange);
	}

	ipc
		.invoke("getUpdateStatus")
		.then(function (payload) {
			restorePrereleaseToggle(payload && payload.preferences);
			restoreKeepBackupsToggle(payload && payload.preferences);
			restoreCheckOnStartupToggle(payload && payload.preferences);
			applyStatusPayload(payload);
			refreshVersionBackupsStatus();
			if (openedFromMandatoryUpdate() || (payload && payload.mandatoryUpdateActive)) {
				return ipc.invoke("checkLatestStableRelease").then(function (stablePayload) {
					applyStatusPayload(stablePayload);
					if (state.cached && state.cached.updateAvailable) {
						runMandatoryUpdateFlow();
					} else {
						setFeedback(
							(stablePayload &&
								stablePayload.result &&
								stablePayload.result.error) ||
								"Could not start required update.",
							true,
						);
					}
					return null;
				});
			}
			var useCache =
				openedFromStartupPrompt() &&
				state.cached &&
				state.cached.updateAvailable;
			if (useCache) {
				setFeedback("Update available — click Update Now when ready.");
				return null;
			}
			return refreshFromMain(false);
		})
		.catch(function (err) {
			setFeedback(String(err && err.message ? err.message : err), true);
		});
});
