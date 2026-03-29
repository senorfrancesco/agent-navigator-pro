# Operator UI Sidebar and Launch Split Button Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the sidebar into a clearer operator rail and replace the topbar's single-path launch button with a split-button that can immediately launch any available runtime path.

**Architecture:** Keep the current backend-served shell and extend only the static HTML/CSS plus the existing frontend state/render layer in `prototype/operator-ui/app.js`. The topbar launch control becomes a split-button: the primary action launches the currently selected runtime path, and the chevron opens a menu whose items launch paths immediately on click.

**Tech Stack:** Static HTML, vanilla JavaScript, CSS, FastAPI-backed operator state.

---

### Task 1: Add plan-aware topbar markup

**Files:**
- Modify: `prototype/operator-ui/index.html`

**Step 1: Add split-button markup**

- Wrap the topbar launch CTA in a dedicated `launch-split-button` container.
- Keep `#topbar-start-button` as the primary launch action.
- Add `#topbar-start-toggle` and `#topbar-launch-menu`.

**Step 2: Improve sidebar structure**

- Wrap section navigation in a dedicated sidebar card with a visible label.
- Keep existing system/status/settings controls grouped in the lower operator rail.

### Task 2: Implement split-button behavior

**Files:**
- Modify: `prototype/operator-ui/app.js`

**Step 1: Render launch menu from `runtimePaths`**

- Populate menu items dynamically.
- Show path title, short note, and availability state.
- Disable unavailable actions.

**Step 2: Wire immediate launch**

- Primary button launches the currently selected path.
- Menu items set `selectedLaunchPath` and run the mapped operator action immediately.
- If the selected path is unavailable, open the reason modal instead of attempting launch.

**Step 3: Add menu open/close behavior**

- Toggle via chevron button.
- Close on outside click and `Escape`.
- Keep menu state in sync with rerenders and language switches.

### Task 3: Polish sidebar and launch controls visually

**Files:**
- Modify: `prototype/operator-ui/styles.css`

**Step 1: Strengthen sidebar as operator rail**

- Give navigation its own card treatment.
- Improve spacing and hierarchy between nav, summary cards, and control cluster.
- Slightly rebalance sidebar width if needed.

**Step 2: Style split-button and menu**

- Make the primary CTA and chevron feel like a single control.
- Style the floating menu to match the existing dark operator shell.
- Ensure responsive behavior on narrow widths.

### Task 4: Localize and verify

**Files:**
- Modify: `prototype/operator-ui/app.js`
- Modify: `TASKS.md`

**Step 1: Localize new labels**

- Add RU/EN labels for the new sidebar nav label and split-button strings.

**Step 2: Verify**

Run:
- `node --check prototype/operator-ui/app.js`
- `git diff --check -- prototype/operator-ui/index.html prototype/operator-ui/app.js prototype/operator-ui/styles.css TASKS.md`

Expected:
- No syntax errors.
- No malformed HTML/CSS hunks.

**Step 3: Record progress**

- Add a short progress note to `TASKS.md` under the existing operator UI polish track.
