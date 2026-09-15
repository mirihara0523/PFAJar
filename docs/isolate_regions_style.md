# Isolate Regions — region picker style guide

## Hierarchy picker

The **Hierarchy** dropdown defaults to curated semantic tiers, with **Advanced — show CCFv3 raw depths** to fall back to the Allen Institute’s raw `st_level` 0–11 ontology. The same two-mode picker ships in the Isolate Regions wizard ([`pages/intensity_wizard.html`](../pages/intensity_wizard.html) + [`js/intensity_wizard.js`](../js/intensity_wizard.js)) and the Viewer/Editor paint controls ([`py/adjust.py`](../py/adjust.py)).

### Semantic tiers (default)

Source of truth lives in `TIER_DEFS` in [`js/structure_catalog.js`](../js/structure_catalog.js) and [`py/structure_catalog.py`](../py/structure_catalog.py). Tiers are evaluated data-driven so future CCF updates do not need acronym hardcoding.

| Tier id | UI label | Rule (over CCFv3 `st_level`) | Examples |
|---------|----------|------------------------------|----------|
| `major` | Major divisions | `st_level == 2` | CH, BS, CB |
| `regions` | Classic regions | `st_level == 5` | Isocortex, HPF, OLF, TH, HY, MB, MY, P |
| `areas` | Functional areas (**default**) | `st_level == 6` | VIS, AUD, SS, MO, ACA, ORB |
| `subareas` | Sub-areas | `st_level == 8` AND `"layer" not in name` | VISp, VISal, SSp-bfd, RSP, individual nuclei |
| `parts` | Area parts | Non-layer node with ≥1 direct child that is a layer (`st_level == 11` or `"layer" in name`) | VISp, RSPagl, RSPd, RSPv, AUDp, SSp-bfd |
| `layers` | Cortical layers | `st_level == 11` OR `"layer" in name` | VISp1, VISp2/3, ACA6a |

**Area parts rollup:** laminar IDs map to the nearest non-layer ancestor (`VISp4`→`VISp`, `RSPv2/3`→`RSPv`); already-non-layer IDs stay unchanged. This is graph-based (not a fixed `st_level`), so it works across uneven CCFv3 branches.

### Advanced (`Advanced — show CCFv3 raw depths`)

Toggle replaces the tier dropdown with `listCcfLevels(catalog)` / `list_ccf_levels(catalog)`, each row formatted by `formatCcfLevelLabel` / `format_ccf_level_label` as **`Level N — count kind (acronym, acronym, …)`** (kind from a simple count + layer-share heuristic — `layers`, `single structure`, `major divisions`, `divisions`, `regions`). Same template both sides, so PyQt and Electron show identical labels.

Advanced help copy (rendered as small italic text under the dropdown when on, in both toolsets):

> Allen Institute CCFv3 ontology depths (st_level 0–11). Some depths group structures that are not anatomically meaningful (e.g. Level 4 contains only Cortical plate). Use the standard tiers above for everyday region picking.

**On-disk parcellation vs picker tier:** The hierarchy dropdown controls which atlas IDs you *select* for output. If the active align run was parcellated (metadata in `{align_leaf}/.masonjar/annotation_parcellation.json`), [`py/annotation_match.py`](../py/annotation_match.py) rolls those selections to match annotation labels at run time — the picker tier is informational; banners on the setup and wizard pages show the applied parcellation level.

### Mode persistence

| Toolset | Persistence |
|---------|-------------|
| Isolate Regions wizard | `sessionStorage["masonjar.ccfPickerMode"]` = `"tiers"` \| `"advanced"` |
| Viewer/Editor | In-memory only (no QSettings; resets per launch) |

`selected_region_ids` are atlas IDs (independent of picker mode), so toggling modes or swapping tiers never clears the selection.

## Grouping level

Parent-area colors use a fixed CCF depth: **`GROUP_STYLE_LEVEL = 6`** (major area, e.g. VIS, SS, AUD, RSP). Implemented in [`js/atlas_region_style.js`](../js/atlas_region_style.js). Changing this constant is a product decision, not per-user.

For each structure row, `groupParentForRegion()` walks `id_path` and picks the ancestor at level 6; if none exists, the nearest shallower ancestor is used.

## Color source

The swatch and row tint use the group parent’s Allen **`color_hex_triplet`** from [`csv/structure_graph.json`](../csv/structure_graph.json). Colors are not assigned randomly per session.

## List rows

| Element | Rule |
|---------|------|
| Row | `border-left: 4px solid` group color (darkened if luminance > 0.72) |
| Background | `color-mix(in srgb, {groupColor} 12%, var(--mj-surface))` |
| Acronym | Small swatch before label |

Layer structures at fine depth inherit the **same** group parent tint as their area parent (e.g. `VISp2/3` matches the VIS family).

## Legend

Shows distinct group parents among **visible** available + selected rows. Search and depth filters change visibility only, not colors.

## Theme

Do not recolor Mason Jar primary/secondary buttons. Tinting applies only to the region picker lists and legend on [`pages/intensity_wizard.html`](../pages/intensity_wizard.html).

## Extension

To support custom ontologies, load a different graph JSON and rebuild [`js/structure_catalog.js`](../js/structure_catalog.js); keep `GROUP_STYLE_LEVEL` documented here.
