<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}
require_once __DIR__.'/ai_imports.php';
require_once __DIR__.'/ai_continuous.php';

/** Private curated knowledge; no crawlers, execution, or model training in PHP. */
function cms_ai_expertise():array {
    return [
        'coding'=>'Coding',
        'science'=>'Science',
        'physics'=>'Physics',
        'time'=>'Time',
        'relativity'=>'Relativity',
        'finance'=>'Finance',
        'mechanics'=>'Mechanics'
    ];
}
function cms_ai_schema(PDO $db):void {
    // Upgrade the original three-choice CHECK without discarding any existing rows.
    // Parent is replaced under foreign_keys=OFF; children still reference the same name.
    $sql=(string)$db->query("SELECT sql FROM sqlite_master WHERE type='table' AND name='cms_ai_profiles'")->fetchColumn();
    if($sql!==''&&!str_contains($sql,"'mechanics'")){
        if($db->inTransaction())throw new RuntimeException('AI expertise migration requires no active transaction.');
        $fk=(int)$db->query('PRAGMA foreign_keys')->fetchColumn();
        $db->exec('PRAGMA foreign_keys=OFF');
        try{
            $db->beginTransaction();
            $db->exec("CREATE TABLE cms_ai_profiles_rebuild (
                slug TEXT PRIMARY KEY CHECK(slug IN ('coding','science','physics','time','relativity','finance','mechanics')),
                title TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)))");
            $db->exec('INSERT INTO cms_ai_profiles_rebuild(slug,title,enabled)
                SELECT slug,title,enabled FROM cms_ai_profiles');
            $db->exec('DROP TABLE cms_ai_profiles');
            $db->exec('ALTER TABLE cms_ai_profiles_rebuild RENAME TO cms_ai_profiles');
            $db->commit();
        }catch(Throwable $e){
            if($db->inTransaction())$db->rollBack();
            throw $e;
        }finally{$db->exec('PRAGMA foreign_keys='.($fk?'ON':'OFF'));}
        if($db->query('PRAGMA foreign_key_check')->fetchColumn()!==false)
            throw new RuntimeException('AI expertise migration foreign-key validation failed.');
    }
    $db->exec("CREATE TABLE IF NOT EXISTS cms_ai_profiles (
        slug TEXT PRIMARY KEY CHECK(slug IN ('coding','science','physics','time','relativity','finance','mechanics')),
        title TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)))");
    $db->exec("CREATE TABLE IF NOT EXISTS cms_ai_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile TEXT NOT NULL REFERENCES cms_ai_profiles(slug),
        title TEXT NOT NULL,source_url TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL,reviewed INTEGER NOT NULL DEFAULT 0 CHECK(reviewed IN (0,1)),
        created_at TEXT NOT NULL)");
    $db->exec("CREATE TABLE IF NOT EXISTS cms_ai_examples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile TEXT NOT NULL REFERENCES cms_ai_profiles(slug),
        prompt TEXT NOT NULL,response TEXT NOT NULL,
        split TEXT NOT NULL CHECK(split IN ('train','eval')),
        reviewed INTEGER NOT NULL DEFAULT 0 CHECK(reviewed IN (0,1)),
        created_at TEXT NOT NULL)");
    $db->exec('CREATE INDEX IF NOT EXISTS cms_ai_documents_review ON cms_ai_documents(profile,reviewed)');
    $db->exec('CREATE INDEX IF NOT EXISTS cms_ai_examples_review ON cms_ai_examples(split,reviewed,profile)');
    cms_ai_import_schema($db);
    $st=$db->prepare('INSERT OR IGNORE INTO cms_ai_profiles(slug,title) VALUES(?,?)');
    foreach(cms_ai_expertise() as $slug=>$title)$st->execute([$slug,$title]);
    // Preserve the existing three profile assignments and data; new topics start empty.
    $db->prepare('UPDATE cms_ai_profiles SET title=? WHERE slug=?')->execute(['Physics','physics']);
    $db->prepare('UPDATE cms_ai_profiles SET title=? WHERE slug=?')->execute(['Time','time']);
}
function cms_ai_profile(mixed $value):string {
    if(!is_string($value)||!array_key_exists($value,cms_ai_expertise()))
        throw new InvalidArgumentException('Invalid expertise profile.');
    return $value;
}
function cms_ai_valid_document(array $input):array {
    $profile=cms_ai_profile($input['profile']??null);
    $title=cms_text($input['title']??null,160,'reference title',true);
    $source=cms_text($input['source_url']??'',240,'source URL');
    if($source!==''&&(!str_starts_with($source,'https://')||filter_var($source,FILTER_VALIDATE_URL)===false))
        throw new InvalidArgumentException('Reference URL must use HTTPS.');
    $content=cms_text($input['content']??null,60000,'reference content',true);
    return [$profile,$title,$source,$content,($input['reviewed']??null)==='1'?1:0];
}
function cms_ai_valid_example(array $input):array {
    $profile=cms_ai_profile($input['profile']??null);
    $split=$input['split']??null;
    if(!in_array($split,['train','eval'],true))throw new InvalidArgumentException('Invalid dataset split.');
    return [$profile,cms_text($input['prompt']??null,12000,'instruction',true),
        cms_text($input['response']??null,30000,'answer',true),
        $split,($input['reviewed']??null)==='1'?1:0];
}
/** Build a side-effect-free stream of records; also useful for evaluation tests. */
function cms_ai_export_records(PDO $db,string $kind):Generator {
    if(!in_array($kind,['knowledge','train','eval'],true))throw new InvalidArgumentException('Invalid export.');
    if($kind==='knowledge'){
        $st=$db->query('SELECT d.id,d.profile,d.title,d.source_url,d.content FROM cms_ai_documents d
            JOIN cms_ai_profiles p ON p.slug=d.profile WHERE p.enabled=1 AND d.reviewed=1 ORDER BY d.id');
        while($d=$st->fetch(PDO::FETCH_ASSOC))yield [
            'kind'=>'sirus-ai-document-v1','profile'=>$d['profile'],'title'=>$d['title'],
            'source'=>$d['source_url'],'text'=>$d['content'],
            'sha256'=>hash('sha256',$d['content']),'reviewed'=>true];
    } else {
        $st=$db->prepare('SELECT e.profile,e.prompt,e.response FROM cms_ai_examples e
            JOIN cms_ai_profiles p ON p.slug=e.profile
            WHERE p.enabled=1 AND e.reviewed=1 AND e.split=? ORDER BY e.id');
        $st->execute([$kind]);
        while($e=$st->fetch(PDO::FETCH_ASSOC))yield ['profile'=>$e['profile'],'messages'=>[
            ['role'=>'user','content'=>$e['prompt']],['role'=>'assistant','content'=>$e['response']]]];
    }
}
function cms_ai_export(PDO $db,string $kind):never {
    cms_need_admin();
    if(!in_array($kind,['knowledge','train','eval'],true))throw new InvalidArgumentException('Invalid export.');
    header('Content-Type: application/x-ndjson; charset=utf-8');
    header('Content-Disposition: attachment; filename="sirus-ai-'.$kind.'.jsonl"');
    header('Cache-Control: private, no-store');
    header('X-Content-Type-Options: nosniff');
    foreach(cms_ai_export_records($db,$kind) as $row)
        echo json_encode($row,JSON_UNESCAPED_UNICODE|JSON_INVALID_UTF8_SUBSTITUTE|JSON_THROW_ON_ERROR)."\n";
    exit;
}
/** The caller checks the admin session and CSRF token before dispatching here. */
function cms_ai_post(PDO $db,string $action):never {
    cms_need_admin();
    if(in_array($action,['ai_set_public_profiles','ai_save_viewer_profiles'],true))
        cms_ai_profile_grants_post($db,$action);
    if($action==='ai_continuous')cms_ai_continuous_post($db,$_POST);
    if($action==='ai_continuous_export')cms_ai_continuous_export($db,$_POST);
    if($action==='ai_evidence')cms_ai_evidence_post($db,$_POST);
    if(str_starts_with($action,'export_'))cms_ai_export($db,substr($action,7));
    if(in_array($action,['ai_upload_pdfs','ai_download_pdf','ai_research_settings','ai_export_research_config','ai_import_manual_draft','ai_approve_manual_batch'],true))
        cms_ai_import_post($db,$action);
    if($action==='ai_add_document'){
        $fields=cms_ai_valid_document($_POST);
        $db->prepare('INSERT INTO cms_ai_documents(profile,title,source_url,content,reviewed,created_at)
            VALUES(?,?,?,?,?,?)')->execute([...$fields,cms_now()]);
        cms_audit($db,'ai-document-add',$fields[0].':'.$db->lastInsertId());
    } elseif($action==='ai_add_example'){
        $fields=cms_ai_valid_example($_POST);
        $db->prepare('INSERT INTO cms_ai_examples(profile,prompt,response,split,reviewed,created_at)
            VALUES(?,?,?,?,?,?)')->execute([...$fields,cms_now()]);
        cms_audit($db,'ai-example-add',$fields[0].':'.$db->lastInsertId());
    } elseif($action==='ai_profile'){
        $profile=cms_ai_profile($_POST['profile']??null);
        $enabled=($_POST['enabled']??null)==='1'?1:0;
        $db->prepare('UPDATE cms_ai_profiles SET enabled=? WHERE slug=?')->execute([$enabled,$profile]);
        cms_audit($db,'ai-profile-toggle',$profile.':'.$enabled);
    } elseif(in_array($action,['ai_review_document','ai_review_example','ai_delete_document','ai_delete_example'],true)){
        $table=str_ends_with($action,'document')?'cms_ai_documents':'cms_ai_examples';
        $id=cms_get_int($_POST['id']??null);
        if(str_starts_with($action,'ai_delete')){
            if(($_POST['confirm']??null)!=='DELETE')throw new InvalidArgumentException('Deletion requires confirmation.');
            $st=$db->prepare("DELETE FROM $table WHERE id=?");$st->execute([$id]);
        } else {
            $reviewed=($_POST['reviewed']??null)==='1'?1:0;
            $st=$db->prepare("UPDATE $table SET reviewed=? WHERE id=?");
            $st->execute([$reviewed,$id]);
        }
        if($st->rowCount()!==1)throw new InvalidArgumentException('Record missing or unchanged.');
        cms_audit($db,'ai-record-update',$table.':'.$id);
    } else throw new InvalidArgumentException('Unknown AI action.');
    cms_flash('AI knowledge registry updated.');
    cms_go('/admin/ai');
}

/** Admin-managed knowledge category publication and per-account grants. */
function cms_ai_profile_grants_post(PDO $db,string $action):never {
    cms_need_admin();
    require_once __DIR__.'/ai_workspace.php';
    cms_ai_workspace_schema($db);
    $options=array_keys(cms_ai_expertise());
    $selected=$_POST['profiles']??[];
    if(!is_array($selected)||count($selected)>count($options) ||
       count($selected)!==count(array_unique($selected)))
        throw new InvalidArgumentException('Invalid category selection.');
    foreach($selected as $profile){
        if(!is_string($profile)||!in_array($profile,$options,true))
            throw new InvalidArgumentException('Invalid category.');
    }
    $db->beginTransaction();
    try {
        if($action==='ai_set_public_profiles'){
            $db->exec('DELETE FROM cms_ai_category_public');
            $stmt=$db->prepare('INSERT INTO cms_ai_category_public(profile,enabled) VALUES(?,1)');
            foreach($selected as $profile)$stmt->execute([$profile]);
            cms_audit($db,'ai-public-profiles',implode(',',$selected));
        }else{
            $viewer=cms_get_int($_POST['viewer_id']??null);
            $st=$db->prepare('SELECT enabled FROM cms_viewers WHERE id=?');$st->execute([$viewer]);
            if($st->fetchColumn()===false)throw new InvalidArgumentException('User not found.');
            $db->prepare('DELETE FROM cms_ai_category_grants WHERE viewer_id=?')->execute([$viewer]);
            $stmt=$db->prepare('INSERT INTO cms_ai_category_grants(viewer_id,profile) VALUES(?,?)');
            foreach($selected as $profile)$stmt->execute([$viewer,$profile]);
            cms_audit($db,'ai-user-profiles',(string)$viewer);
        }
        $db->commit();
    }catch(Throwable $e){if($db->inTransaction())$db->rollBack();throw $e;}
    cms_flash('AI category permissions updated.');
    cms_go('/admin/ai');
}
function cms_ai_profile_permissions_panel(PDO $db):void {
    require_once __DIR__.'/ai_workspace.php';
    cms_ai_workspace_schema($db);
    $public=$db->query('SELECT profile FROM cms_ai_category_public WHERE enabled=1')
       ->fetchAll(PDO::FETCH_COLUMN);
    $viewers=$db->query('SELECT id,username FROM cms_viewers WHERE enabled=1 ORDER BY username LIMIT 300')
       ->fetchAll(PDO::FETCH_ASSOC);
    $grants=$db->query('SELECT viewer_id,profile FROM cms_ai_category_grants')->fetchAll(PDO::FETCH_ASSOC);
    $byViewer=[];
    foreach($grants as $grant)$byViewer[$grant['viewer_id']][]=$grant['profile'];
    ?><div class="admin-panel"><h2>AI category permissions</h2>
      <p>Public categories can be queried by guests. Only publish categories whose reviewed content is safe for everyone.</p>
      <form method="post" action="/admin/ai">
      <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
      <input type="hidden" name="action" value="ai_set_public_profiles">
      <?php foreach(cms_ai_expertise() as $slug=>$title):?>
        <label><input type="checkbox" name="profiles[]" value="<?=cms_esc($slug)?>"
          <?=in_array($slug,$public,true)?'checked':''?>> Public <?=cms_esc($title)?></label>
      <?php endforeach;?>
      <button class="btn primary" type="submit">Save public categories</button></form>
      <h3>Registered user grants</h3>
      <?php foreach($viewers as $viewer):?>
        <form method="post" action="/admin/ai">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_save_viewer_profiles">
        <input type="hidden" name="viewer_id" value="<?=(int)$viewer['id']?>">
        <strong><?=cms_esc($viewer['username'])?></strong>
        <?php foreach(cms_ai_expertise() as $slug=>$title):?>
          <label><input type="checkbox" name="profiles[]" value="<?=cms_esc($slug)?>"
           <?=in_array($slug,$byViewer[$viewer['id']]??[],true)?'checked':''?>> <?=cms_esc($title)?></label>
        <?php endforeach;?>
        <button class="btn subtle" type="submit">Save user access</button></form>
      <?php endforeach;?>
      </div><?php
}

function cms_ai_page(PDO $db,?string $error=null):void {
    cms_need_admin();
    $profiles=$db->query('SELECT * FROM cms_ai_profiles ORDER BY slug')->fetchAll(PDO::FETCH_ASSOC);
    cms_admin_shell_start($db,'AI Knowledge & Training');
    if($error)echo '<div class="form-error">'.cms_esc($error).'</div>';
    $flash=cms_take_flash();
    if($flash)echo '<div class="flash-message">'.cms_esc($flash['message']??'').'</div>';
    ?>
    <div class="admin-panel"><h2>Private specialization library</h2>
      <p>Curate reviewed sources and verified examples for seven expertise areas including finance and mechanics. This page never sends files to Gentoo, trains weights, or executes remote code.</p>
      <p class="muted">Exports include reviewed items from enabled profiles only. Evaluation questions are withheld from training exports.</p>
      <div class="form-actions">
        <?php foreach(['knowledge'=>'Knowledge for RAG','train'=>'LoRA training','eval'=>'Held-out evaluation'] as $kind=>$label):?>
        <form method="post" action="/admin/ai" class="inline-action">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="export_<?=cms_esc($kind)?>">
          <button class="btn small" type="submit">Export <?=cms_esc($label)?> JSONL</button>
        </form><?php endforeach;?>
      </div>
    </div>
    <?php cms_ai_continuous_panel($db); ?>
    <?php cms_ai_evidence_panel($db); ?>
    <?php cms_gentoo_pdf_panel($db); ?>
    <?php cms_ai_import_panel($db); ?>
    <div class="admin-panel"><h2>Profiles</h2>
      <div class="ai-profiles"><?php foreach($profiles as $p):?>
        <form method="post" action="/admin/ai" class="ai-profile">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="ai_profile">
          <input type="hidden" name="profile" value="<?=cms_esc($p['slug'])?>">
          <strong><?=cms_esc($p['title'])?></strong>
          <label><input type="checkbox" name="enabled" value="1" <?=(int)$p['enabled']===1?'checked':''?>> Included in exports</label>
          <button type="submit" class="btn small">Save</button>
        </form><?php endforeach;?></div>
    </div>
    <div class="admin-panel"><h2>Add reference</h2>
      <form method="post" action="/admin/ai">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_add_document">
        <div class="form-grid">
          <label>Subject<select name="profile"><?php foreach($profiles as $p):?><option value="<?=cms_esc($p['slug'])?>"><?=cms_esc($p['title'])?></option><?php endforeach;?></select></label>
          <label>Title<input name="title" maxlength="160" required></label>
          <label>Source HTTPS URL (optional)<input name="source_url" type="url" maxlength="240"></label>
        </div>
        <label>Verified source text<textarea name="content" maxlength="60000" rows="8" required></textarea></label>
        <label><input type="checkbox" name="reviewed" value="1"> Reviewed for factual quality and usage rights</label>
        <button class="btn primary" type="submit">Save reference</button>
      </form>
    </div>
    <div class="admin-panel"><h2>Add supervised example</h2>
      <form method="post" action="/admin/ai">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_add_example">
        <div class="form-grid">
          <label>Subject<select name="profile"><?php foreach($profiles as $p):?><option value="<?=cms_esc($p['slug'])?>"><?=cms_esc($p['title'])?></option><?php endforeach;?></select></label>
          <label>Dataset<select name="split"><option value="train">Training</option><option value="eval">Held-out evaluation</option></select></label>
        </div>
        <label>Question / instruction<textarea name="prompt" maxlength="12000" rows="4" required></textarea></label>
        <label>Verified ideal answer<textarea name="response" maxlength="30000" rows="6" required></textarea></label>
        <label><input type="checkbox" name="reviewed" value="1"> Reviewed for accuracy</label>
        <button class="btn primary" type="submit">Save example</button>
      </form>
    </div>
    <?php foreach(['cms_ai_documents'=>['References','title','ai_review_document','ai_delete_document'],
                  'cms_ai_examples'=>['Examples','prompt','ai_review_example','ai_delete_example']] as $table=>$meta):
        $records=$db->query("SELECT id,profile,$meta[1],reviewed FROM $table ORDER BY id DESC LIMIT 100")->fetchAll(PDO::FETCH_ASSOC);?>
      <div class="admin-panel table-scroll"><h2><?=cms_esc($meta[0])?> (latest 100)</h2>
        <table><thead><tr><th>ID</th><th>Profile</th><th>Content</th><th>Review</th><th>Actions</th></tr></thead><tbody>
        <?php foreach($records as $record):?><tr>
          <td><?=(int)$record['id']?></td><td><?=cms_esc($record['profile'])?></td>
          <td><?=cms_esc(substr($record[$meta[1]],0,160))?></td>
          <td><?=(int)$record['reviewed']===1?'Approved':'Pending'?></td>
          <td>
          <form method="post" action="/admin/ai" class="inline-action">
            <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
            <input type="hidden" name="action" value="<?=cms_esc($meta[2])?>">
            <input type="hidden" name="id" value="<?=(int)$record['id']?>">
            <input type="hidden" name="reviewed" value="<?=(int)$record['reviewed']===1?'0':'1'?>">
            <button class="btn small" type="submit"><?=(int)$record['reviewed']===1?'Unreview':'Approve'?></button>
          </form>
          <form method="post" action="/admin/ai" class="inline-action" data-confirm="Delete this AI record permanently?">
            <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
            <input type="hidden" name="action" value="<?=cms_esc($meta[3])?>">
            <input type="hidden" name="id" value="<?=(int)$record['id']?>">
            <input type="hidden" name="confirm" value="DELETE">
            <button class="btn small danger" type="submit">Delete</button>
          </form></td>
        </tr><?php endforeach;?></tbody></table>
      </div>
    <?php endforeach;
    cms_ai_profile_permissions_panel($db);
    cms_admin_shell_end($db);
}
