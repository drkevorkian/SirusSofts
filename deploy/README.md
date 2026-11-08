# SirusSofts Deployment API — 0.1 staging

**Status:** Source committed on feature/webhook-deployer-v0.1. NOT installed on the IONOS server and NOT connected to GitHub webhooks yet.

This is a small, Python-standard-library GitHub webhook receiver for the SirusSofts Ubuntu VPS. It does not require the Plesk trial. It accepts verified GitHub push notifications, queues updates, fetches the target commit from the configured repository, checks the publish directory, PHP-lints code when PHP is present, and atomically changes a per-project current symlink.

## Design

ChatGPT edits GitHub -> GitHub sends signed push webhook -> HTTPS reverse proxy -> Sirus deploy agent on 127.0.0.1:8787 -> secure repo allowlist -> read-only Git checkout -> isolated staged release -> current symlink switch.

No cron polling, no machine reboot, no arbitrary command endpoint, no automatic database migrations.

Independent project directories:
- /srv/sirussofts/sites/cms/releases/<SHA> and /srv/sirussofts/sites/cms/current
- /srv/sirussofts/sites/cpoint/releases/<SHA> and /srv/sirussofts/sites/cpoint/current

The web server can later map both the project subdomain and project path to the same current release. That routing is NOT installed by this version.

## Ubuntu setup

1. Inspect these files before running anything. This repository does not currently prove where the CMS files are located on your VPS.
2. From this repository's deploy directory on Ubuntu run: sudo bash install-ubuntu.sh
3. Edit /etc/sirusdeploy/projects.json. Set source_subdir to the exact directory containing files intended to be publicly served. Start with enabled=false. The example public and server/public paths are placeholders, not confirmed paths.
4. For each private GitHub repository, configure a distinct read-only deploy key at its configured ssh_key path and register its PUBLIC key with that repo under Settings > Deploy keys. Only the agent's service account should read the private key. Verify the GitHub SSH host key fingerprint before adding it to known_hosts.
5. If a repository is public, transport=https can read without a deploy key. Keep the deployment scope restricted to the configured repository and branch.
6. Set enabled=true in the config for a reviewed project. Check syntax with: python3 /opt/sirusdeploy/sirus_deploy.py check. Then restart ONLY the agent with: sudo systemctl restart sirus-deploy
7. Add nginx-location.example.conf to the EXISTING HTTPS server block for sirussofts.com, using Plesk Additional nginx directives if this VPS is Plesk-managed. Do not overwrite the existing website or generate a competing vhost. Test with nginx -t and gracefully reload nginx.
8. Under GitHub repository Settings > Webhooks, add the URL https://sirussofts.com/_deploy/github, use application/json, choose Just the push event, and enter the exact random secret stored at /etc/sirusdeploy/webhook-secret. Never commit or share this secret.
9. Push an approved code change to the configured branch. Watch journalctl -u sirus-deploy -f and run python3 /opt/sirusdeploy/sirus_deploy.py status as the service account for recent job statuses.

Your website's PHP-FPM/static vhosts must be configured to serve each project's current release. The agent intentionally does not rewrite nginx, Apache, DNS, SSL, or database settings. A PHP file placed under an incorrectly configured static site can expose source: do NOT publish PHP projects without the correct PHP handler.

## Security properties

- HMAC-SHA256 verification of the raw webhook request, per GitHub X-Hub-Signature-256.
- Exact allowlist for repository, branch and publish directory.
- Separate read-only Git deploy credentials; no user-supplied clone URL.
- No root account for the receiving service. No public listener on TCP 8787.
- Durable SQLite work queue, duplicate-delivery detection, skip stale push events.
- Reject symlinks and common secrets in published source tree.
- Validate PHP syntax before activation.
- Previous release directories remain for manual rollback.

Remember: an allowed GitHub repository is a trusted source of new deployed PHP code. Use branch protection/reviews if you want code approval before live activation.

## Future project catalog

The connected GitHub account has 20 repositories, but not all are web applications. The next version should automatically discover repository metadata and provide a project catalog. Each project will need an independent slug, visibility decision, runtime type, deployment path, and route. Private repositories should NOT be published by default.

GitHub ordinary webhooks are configured per repository. A GitHub App installation or per-repository hook configuration is necessary for account-wide push triggers. This implementation uses per-repository hooks for the explicit allowlist first.

## References

- GitHub HMAC signature verification: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
- GitHub webhook types: https://docs.github.com/en/webhooks/types-of-webhooks
- GitHub SSH fingerprints: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
