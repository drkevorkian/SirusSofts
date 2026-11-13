<?php
declare(strict_types=1);

/**
 * Project storage registry. Standalone module; does not modify existing CMS DB.
 * Registry lives in a private SQLite file outside the public document root.
 */
final class SirusStorageRegistry
{
    private PDO $db;

    public function __construct(string $absolutePath)
    {
        if (!str_starts_with($absolutePath, '/') || str_contains($absolutePath, "\0")) {
            throw new InvalidArgumentException('Registry path must be absolute');
        }
        $dir = dirname($absolutePath);
        if (!is_dir($dir) || !is_writable($dir)) {
            throw new RuntimeException('Private registry directory must exist and be writable');
        }
        $this->db = new PDO('sqlite:' . $absolutePath, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $this->db->exec('PRAGMA busy_timeout=5000');
        $this->db->exec('CREATE TABLE IF NOT EXISTS project_storage (
            project_id TEXT PRIMARY KEY,
            driver TEXT NOT NULL CHECK(driver IN (\'sqlite\',\'mariadb\',\'mysql\')),
            connection_ref TEXT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 0 CHECK(schema_version >= 0),
            primary_node TEXT NOT NULL,
            backup_policy TEXT NOT NULL DEFAULT \'manual\',
            migration_state TEXT NOT NULL DEFAULT \'idle\'
                CHECK(migration_state IN (\'idle\',\'preflight\',\'copying\',\'verifying\',\'ready\',\'failed\')),
            updated_at TEXT NOT NULL
        )');
        @chmod($absolutePath, 0600);
    }

    public function put(string $projectId, string $driver, string $connectionRef, string $primaryNode, int $schemaVersion = 0): void
    {
        if (!preg_match('/^[a-z][a-z0-9-]{1,63}$/D', $projectId) ||
            !in_array($driver, ['sqlite', 'mariadb', 'mysql'], true) ||
            !preg_match('/^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$/D', $connectionRef) ||
            !preg_match('/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$/D', $primaryNode) ||
            $schemaVersion < 0) {
            throw new InvalidArgumentException('Invalid project storage metadata');
        }
        $stmt = $this->db->prepare('INSERT INTO project_storage
            (project_id,driver,connection_ref,schema_version,primary_node,updated_at)
            VALUES (:id,:driver,:ref,:version,:node,:updated)
            ON CONFLICT(project_id) DO UPDATE SET
              driver=excluded.driver, connection_ref=excluded.connection_ref,
              schema_version=excluded.schema_version, primary_node=excluded.primary_node,
              updated_at=excluded.updated_at');
        $stmt->execute([
            ':id'=>$projectId, ':driver'=>$driver, ':ref'=>$connectionRef,
            ':version'=>$schemaVersion, ':node'=>$primaryNode,
            ':updated'=>gmdate('Y-m-d\TH:i:s\Z'),
        ]);
    }

    public function get(string $projectId): ?array
    {
        $stmt = $this->db->prepare('SELECT * FROM project_storage WHERE project_id=:id');
        $stmt->execute([':id'=>$projectId]);
        return $stmt->fetch() ?: null;
    }

    public function all(): array
    {
        return $this->db->query('SELECT * FROM project_storage ORDER BY project_id')->fetchAll();
    }
}
