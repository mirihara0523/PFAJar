require("dotenv").config();
module.exports = {
	packagerConfig: {
		osxSign: {},
		asar: false,
		icon: "assets/icons/icon",
		ignore: [
			/^\/\.venv(?:\/|$)/,
			/^\/\.test-seam-fixture\.tif$/,
			/^\/\.preview-order-smoke(?:[-.]|$)/,
			/^\/\.ruff_cache(?:\/|$)/,
			/^\/(?:\.eslintignore|\.eslintrc\.js|\.gitattributes|\.gitignore|forge\.config\.js|requirements\.txt)$/,
			/^\/(?:AI_COLLAB_GUIDE|DEVELOPER_HANDOFF|STATUS)\.md$/,
			/^\/legacy_atlas\.nrrd$/,
			/\.bak(?:[_\.-]|$)/,
			// Keep the in-app PDF help, but leave development and release
			// documentation in the source repository and coordination folder.
			/^\/docs\/(?!belljar_guide\.pdf$)/,
			// Build, test, benchmark, and release utilities are source-only; the
			// running app never launches files from either development tree.
			/^\/scripts(?:\/|$)/,
			/^\/python(?:\/|$)/,
			/^\/patches(?:\/|$)/,
			/^\/py\/(?:test_|align_seam_ab_test\.py$|__pycache__(?:\/|$))/,
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
			// 2026-09-23: @fortawesome/fontawesome-free ships every icon as
			// (a) an individual SVG file under svgs/ (8,182 files, ~6.4MB),
			// (b) a combined sprite sheet per style under sprites/ (~1.5MB),
			// (c) an alternate "SVG with JS" framework under js/ (~6.1MB),
			// (d) build metadata under metadata/ (~6.2MB), and (e) the Less/
			// Sass sources the compiled CSS was built from (~536KB) -- none
			// of which this app uses. Every page loads the icon font
			// instead (css/all.css + webfonts/*.woff2/.ttf), confirmed by
			// grepping pages/js for any reference to these five folders
			// (none found) and for which fa-* icon classes are actually
			// used (14, all fa-solid). Excluding just these five folders
			// keeps css/ and webfonts/ (which all.css's @font-face rules
			// need) while dropping ~20MB of unused files from every
			// packaged build.
			/^\/node_modules\/@fortawesome\/fontawesome-free\/(svgs|js|sprites|metadata|less|scss)(?:\/|$)/,
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
