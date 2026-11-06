# WORK domain

Projects for work. Their remotes are on the **corporate GitLab**. Everything in
this file is committed to a personal GitHub repo, so **company-specific details belong in
`.claude/COMPANY.local.md`** (gitignored) and nowhere else.

## Rules
- Use `glab` for merge requests, issues and pipelines. Never use `gh` here.
- Never push work code, logs or data to personal GitHub, gists or public pastebins.
- Never add personal accounts, tokens or MCP servers to work projects.
- Follow each project's own CONTRIBUTING / MR template and branch naming.
- CI is `.gitlab-ci.yml`. Review its `rules:`, protected variables and runners carefully.

## Reviewers
The two mandatory reviewers apply (see the workspace CLAUDE.md). Also check licence and
compliance where the company requires it (details in COMPANY.local.md).

@.claude/COMPANY.local.md
@.claude/LEARNINGS.md
