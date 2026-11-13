<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED', true);
require dirname(__DIR__) . '/app/core.php';
require dirname(__DIR__) . '/app/site.php';
require dirname(__DIR__) . '/app/admin.php';
if (!extension_loaded('pdo_sqlite')) { fwrite(STDERR, "pdo_sqlite required\n"); exit(2); }
function hostingCheck(bool $okay, string $message): void {
    if (!$okay) { throw new RuntimeException($message); }
}
$db = new PDO('sqlite::memory:', null, null, [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
]);
$db->exec('PRAGMA foreign_keys=ON');
cms_schema($db);
cms_schema($db);
$now = cms_now();
$db->prepare("INSERT INTO cms_projects
    (slug,title,summary,body,category,icon,kind,state,sort_order,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)")->execute([
       'example', 'Sample', 'Unpublished test', '', 'Engineering', '*', 'app', 'draft', 10, $now, $now
    ]);
$_SESSION = [];
try {
    cms_hosting_save($db, 'example', []);
    throw new RuntimeException('Unauthenticated hosting update succeeded');
} catch (RuntimeException $expected) {
    hostingCheck(str_contains($expected->getMessage(), 'Sign in required'), 'Incorrect authentication result');
}
$_SESSION['cms_admin_id'] = 1;
$_SESSION['cms_username'] = 'operator';
$_SERVER['HTTP_HOST'] = 'sirussofts.org';
$_SERVER['REMOTE_ADDR'] = '127.0.0.1';
$input = [
    'assigned_node' => 'gentoo-home',
    'database_backend' => 'mariadb',
    'source_repository' => 'drkevorkian/Example',
    'source_ref' => 'release/v2',
    'release_policy' => 'manual-review',
];
$record = cms_hosting_save($db, 'example', $input);
hostingCheck($record['source_ref'] === 'release/v2', 'Hosting preferences persistence');
hostingCheck($record['assigned_node'] === 'gentoo-home', 'Assigned server persisted');
hostingCheck($db->query("SELECT state FROM cms_projects WHERE slug='example'")->fetchColumn() === 'draft',
    'Preferences must not publish a project');
hostingCheck($record['database_backend'] === 'mariadb', 'Database selection metadata');
foreach ([
    ['assigned_node' => 'evil;rm', 'database_backend' => 'sqlite', 'source_repository' => '', 'source_ref' => '', 'release_policy' => 'hold'],
    array_merge($input, ['source_ref' => '../../etc']),
    array_merge($input, ['source_repository' => 'bad/source/extra']),
    array_merge($input, ['database_backend' => 'oracle']),
    array_merge($input, ['release_policy' => 'auto-deploy']),
] as $bad) {
    try {
        cms_hosting_save($db, 'example', $bad);
        throw new RuntimeException('Invalid hosting config accepted');
    } catch (InvalidArgumentException $expected) {}
}
try {
    cms_hosting_save($db, 'cpoint', $input);
    throw new RuntimeException('Protected project accepted');
} catch (InvalidArgumentException $expected) {}
ob_start();
cms_vhost_page($db, null);
$html = ob_get_clean();
hostingCheck(str_contains($html, 'gentoo-home'), 'Assigned node not rendered');
hostingCheck(str_contains($html, 'drkevorkian/Example'), 'Source repository not rendered');
hostingCheck(str_contains($html, 'Save preferences'), 'Save preferences UI missing');
hostingCheck(str_contains($html, 'ROLLBACK example'), 'Rollback confirmation missing');
$db->exec("DELETE FROM cms_projects WHERE slug='example'");
hostingCheck($db->query('SELECT count(*) FROM cms_hosting_preferences')->fetchColumn() == 0,
    'Preferences not cascaded after project removal');
echo "PASS: hosting metadata validation, authorization, rendering, draft isolation and deletion\n";
