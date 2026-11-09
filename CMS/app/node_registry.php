<?php
declare(strict_types=1);

/**
 * Private coordinator registry: signed node heartbeats, replay protection and
 * explicitly assigned workload metadata. No remote code execution here.
 */
final class SirusNodeRegistry
{
    private PDO $db;

    public function __construct(string $path)
    {
        if (!extension_loaded('pdo_sqlite') || !str_starts_with($path, '/') ||
            str_contains($path, "\0") || is_link($path) || !is_dir(dirname($path)) ||
            !is_writable(dirname($path))) {
            throw new InvalidArgumentException('Node registry requires a writable private SQLite path');
        }
        $public = realpath(dirname(__DIR__));
        $parent = realpath(dirname($path));
        if ($public !== false && ($parent === $public || str_starts_with((string)$parent, $public . '/'))) {
            throw new InvalidArgumentException('Node registry must be outside the public CMS');
        }
        $this->db = new PDO('sqlite:' . $path, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $this->db->exec('PRAGMA busy_timeout=5000');
        $this->db->exec("CREATE TABLE IF NOT EXISTS node_status (
            node_id TEXT PRIMARY KEY, os TEXT NOT NULL, init_system TEXT NOT NULL,
            agent_version TEXT NOT NULL, cpu_threads INTEGER NOT NULL, ram_bytes INTEGER NOT NULL,
            gpu_vendors_json TEXT NOT NULL, capabilities_json TEXT NOT NULL,
            last_seen INTEGER NOT NULL, last_nonce TEXT NOT NULL,
            metrics_json TEXT NOT NULL DEFAULT '{}'
        )");
        $columns = $this->db->query('PRAGMA table_info(node_status)')->fetchAll(PDO::FETCH_ASSOC);
        if (!in_array('metrics_json', array_column($columns, 'name'), true)) {
            $this->db->exec("ALTER TABLE node_status ADD COLUMN metrics_json TEXT NOT NULL DEFAULT '{}'");
        }
        $this->db->exec("CREATE TABLE IF NOT EXISTS heartbeat_nonces (
            node_id TEXT NOT NULL, nonce TEXT NOT NULL, seen_at INTEGER NOT NULL,
            PRIMARY KEY(node_id,nonce)
        )");
        $this->db->exec("CREATE TABLE IF NOT EXISTS assigned_workloads (
            workload_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, project_id TEXT NOT NULL,
            workload_kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            FOREIGN KEY(node_id) REFERENCES node_status(node_id)
        )");
        $this->db->exec('PRAGMA foreign_keys=ON');
        @chmod($path, 0600);
    }

    private static function id(string $value): bool
    {
        return (bool)preg_match('/^[a-z][a-z0-9-]{1,63}$/D', $value);
    }


    /** Reject inconsistent/unbounded telemetry; preserve old 0.1 agents. */
    private static function metrics(mixed $metrics, int $ram): array
    {
        if ($metrics === null) return [];
        if (!is_array($metrics) || array_is_list($metrics) ||
            array_diff(array_keys($metrics), [
                'cpu_pct','cpu_temp_c','load_1','memory_used_bytes',
                'disk_used_bytes','disk_total_bytes','uptime_seconds','gpus'
            ])) throw new InvalidArgumentException('Invalid performance metrics');
        foreach (['cpu_pct' => 100.0, 'cpu_temp_c' => 140.0, 'load_1' => 1024.0] as $field => $upper) {
            if (!array_key_exists($field, $metrics) || $metrics[$field] === null) continue;
            $value=$metrics[$field];
            $lower=$field==='cpu_temp_c' ? -20 : 0;
            if ((!is_int($value) && !is_float($value)) || !is_finite((float)$value)
                || $value<$lower || $value>$upper)
                throw new InvalidArgumentException('Invalid '.$field);
        }
        $max=['memory_used_bytes'=>$ram, 'disk_used_bytes'=>1125899906842624,
              'disk_total_bytes'=>1125899906842624, 'uptime_seconds'=>3153600000];
        foreach ($max as $field=>$upper) {
            if (!array_key_exists($field, $metrics)) continue;
            if (!is_int($metrics[$field]) || $metrics[$field]<0 || $metrics[$field]>$upper)
                throw new InvalidArgumentException('Invalid '.$field);
        }
        if (isset($metrics['disk_used_bytes'],$metrics['disk_total_bytes']) &&
            $metrics['disk_total_bytes']>0 &&
            $metrics['disk_used_bytes']>$metrics['disk_total_bytes'])
            throw new InvalidArgumentException('Disk use exceeds capacity');
        if (isset($metrics['gpus'])) {
            if (!is_array($metrics['gpus']) || !array_is_list($metrics['gpus']) ||
                count($metrics['gpus'])>16)
                throw new InvalidArgumentException('Invalid GPU metrics');
            foreach ($metrics['gpus'] as $item) {
                if (!is_array($item) || array_is_list($item) ||
                    !is_string($item['vendor']??null) ||
                    !preg_match('/^0x[0-9a-f]{4}$/D',$item['vendor']) ||
                    !is_string($item['name']??null) ||
                    !preg_match('/^[^\x00-\x1F\x7F]{1,80}$/uD',$item['name']) ||
                    array_diff(array_keys($item),[
                        'vendor','name','usage_pct','temp_c',
                        'vram_used_bytes','vram_total_bytes'
                    ])) throw new InvalidArgumentException('Invalid GPU identity');
                foreach (['usage_pct'=>[0,100], 'temp_c'=>[-20,140]] as $key=>$range) {
                    $val=$item[$key]??null;
                    if ($val!==null && ((!is_int($val)&&!is_float($val)) ||
                        !is_finite((float)$val) || $val<$range[0] || $val>$range[1]))
                        throw new InvalidArgumentException('Invalid GPU gauge');
                }
                foreach (['vram_used_bytes','vram_total_bytes'] as $key) {
                    $val=$item[$key]??null;
                    if ($val!==null && (!is_int($val) || $val<0 || $val>1125899906842624))
                        throw new InvalidArgumentException('Invalid GPU memory');
                }
            }
        }
        return $metrics;
    }

    public function acceptHeartbeat(
        string $body, string $signature, callable $resolveNodeKey, ?int $now = null
    ): array {
        $now ??= time();
        if ($body === '' || strlen($body) > 8192 || !preg_match('/^[0-9a-f]{64}$/D', $signature)) {
            throw new InvalidArgumentException('Invalid signed heartbeat envelope');
        }
        $payload = json_decode($body, true, 12, JSON_THROW_ON_ERROR);
        if (!is_array($payload)) { throw new InvalidArgumentException('Invalid node heartbeat'); }
        $id = $payload['node_id'] ?? null;
        $timestamp = $payload['sent_at'] ?? null;
        $nonce = $payload['nonce'] ?? null;
        $os = $payload['os'] ?? null;
        $init = $payload['init'] ?? null;
        $version = $payload['agent_version'] ?? null;
        $cpus = $payload['cpu_threads'] ?? null;
        $ram = $payload['ram_bytes'] ?? null;
        $gpu = $payload['gpu_vendors'] ?? null;
        $capabilities = $payload['capabilities'] ?? null;
        $metrics = self::metrics($payload['metrics'] ?? null, is_int($ram)?$ram:0);
        if (!is_string($id) || !self::id($id) || !is_int($timestamp) ||
            abs($timestamp - $now) > 120 || !is_string($nonce) ||
            !preg_match('/^[0-9a-f]{32}$/D', $nonce) ||
            !in_array($os, ['gentoo', 'ubuntu', 'linux'], true) ||
            !in_array($init, ['openrc', 'systemd', 'other'], true) ||
            !is_string($version) || !preg_match('/^[0-9a-zA-Z_.-]{1,32}$/D', $version) ||
            !is_int($cpus) || $cpus < 1 || $cpus > 1024 ||
            !is_int($ram) || $ram < 1024 * 1024 || $ram > 1125899906842624 ||
            !is_array($gpu) || count($gpu) > 16 || !array_is_list($gpu) ||
            !is_array($capabilities) || count($capabilities) > 32 || !array_is_list($capabilities)) {
            throw new InvalidArgumentException('Invalid node telemetry');
        }
        foreach ($gpu as $vendor) {
            if (!is_string($vendor) || !preg_match('/^0x[0-9a-f]{4}$/D', $vendor)) {
                throw new InvalidArgumentException('Invalid GPU vendor');
            }
        }
        foreach ($capabilities as $capability) {
            if (!is_string($capability) ||
                !preg_match('/^[a-z][a-z0-9_-]{0,31}$/D', $capability)) {
                throw new InvalidArgumentException('Invalid node capability');
            }
        }
        $key = $resolveNodeKey($id);
        if (!is_string($key) || strlen($key) !== 32 ||
            !hash_equals(hash_hmac('sha256', $body, $key), $signature)) {
            throw new RuntimeException('Node heartbeat authentication failed');
        }
        $this->db->beginTransaction();
        try {
            $this->db->prepare('DELETE FROM heartbeat_nonces WHERE seen_at < ?')
                ->execute([$now - 86400]);
            $check = $this->db->prepare('SELECT 1 FROM heartbeat_nonces WHERE node_id=? AND nonce=?');
            $check->execute([$id, $nonce]);
            if ($check->fetchColumn()) { throw new RuntimeException('Replayed node heartbeat'); }
            $this->db->prepare('INSERT INTO heartbeat_nonces VALUES (?,?,?)')
                ->execute([$id, $nonce, $now]);
            $this->db->prepare('INSERT INTO node_status
                (node_id,os,init_system,agent_version,cpu_threads,ram_bytes,
                 gpu_vendors_json,capabilities_json,last_seen,last_nonce,metrics_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(node_id) DO UPDATE SET
                os=excluded.os,init_system=excluded.init_system,
                agent_version=excluded.agent_version,cpu_threads=excluded.cpu_threads,
                ram_bytes=excluded.ram_bytes,gpu_vendors_json=excluded.gpu_vendors_json,
                capabilities_json=excluded.capabilities_json,last_seen=excluded.last_seen,
                last_nonce=excluded.last_nonce,metrics_json=excluded.metrics_json')
                ->execute([$id, $os, $init, $version, $cpus, $ram,
                    json_encode($gpu, JSON_THROW_ON_ERROR),
                    json_encode($capabilities, JSON_THROW_ON_ERROR), $now, $nonce,
                    json_encode($metrics, JSON_THROW_ON_ERROR)]);
            $this->db->commit();
        } catch (Throwable $error) {
            if ($this->db->inTransaction()) { $this->db->rollBack(); }
            throw $error;
        }
        return ['accepted' => true, 'node_id' => $id, 'server_time' => $now];
    }

    public function getNode(string $id, ?int $now = null): ?array
    {
        $stmt = $this->db->prepare('SELECT * FROM node_status WHERE node_id=?');
        $stmt->execute([$id]);
        $node = $stmt->fetch();
        if (!$node) { return null; }
        $node['health'] = $node['last_seen'] >= ($now ?? time()) - 180 ? 'online' : 'offline';
        $node['gpu_vendors'] = json_decode($node['gpu_vendors_json'], true);
        $node['capabilities'] = json_decode($node['capabilities_json'], true);
        $node['metrics'] = json_decode($node['metrics_json'], true) ?: [];
        unset($node['gpu_vendors_json'], $node['capabilities_json'],
              $node['last_nonce'], $node['metrics_json']);
        return $node;
    }

    /** Public telemetry fields only; excludes heartbeat nonce and authentication key. */
    public function allNodes(?int $now = null): array
    {
        $now ??= time();
        $rows = $this->db->query('SELECT node_id,os,init_system,agent_version,
            cpu_threads,ram_bytes,gpu_vendors_json,capabilities_json,metrics_json,last_seen
            FROM node_status ORDER BY node_id')->fetchAll(PDO::FETCH_ASSOC);
        foreach ($rows as &$row) {
            $row['health'] = (int)$row['last_seen'] >= $now - 180 ? 'online' : 'offline';
            $row['gpu_vendors'] = json_decode($row['gpu_vendors_json'], true);
            $row['capabilities'] = json_decode($row['capabilities_json'], true);
            $row['metrics'] = json_decode($row['metrics_json'], true) ?: [];
            unset($row['gpu_vendors_json'], $row['capabilities_json'], $row['metrics_json']);
        }
        unset($row);
        return $rows;
    }

    /** Queues metadata only. There is deliberately no remote command executor. */
    public function assignWorkload(string $id, string $nodeId, string $projectId, string $kind): void
    {
        if (!preg_match('/^[a-zA-Z0-9_-]{8,64}$/D', $id) ||
            !self::id($nodeId) || !self::id($projectId) ||
            !in_array($kind, ['build', 'test', 'index', 'compute'], true) ||
            $this->getNode($nodeId) === null) {
            throw new InvalidArgumentException('Invalid workload assignment');
        }
        $now = time();
        $this->db->prepare('INSERT INTO assigned_workloads
           (workload_id,node_id,project_id,workload_kind,state,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?)')
           ->execute([$id, $nodeId, $projectId, $kind, 'queued', $now, $now]);
    }

    public function workloadsFor(string $nodeId): array
    {
        $stmt = $this->db->prepare('SELECT workload_id,project_id,workload_kind,state,created_at,updated_at
            FROM assigned_workloads WHERE node_id=? ORDER BY created_at,workload_id');
        $stmt->execute([$nodeId]);
        return $stmt->fetchAll();
    }
}
