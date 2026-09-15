"use strict";

var assert = require("assert");
var dual = require("../js/region_dual_list");

function testTransferAdd() {
	var out = dual.transferAdd([1, 2], [2, 3]);
	assert.deepStrictEqual(out, [2, 3, 1]);
}

function testTransferRemove() {
	var out = dual.transferRemove([2], [1, 2, 3]);
	assert.deepStrictEqual(out, [1, 3]);
}

function testRangeSelectIndices() {
	assert.deepStrictEqual(dual.rangeSelectIndices(1, 4, 10), [1, 2, 3, 4]);
	assert.deepStrictEqual(dual.rangeSelectIndices(-1, 2, 5), [2]);
}

testTransferAdd();
testTransferRemove();
testRangeSelectIndices();
console.log("test-region-dual-list: ok");
