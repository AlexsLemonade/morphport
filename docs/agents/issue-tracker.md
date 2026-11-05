# Issue tracker: Local Markdown

Agent-skill issues and specs for this repo live as markdown files in `.scratch/`.
The `.scratch/` directory is gitignored: these files are for local, individual use only, to plan and track one person's work, and are not shared with the team.

## Relationship to GitHub issues

The project's shared issue tracker is still GitHub Issues (`AlexsLemonade/morphport`).
GitHub issues should still be filed as needed, but a human must file them manually.

- Agents must **not** create, edit, comment on, label, or close GitHub issues.
  Reading GitHub issues (e.g. `gh issue view`) is fine.
- When a `.scratch/` ticket describes work that the team should know about or track, suggest filing a GitHub issue and offer a draft title and body for the human to file.
- When a `.scratch/` ticket corresponds to a GitHub issue, record it on a `GitHub: #<number>` line near the top of the file.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`, never a single combined tickets file
- Triage state is recorded as a `Status:` line near the top of each issue file (see `triage-labels.md` for the role strings)
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path.
The user will normally pass the path or the issue number directly.

## Wayfinding operations

Used by `/wayfinder`.
The **map** is a file with one **child** file per ticket.

- **Map**: `.scratch/<effort>/map.md` (the Notes / Decisions-so-far / Fog body).
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`, with the question in the body.
  A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a `Status:` line records `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top.
  A ticket is unblocked when every file it lists is `resolved`.
- **Frontier**: scan `.scratch/<effort>/issues/` for files that are open, unblocked, and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Status: resolved`, then append a context pointer (gist + link) to the map's Decisions-so-far in `map.md`.
