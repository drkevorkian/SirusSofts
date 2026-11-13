<?php
declare(strict_types=1);

/**
 * Consistent, encrypted SQLite snapshot format. Intended for a restricted CLI
 * backup worker, never an unauthenticated HTTP request. No remote upload here.
 */
final class SirusEncryptedSqliteBackup
{
    private const MAGIC = 'SIRUSBK1';
    private const BLOCK = 65536;
    private const MAX_CIPHER_BLOCK = self::BLOCK + 64;

    private static function privatePath(string $path): void
    {
        if (!str_starts_with($path, '/') || str_contains($path, "\0") || is_link($path)) {
            throw new InvalidArgumentException('Backup paths must be absolute and cannot be symlinks');
        }
        $directory = realpath(dirname($path));
        $public = realpath(dirname(__DIR__));
        if ($directory === false || !is_dir($directory) || !is_writable($directory) ||
            ($public !== false &&
             ($directory === $public || str_starts_with($directory, $public . '/')))) {
            throw new InvalidArgumentException('Backup files must be stored in an existing writable private directory');
        }
    }

    private static function validate(string $projectId, string $key): void
    {
        if (!preg_match('/^[a-z][a-z0-9-]{1,63}$/D', $projectId)) {
            throw new InvalidArgumentException('Invalid project ID');
        }
        if (!extension_loaded('sodium') || strlen($key) !== SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_KEYBYTES) {
            throw new InvalidArgumentException('A 32-byte sodium secretstream key is required');
        }
    }

    private static function writeAll($stream, string $bytes): void
    {
        $offset = 0;
        while ($offset < strlen($bytes)) {
            $count = fwrite($stream, substr($bytes, $offset));
            if ($count === false || $count === 0) { throw new RuntimeException('Backup write failed'); }
            $offset += $count;
        }
    }

    private static function readExact($stream, int $length): string
    {
        $bytes = '';
        while (strlen($bytes) < $length) {
            $chunk = fread($stream, $length - strlen($bytes));
            if ($chunk === false || $chunk === '') { throw new RuntimeException('Truncated encrypted backup'); }
            $bytes .= $chunk;
        }
        return $bytes;
    }

    public static function create(
        string $projectId, string $databasePath, string $archivePath,
        string $manifestPath, string $key, int $schemaVersion = 0
    ): array {
        self::validate($projectId, $key);
        if (!extension_loaded('pdo_sqlite') || $schemaVersion < 0) {
            throw new InvalidArgumentException('pdo_sqlite and a nonnegative schema version are required');
        }
        foreach ([$databasePath, $archivePath, $manifestPath] as $path) { self::privatePath($path); }
        if (!is_file($databasePath) || is_link($databasePath) ||
            $archivePath === $manifestPath || file_exists($archivePath) || file_exists($manifestPath)) {
            throw new InvalidArgumentException('Source must exist and backup destinations must not exist');
        }
        $snapshot = tempnam(dirname($archivePath), '.sirus-snapshot-');
        if ($snapshot === false) { throw new RuntimeException('Cannot create private snapshot staging path'); }
        unlink($snapshot); // SQLite VACUUM INTO requires a nonexistent target.
        $created = false;
        try {
            $pdo = new PDO('sqlite:' . realpath($databasePath), null, null, [
                PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_TIMEOUT => 10,
            ]);
            $pdo->exec('PRAGMA busy_timeout=10000');
            // VACUUM INTO creates a transactionally consistent snapshot, including WAL data.
            // SQLite creates this plaintext staging file. Restrict its mode
            // at creation rather than relying on a later chmod (which would
            // leave a brief exposure under a permissive process umask).
            $priorMask = umask(0077);
            try {
                $pdo->exec('VACUUM main INTO ' . $pdo->quote($snapshot));
            } finally {
                umask($priorMask);
            }
            $pdo = null;
            if (!is_file($snapshot)) { throw new RuntimeException('SQLite snapshot missing'); }
            $input = fopen($snapshot, 'rb');
            $output = fopen($archivePath, 'xb');
            if ($input === false || $output === false) {
                if (is_resource($input)) { fclose($input); }
                if (is_resource($output)) { fclose($output); }
                throw new RuntimeException('Cannot open snapshot or encrypted backup');
            }
            $created = true;
            chmod($archivePath, 0600);
            try {
                [$state, $header] = sodium_crypto_secretstream_xchacha20poly1305_init_push($key);
                self::writeAll($output, self::MAGIC . $header);
                while (!feof($input)) {
                    $plaintext = fread($input, self::BLOCK);
                    if ($plaintext === false) { throw new RuntimeException('Snapshot read failed'); }
                    if ($plaintext === '') { break; }
                    $ciphertext = sodium_crypto_secretstream_xchacha20poly1305_push(
                        $state, $plaintext, '', SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_TAG_MESSAGE
                    );
                    self::writeAll($output, pack('N', strlen($ciphertext)) . $ciphertext);
                }
                $final = sodium_crypto_secretstream_xchacha20poly1305_push(
                    $state, '', '', SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_TAG_FINAL
                );
                self::writeAll($output, pack('N', strlen($final)) . $final);
                fflush($output);
            } finally {
                fclose($input);
                fclose($output);
            }
            $manifest = [
                'format_version' => 1,
                'project_id' => $projectId,
                'source_driver' => 'sqlite',
                'schema_version' => $schemaVersion,
                'created_utc' => gmdate('Y-m-d\TH:i:s\Z'),
                'snapshot_consistency_method' => 'sqlite-vacuum-into',
                'encryption' => 'sodium-secretstream-xchacha20poly1305',
                'database_sha256' => hash_file('sha256', $snapshot),
                'database_bytes' => filesize($snapshot),
                'files' => [[
                    'name' => basename($archivePath),
                    'bytes' => filesize($archivePath),
                    'sha256' => hash_file('sha256', $archivePath),
                ]],
            ];
            $handle = fopen($manifestPath, 'x');
            if ($handle === false) { throw new RuntimeException('Cannot create recovery manifest'); }
            chmod($manifestPath, 0600);
            try {
                self::writeAll($handle, json_encode($manifest, JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR) . "\n");
            } finally { fclose($handle); }
            return $manifest;
        } catch (Throwable $error) {
            if ($created) { @unlink($archivePath); }
            @unlink($manifestPath);
            throw $error;
        } finally {
            @unlink($snapshot);
        }
    }

    private static function inspect(
        string $archivePath, string $manifestPath, string $key,
        string $expectedProject, ?string $writePath = null
    ): array {
        self::validate($expectedProject, $key);
        $raw = @file_get_contents($manifestPath);
        if ($raw === false || strlen($raw) > 65536) { throw new RuntimeException('Missing or oversized recovery manifest'); }
        $manifest = json_decode($raw, true, 20, JSON_THROW_ON_ERROR);
        if (!is_array($manifest) ||
            ($manifest['format_version'] ?? null) !== 1 ||
            ($manifest['project_id'] ?? null) !== $expectedProject ||
            ($manifest['source_driver'] ?? null) !== 'sqlite' ||
            ($manifest['encryption'] ?? null) !== 'sodium-secretstream-xchacha20poly1305' ||
            !is_array($manifest['files'] ?? null) || count($manifest['files']) !== 1 ||
            ($manifest['files'][0]['name'] ?? null) !== basename($archivePath) ||
            ($manifest['files'][0]['bytes'] ?? null) !== filesize($archivePath) ||
            !is_string($manifest['files'][0]['sha256'] ?? null) ||
            !hash_equals($manifest['files'][0]['sha256'], hash_file('sha256', $archivePath) ?: '') ||
            !is_string($manifest['database_sha256'] ?? null)) {
            throw new RuntimeException('Backup manifest or encrypted file checksum mismatch');
        }
        $input = fopen($archivePath, 'rb');
        if ($input === false) { throw new RuntimeException('Cannot read encrypted backup'); }
        $output = null;
        try {
            if ($writePath !== null) {
                $output = fopen($writePath, 'xb');
                if ($output === false) { throw new RuntimeException('Cannot create restore staging file'); }
                chmod($writePath, 0600);
            }
            if (self::readExact($input, strlen(self::MAGIC)) !== self::MAGIC) {
                throw new RuntimeException('Unknown encrypted backup format');
            }
            $header = self::readExact($input, SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_HEADERBYTES);
            $state = sodium_crypto_secretstream_xchacha20poly1305_init_pull($header, $key);
            $hash = hash_init('sha256');
            $size = 0;
            $complete = false;
            while (!$complete) {
                $rawLength = self::readExact($input, 4);
                $length = unpack('N', $rawLength)[1];
                if ($length < SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_ABYTES ||
                    $length > self::MAX_CIPHER_BLOCK) {
                    throw new RuntimeException('Invalid encrypted record length');
                }
                $decoded = sodium_crypto_secretstream_xchacha20poly1305_pull(
                    $state, self::readExact($input, $length)
                );
                if ($decoded === false) { throw new RuntimeException('Backup authentication failed'); }
                [$plaintext, $tag] = $decoded;
                if (!in_array($tag, [
                    SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_TAG_MESSAGE,
                    SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_TAG_FINAL,
                ], true)) { throw new RuntimeException('Unsupported encrypted record tag'); }
                hash_update($hash, $plaintext);
                $size += strlen($plaintext);
                if (is_resource($output)) { self::writeAll($output, $plaintext); }
                $complete = $tag === SODIUM_CRYPTO_SECRETSTREAM_XCHACHA20POLY1305_TAG_FINAL;
            }
            if (fread($input, 1) !== '' ||
                !hash_equals($manifest['database_sha256'], hash_final($hash)) ||
                $size !== ($manifest['database_bytes'] ?? null)) {
                throw new RuntimeException('Backup plaintext checksum mismatch or trailing bytes');
            }
        } finally {
            fclose($input);
            if (is_resource($output)) { fclose($output); }
        }
        return $manifest;
    }

    public static function verify(string $archivePath, string $manifestPath, string $key, string $expectedProject): array
    {
        self::privatePath($archivePath);
        self::privatePath($manifestPath);
        return self::inspect($archivePath, $manifestPath, $key, $expectedProject);
    }

    /**
     * Restore into a NEW pathname only, validate SQLite integrity before exposure.
     * The original database is never replaced or deleted.
     */
    public static function restore(
        string $archivePath, string $manifestPath, string $targetPath,
        string $key, string $expectedProject
    ): array {
        self::privatePath($targetPath);
        if (file_exists($targetPath) || is_link($targetPath)) {
            throw new InvalidArgumentException('Restore target already exists');
        }
        $staging = tempnam(dirname($targetPath), '.sirus-restore-');
        if ($staging === false) { throw new RuntimeException('Cannot create restore staging pathname'); }
        unlink($staging);
        try {
            $manifest = self::inspect($archivePath, $manifestPath, $key, $expectedProject, $staging);
            $pdo = new PDO('sqlite:' . $staging, null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
            if ($pdo->query('PRAGMA integrity_check')->fetchColumn() !== 'ok') {
                throw new RuntimeException('Restored SQLite integrity check failed');
            }
            $pdo = null;
            if (!link($staging, $targetPath)) {
                throw new RuntimeException('Restore target appeared or could not be created');
            }
            chmod($targetPath, 0600);
            return $manifest;
        } finally {
            @unlink($staging);
        }
    }
}
