<?php
declare(strict_types=1);

/**
 * Read-only migration preflight / verification for SQLite databases.
 * Cross-engine migrations remain blocked until a dialect-aware copier exists.
 * This module never cuts over a live project or deletes its original DB.
 */
final class SirusSqliteMigration
{
    private static function quoteIdentifier(string $identifier): string
    {
        return '"' . str_replace('"', '""', $identifier) . '"';
    }

    public static function inspect(PDO $db): array
    {
        if ($db->getAttribute(PDO::ATTR_DRIVER_NAME) !== 'sqlite') {
            throw new InvalidArgumentException('SQLite-only migration preflight');
        }
        if ($db->query('PRAGMA integrity_check')->fetchColumn() !== 'ok') {
            throw new RuntimeException('Source SQLite integrity_check failed');
        }
        if ($db->query('PRAGMA foreign_key_check')->fetch() !== false) {
            throw new RuntimeException('Source contains invalid foreign-key references');
        }
        $objects = $db->query("SELECT type,name,sql FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")->fetchAll(PDO::FETCH_ASSOC);
        $tables = [];
        $features = ['views' => [], 'triggers' => [], 'virtual_tables' => []];
        foreach ($objects as $object) {
            $name = (string)$object['name'];
            $sql = (string)($object['sql'] ?? '');
            if ($object['type'] === 'view') { $features['views'][] = $name; }
            if ($object['type'] === 'trigger') { $features['triggers'][] = $name; }
            if ($object['type'] !== 'table') { continue; }
            if (stripos(ltrim($sql), 'CREATE VIRTUAL TABLE') === 0) {
                $features['virtual_tables'][] = $name;
                continue;
            }
            $query = $db->query('SELECT * FROM ' . self::quoteIdentifier($name));
            $hash = hash_init('sha256');
            $count = 0;
            // Ordering is independent of rowid; an ORDER BY of all columns
            // preserves duplicates and supports WITHOUT ROWID tables.
            $columns = [];
            for ($i = 0; $i < $query->columnCount(); $i++) {
                $column = $query->getColumnMeta($i);
                $columns[] = (string)$column['name'];
            }
            $query->closeCursor();
            if ($columns) {
                $orderBy = implode(',', array_map([self::class, 'quoteIdentifier'], $columns));
                $query = $db->query('SELECT * FROM ' . self::quoteIdentifier($name) . ' ORDER BY ' . $orderBy);
                while ($row = $query->fetch(PDO::FETCH_NUM)) {
                    hash_update($hash, serialize($row));
                    $count++;
                }
            }
            $tables[$name] = [
                'rows' => $count,
                'rows_sha256' => hash_final($hash),
                'schema_sha256' => hash('sha256', $sql),
            ];
        }
        ksort($tables);
        return [
            'driver' => 'sqlite',
            'tables' => $tables,
            'features' => $features,
            'schema_objects_sha256' => hash('sha256', json_encode($objects, JSON_THROW_ON_ERROR)),
        ];
    }

    public static function preflight(PDO $source, string $targetDriver): array
    {
        if (!in_array($targetDriver, ['sqlite', 'mysql', 'mariadb'], true)) {
            throw new InvalidArgumentException('Unsupported target database');
        }
        $report = self::inspect($source);
        $blockers = [];
        if ($targetDriver !== 'sqlite') {
            $blockers[] = 'Cross-engine schema and data translation is not implemented';
        }
        if ($report['features']['virtual_tables']) {
            $blockers[] = 'Virtual tables require a project-specific migration';
        }
        return [
            'target_driver' => $targetDriver,
            'can_migrate_automatically' => $blockers === [],
            'blockers' => $blockers,
            'source' => $report,
        ];
    }

    public static function verify(PDO $source, PDO $target): array
    {
        $original = self::inspect($source);
        $copy = self::inspect($target);
        if ($original !== $copy) {
            throw new RuntimeException('Migration verification: schema or data fingerprints differ');
        }
        return ['verified' => true, 'tables' => count($original['tables']),
                'fingerprint' => hash('sha256', json_encode($copy, JSON_THROW_ON_ERROR))];
    }
}

/**
 * Durable, non-activating migration journal. The coordinator must separately
 * authorize cutover only after target verification and confirmed backups.
 */
final class SirusMigrationJournal
{
    private PDO $db;

    public function __construct(string $path)
    {
        if (!str_starts_with($path, '/') || is_link($path) || !is_dir(dirname($path))) {
            throw new InvalidArgumentException('Journal must have an absolute private pathname');
        }
        $this->db = new PDO('sqlite:' . $path, null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]);
        $this->db->exec('PRAGMA busy_timeout=5000');
        $this->db->exec("CREATE TABLE IF NOT EXISTS migrations (
            migration_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            source_ref TEXT NOT NULL, target_ref TEXT NOT NULL,
            state TEXT NOT NULL, created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL
        )");
        @chmod($path, 0600);
    }

    private static function validRef(string $ref): bool
    {
        return (bool)preg_match('/^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$/D', $ref);
    }

    public function begin(string $project, string $sourceRef, string $targetRef): string
    {
        if (!preg_match('/^[a-z][a-z0-9-]{1,63}$/D', $project) ||
            !self::validRef($sourceRef) || !self::validRef($targetRef) ||
            $sourceRef === $targetRef) {
            throw new InvalidArgumentException('Invalid migration project or connection references');
        }
        $id = bin2hex(random_bytes(16));
        $now = gmdate('Y-m-d\TH:i:s\Z');
        $this->db->prepare("INSERT INTO migrations VALUES (?,?,?,?,?,?,?)")
            ->execute([$id, $project, $sourceRef, $targetRef, 'requested', $now, $now]);
        return $id;
    }

    public function advance(string $id, string $next): void
    {
        $nextByState = [
            'requested' => ['preflight_passed', 'failed'],
            'preflight_passed' => ['snapshot_verified', 'failed'],
            'snapshot_verified' => ['copying', 'failed'],
            'copying' => ['target_verified', 'failed'],
            'target_verified' => ['activated', 'failed'],
            'activated' => ['rolled_back'],
            'failed' => [],
            'rolled_back' => [],
        ];
        $this->db->beginTransaction();
        try {
            $row = $this->get($id);
            if (!$row || !in_array($next, $nextByState[$row['state']] ?? [], true)) {
                throw new InvalidArgumentException('Invalid migration state transition');
            }
            $stmt = $this->db->prepare('UPDATE migrations SET state=?,updated_utc=? WHERE migration_id=? AND state=?');
            $stmt->execute([$next, gmdate('Y-m-d\TH:i:s\Z'), $id, $row['state']]);
            if ($stmt->rowCount() !== 1) {
                throw new RuntimeException('Concurrent migration update');
            }
            $this->db->commit();
        } catch (Throwable $e) {
            if ($this->db->inTransaction()) { $this->db->rollBack(); }
            throw $e;
        }
    }

    public function get(string $id): ?array
    {
        $stmt = $this->db->prepare('SELECT * FROM migrations WHERE migration_id=?');
        $stmt->execute([$id]);
        return $stmt->fetch() ?: null;
    }
}
