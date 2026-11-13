# SirusSofts Release Planner — review, approve, stage

The original Update Center includes read-only operating system package
simulations and a read-only GitHub source index. This addition introduces
the first **explicit source release approval workflow**.

## Operator workflow

1. In `/admin/updates`, run **Check GitHub source revisions**. The
   restricted GitHub indexer resolves the approved branch and indexes
   up to three **complete** immutable commit manifests. A historical
   revision that lacks required components is not offered.
2. Choose Ubuntu or Gentoo, a fixed SirusSofts component, and a revision
   from the indexed manifests. **Create release plan** captures the exact
   Git commit SHA, SHA-256 source digest and current installed digest.
3. Review the plan. Approval requires the signed-in administrator's
   current password and the exact phrase `APPROVE <12-char-commit>`.
   Approval is rejected if the plan exceeds one hour, its source digest
   no longer appears in the trusted index, Gentoo is offline, or its
   installed baseline changed.
4. Once approved, the next run of the credential-isolated GitHub source
   indexer downloads **only that fixed source file at the pinned commit**,
   verifies its SHA-256 against the approved manifest, and stores the
   bytes under the private `nodes/source_artifacts/` directory.
   It checks free space and refuses a hash mismatch. The WebSocket
   displays **Staged; not installed** when the private artifact is ready.
5. Canceling a plan prevents further staging. Existing staged source
   files remain private for audit and should be removed only by a
   separately reviewed retention process.

The website never receives the GitHub token. GitHub file contents are
not put in `public_html` or Gentoo's program directory, and no new
remote-command capability is created.

## Current deployable scope

- Ubuntu: selected CMS PHP/JS, Ubuntu job worker and WebSocket service
- Gentoo: signed node agent, dashboard and reverse tunnel
- Fixed host names and explicit component allowlist
- Live approval/staging state from the existing WebSocket
- Read-only, SHA-256-validated source staging

**No actual code replacement, package installation, privileged service
restart or rollback has been enabled by this PR.**

An approved plan is **not** a safe deployment authorization by itself.
The eventual root-owned Ubuntu and Gentoo executors must independently
re-check the immutable plan and installed baseline, verify staged bytes,
check disk space and backup/rollback prerequisites, update a versioned
release directory, provide health-check gates, and roll back if necessary.
The CMS/PHP and its existing unprivileged workers must never receive
arbitrary sudo/shell access.

## Deployment prerequisites

The sole production source branch is `main`. Test reviewed source changes before
committing them to `main`; the indexer and CMS both require that branch. The Ubuntu CMS upgrade remains the
existing guarded `08-upgrade-cms-hestia.sh --plan` / `--apply`
process. The source indexer is separately installed by
`12-install-github-source-index.sh` and needs its script updated using
the existing reviewed install workflow. The WebSocket executable is also
separate from the PHP CMS upgrade.

Use `php CMS/tests/source_release_test.php` and
`python3 -m unittest discover -s deploy/tests -p 'test_github_source_index.py' -v`
before deployment. Keep a known-good backup and verify both the public
website and Gentoo tunnel remain accessible afterward.
