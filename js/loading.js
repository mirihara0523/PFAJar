"use strict";

var ipc = require("electron").ipcRenderer;
var lastUpdateTimestamp = 0;

ipc.on("updateStatus", function (_event, response) {
	if (!response.timestamp || response.timestamp > lastUpdateTimestamp) {
		if (response.timestamp) {
			lastUpdateTimestamp = response.timestamp;
		}
		var status = document.getElementById("status");
		if (status) {
			status.innerHTML = response.message || response;
		}
	}
});

ipc.send("getVersion");
ipc.on("version", function (_event, response) {
	var version = document.getElementById("version");
	if (version) {
		version.textContent = response;
	}
});

var guide = document.getElementById("guide");
if (guide) {
	guide.addEventListener("click", function (event) {
		event.preventDefault();
		ipc.send("openGuide");
	});
}
