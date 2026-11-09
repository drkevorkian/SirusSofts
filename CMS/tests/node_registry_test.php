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
    $data['nonce'] = bin2hex(random_bytes(16));
    $data['metrics'] = [
        'cpu_pct'=>34.1,'cpu_temp_c'=>56.2,'load_1'=>1.3,
        'memory_used_bytes'=>1073741824,'disk_used_bytes'=>10737418240,
        'disk_total_bytes'=>107374182400,'uptime_seconds'=>1500,
        'gpus'=>[
          ['vendor'=>'0x10de','name'=>'NVIDIA GPU','usage_pct'=>49.3,
           'temp_c'=>61.0,'vram_used_bytes'=>536870912,'vram_total_bytes'=>2147483648],
          ['vendor'=>'0x1002','name'=>'AMD GPU','usage_pct'=>10.0,
           'temp_c'=>45.0,'vram_used_bytes'=>268435456,'vram_total_bytes'=>4294967296]
        ]
    ];
    $telemetry=json_encode($data,JSON_THROW_ON_ERROR);
    $registry->acceptHeartbeat($telemetry,hash_hmac('sha256',$telemetry,$key),$resolve,$now);
    $node=$registry->getNode('gentoo-home',$now);
    nodeCheck(abs($node['metrics']['cpu_pct']-34.1)<0.1,'Signed CPU usage retained');
    nodeCheck(count($node['metrics']['gpus'])===2,'Both GPUs retained');
    $bad=$data;
    $bad['nonce']=bin2hex(random_bytes(16));$bad['metrics']['cpu_pct']=999;
    $badBody=json_encode($bad,JSON_THROW_ON_ERROR);
    try{
        $registry->acceptHeartbeat($badBody,hash_hmac('sha256',$badBody,$key),$resolve,$now);
        throw new RuntimeException('Out-of-range CPU usage accepted');
    }catch(InvalidArgumentException $expected){}
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
