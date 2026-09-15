"use strict";

/**
 * @param {Array<{ label: string, href?: string }>} steps
 * @param {HTMLElement|string} container
 */
function renderTrail(steps, container) {
	var el =
		typeof container === "string"
			? document.getElementById(container)
			: container;
	if (!el || !steps || !steps.length) {
		return;
	}
	el.innerHTML = "";
	el.classList.add("nav-trail");
	for (var i = 0; i < steps.length; i++) {
		var step = steps[i];
		if (i > 0) {
			var sep = document.createElement("span");
			sep.className = "nav-trail-sep";
			sep.setAttribute("aria-hidden", "true");
			sep.textContent = " → ";
			el.appendChild(sep);
		}
		if (i < steps.length - 1 && step.href) {
			var link = document.createElement("a");
			link.href = step.href;
			link.textContent = step.label;
			el.appendChild(link);
		} else {
			var current = document.createElement("span");
			current.className = "nav-trail-current";
			current.textContent = step.label;
			el.appendChild(current);
		}
	}
}

module.exports = {
	renderTrail: renderTrail,
};
