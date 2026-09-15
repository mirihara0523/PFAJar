"use strict";

var assert = require("assert");

var wizardBusy = require("../js/wizard_busy");

function testBusyFlag() {
	wizardBusy.setWizardBusy({ busy: true });
	assert.strictEqual(wizardBusy.isWizardBusy(), true);
	wizardBusy.setWizardBusy({ busy: false });
	assert.strictEqual(wizardBusy.isWizardBusy(), false);
}

function testGuardClickWhenBusy() {
	var calls = 0;
	wizardBusy.setWizardBusy({ busy: true });
	var fn = wizardBusy.guardClick(function () {
		calls += 1;
	});
	fn();
	assert.strictEqual(calls, 0);
	wizardBusy.setWizardBusy({ busy: false });
}

function run() {
	testBusyFlag();
	testGuardClickWhenBusy();
	console.log("test-wizard-busy: PASS");
}

run();
