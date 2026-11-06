# PERSONAL domain

Personal projects, hosted on **GitHub** (`gh` CLI).

## Standard project shape
- `docker-compose.yml` is the baseline for running locally (one service per container,
  named volumes, healthchecks, no secrets in the file; values come from `.env`, documented in
  `.env.example`).
- A multi-stage `Dockerfile` with a non-root user and pinned base images.
- GitHub Actions: build the Docker image on PRs, then build and push to GHCR
  (`ghcr.io/<owner>/<repo>`) on `main` and on `v*` tags, using `docker/build-push-action`.
  Pin actions to major versions or SHAs, and grant minimal `permissions:`.
- When creating a new project, scaffold these pieces unless told otherwise.

@.claude/LEARNINGS.md
