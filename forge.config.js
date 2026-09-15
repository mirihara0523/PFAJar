require("dotenv").config();
module.exports = {
	packagerConfig: {
		osxSign: {},
		asar: false,
		icon: "assets/icons/icon",
		ignore: [
			/^\/\.venv(?:\/|$)/,
			/^\/\.test-seam-fixture\.tif$/,
			"src",
			"tsconfig.json",
			"yarn.lock",
			".env",
			"README.md",
			"LICENSE",
			"^python/", // top-level python/ package only — not node_modules/python-shell
			"vendor",
			".cursor",
			".gitmodules",
		],
	},
	makers: [
		{
			name: "@electron-forge/maker-zip",
			platforms: ["win32"],
		},
		{
			name: "@electron-forge/maker-dmg",
			config: {
				format: "ULFO",
			},
		},
		{
			name: "@electron-forge/maker-deb",
		},
	],
};
