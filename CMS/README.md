# SirusSofts CMS — drop-in website package

**This `CMS/` directory is THE installable CMS.** Its actual homepage entry point is
**[`CMS/index.php`](index.php)**. There is no `public/` child, no relocating
`app/` by hand, and no `deploy/cms/` duplicate.

The source is a PHP 8.1+ / SQLite CMS with a **public homepage**,
responsive dark-blue design, project directory, project landing pages,
ordinary content pages, and a protected `/admin` for projects, content,
site settings, password changes and the activity log.

## Package layout

```text
CMS/
  index.php              <-- PUBLIC HOMEPAGE / FRONT CONTROLLER
  api.php                <-- legacy Android upload compatibility
  .htaccess              <-- IMPORTANT: include hidden files in your upload
  assets/
    cms.css
    cms.js
  app/
    core.php              <-- internal runtime: direct HTTP execution blocked
    site.php
    admin.php
  bin/
    setup.php             <-- CLI-only first-run setup; NOT a web installer
    provision-subdomain.php  <-- CLI-only, dry-run-first Plesk helper
  tests/
    smoke.php             <-- CLI-only smoke test
  README.md
```

**Drag-and-drop means copy this entire directory structure together.**
Do not copy `index.php` without `app/` or `assets/`. There are two ways
to make the package your website root:

1. **Recommended:** open `CMS/`, select **all its contents including the
   hidden `.htaccess`**, and copy/drop them directly into
   `/var/www/vhosts/sirussofts.com/httpdocs/`. Then
   `httpdocs/index.php` is the public homepage.
2. **Keep the folder intact:** upload the **entire `CMS/` directory** into
   `httpdocs/CMS/`, then change the main domain's Plesk **Document root**
   from `httpdocs` to `httpdocs/CMS`. The webserver then treats
   `CMS/index.php` as `sirussofts.com/`. Simply uploading
   `httpdocs/CMS` without changing the document root does *not*
   replace the root homepage.

In both layouts, the CMS automatically uses one private SQLite database at
`/var/www/vhosts/sirussofts.com/private/sirus-cms/data/cms.sqlite3`.
It is **not contained in or served from `CMS/`**. Private PHP components
within `CMS/app/` have explicit direct-access guards, and Apache denies
requests for `app/`, `bin/`, `tests/`, `data/` and `private/`.
The `.htaccess` rules require Apache/Plesk support; adjust equivalent
nginx rules if hosting bypasses Apache.

## First install (required once)

**Your live root currently contains cPoint.** Do not overwrite that
working site or its SQLite database without completing the migration
below and taking a backup.

1. Back up existing `httpdocs` including dotfiles and the private cPoint
   SQLite database. For WAL SQLite databases use the SQLite `.backup`
   command or a proper consistent snapshot, not only a copy of the live
   main DB file.
2. Stage the **current** files from repository `deploy/web/` into a
   `cpoint/` directory inside **whichever folder is the CMS document
   root**. Copy the latest patched `deploy/web/lib.php`, preserving
   the old `setup-key.php` where required. The patched PHP computes
   the **original `httpdocs` hash** and therefore opens the existing
   `private/cpoint-<legacy-hash>/cpoint.sqlite3`; it must not
   silently create a new cPoint database.
3. Test `/cpoint/index.php` and `/cpoint/api.php?r=health`, ensuring
   existing cPoint clients and historical data remain visible. Keep
   the existing root `api.php` running during this staging phase.
4. Copy all **`CMS/` contents** into your selected document root as
   explained above. The new `CMS/api.php` is a small compatibility
   wrapper delegating to `cpoint/api.php`; switch the root
   `api.php` **only after** checking the relocated app and preserving
   its old Android upload endpoint.
5. **Root SSH access is fully supported.** From the actual CMS document
   root (whether that is `httpdocs` or `httpdocs/CMS`), run as root:
   ```bash
   php bin/setup.php
   ```
   The installer detects the hosting account from Plesk's `httpdocs`
   directory owner. It creates only the CMS's private storage with that
   account's ownership, then **drops its root privileges before creating
   SQLite or reading your new administrator password**. You do not need
   to log in separately as the Plesk site user.

   If Plesk's `httpdocs` folder is root-owned or its ownership cannot
   identify the PHP hosting account, run this instead, still from your
   root session:
   ```bash
   php bin/setup.php --web-user=YOUR_PLESK_SYSTEM_USER
   ```
   Find that username in Plesk's Hosting Settings (System user), or
   inspect it with `ls -ld /var/www/vhosts/sirussofts.com/httpdocs`.
   Do **not** use `root` as the website user or change permissions to
   `777`. The installer keeps the SQLite database and its directory
   restricted to the hosting user.

   Choose a unique CMS administrator password of at least 14 characters.
   This creates the private CMS SQLite database and a published cPoint
   project entry; it **does not touch** cPoint's database or phone token.
   It refuses to overwrite an existing CMS database. PHP CLI needs
   `pdo_sqlite` and the `posix` extension for privilege dropping.
   If the Plesk `private/` parent blocks the site user, the installer
   stops with a permissions message rather than creating a broken
   root-owned database. It is CLI-only by design.
6. Verify `https://sirussofts.com/` is a **public homepage**, and
   `https://sirussofts.com/admin` is a separate login. Confirm
   `/projects`, `/about`, public project pages, and the admin
   project/settings editors. Test old `/api.php?r=health` and the
   actual Android upload acknowledgement **before** considering the
   migration complete.

**Rollback:** restore the backed-up root `index.php`, `api.php`,
`.htaccess` and other root cPoint files. Keep the original private cPoint
database untouched. Preserve the new CMS DB separately for later use.

## The two project URL forms

For the seeded cPoint project, configure:

- `https://sirussofts.com/cpoint/` → your document-root `cpoint/`
  directory (same running app).
- `https://cpoint.sirussofts.com/` → **the same physical**
  `cpoint/` directory, configured as a subdomain in Plesk.

Every new CMS-only content project gets `/project-slug` immediately
after publication. The subdomain `project-slug.sirussofts.com` additionally
requires a DNS A/AAAA record, the appropriate Plesk virtual host / shared
CMS document root, and a valid TLS certificate.

Every separate executable project gets its own `project-slug/` directory
inside the same CMS document root. Configure that subdomain to point at
the **same app directory**. The CMS project form manages the listing and
content; it does **not** install executable applications, issue TLS
certificates, or create DNS records automatically.

The optional reviewed `bin/provision-subdomain.php` is CLI-only,
defaults to dry run, and needs explicit `--apply`/root terminal
confirmation. Check the displayed document root before executing.
Example (after creating the project in the admin UI):

```bash
php bin/provision-subdomain.php cpoint
sudo php bin/provision-subdomain.php cpoint --apply
```

Plesk DNS/certificates still need separate configuration.

## Security and limitations

- Public homepage and published projects are open to everyone.
  Only `/admin` needs the CMS login.
- **Root-only server access is supported for setup.** Run `php bin/setup.php`
  from root; it switches its own effective Linux UID/GID to the Plesk
  PHP site owner before SQLite creation. Root does not serve PHP requests.
- CMS and cPoint are **separate administrators**; no accidental SSO.
- The CMS database remains **outside the served document root**.
- PHP components inside `app/` reject direct requests; the bundled
  Apache rewrite rules deny internal directories.
- Passwords are hashed, administrator posts use CSRF tokens,
  logins are rate-limited, and changes are audited.
- Page/project content is escaped; this is not a browser-based PHP editor.
- No web-exposed Ubuntu shell, file command console, or unattended
  deployment is provided. Other Linux services are deployed independently.
- An app marked Draft in the CMS is only hidden from its *listing*:
  the actual app/API must be disabled separately if needed.
- No GitHub Actions, paid hosted runners or workflow files are used.

## Local checks

```bash
php -l CMS/index.php
php -l CMS/app/core.php
php -l CMS/app/site.php
php -l CMS/app/admin.php
php -l CMS/bin/setup.php
php -l CMS/bin/provision-subdomain.php
php CMS/tests/smoke.php
python -m unittest tests.test_sirus_cms_contract
```

The PHP smoke test runs route/escape checks; its in-memory database
checks additionally need `pdo_sqlite`. A real Plesk/Android upload
test is required after deployment. GitHub source changes do not alter
the live server automatically.

## Development workflow

**Repository source of truth:** `CMS/`. The old
`deploy/cms/public/` arrangement was removed. Update `CMS/`,
run local tests, keep `RECOVERY.md` current, and merge reviewed
changes into `main` for installation. Do **not** use GitHub Actions.
