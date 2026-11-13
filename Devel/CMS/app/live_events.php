<?php
declare(strict_types=1);
if (!defined('SIRUS_CMS_BOOTSTRAPPED')) { http_response_code(404); exit; }

/**
 * Private, admin-only notification journal. Call only from trusted PHP code,
 * never directly with arbitrary browser-supplied topic or payload data.
 *
 * Producers should send minimal metadata (IDs/counts) rather than message
 * bodies, credentials, email addresses or secrets.
 */
function cms_live_publish(string $topic, array $payload): int {
    $allowed=[
        'job.changed','node.changed','message.received','mail.received',
        'deployment.changed','site.notice'
    ];
    if(!in_array($topic,$allowed,true) || array_is_list($payload))
        throw new InvalidArgumentException('Invalid live notification topic or payload.');
    $json=json_encode($payload,JSON_THROW_ON_ERROR);
    if(strlen($json)>2048)
        throw new InvalidArgumentException('Live notification too large.');
    $nodeDir=cms_private_directory().'/nodes';
    if(!is_dir($nodeDir) || is_link($nodeDir))
        throw new RuntimeException('Private notifications directory missing.');
    $file=$nodeDir.'/live_events.sqlite3';
    if(is_link($file))throw new RuntimeException('Unsafe notification database path.');
    $db=new PDO('sqlite:'.$file,null,null,[
        PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC
    ]);
    @chmod($file,0600);
    $db->exec('PRAGMA busy_timeout=5000');
    $db->exec('PRAGMA journal_mode=WAL');
    $db->exec('CREATE TABLE IF NOT EXISTS live_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )');
    $db->beginTransaction();
    try{
        $stmt=$db->prepare('INSERT INTO live_events(topic,payload_json,created_at)
                            VALUES(?,?,?)');
        $stmt->execute([$topic,$json,time()]);
        $id=(int)$db->lastInsertId();
        $db->exec('DELETE FROM live_events WHERE id NOT IN
            (SELECT id FROM live_events ORDER BY id DESC LIMIT 200)');
        $db->commit();
        return $id;
    }catch(Throwable $error){
        if($db->inTransaction())$db->rollBack();
        throw $error;
    }
}
