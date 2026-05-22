# nueip-mcp

Public personal-use MCP server. **No internal references** (company names,
employee-ID formats specific to one company, etc.) — use placeholders like
`<your-company-code>`, `<your-employee-id>` in docs / install scripts.

## Version sync

- `pyproject.toml` → `version`
- After bumping: `uv lock` to regenerate `uv.lock` (it pins `nueip-mcp` itself)

## Companion repo

Claude Code skill wrapper lives in [`agent-skills-pub`](https://github.com/tankfinal/agent-skills-pub)
(local: `~/CursorProjects/agent-skills-pub`). Tool names exposed here as
`mcp__nueip__*` are consumed by that skill.

## Release workflow

`gh release create vX.Y.Z -R tankfinal/nueip-mcp --target main --latest --title "..." -F -`.
Gotcha: `git push origin --delete <tag>` deletes the tag ref but leaves the
GitHub Release page (with old notes) — also run `gh release delete <tag>`.

## What this server depends on (for breakage triage)

Reverse-engineered XHR endpoints in `src/nueip_mcp/client.py`. NUEiP schema
changes are the most common break source — `*_URL` constants + `FE_PNO` table
at the top of `client.py` are the usual edit points.
