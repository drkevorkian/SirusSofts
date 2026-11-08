<?php
declare(strict_types=1);
require dirname(__DIR__) . '/app/node_registry.php';
if (!extension_loaded('pdo_sqlite')) { fwrite(STDERR, "pdo_sqlite required\n"); exit(2); }
function nodeCheck(bool $ok, string $message): void {
    if (!$ok) { throw new RuntimeException($message); }
}
$dir = sys_get_temp_dir() . '/sirus-node-' . bin2hex(random_bytes(8));
if (!mkdir($dir, 0700)) { throw new RuntimeException('mkdir failed'); }
try {
    $registry = new SirusNodeRegistry($dir . '/nodes.sqlite');
    $key = random_bytes(32);
    $now = 1794154000;
    $data = [
        'node_id' => 'gentoo-home', 'sent_at' => $now, 'nonce' => bin2hex(random_bytes(16)),
        'os' => 'gentoo', 'init' => 'openrc', 'agent_version' => '0.1.0',
        'cpu_threads' => 16, 'ram_bytes' => 34359738368,
        'gpu_vendors' => ['0x10de', '0x1002'], 'capabilities' => ['compute','index'],
    ];
    $body = json_encode($data, JSON_THROW_ON_ERROR);
    $sig = hash_hmac('sha256', $body, $key);
    $resolve = fn (string $id): string => $id === 'gentoo-home' ? $key : '';
    $result = $registry->acceptHeartbeat($body, $sig, $resolve, $now);
    nodeCheck($result['accepted'] === true, 'Signed heartbeat accepted');
    nodeCheck($registry->getNode('gentoo-home', $now)['health'] === 'online', 'Online node');
    nodeCheck($registry->getNode('gentoo-home', $now+600)['health'] === 'offline', 'Node interruption detected');
    try {
        $registry->acceptHeartbeat($body, $sig, $resolve, $now);
        throw new RuntimeException('Replay accepted');
    } catch (RuntimeException $e) {
        nodeCheck(str_contains($e->getMessage(), 'Replayed'), 'Wrong replay exception');
    }
    $data['nonce'] = bin2hex(random_bytes(16));
    $body = json_encode($data, JSON_THROW_ON_ERROR);
    try {
        $registry->acceptHeartbeat($body, str_repeat('0', 64), $resolve, $now);
        throw new RuntimeException('Bad MAC accepted');
    } catch (RuntimeException $e) {
        nodeCheck(str_contains($e->getMessage(), 'authentication'), 'Wrong MAC exception');
    }
    try {
        $registry->acceptHeartbeat($body, hash_hmac('sha256', $body, $key), $resolve, $now+500);
        throw new RuntimeException('Stale heartbeat accepted');
    } catch (InvalidArgumentException $expected) {}
    nodeCheck(count($registry->allNodes($now)) === 1, 'List recorded telemetry');
    nodeCheck(!isset($registry->allNodes($now)[0]['last_nonce']), 'Nonce excluded from admin telemetry');
    $registry->assignWorkload('job-example-0001','gentoo-home','example','compute');
    $jobs = $registry->workloadsFor('gentoo-home');
    nodeCheck(count($jobs) === 1 && $jobs[0]['state'] === 'queued', 'Assignment persisted');
    echo "PASS: signed node registry, replay rejection, offline detection and queued assignment\n";
} finally {
    foreach (glob($dir . '/*') ?: [] as $file) { @unlink($file); }
    @rmdir($dir);
}
