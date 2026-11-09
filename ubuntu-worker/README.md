# Deprecated Ubuntu worker — wrong execution direction

**Do not install or enable** `sirus-ubuntu-worker.service`. Ubuntu IS the
website/coordinator, and Gentoo performs delegated work. This directory
is retained only to document the earlier prototype and avoid silently
removing prior source files. Its installer now refuses the `--install`
operation.

Use the standalone [Gentoo worker](../gentoo-node/README.md) instead.
The CMS stores work assignments, verifies signed Gentoo claims, and
receives results in `CMS/app/work_broker.php`. All actual delegated
computations run on Gentoo.
