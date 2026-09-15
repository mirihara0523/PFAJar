"use strict";

var pageInit = require("./page_init");
var navTrail = require("./nav_trail");

pageInit.onReady(function () {
	pageInit.installGlobalErrorHandler();
	navTrail.renderTrail(
		[
			{ label: "Start", href: "./menu.html" },
			{ label: "Settings" },
		],
		"navTrail",
	);
});
