"use strict";

var pageInit = require("./page_init");
var navTrail = require("./nav_trail");
var ioFairshareSettings = require("./io_fairshare_settings");

pageInit.onReady(function () {
	pageInit.installGlobalErrorHandler();
	navTrail.renderTrail(
		[
			{ label: "Start", href: "./menu.html" },
			{ label: "Settings", href: "./settings.html" },
			{ label: "Network" },
		],
		"navTrail",
	);
	ioFairshareSettings.bindNetworkSharing("ioFairsharePanel");
});
