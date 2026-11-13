# Development — SirusSofts

## The code is HERE: [Devel/](https://github.com/drkevorkian/SirusSofts/tree/devel/Devel)

This repository has **one production branch: [main](https://github.com/drkevorkian/SirusSofts/tree/main)**.

- **Production code:** `main/CMS/`, `main/gentoo-node/`, `main/deploy/`, etc.
- **Development copy:** `devel/Devel/CMS/`, `devel/Devel/gentoo-node/`, `devel/Devel/deploy/`, etc.
- `Devel/` is an **actual copy of main's complete tracked tree**, not a pointer, website route, symlink, or hidden checkout.
- Changes under `Devel/` do **not** affect the website or the production Ubuntu/Gentoo installations. Never configure the site to deploy from `devel`.
- All development edits belong inside `Devel/`. The branch root (outside `Devel/`) is only the copied baseline and the development test workflow; **do not develop against that root**.

## The release rule

1. Work in `devel` under the visible `Devel/` folder only.
2. Push the development commit. GitHub runs **Devel - complete mirrored SirusSofts test suite** with its working directory set to `Devel/` and the same full PHP/Python/JavaScript/Nginx tests as production. Fix failures in `Devel/`, rerun, and require **zero failing checks**.
3. After successful testing and release readiness, use **[Actions → Publish tested Devel to main](https://github.com/drkevorkian/SirusSofts/actions/workflows/promote-devel.yml)**. Its production-side script refuses to proceed unless the **latest `devel` commit** has passed its full test suite. It copies **only the changed project files** from `Devel/` to the same paths in `main` (e.g. `Devel/CMS/index.php` → `main/CMS/index.php`), never the `Devel/` directory itself.
4. Production changes are a fast-forward **push to `main`**, not a PR left indefinitely on a side branch. A concurrently modified production file stops promotion rather than overwriting it.
5. Ubuntu/Gentoo continue pulling and deploying from `main` using their existing release process. Publishing in GitHub is distinct from installing on the hosts.

This release action is explicitly triggered **after** testing, not on every experimental commit, so an incomplete experiment that happens to pass static tests cannot silently change production. The assistant is responsible for performing the release when the change is ready.

## Finding the code

- [Full copied website and CMS](Devel/CMS/)
- [Copied Gentoo node](Devel/gentoo-node/)
- [Copied Ubuntu deployment scripts](Devel/deploy/)
- [Copied test suites and workflow definitions](Devel/.github/workflows/)

The root-level `DEVELOPMENT.md` is intentionally outside the mirror so that the `Devel/` copy can remain identical to `main` at initialization.
