# GMD CO Processing
QGIS plugin for EA post-processing of GMD. Requires QGIS 3.22 or later.

## Install (with automatic updates)
The plugin has two release channels, each with its own plugin repository URL. Add **one** of them.

| Channel | Who it is for | Version looks like | Plugin repository URL |
|---|---|---|---|
| **Stable** | Everyone doing production work | `0.1.0` | **https://raw.githubusercontent.com/GMD-Repository/GMD_COProcessing/plugin-repo/plugins.xml** |
| **Beta** (pre-release) | Testers and developers trying the next version | `0.1.1-beta.1` | **https://raw.githubusercontent.com/GMD-Repository/GMD_COProcessing/plugin-repo-beta/plugins.xml** |

1. In QGIS open **Plugins > Manage and Install Plugins > Settings**.
2. For **Beta** only: tick **Show also experimental plugins**.
3. Click **Add…** and enter:
   - Name: `GMD CO Processing` (Stable) or `GMD CO Processing Beta` (Beta)
   - URL: the URL of your channel from the table above
4. Go to **All**, search for **GMD CO Processing** and click **Install**.

QGIS checks the URL and offers an **Upgrade** when a new version of that channel is released.
In the Plugin Manager a Beta build has the description prefix **[BETA]**.

To switch channels, remove the other repository in **Settings** (or untick **Enabled**), then
**Reload all repositories** and install from the channel you kept. Do not keep both enabled: QGIS
would always offer the highest version of the two.

## Use
A **GMD CO Processing** menu appears in the menu bar. The toolbar button opens the panel, where you
pick the EA layer and run the scripts in [gmd_processing_tools/](gmd_processing_tools/).
See [gmd_processing_tools/README.md](gmd_processing_tools/README.md) for writing scripts.

## Documentation
How to use each tool: **[docs/README.md](docs/README.md)** — one page per script in
[docs/tools/](docs/tools/) (EA MBI Checker, National-Level MBI Checker, EA vs Barangay Check,
MBI Coverage Selector, EARF Post-Processing).

## Teams and roles
All teams sit under the parent team **Contributors** in the `GMD-Repository` organization.

| Team | GitHub role on this repo | What they do |
|---|---|---|
| **Maintainer** (`psacjperez`) | Admin | Reviews and merges every pull request; the **only** one who can merge into `beta` and `main`, so the only one who can trigger a Beta or a Stable release. |
| **Contributors / Developers** | Write | Push their own branches and open pull requests into `beta`. Cannot push or merge into `beta` or `main`. |
| **Contributors / Testers** (testers / triage) | Triage | Install the **Beta** channel, test it and report problems as GitHub issues; label, assign and close issues. Cannot push code. |

## Branches

| Branch | What it holds | Who updates it | Release |
|---|---|---|---|
| `<your-name>` or `feature/<topic>` | One developer's work in progress | The developer | None |
| `beta` | Everything reviewed and merged, waiting to go stable | Maintainer only (merging PRs) | **Beta** pre-release `vX.Y.Z-beta.N` on every merge |
| `main` | The stable version | Maintainer only (merging `beta` → `main`) | **Stable** release `vX.Y.Z` on every merge |
| `plugin-repo`, `plugin-repo-beta` | The published zip + `plugins.xml` | The release workflow only | Never edit |

```
your branch ──PR──▶ beta ──(Beta pre-release v0.3.22-beta.1, -beta.2 …)──▶ testers
                     │
                     └──PR (maintainer)──▶ main ──(Stable release v0.3.22)──▶ everyone
```

## Contributing: commit and push step by step
For developers. Never commit to `beta` or `main` directly — GitHub rejects it.

1. **Get the latest `beta`** (first time: `git clone https://github.com/GMD-Repository/GMD_COProcessing.git GMD_COProcessing`
   inside `.../profiles/default/python/plugins/`):
   ```bash
   git checkout beta
   git pull origin beta
   ```
2. **Make your branch from `beta`** (once per task; reuse your own branch if you keep one, e.g. `cole`):
   ```bash
   git checkout -b feature/mbi-export-fix        # or: git checkout cole && git merge beta
   ```
3. **Change the code and test it in QGIS** (reload the plugin). Update the tool's Help tab and
   `docs/tools/<script>.md` when you change buttons, fields or outputs, and add a line to the
   `changelog` in `metadata.txt`.
4. **Commit** — small commits with a clear message:
   ```bash
   git status                      # check what changed
   git add gmd_processing_tools/mbi_coverage_selector.py docs/tools/mbi_coverage_selector.md
   git commit -m "fix: MBI Coverage Selector keeps maximized window on resume"
   ```
   Message prefixes: `feat:` new feature, `fix:` bug fix, `docs:` documentation only, `refactor:`, `chore:`.
5. **Push your branch**:
   ```bash
   git push -u origin feature/mbi-export-fix
   ```
6. **Open a pull request into `beta`** on GitHub (**Compare & pull request**, base: **`beta`**, never `main`).
   Fill in the checklist. The maintainer is added as reviewer automatically (`.github/CODEOWNERS`).
7. **Answer review comments** by committing and pushing to the same branch; the PR updates itself.
8. **The maintainer merges** the PR → a Beta pre-release is published automatically
   (e.g. `v0.3.22-beta.1`) to the Beta URL. Testers upgrade and test it.
9. After merging, update your branch before the next task: `git checkout beta && git pull origin beta`.

### Testers: report a problem
1. Use the **Beta** URL above and keep the plugin upgraded.
2. On GitHub open **Issues > New issue > Bug report**, choose channel **Beta**, give the version
   (Plugins > Installed), the tool, the steps and the Log text or a screenshot.
3. Triage: add labels, link duplicates, close issues fixed in a later Beta.

## Releases (maintainer only)
Releases are automatic (`.github/workflows/release.yml`). A merge that only changes `README.md`,
`docs/`, `.claude/` or the `.github` templates publishes nothing.

| Merge into | Version | Example | Published to |
|---|---|---|---|
| `beta` | next stable version + `-beta.N` | after `v0.3.21`: `v0.3.22-beta.1`, `v0.3.22-beta.2` … | GitHub **pre-release**, Beta URL (experimental) |
| `main` | latest stable + 1 patch | `v0.3.22` | GitHub **latest release**, Stable URL |

QGIS orders them correctly: `0.3.21` < `0.3.22-beta.1` < `0.3.22-beta.2` < `0.3.22`.

**Version reset:** the numbering starts again at **`0.1.0-beta.1`** (first Beta) and **`0.1.0`** (first Stable).
The earlier releases `v0.1.0` – `v0.3.20` are archived: their tags are renamed `archive/v…` and the releases are
titled **[Archived]**, so the workflow ignores them. Because QGIS never offers a lower version as an upgrade,
anyone who still has `0.3.x` installed must **uninstall GMD CO Processing once and install it again** from their
channel's URL.

**Stable release:** open a pull request **`beta` → `main`**, check the testers' issues are closed, and merge it.

**Bigger version jump** (e.g. `v0.4.0`): publish a release by hand on GitHub with that tag
(`v0.4.0`, or `v0.4.0-beta.1` with **Set as a pre-release** ticked for the Beta channel). The next
automatic release continues from it.

### One-time GitHub setup (maintainer)
1. **Create the `beta` branch** from `main`: `git checkout main && git pull && git checkout -b beta && git push -u origin beta`.
2. **Teams** — Organization **GMD-Repository > Teams > New team**: create **Contributors**, then the child
   teams **Developers** and **Testers** (parent: Contributors). Add the members.
3. **Team access** — repo **Settings > Collaborators and teams > Add teams**:
   Developers → **Write**, Testers → **Triage**. Give `psacjperez` **Admin**; lower anyone else who has Admin or Maintain.
4. **Branch rules** — repo **Settings > Rules > Rulesets > New branch ruleset**, name `protect beta and main`:
   - Enforcement: **Active**. Target branches: **Include by pattern** `beta` and `main`.
   - Bypass list: **add `psacjperez` only** (Always allow).
   - Tick **Restrict deletions**, **Block force pushes**, **Restrict updates** (no one but the bypass list can push *or merge*).
   - Tick **Require a pull request before merging**: 1 approval, **Require review from Code Owners**,
     **Dismiss stale approvals**, **Require conversation resolution**.
5. **Release branches** — a second ruleset `release branches` for `plugin-repo` and `plugin-repo-beta`:
   **Restrict deletions** only (the workflow force-pushes them, so do not block force pushes).
6. **Issues labels** — create `needs-triage`, `bug`, `enhancement` (used by the issue forms).

## Folder structure
The repo root is the plugin folder, so it can be cloned straight into the QGIS plugins folder for
development (`git clone ... GMD_COProcessing` inside `.../profiles/default/python/plugins/`), then
`git pull` and reload the plugin to update.
```
__init__.py  plugin.py  metadata.txt  icon.svg  LICENSE
gmd_processing_tools/        # scripts, icons, styles
ui/                          # panel
.github/                     # release workflow, CODEOWNERS, issue / PR templates (left out of the zip)
```
