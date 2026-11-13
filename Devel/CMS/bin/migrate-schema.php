<?php
declare(strict_types=1);
/**
 * Offline idempotent CMS schema upgrader. Execute as the restricted sirusweb
 * account, never as root. No publication, project deployment, or data deletion.
 */
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
define('SIRUS_CMS_BOOTSTRAPPED', true);
require dirname(__DIR__) . '/app/core.php';
require dirname(__DIR__) . '/app/source_releases.php';
try {
    $db = cms_db();
    cms_schema($db);
    // The root-owned release controller checks for these tables during its
    // read-only plan. They must exist immediately after a CMS code upgrade,
    // not depend on an administrator visiting /admin/updates first.
    cms_release_schema($db);
    echo "PASS: CMS and Release Planner schemas are current; existing data retained.\n";
} catch (Throwable $error) {
    error_log('CMS schema upgrade failed: ' . get_class($error));
    fwrite(STDERR, "FAIL: CMS schema upgrade failed; database unchanged if transaction rolled back.\n");
    exit(1);
}
