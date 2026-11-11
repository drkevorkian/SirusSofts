<?php
declare(strict_types=1);
if (!defined('SIRUS_CMS_BOOTSTRAPPED')) { http_response_code(404); exit; }

/**
 * A source release APPROVAL is NOT an installation command.
 * Only immutable, indexed GitHub revisions and fixed local components qualify.
 * Privileged host executors must be added and reviewed separately.
 */
function cms_release_components(): array {
    return [
        'ubuntu-primary'=>[
            'CMS/app/admin.php'=>'CMS administration',
            'CMS/app/operations.php'=>'CMS infrastructure',
            'CMS/assets/live.js'=>'Live browser client',
            'ubuntu-worker/worker.py'=>'Ubuntu job worker',
            'realtime/server.py'=>'WebSocket daemon',
        ],
        'gentoo-home'=>[
            'gentoo-node/client.py'=>'Gentoo agent',
            'gentoo-node/dashboard.py'=>'Gentoo dashboard',
            'gentoo-node/tunnel.py'=>'Gentoo reverse tunnel',
        ],
    ];
}

function cms_release_schema(PDO $db): void {
    $db->exec("CREATE TABLE IF NOT EXISTS cms_source_release_plans (
        id TEXT PRIMARY KEY,
        host TEXT NOT NULL CHECK(host IN ('ubuntu-primary','gentoo-home')),
        source_path TEXT NOT NULL,
        commit_sha TEXT NOT NULL,
        target_sha256 TEXT NOT NULL,
        baseline_sha256 TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('planned','approved','canceled')),
        created_by INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        approved_at INTEGER NOT NULL DEFAULT 0
    )");
    $db->exec('CREATE INDEX IF NOT EXISTS cms_source_plan_created
               ON cms_source_release_plans(created_at DESC)');
    $db->exec("CREATE TABLE IF NOT EXISTS cms_source_deploy_requests (
        id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL REFERENCES cms_source_release_plans(id),
        host TEXT NOT NULL CHECK(host IN ('ubuntu-primary')),
        action TEXT NOT NULL CHECK(action IN ('apply','rollback')),
        state TEXT NOT NULL CHECK(state IN ('queued','running','done','failed','interrupted')),
        created_by INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        error TEXT NOT NULL DEFAULT '',
        result_json TEXT NOT NULL DEFAULT '{}'
    )");
    $db->exec('CREATE INDEX IF NOT EXISTS cms_source_deploy_pending
               ON cms_source_deploy_requests(host,state,created_at)');
}

function cms_release_index(?int $now=null): array {
    $now ??= time();
    $file=cms_private_directory().'/nodes/source_index.json';
    if(is_link($file) || !is_file($file) ||
       ($bytes=filesize($file))===false || $bytes>65536)
        throw new InvalidArgumentException('Source version index is unavailable.');
    $index=json_decode((string)file_get_contents($file),true,12,JSON_THROW_ON_ERROR);
    if(!is_array($index) || ($index['repository']??null)!=='drkevorkian/SirusSofts' ||
       ($index['branch']??null)!=='feature/cms-vhost-distributed-nodes' ||
       !is_int($index['checked_at']??null) ||
       $index['checked_at']>$now+120 || $index['checked_at']<$now-86400)
        throw new InvalidArgumentException('GitHub source index is stale or invalid. Check GitHub updates first.');
    if(!is_array($index['candidates']??null) ||
       count($index['candidates'])<1 || count($index['candidates'])>3)
        throw new InvalidArgumentException('GitHub index lacks approved release candidates.');
    return $index;
}

function cms_release_target(array $index,string $commit,string $host,string $path): string {
    $components=cms_release_components();
    if(!isset($components[$host][$path]) || !preg_match('/^[a-f0-9]{40}$/D',$commit))
        throw new InvalidArgumentException('Unsupported host, component or GitHub revision.');
    foreach($index['candidates'] as $candidate){
        if(!is_array($candidate) || ($candidate['commit']??null)!==$commit)
            continue;
        foreach(($candidate['files']??[]) as $file){
            if(is_array($file) && ($file['path']??null)===$path &&
               ($file['platform']??null)===($host==='gentoo-home'?'gentoo':'ubuntu') &&
               is_string($file['sha256']??null) &&
               preg_match('/^[a-f0-9]{64}$/D',$file['sha256']))
                return $file['sha256'];
        }
    }
    throw new InvalidArgumentException('Component not present in the selected approved release.');
}

function cms_release_installed(string $host,string $path): ?string {
    $ubuntu=[
        'CMS/app/admin.php'=>cms_site_root().'/app/admin.php',
        'CMS/app/operations.php'=>cms_site_root().'/app/operations.php',
        'CMS/assets/live.js'=>cms_site_root().'/assets/live.js',
        'ubuntu-worker/worker.py'=>'/usr/local/libexec/sirus-ubuntu-worker.py',
        'realtime/server.py'=>'/usr/local/libexec/sirus-live-socket.py',
    ];
    if($host==='ubuntu-primary'){
        $file=$ubuntu[$path]??null;
        if(!is_string($file) || is_link($file) || !is_file($file) ||
           filesize($file)>2000000 || !is_readable($file))
            return null;
        return hash_file('sha256',$file) ?: null;
    }
    if($host==='gentoo-home'){
        $dbPath=cms_private_directory().'/nodes/registry.sqlite3';
        if(!is_file($dbPath) || is_link($dbPath))
            throw new InvalidArgumentException('Gentoo node registry is unavailable.');
        $node=(new SirusNodeRegistry($dbPath))->getNode('gentoo-home');
        if(!$node || $node['health']!=='online' || ($node['os']??'')!=='gentoo')
            throw new InvalidArgumentException('Gentoo is offline; cannot approve stale installed code.');
        foreach(($node['metrics']['software_files']??[]) as $file){
            if(($file['path']??null)===$path &&
               is_string($file['sha256']??null) &&
               preg_match('/^[a-f0-9]{64}$/D',$file['sha256']))
                return $file['sha256'];
        }
    }
    return null;
}

function cms_release_create(PDO $db,string $host,string $path,string $commit,
    ?callable $installed=null,?int $now=null): string {
    cms_need_admin();
    $now??=time();
    $index=cms_release_index($now);
    $target=cms_release_target($index,$commit,$host,$path);
    $baseline=($installed ?? 'cms_release_installed')($host,$path);
    if(!is_string($baseline) || !preg_match('/^[a-f0-9]{64}$/D',$baseline))
        throw new InvalidArgumentException('Installed component hash is unavailable; cannot plan replacement.');
    if(hash_equals($target,$baseline))
        throw new InvalidArgumentException('Installed component already matches the selected revision.');
    cms_release_schema($db);
    $existing=$db->prepare("SELECT COUNT(*) FROM cms_source_release_plans
                         WHERE host=? AND source_path=? AND state IN ('planned','approved')");
    $existing->execute([$host,$path]);
    if((int)$existing->fetchColumn()>=3)
        throw new InvalidArgumentException('Too many pending plans for this component.');
    $id=bin2hex(random_bytes(16));
    $db->prepare('INSERT INTO cms_source_release_plans
        (id,host,source_path,commit_sha,target_sha256,baseline_sha256,state,
         created_by,created_at) VALUES(?,?,?,?,?,?,\'planned\',?,?)')
      ->execute([$id,$host,$path,$commit,$target,$baseline,(int)$_SESSION['cms_admin_id'],$now]);
    cms_audit($db,'plan-source-release',$host.':'.$path.':'.substr($commit,0,12));
    return $id;
}

function cms_release_approve(PDO $db,string $id,string $password,string $confirm,
    ?callable $installed=null,?int $now=null): void {
    cms_need_admin();
    $now??=time();
    if(!preg_match('/^[a-f0-9]{32}$/D',$id))
        throw new InvalidArgumentException('Invalid release plan ID.');
    cms_release_schema($db);
    $select=$db->prepare('SELECT * FROM cms_source_release_plans WHERE id=?');
    $select->execute([$id]);$plan=$select->fetch(PDO::FETCH_ASSOC);
    if(!$plan || $plan['state']!=='planned')
        throw new InvalidArgumentException('This release plan can no longer be approved.');
    if($confirm!=='APPROVE '.substr($plan['commit_sha'],0,12))
        throw new InvalidArgumentException('Type the exact commit approval phrase.');
    $user=$db->prepare('SELECT password_hash FROM cms_admins WHERE id=?');
    $user->execute([(int)$_SESSION['cms_admin_id']]);
    $hash=$user->fetchColumn();
    if(!is_string($hash) || !password_verify($password,$hash))
        throw new InvalidArgumentException('Administrator password was incorrect.');
    if($now-(int)$plan['created_at']>3600)
        throw new InvalidArgumentException('Plan expired; generate a fresh preflight.');
    $index=cms_release_index($now);
    $target=cms_release_target($index,$plan['commit_sha'],$plan['host'],$plan['source_path']);
    if(!hash_equals($target,$plan['target_sha256']))
        throw new InvalidArgumentException('Approved GitHub fingerprint changed; create a fresh plan.');
    $current=($installed ?? 'cms_release_installed')($plan['host'],$plan['source_path']);
    if(!is_string($current) || !hash_equals($current,$plan['baseline_sha256']))
        throw new InvalidArgumentException('Installed code has changed; create a fresh plan.');
    $db->beginTransaction();
    try{
        $update=$db->prepare("UPDATE cms_source_release_plans
                              SET state='approved',approved_at=?
                              WHERE id=? AND state='planned'");
        $update->execute([$now,$id]);
        if($update->rowCount()!==1)
            throw new InvalidArgumentException('Release plan was already processed.');
        cms_audit($db,'approve-source-release',$plan['host'].':'.$id);
        $db->commit();
    }catch(Throwable $error){
        if($db->inTransaction())$db->rollBack();
        throw $error;
    }
}

function cms_release_cancel(PDO $db,string $id): void {
    cms_need_admin();
    if(!preg_match('/^[a-f0-9]{32}$/D',$id))
        throw new InvalidArgumentException('Invalid release plan ID.');
    cms_release_schema($db);
    $st=$db->prepare("UPDATE cms_source_release_plans SET state='canceled'
                     WHERE id=? AND state IN ('planned','approved')");
    $st->execute([$id]);
    if($st->rowCount()!==1)
        throw new InvalidArgumentException('Active plan not found.');
    cms_audit($db,'cancel-source-release',$id);
}

function cms_release_recent(PDO $db): array {
    cms_need_admin();
    cms_release_schema($db);
    return $db->query('SELECT id,host,source_path,commit_sha,target_sha256,
               baseline_sha256,state,created_at,approved_at
               FROM cms_source_release_plans ORDER BY created_at DESC,id DESC LIMIT 30')
               ->fetchAll(PDO::FETCH_ASSOC);
}

/**
 * Queue a fixed, reviewed Ubuntu Python service deployment.
 * Only the separate root-owned controller can carry out the request.
 */
function cms_release_request_deployment(PDO $db,string $action,string $id,
    string $password,string $confirmation,?int $now=null): string {
    cms_need_admin();
    $now??=time();
    if(!preg_match('/^[a-f0-9]{32}$/D',$id) ||
       !in_array($action,['apply','rollback'],true))
        throw new InvalidArgumentException('Invalid deployment request.');
    cms_release_schema($db);
    $stmt=$db->prepare('SELECT * FROM cms_source_release_plans WHERE id=?');
    $stmt->execute([$id]);
    $plan=$stmt->fetch(PDO::FETCH_ASSOC);
    if(!$plan || $plan['state']!=='approved' || $plan['host']!=='ubuntu-primary' ||
       !in_array($plan['source_path'],['ubuntu-worker/worker.py','realtime/server.py'],true))
        throw new InvalidArgumentException('This component is not eligible for automatic deployment.');
    $phrase=($action==='apply'?'DEPLOY ':'ROLLBACK ').substr($plan['commit_sha'],0,12);
    if(!hash_equals($phrase,$confirmation))
        throw new InvalidArgumentException('Enter the exact deployment confirmation phrase.');
    $user=$db->prepare('SELECT password_hash FROM cms_admins WHERE id=?');
    $user->execute([(int)$_SESSION['cms_admin_id']]);
    $hash=$user->fetchColumn();
    if(!is_string($hash) || !password_verify($password,$hash))
        throw new InvalidArgumentException('Administrator password was incorrect.');
    if($action==='apply'){
        if(!0 <= $now-(int)$plan['approved_at'] ||
           $now-(int)$plan['approved_at']>86400)
            throw new InvalidArgumentException('Approved deployment is too old; review the release again.');
        $staging=cms_private_directory().'/nodes/source_artifacts';
        $artifact=$staging.'/'.$id.'.source';
        $manifest=$staging.'/'.$id.'.json';
        if(is_link($staging) || !is_dir($staging) ||
           is_link($artifact) || !is_file($artifact) ||
           is_link($manifest) || !is_file($manifest) ||
           filesize($artifact)>2000000 || filesize($manifest)>2048 ||
           !hash_equals($plan['target_sha256'],(string)hash_file('sha256',$artifact)))
            throw new InvalidArgumentException('Matching private source artifact has not been staged.');
        $data=json_decode((string)file_get_contents($manifest),true);
        if(!is_array($data) || ($data['id']??null)!==$id ||
           ($data['commit']??null)!==$plan['commit_sha'] ||
           ($data['target_sha256']??null)!==$plan['target_sha256'] ||
           ($data['baseline_sha256']??null)!==$plan['baseline_sha256'])
            throw new InvalidArgumentException('Staged release manifest is inconsistent.');
        $current=cms_release_installed($plan['host'],$plan['source_path']);
        if(!is_string($current) || !hash_equals($plan['baseline_sha256'],$current))
            throw new InvalidArgumentException('Installed component has changed since approval.');
    }
    $db->beginTransaction();
    try{
        $blocked=$db->prepare("SELECT COUNT(*) FROM cms_source_deploy_requests
                WHERE plan_id=? AND action=? AND state IN ('queued','running','done')");
        $blocked->execute([$id,$action]);
        if((int)$blocked->fetchColumn()>0)
            throw new InvalidArgumentException('That release operation has already been requested.');
        if($action==='rollback'){
            $successful=$db->prepare("SELECT COUNT(*) FROM cms_source_deploy_requests
                         WHERE plan_id=? AND action='apply' AND state='done'");
            $successful->execute([$id]);
            if((int)$successful->fetchColumn()!==1)
                throw new InvalidArgumentException('There is no verified website deployment to roll back.');
        }
        $request=bin2hex(random_bytes(16));
        $db->prepare("INSERT INTO cms_source_deploy_requests
            (id,plan_id,host,action,state,created_by,created_at,updated_at)
            VALUES(?,?,? ,?,'queued',?,?,?)")
           ->execute([$request,$id,$plan['host'],$action,
                       (int)$_SESSION['cms_admin_id'],$now,$now]);
        cms_audit($db,'queue-source-'.$action,$id);
        $db->commit();
        return $request;
    }catch(Throwable $error){
        if($db->inTransaction())$db->rollBack();
        throw $error;
    }
}

function cms_release_post(PDO $db,string $action): never {
    cms_need_admin();
    if($action==='plan_source_release'){
        $id=cms_release_create($db,(string)($_POST['host']??''),
            (string)($_POST['component']??''),(string)($_POST['commit']??''));
        $message='Release plan created; no code changed.';
        $state='planned';
    }elseif($action==='approve_source_release'){
        $id=(string)($_POST['id']??'');
        cms_release_approve($db,$id,(string)($_POST['password']??''),
            (string)($_POST['confirm']??''));
        $message='Release explicitly approved; installation is not enabled.';
        $state='approved';
    }elseif($action==='request_source_deploy' || $action==='request_source_rollback'){
        $operation=$action==='request_source_deploy'?'apply':'rollback';
        $id=cms_release_request_deployment($db,$operation,(string)($_POST['id']??''),
             (string)($_POST['password']??''),(string)($_POST['confirm']??''));
        $message='Deployment request queued for privileged local execution.';
        $state='queued';
    }elseif($action==='cancel_source_release'){
        $id=(string)($_POST['id']??'');
        cms_release_cancel($db,$id);
        $message='Release plan canceled.';
        $state='canceled';
    }else{
        throw new InvalidArgumentException('Unsupported source release operation.');
    }
    if(str_contains((string)($_SERVER['HTTP_ACCEPT']??''),'application/json')){
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        echo json_encode(['ok'=>true,'request_id'=>$id,'state'=>$state],JSON_THROW_ON_ERROR);
        exit;
    }
    cms_flash($message);
    cms_go('/admin/updates');
}

function cms_release_planner(PDO $db): void {
    cms_need_admin();
    $revisions=[];
    try{
        $index=cms_release_index();
        $revisions=$index['candidates'];
    }catch(Throwable $error){}
    ?>
    <div class="admin-panel">
      <h2>Source release plans</h2>
      <p>Choose an indexed GitHub revision and an installed component. Creating
      a plan does not modify any host. Approval requires your administrator password,
      the exact commit fingerprint and a fresh matching baseline.</p>
      <form method="post" action="/admin/updates" data-live-submit data-release-planner>
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="plan_source_release">
        <label>Host<select name="host" required>
          <option value="ubuntu-primary">Ubuntu gateway</option>
          <option value="gentoo-home">Gentoo compute</option>
        </select></label>
        <label>Component<select name="component" required>
          <?php foreach(cms_release_components() as $host=>$components):?>
            <optgroup label="<?=cms_esc($host)?>">
              <?php foreach($components as $path=>$name):?>
                <option value="<?=cms_esc($path)?>" data-release-host="<?=cms_esc($host)?>"><?=cms_esc($name)?></option>
              <?php endforeach;?>
            </optgroup>
          <?php endforeach;?>
        </select></label>
        <label>GitHub revision<select name="commit" data-live-release-commits required>
          <?php foreach($revisions as $rev):
            if(!is_array($rev) || !preg_match('/^[a-f0-9]{40}$/D',(string)($rev['commit']??'')))continue;?>
            <option value="<?=cms_esc($rev['commit'])?>"><?=cms_esc(substr($rev['commit'],0,12).' — '.($rev['message']??''))?></option>
          <?php endforeach;?>
        </select></label>
        <button type="submit" class="btn primary" <?=!$revisions?'disabled':''?>>Create release plan</button>
      </form>
      <p class="muted small">Plans expire after one hour. Approved plans are
      recorded for the later deployment executor. Only approved Ubuntu Python
      services can be submitted for installation after independent password
      confirmation. Full CMS and Gentoo releases remain manually supervised.</p>
    </div>
    <div class="admin-panel">
      <h2>Release plan history</h2>
      <div data-live-release-plans aria-live="polite">
        <div class="table-scroll"><table><thead><tr>
          <th>Host</th><th>Component</th><th>GitHub commit</th><th>Status</th><th>Control</th>
        </tr></thead><tbody>
        <?php foreach(cms_release_recent($db) as $plan):?>
          <tr><td><?=cms_esc($plan['host'])?></td>
              <td><?=cms_esc($plan['source_path'])?></td>
              <td><code><?=cms_esc(substr($plan['commit_sha'],0,12))?></code></td>
              <td><?=cms_esc($plan['state'])?></td>
              <td>Use live control once connected</td></tr>
        <?php endforeach;?>
        </tbody></table></div>
      </div>
    </div>
    <?php
}
