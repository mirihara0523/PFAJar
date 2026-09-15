# Commit and release messages (human-facing)

[`scripts/publish-release.js`](../scripts/publish-release.js) reads **only** [`RELEASE_NOTES.md`](RELEASE_NOTES.md).

**Everything pushed to GitHub is public-facing** (commits, tags, prereleases, release notes). Write for lab users / the public; do not put secrets, local NAS project paths, or one-off repair scratch in the repo.

---

## Git commits

**Subject** (≤72 characters): what changed for someone using the app. Imperative mood. **No** `v2.4.x:` prefix—the git tag carries the version.

**Body** (optional, 2–4 sentences): what was broken or missing, what you did, who benefits. Plain language.

### Bad

```
v2.4.9: fix CZI multi-Z signal preview numpy truthiness
```

```
Fix cumulative orient geometry preview with ordered ops.

Per-slice geometry.ops records click order; DOMMatrix preview matches apply_geometry.py on disk.
```

### Good

```
Orient preview keeps rotate and flip steps together

Rotating a slice and then flipping it no longer looked like the rotation was undone. The CZI import wizard and Orient slices from the menu now show the same combined preview you get after Apply geometry.
```

Before committing a release, run:

```bash
node scripts/release-message.js
```

Copy the suggested subject and body, or write your own following the same tone.

---

## GitHub release notes (`docs/RELEASE_NOTES.md`)

Add a section **before** `node scripts/publish-release.js`:

```markdown
## v2.4.11

**What's new**

Two or three sentences a lab member can read without opening the repo. Describe what they can do differently in Mason Jar (tool names from the UI are fine).

**Changes**

- Short bullet list of user-visible changes (optional but helpful).
- Still no file paths, test names, or “for agents” instructions.

**Commit subject** (optional)

One-line git commit subject; `release-message.js` uses this if present.

**Commit body** (optional)

Paragraph for the git commit body.
```

Newest version at the **top** of the file.

### Bad (What’s new)

> Cumulative orient geometry: per-slice `geometry.ops` with DOMMatrix preview WYSIWYG in `js/orient_geometry.js`…

### Good (What’s new)

> When you rotate a slice in Orient, then flip it, the preview keeps both steps instead of looking like the rotation was undone. The same behavior applies in the CZI import wizard and **Orient slices** from the start menu.

---

## Release checklist

1. Bump `version` in `package.json`.
2. Add **`docs/RELEASE_NOTES.md`** section for that version (human copy).
3. `node scripts/release-message.js` → commit with human subject/body.
4. `node scripts/build-release.js`
5. `git tag vX.Y.Z` → `node scripts/publish-release.js`
