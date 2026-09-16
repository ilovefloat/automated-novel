# automated-novel Codex instructions

## Role

You are the maintenance agent for this repository. The Gemini API generates the novel; your job is to maintain the generation engine, prompts, validation logic, state machinery, GitHub Actions, and GitHub Pages publishing infrastructure.

## Priorities

1. Preserve the core invariant: the text the reader sees is exactly the manuscript being written by the person inside the story. Do not introduce an outer narrator or a separate nested novel.
2. Preserve canon discipline: facts, beliefs, hypotheses, and unresolved questions must remain distinct.
3. Preserve deterministic repository mechanics: failed generation must not publish an episode or advance story state.
4. Keep generation resilient to transient Gemini API failures and model availability changes.
5. Keep generated episodes and the story itself intact unless a task explicitly authorizes content repair.

## Maintenance policy

- Inspect the current repository and recent generated episodes before changing anything.
- Treat `docs/episodes/*.md` and `state/story_state.json` as generated story data, not as code to rewrite casually.
- Do not change or delete published episode text during routine engine maintenance.
- Prefer fixing the engine, prompts, validators, state extraction, or workflows when a problem is caused by the generation system.
- If an existing episode exposes an engine defect, document the defect and fix the engine so future episodes do not repeat it. Only repair historical story data when it is demonstrably corrupt or inconsistent with the repository's own invariants.
- Do not add Novelpia functionality or alter Novelpia publishing unless explicitly requested.
- Do not add unnecessary README status/history prose.

## Required verification

After changes:

- Run `python -m py_compile scripts/*.py`.
- Run repository validation scripts that exist and are relevant.
- Inspect the diff and make sure no generated episode was changed unintentionally.
- If a workflow changed, validate its YAML structure as far as the available tools permit.
- If generation logic changed, exercise the affected path without publishing a new episode when possible.

## Change policy

Make the smallest robust change that fixes the identified problem. Do not refactor working code merely for style. If no meaningful problem is found, leave the working tree unchanged.
