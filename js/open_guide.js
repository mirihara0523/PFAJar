"use strict";

var ipc = require("electron").ipcRenderer;

function bindOpenGuide(elementId) {
	var el = document.getElementById(elementId);
	if (!el) {
		return;
	}
	el.addEventListener("click", function (event) {
		event.preventDefault();
		ipc.send("openGuide");
	});
}

bindOpenGuide("openGuide");
bindOpenGuide("guide");
