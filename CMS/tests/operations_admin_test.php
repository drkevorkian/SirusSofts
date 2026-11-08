<?php
declare(strict_types=1);
define('SIRUS_CMS_BOOTSTRAPPED', true);
require dirname(__DIR__) . '/app/core.php';
require dirname(__DIR__) . '/app/site.php';
require dirname(__DIR__) . '/app/admin.php';
require_once dirname(__DIR__) . '/app/operations.php';

if (!extension_loaded('pdo_sqlite')) {
    fwrite(STDERR, "pdo_sqlite required\n");
    exit(2);
}
function operationsCheck(bool $condition, string $message): void {
    if (!$condition) { throw new RuntimeException($message); }
}
$dir = sys_get_temp_dir() . '/sirus-operations-' . bin2hex(random_bytes(8));
if (!mkdir($dir, 0700)) { throw new RuntimeException('mkdir failed'); }
try {
    $_SERVER['HTTP_HOST'] = 'sirussofts.org';
    $_SERVER['REMOTE_ADDR'] = '127.0.0.1';
    $_SESSION = [];
    try {
        cms_operations_snapshot();
        throw new RuntimeException('Unauthenticated infrastructure read accepted');
    } catch (RuntimeException $expected) {
        operationsCheck(str_contains($expected->getMessage(), 'Sign in required'),
            'Unexpected unauthorized exception');
    }
    $_SESSION['cms_admin_id'] = 1;
    $_SESSION['cms_username'] = 'admin';
    $storage = new SirusStorageRegistry($dir . '/storage.sqlite');
    $storage->put('example', 'sqlite', 'private_secret_ref', 'gentoo-home', 2);
    $nodes = new SirusNodeRegistry($dir . '/nodes.sqlite');
    $heartbeat = [
        'node_id' => 'gentoo-home', 'sent_at' => time(),
        'nonce' => bin2hex(random_bytes(16)), 'os' => 'gentoo', 'init' => 'openrc',
        'agent_version' => '0.1.0', 'cpu_threads' => 16,
        'ram_bytes' => 32 * 1073741824,
        'gpu_vendors' => ['0x10de','0x1002'], 'capabilities' => ['compute'],
    ];
    $key = random_bytes(32);
    $payload = json_encode($heartbeat, JSON_THROW_ON_ERROR);
    $nodes->acceptHeartbeat($payload, hash_hmac('sha256',$payload,$key),fn()=>$key);
    putenv('SIRUS_STORAGE_REGISTRY_FILE=' . $dir . '/storage.sqlite');
    putenv('SIRUS_NODE_REGISTRY_FILE=' . $dir . '/nodes.sqlite');
    $db = new PDO('sqlite::memory:', null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    cms_schema($db);
    ob_start();
    cms_operations_page($db);
    $html = ob_get_clean();
    operationsCheck(str_contains($html, 'gentoo-home'), 'Node telemetry not rendered');
    operationsCheck(str_contains($html, 'example'), 'Project storage not rendered');
    operationsCheck(str_contains($html, 'sirussofts.org'), 'CMS layout missing');
    operationsCheck(!str_contains($html, 'private_secret_ref'), 'Connection refs should not appear');
    operationsCheck(!str_contains($html, bin2hex($key)), 'Secret key must not be rendered');
    operationsCheck(str_contains($html, 'Infrastructure'), 'Infrastructure heading missing');
    echo "PASS: private administrator infrastructure dashboard with node/storage data\n";
} finally {
    putenv('SIRUS_STORAGE_REGISTRY_FILE');
    putenv('SIRUS_NODE_REGISTRY_FILE');
    foreach (glob($dir . '/*') ?: [] as $file) { @unlink($file); }
    @rmdir($dir);
}
