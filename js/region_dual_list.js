"use strict";

/**
 * Transfer highlighted ids from source list to target, skipping duplicates.
 * @param {number[]} sourceHighlight
 * @param {number[]} targetIds
 * @returns {number[]}
 */
function transferAdd(sourceHighlight, targetIds) {
	var out = (targetIds || []).slice();
	var seen = {};
	for (var i = 0; i < out.length; i++) {
		seen[out[i]] = true;
	}
	for (var j = 0; j < sourceHighlight.length; j++) {
		var id = sourceHighlight[j];
		if (!seen[id]) {
			seen[id] = true;
			out.push(id);
		}
	}
	return out;
}

/**
 * Remove highlighted ids from target list.
 * @param {number[]} sourceHighlight
 * @param {number[]} targetIds
 * @returns {number[]}
 */
function transferRemove(sourceHighlight, targetIds) {
	if (!sourceHighlight.length) {
		return (targetIds || []).slice();
	}
	var remove = {};
	for (var i = 0; i < sourceHighlight.length; i++) {
		remove[sourceHighlight[i]] = true;
	}
	return (targetIds || []).filter(function (id) {
		return !remove[id];
	});
}

/**
 * Range-select indices between anchor and index (inclusive).
 * @param {number} anchor
 * @param {number} index
 * @param {number} rowCount
 * @returns {number[]}
 */
function rangeSelectIndices(anchor, index, rowCount) {
	if (anchor < 0 || index < 0 || rowCount <= 0) {
		return index >= 0 ? [index] : [];
	}
	var lo = Math.min(anchor, index);
	var hi = Math.max(anchor, index);
	var out = [];
	for (var i = lo; i <= hi && i < rowCount; i++) {
		out.push(i);
	}
	return out;
}

function highlightedIds(map) {
	var out = [];
	for (var k in map) {
		if (Object.prototype.hasOwnProperty.call(map, k)) {
			out.push(Number(k));
		}
	}
	return out;
}

/**
 * @param {object} opts
 * @param {HTMLElement} opts.availablePanel
 * @param {HTMLElement} opts.includedPanel
 * @param {HTMLElement} opts.addBtn
 * @param {HTMLElement} opts.removeBtn
 * @param {HTMLElement} [opts.addAllBtn]
 * @param {HTMLElement} [opts.removeAllBtn]
 * @param {function(string): object[]} opts.listAvailableRegions
 * @param {function(number): object|null} opts.getRegionById
 * @param {function(object): object} [opts.rowStyle]
 * @param {function(number[]): void} [opts.onIncludedChange]
 * @param {number[]} [opts.initialIncludedIds]
 */
function createRegionDualList(opts) {
	opts = opts || {};
	var availablePanel = opts.availablePanel;
	var includedPanel = opts.includedPanel;
	var listAvailableRegions = opts.listAvailableRegions;
	var getRegionById = opts.getRegionById;
	var rowStyle = opts.rowStyle;
	var onIncludedChange = opts.onIncludedChange;

	var includedIds = (opts.initialIncludedIds || []).slice();
	var searchQuery = "";
	var availableHighlight = {};
	var includedHighlight = {};
	var lastAvailableIndex = -1;
	var lastIncludedIndex = -1;
	var visibleAvailableIds = [];

	function includedSet() {
		var out = {};
		for (var i = 0; i < includedIds.length; i++) {
			out[includedIds[i]] = true;
		}
		return out;
	}

	function notify() {
		if (typeof onIncludedChange === "function") {
			onIncludedChange(includedIds.slice());
		}
	}

	function regionLabel(node) {
		return node.acronym + " — " + node.name;
	}

	function paintRow(row, node, selected) {
		row.className = "region-picker-row";
		row.setAttribute("role", "option");
		row.setAttribute("data-id", String(node.id));
		if (selected) {
			row.classList.add("selected-row");
		}
		if (rowStyle) {
			var style = rowStyle(node);
			row.style.borderLeftColor = style.borderLeftColor || "";
			row.style.backgroundColor = style.backgroundColor || "";
			if (style.swatchColor) {
				var sw = document.createElement("span");
				sw.className = "region-swatch";
				sw.style.backgroundColor = style.swatchColor;
				row.appendChild(sw);
			}
		}
		row.appendChild(document.createTextNode(regionLabel(node)));
	}

	function renderAvailable() {
		if (!availablePanel || !listAvailableRegions) {
			return;
		}
		var regions = listAvailableRegions(searchQuery);
		var taken = includedSet();
		availablePanel.innerHTML = "";
		visibleAvailableIds = [];
		for (var r = 0; r < regions.length; r++) {
			var node = regions[r];
			if (taken[node.id]) {
				continue;
			}
			visibleAvailableIds.push(node.id);
			var row = document.createElement("div");
			paintRow(row, node, !!availableHighlight[node.id]);
			(function (idx, rid) {
				row.addEventListener("click", function (ev) {
					if (ev.shiftKey && lastAvailableIndex >= 0) {
						var indices = rangeSelectIndices(
							lastAvailableIndex,
							idx,
							visibleAvailableIds.length,
						);
						for (var i = 0; i < indices.length; i++) {
							availableHighlight[visibleAvailableIds[indices[i]]] = true;
						}
					} else if (ev.metaKey || ev.ctrlKey) {
						if (availableHighlight[rid]) {
							delete availableHighlight[rid];
						} else {
							availableHighlight[rid] = true;
						}
						lastAvailableIndex = idx;
					} else {
						availableHighlight = {};
						availableHighlight[rid] = true;
						lastAvailableIndex = idx;
					}
					renderAvailable();
				});
			})(visibleAvailableIds.length - 1, node.id);
			availablePanel.appendChild(row);
		}
	}

	function renderIncluded() {
		if (!includedPanel) {
			return;
		}
		includedPanel.innerHTML = "";
		for (var i = 0; i < includedIds.length; i++) {
			var id = includedIds[i];
			var node = getRegionById(id);
			if (!node) {
				continue;
			}
			var row = document.createElement("div");
			paintRow(row, node, !!includedHighlight[id]);
			(function (idx, rid) {
				row.addEventListener("click", function (ev) {
					if (ev.shiftKey && lastIncludedIndex >= 0) {
						var indices = rangeSelectIndices(
							lastIncludedIndex,
							idx,
							includedIds.length,
						);
						for (var j = 0; j < indices.length; j++) {
							includedHighlight[includedIds[indices[j]]] = true;
						}
					} else if (ev.metaKey || ev.ctrlKey) {
						if (includedHighlight[rid]) {
							delete includedHighlight[rid];
						} else {
							includedHighlight[rid] = true;
						}
						lastIncludedIndex = idx;
					} else {
						includedHighlight = {};
						includedHighlight[rid] = true;
						lastIncludedIndex = idx;
					}
					renderIncluded();
				});
			})(i, id);
			includedPanel.appendChild(row);
		}
	}

	function render() {
		renderAvailable();
		renderIncluded();
	}

	if (opts.addBtn) {
		opts.addBtn.addEventListener("click", function () {
			includedIds = transferAdd(highlightedIds(availableHighlight), includedIds);
			availableHighlight = {};
			lastAvailableIndex = -1;
			notify();
			render();
		});
	}
	if (opts.removeBtn) {
		opts.removeBtn.addEventListener("click", function () {
			includedIds = transferRemove(highlightedIds(includedHighlight), includedIds);
			includedHighlight = {};
			lastIncludedIndex = -1;
			notify();
			render();
		});
	}
	if (opts.addAllBtn) {
		opts.addAllBtn.addEventListener("click", function () {
			var regions = listAvailableRegions(searchQuery);
			for (var i = 0; i < regions.length; i++) {
				includedIds = transferAdd([regions[i].id], includedIds);
			}
			availableHighlight = {};
			notify();
			render();
		});
	}
	if (opts.removeAllBtn) {
		opts.removeAllBtn.addEventListener("click", function () {
			includedIds = [];
			includedHighlight = {};
			notify();
			render();
		});
	}

	render();

	return {
		render: render,
		setSearchQuery: function (q) {
			searchQuery = q || "";
			renderAvailable();
		},
		setIncludedIds: function (ids) {
			includedIds = (ids || []).slice();
			availableHighlight = {};
			includedHighlight = {};
			render();
		},
		getIncludedIds: function () {
			return includedIds.slice();
		},
	};
}

module.exports = {
	createRegionDualList: createRegionDualList,
	transferAdd: transferAdd,
	transferRemove: transferRemove,
	rangeSelectIndices: rangeSelectIndices,
};
