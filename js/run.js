"use strict";

/**
 * Load js modules from pages/*.html (require("./x") otherwise resolves under pages/).
 * <script src="../js/run.js" data-entry="menu.js"></script>
 * <script src="../js/run.js" data-entry="a.js,b.js"></script>
 */
(function () {
	var path = require("path");
	var script = document.currentScript;
	if (!script) {
		return;
	}
	var entry = script.getAttribute("data-entry");
	if (!entry) {
		return;
	}

	if (document.body) {
		var body = document.body;
		var pageBase = path.basename(
			decodeURIComponent(window.location.pathname || ""),
		);
		if (
			!body.classList.contains("menu-page") &&
			!body.classList.contains("wizard-page") &&
			pageBase !== "loading.html"
		) {
			body.classList.add("tool-page");
		}
	}

	function getAppRoot() {
		var p = decodeURIComponent(window.location.pathname || "");
		p = p.replace(/\\/g, "/");
		if (/^\/[A-Za-z]:\//.test(p)) {
			p = p.slice(1);
		}
		return path.dirname(path.dirname(p));
	}

	var jsDir = path.join(getAppRoot(), "js");
	var names = entry.split(",").map(function (s) {
		return s.trim();
	});

	for (var i = 0; i < names.length; i++) {
		if (!names[i]) {
			continue;
		}
		try {
			require(path.join(jsDir, names[i]));
		} catch (err) {
			var msg =
				"Mason Jar failed to load " +
				names[i] +
				":\n" +
				(err && err.message ? err.message : String(err));
			console.error(msg, err);
			alert(msg);
			break;
		}
	}
})();
