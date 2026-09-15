"use strict";

/**
 * Smoke test for py/dapi_cleanup.py on a synthetic DAPI-like image.
 * Run: node scripts/test-dapi-cleanup.js
 */
var assert = require("assert");
var fs = require("fs");
var path = require("path");
var os = require("os");
var { spawnSync } = require("child_process");
var helpers = require("./test-helpers");

function resolveEnvPython() {
	var homeDir = path.join(os.homedir(), ".masonjar");
	var benv = path.join(homeDir, "benv");
	if (!fs.existsSync(benv)) {
		return null;
	}
	if (process.platform === "win32") {
		return path.join(homeDir, "benv", "Scripts", "python.exe");
	}
	return path.join(homeDir, "benv", "bin", "python3");
}

function writeSyntheticWithPython(python, outFile) {
	var code =
		"import numpy as np, tifffile as tiff\n" +
		"h,w=96,128\n" +
		"y,x=np.ogrid[:h,:w]\n" +
		"mask=((x-w*0.45)/(w*0.22))**2+((y-h*0.5)/(h*0.28))**2<=1\n" +
		// DAPI convention: bright tissue ellipse on a dark background (real
		// fluorescence). isolate_tissue_mask keeps the brighter class.
		"img=np.where(mask,220+(x%9),20+(y%5)).astype(np.uint16)\n" +
		"tiff.imwrite(" +
		JSON.stringify(outFile) +
		", img)\n";
	var r = spawnSync(python, ["-c", code], { encoding: "utf8" });
	if (r.status !== 0) {
		throw new Error("synthetic write failed: " + (r.stderr || r.stdout));
	}
}

function readPngStats(python, filePath) {
	var code =
		"import numpy as np, cv2, json, sys\n" +
		"img=cv2.imread(sys.argv[1], cv2.IMREAD_UNCHANGED)\n" +
		"h,w=img.shape[:2]\n" +
		"border=np.concatenate([img[0,:],img[-1,:],img[1:-1,0],img[1:-1,-1]])\n" +
		"cy,cx=h//2,w//2\n" +
		"tissue=img[cy-10:cy+10,cx-15:cx+15]\n" +
		"print(json.dumps({'dtype':str(img.dtype),'shape':list(img.shape),'border_mean':float(border.mean()),'border_std':float(border.std()),'tissue_mean':float(tissue.mean()),'max':int(img.max())}))\n";
	var r = spawnSync(python, ["-c", code, filePath], { encoding: "utf8" });
	if (r.status !== 0) {
		throw new Error("read failed: " + (r.stderr || r.stdout));
	}
	return JSON.parse(r.stdout.trim());
}

function main() {
	var repoRoot = path.join(__dirname, "..");
	var scriptPath = path.join(repoRoot, "py", "dapi_cleanup.py");
	var python = resolveEnvPython();
	if (!python || !fs.existsSync(python)) {
		console.log(
			"test-dapi-cleanup.js: SKIP (no ~/.masonjar/benv — run Mason Jar once to bootstrap)",
		);
		return;
	}

	var tmp = helpers.tmpDir("mj-dapi-cleanup-");
	try {
		var inDir = path.join(tmp, "in");
		var outDir = path.join(tmp, "out");
		fs.mkdirSync(inDir, { recursive: true });
		fs.mkdirSync(outDir, { recursive: true });
		var src = path.join(inDir, "M528_s001.tif");
		writeSyntheticWithPython(python, src);

		var run = spawnSync(
			python,
			[
				scriptPath,
				"-i",
				inDir,
				"-o",
				outDir,
				"--isolate",
				"--saturation",
				"5",
			],
			{ cwd: path.join(repoRoot, "py"), encoding: "utf8" },
		);
		assert.strictEqual(run.status, 0, run.stderr || run.stdout);
		assert.match(run.stdout, /Done!/);

		var outFile = path.join(outDir, "M528_s001.png");
		assert.ok(fs.existsSync(outFile), "output png missing");

		var stats = readPngStats(python, outFile);
		assert.strictEqual(stats.dtype, "uint8");
		assert.deepStrictEqual(stats.shape, [96, 128]);
		assert.ok(stats.tissue_mean > stats.border_mean + 20, "expected brighter tissue");
		assert.ok(stats.border_std < 8, "border should be uniform, std=" + stats.border_std);
	} finally {
		helpers.rmDir(tmp);
	}
	console.log("test-dapi-cleanup.js: OK");
}

main();
