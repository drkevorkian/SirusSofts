<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED', true);
require dirname(__DIR__) . '/app/core.php';
require dirname(__DIR__) . '/app/site.php';
require dirname(__DIR__) . '/app/admin.php';

function catalogCheck(bool $ok, string $msg): void
{
    if (!$ok) { throw new RuntimeException($msg); }
}
$names = [
    'drkevorkian/Gentoo_InstallerV3',
    'drkevorkian/Gentoo-Installer',
    'drkevorkian/Gentoo-Installer-2',
    'drkevorkian/AI_Bridge',
    'drkevorkian/AI_BridgeV-2',
    'drkevorkian/WorldCom',
    'drkevorkian/Sentinal_trader',
];
$records = array_map(static fn($name) => ['full_name' => $name,
    'site_visibility' => 'private', 'hosting_requested' => 0], $names);
$grouped = cms_github_grouped_projects($records);
catalogCheck(count($grouped) === 4, 'Expected four unrelated families');
$gentoo = array_values(array_filter($grouped, static fn($x) => $x['key'] === 'drkevorkian/gentoo-installer'));
catalogCheck(count($gentoo) === 1, 'Gentoo Installer family missing');
catalogCheck(array_column($gentoo[0]['versions'], 'version_label') ===
    ['Original / unversioned', 'v2', 'v3'], 'Incorrect version order');
$bridge = array_values(array_filter($grouped, static fn($x) => $x['key'] === 'drkevorkian/ai-bridge'));
catalogCheck(count($bridge) === 1 && count($bridge[0]['versions']) === 2, 'AI Bridge family');
catalogCheck(cms_github_project_identity('a/foo-2026')['family_key'] === 'a/foo-2026',
    'Year suffix must remain distinct');
catalogCheck(cms_github_project_identity('a/.528')['family_key'] === 'a/528',
    'Non-version dot prefix');
try {
    cms_github_project_identity('bad/../path');
    throw new RuntimeException('Invalid repository accepted');
} catch (InvalidArgumentException $expected) {}
$crossOwner = cms_github_grouped_projects([
    ['full_name' => 'person-a/Gentoo-Installer'], ['full_name' => 'person-b/Gentoo-Installer-2'],
]);
catalogCheck(count($crossOwner) === 2, 'Owner boundary violated');
if (!extension_loaded('pdo_sqlite')) {
    fwrite(STDERR, "pdo_sqlite required for catalog CMS integration\n");
    exit(2);
}
$dir = sys_get_temp_dir() . '/sirus-catalog-' . bin2hex(random_bytes(8));
if (!mkdir($dir, 0700)) { throw new RuntimeException('mkdir failed'); }
try {
    $_SERVER['HTTP_HOST'] = 'sirussofts.org';
    $_SERVER['REMOTE_ADDR'] = '127.0.0.1';
    $_SESSION = [];
    try {
        cms_github_catalog_records();
        throw new RuntimeException('Public catalog metadata access accepted');
    } catch (RuntimeException $expected) {
        catalogCheck(str_contains($expected->getMessage(), 'Sign in required'), 'Unauthorized catalog read');
    }
    $_SESSION['cms_admin_id'] = 1;
    $_SESSION['cms_username'] = 'admin';
    $path = $dir . '/catalog.sqlite';
    $conn = new PDO('sqlite:' . $path, null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    $conn->exec('CREATE TABLE projects (
        full_name TEXT PRIMARY KEY, github_private INTEGER, site_visibility TEXT,
        description TEXT, branch TEXT, archived INTEGER, html_url TEXT,
        sync_status TEXT, hosting_requested INTEGER, checked TEXT)');
    $stmt = $conn->prepare('INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?)');
    $stmt->execute(['drkevorkian/AI_Bridge',1,'private','secret project','main',0,
        'https://github.com/drkevorkian/AI_Bridge','synced',0,'2026-11-08T00:00:00Z']);
    $stmt->execute(['drkevorkian/AI_BridgeV-2',1,'private','secret v2','main',0,
        'https://github.com/drkevorkian/AI_BridgeV-2','synced',0,'2026-11-08T00:00:00Z']);
    $conn = null;
    putenv('SIRUS_GITHUB_CATALOG_FILE=' . $path);
    $catalog = cms_github_catalog_records();
    catalogCheck($catalog['state'] === 'Available (read-only)', 'Catalog inaccessible');
    catalogCheck(count($catalog['groups']) === 1, 'Catalog family grouping');
    $db = new PDO('sqlite::memory:', null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    cms_schema($db);
    ob_start();
    cms_github_projects_page($db);
    $html = ob_get_clean();
    catalogCheck(str_contains($html, 'Ai Bridge') && str_contains($html, 'v2'),
        'Family and versions not rendered in CMS');
    catalogCheck(str_contains($html, 'private'), 'Private visibility not preserved');
    catalogCheck(!str_contains($html, 'secret project') && !str_contains($html, 'secret v2'),
        'Private description must not be exposed in admin view');
    echo "PASS: PHP catalog family grouping parity, private cached metadata and admin view\n";
} finally {
    putenv('SIRUS_GITHUB_CATALOG_FILE');
    foreach (glob($dir . '/*') ?: [] as $item) { @unlink($item); }
    @rmdir($dir);
}
