"use strict";

(function () {
	var path = require("path");
	var fs = require("fs");

	function getAppRoot() {
		var p = decodeURIComponent(window.location.pathname || "");
		p = p.replace(/\\/g, "/");
		if (/^\/[A-Za-z]:\//.test(p)) {
			p = p.slice(1);
		}
		return path.dirname(path.dirname(p));
	}

	function setVersion() {
		var el = document.getElementById("version");
		if (!el) {
			return;
		}
		try {
			var raw = fs.readFileSync(
				path.join(getAppRoot(), "package.json"),
				"utf8",
			);
			var version = JSON.parse(raw).version;
			if (version) {
				el.textContent = "Version " + version;
			}
		} catch (err) {
			console.error("hub_version:", err);
		}
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", setVersion);
	} else {
		setVersion();
	}
})();
