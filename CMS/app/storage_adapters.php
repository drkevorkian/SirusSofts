<?php
declare(strict_types=1);

/**
 * Database connection adapters for project-owned databases.
 * Configuration is supplied by a trusted coordinator, not HTTP request data.
 * Secrets are obtained by reference and never stored in the CMS registry.
 */
interface SirusStorageAdapter
{
    public function driver(): string;
    public function connect(array $configuration, callable $resolveSecret): PDO;
}

final class SirusSqliteAdapter implements SirusStorageAdapter
{
    public function driver(): string { return 'sqlite'; }

    public function connect(array $configuration, callable $resolveSecret): PDO
    {
        if (!extension_loaded('pdo_sqlite')) {
            throw new RuntimeException('pdo_sqlite extension required');
        }
        $path = $configuration['path'] ?? null;
        if (!is_string($path) || !str_starts_with($path, '/') ||
            str_contains($path, "\0") || is_link($path) || !is_file($path)) {
            throw new InvalidArgumentException('SQLite database must be an existing absolute, regular file');
        }
        $real = realpath($path);
        $public = realpath(dirname(__DIR__));
        if ($real === false || ($public !== false &&
            str_starts_with($real, $public . DIRECTORY_SEPARATOR))) {
            throw new InvalidArgumentException('Database cannot reside in the public CMS tree');
        }
        $pdo = new PDO('sqlite:' . $real, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
        ]);
        $pdo->exec('PRAGMA busy_timeout=5000');
        $pdo->exec('PRAGMA foreign_keys=ON');
        return $pdo;
    }
}

final class SirusMysqlAdapter implements SirusStorageAdapter
{
    public function __construct(private string $engine = 'mysql')
    {
        if (!in_array($engine, ['mysql', 'mariadb'], true)) {
            throw new InvalidArgumentException('Unsupported SQL driver');
        }
    }

    public function driver(): string { return $this->engine; }

    public function connect(array $configuration, callable $resolveSecret): PDO
    {
        if (!extension_loaded('pdo_mysql')) {
            throw new RuntimeException('pdo_mysql extension required');
        }
        $host = $configuration['host'] ?? '';
        $port = $configuration['port'] ?? 3306;
        $database = $configuration['database'] ?? '';
        $username = $configuration['username'] ?? '';
        $secretRef = $configuration['secret_ref'] ?? '';
        foreach ([$host, $database, $username, $secretRef] as $field) {
            if (!is_string($field) || $field === '' || strlen($field) > 128 ||
                preg_match('/[\x00-\x20;\\]/', $field)) {
                throw new InvalidArgumentException('Invalid MySQL connection setting');
            }
        }
        if (!preg_match('/^[a-zA-Z0-9_.-]+$/D', $host) ||
            !preg_match('/^[a-zA-Z0-9_]+$/D', $database) ||
            !preg_match('/^[a-zA-Z0-9_.-]+$/D', $username) ||
            !preg_match('/^[a-zA-Z][a-zA-Z0-9_.-]*$/D', $secretRef) ||
            !is_int($port) || $port < 1 || $port > 65535) {
            throw new InvalidArgumentException('Invalid MySQL host, credentials reference, or port');
        }
        $local = in_array(strtolower($host), ['localhost', '127.0.0.1', '::1'], true);
        $options = [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
            PDO::ATTR_TIMEOUT => 5,
        ];
        if (!$local) {
            $ca = $configuration['ssl_ca'] ?? null;
            if (!is_string($ca) || !is_file($ca) || is_link($ca) ||
                !defined('PDO::MYSQL_ATTR_SSL_CA') ||
                !defined('PDO::MYSQL_ATTR_SSL_VERIFY_SERVER_CERT')) {
                throw new InvalidArgumentException('Remote MySQL requires a local CA file and certificate validation');
            }
            $options[PDO::MYSQL_ATTR_SSL_CA] = $ca;
            $options[PDO::MYSQL_ATTR_SSL_VERIFY_SERVER_CERT] = true;
        }
        $password = $resolveSecret($secretRef);
        if (!is_string($password) || $password === '') {
            throw new RuntimeException('Missing database credential');
        }
        $dsn = "mysql:host={$host};port={$port};dbname={$database};charset=utf8mb4";
        return new PDO($dsn, $username, $password, $options);
    }
}

final class SirusStorageAdapters
{
    public static function forDriver(string $driver): SirusStorageAdapter
    {
        return match ($driver) {
            'sqlite' => new SirusSqliteAdapter(),
            'mysql', 'mariadb' => new SirusMysqlAdapter($driver),
            default => throw new InvalidArgumentException('Unsupported storage backend'),
        };
    }
}
