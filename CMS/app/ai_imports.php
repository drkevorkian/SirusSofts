<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/** Admin-only staging: PDFs are never served from public_html or executed. */
function cms_ai_import_schema(PDO $db):void {
    $db->exec("CREATE TABLE IF NOT EXISTS cms_ai_pdf_uploads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile TEXT NOT NULL REFERENCES cms_ai_profiles(slug),
        title TEXT NOT NULL,original_name TEXT NOT NULL,file_key TEXT NOT NULL UNIQUE,
        sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'staged'
           CHECK(status IN ('staged','processed')),
        created_at TEXT NOT NULL)");
    $db->exec('CREATE INDEX IF NOT EXISTS cms_ai_pdf_profiles ON cms_ai_pdf_uploads(profile,created_at)');
    $db->exec("CREATE TABLE IF NOT EXISTS cms_ai_research_config (
        id INTEGER PRIMARY KEY CHECK(id=1),depth INTEGER NOT NULL DEFAULT 50
        CHECK(depth BETWEEN 1 AND 5000),
        engines TEXT NOT NULL DEFAULT 'google,bing,yahoo,duckduckgo,brave,mojeek')");
    $db->exec('INSERT OR IGNORE INTO cms_ai_research_config(id) VALUES(1)');
}
function cms_ai_depth(mixed $raw):int {
    if(!is_string($raw)||!preg_match('/^[1-9][0-9]{0,3}$/D',$raw)
       ||(int)$raw>5000)
        throw new InvalidArgumentException('Search depth must be 1–5000 URLs.');
    return (int)$raw;
}
function cms_ai_engine_names():array {
    return ['google'=>'Google','bing'=>'Bing / MSN','yahoo'=>'Yahoo',
            'duckduckgo'=>'DuckDuckGo','brave'=>'Brave','mojeek'=>'Mojeek'];
}
function cms_ai_engine_selection(mixed $raw):array {
    if(!is_array($raw))throw new InvalidArgumentException('Choose at least one engine.');
    if(count($raw)<1 || count($raw)>6)throw new InvalidArgumentException('Choose 1–6 engines.');
    $selection=[];
    foreach($raw as $name){
        if(!is_string($name)||!array_key_exists($name,cms_ai_engine_names())
           ||in_array($name,$selection,true))
            throw new InvalidArgumentException('Invalid or duplicate search engine.');
        $selection[]=$name;
    }
    return $selection;
}
function cms_ai_pdf_directory():string {
    $root=cms_private_directory();
    $folder=$root.'/ai-pdf-uploads';
    if(is_link($folder))throw new RuntimeException('Unsafe PDF storage path.');
    if(!is_dir($folder)&&!mkdir($folder,0700,true)&&!is_dir($folder))
        throw new RuntimeException('Could not initialize private PDF storage.');
    if(is_link($folder)||!is_dir($folder)||!str_starts_with(
        (string)realpath($folder),(string)realpath($root).'/'))
        throw new RuntimeException('PDF storage is outside private root.');
    return $folder;
}
function cms_ai_pdf_filename(mixed $raw):string {
    if(!is_string($raw))throw new InvalidArgumentException('Invalid PDF filename.');
    $name=basename(str_replace('\\','/',$raw));
    if(strlen($name)<5||strlen($name)>180||!preg_match('/\.pdf$/iD',$name)||
        preg_match('/[\x00-\x1f\x7f]/',$name))
        throw new InvalidArgumentException('PDF must have a .pdf filename under 180 bytes.');
    return $name;
}
function cms_ai_pdf_header(string $path):bool {
    if(!is_file($path)||is_link($path))return false;
    $f=fopen($path,'rb');
    if(!$f)return false;
    try{$prefix=fread($f,8);}finally{fclose($f);}
    return is_string($prefix)&&str_starts_with($prefix,'%PDF-');
}
function cms_ai_upload_pdf(PDO $db,array $file,string $profile):void {
    $name=cms_ai_pdf_filename($file['name']??null);
    if(($file['error']??-1)!==UPLOAD_ERR_OK)
        throw new InvalidArgumentException('PDF upload failed (PHP/Nginx size limits may apply).');
    $bytes=$file['size']??null;
    $tmp=$file['tmp_name']??null;
    if(!is_int($bytes)||$bytes<32||$bytes>25*1024*1024)
        throw new InvalidArgumentException('Each PDF must be 32 bytes to 25 MiB.');
    if(!is_string($tmp)||!is_uploaded_file($tmp)||!cms_ai_pdf_header($tmp))
        throw new InvalidArgumentException('Uploaded file is not a valid PDF upload.');
    $mime=(new finfo(FILEINFO_MIME_TYPE))->file($tmp);
    if(!in_array($mime,['application/pdf','application/octet-stream'],true))
        throw new InvalidArgumentException('Uploaded content is not a PDF.');
    $folder=cms_ai_pdf_directory();
    $key=bin2hex(random_bytes(20)).'.pdf';
    $dst=$folder.'/'.$key;
    if(!move_uploaded_file($tmp,$dst))throw new RuntimeException('Could not store PDF.');
    chmod($dst,0600);
    try{
        $db->prepare('INSERT INTO cms_ai_pdf_uploads
            (profile,title,original_name,file_key,sha256,bytes,status,created_at)
            VALUES(?,?,?,?,?,?,?,?)')
           ->execute([$profile,substr($name,0,160),$name,$key,
                      hash_file('sha256',$dst),$bytes,'staged',cms_now()]);
        cms_audit($db,'ai-pdf-staged',$profile.':'.$db->lastInsertId());
    }catch(Throwable $e){unlink($dst);throw $e;}
}
function cms_ai_upload_manuals(PDO $db):never {
    $profile=cms_ai_profile($_POST['profile']??null);
    $source=$_FILES['manuals']??null;
    if(!is_array($source)||!isset($source['name'])||!is_array($source['name']))
        throw new InvalidArgumentException('Select one or more PDF files.');
    $count=count($source['name']);
    if($count<1||$count>10)throw new InvalidArgumentException('Upload 1–10 PDFs per batch.');
    $fileKeys=['name','type','tmp_name','error','size'];
    // Validate all upload errors and filenames before moving any file.
    foreach(range(0,$count-1) as $i){
        foreach($fileKeys as $key){
            if(!isset($source[$key][$i]))
                throw new InvalidArgumentException('Malformed PDF upload.');
        }
        cms_ai_pdf_filename($source['name'][$i]);
        if($source['error'][$i]!==UPLOAD_ERR_OK)
            throw new InvalidArgumentException('PDF upload rejected by PHP/Nginx; check size limits.');
    }
    foreach(range(0,$count-1) as $i){
        $one=[];
        foreach($fileKeys as $key)$one[$key]=$source[$key][$i];
        cms_ai_upload_pdf($db,$one,$profile);
    }
    cms_flash($count.' PDFs staged privately. Export/transfer for Gentoo extraction; nothing was automatically approved.');
    cms_go('/admin/ai');
}
function cms_ai_download_pdf(PDO $db):never {
    $id=cms_get_int($_POST['id']??null);
    $st=$db->prepare('SELECT original_name,file_key,sha256 FROM cms_ai_pdf_uploads WHERE id=?');
    $st->execute([$id]);$row=$st->fetch(PDO::FETCH_ASSOC);
    if(!$row||!preg_match('/^[0-9a-f]{40}\.pdf$/D',$row['file_key']))
        throw new InvalidArgumentException('PDF not found.');
    $file=cms_ai_pdf_directory().'/'.$row['file_key'];
    if(is_link($file)||!is_file($file))
        throw new RuntimeException('Private PDF storage is unavailable.');
    if(!hash_equals($row['sha256'],hash_file('sha256',$file)))
        throw new RuntimeException('Stored PDF integrity verification failed.');
    $name=preg_replace('/[^a-zA-Z0-9._-]/','_',basename($row['original_name']));
    header('Content-Type: application/pdf');
    header('Content-Disposition: attachment; filename="'.$name.'"');
    header('Cache-Control: private, no-store');
    header('X-Content-Type-Options: nosniff');
    header('Content-Length: '.filesize($file));
    readfile($file);
    exit;
}
function cms_ai_import_post(PDO $db,string $action):never {
    cms_need_admin();
    if($action==='ai_upload_pdfs')cms_ai_upload_manuals($db);
    if($action==='ai_download_pdf')cms_ai_download_pdf($db);
    if($action==='ai_research_settings'){
        $depth=cms_ai_depth($_POST['depth']??null);
        $engines=cms_ai_engine_selection($_POST['engines']??null);
        $db->prepare('UPDATE cms_ai_research_config SET depth=?,engines=? WHERE id=1')
           ->execute([$depth,implode(',',$engines)]);
        cms_audit($db,'ai-search-settings',$depth.':'.implode(',',$engines));
        cms_flash('Search policy saved. Gentoo deployment/config synchronization is still required.');
        cms_go('/admin/ai');
    }
    if($action==='ai_export_research_config'){
        $row=$db->query('SELECT depth,engines FROM cms_ai_research_config WHERE id=1')
                ->fetch(PDO::FETCH_ASSOC);
        header('Content-Type: application/json; charset=utf-8');
        header('Content-Disposition: attachment; filename="sirus-ai-research-config.json"');
        header('Cache-Control: private, no-store');
        echo json_encode(['schema'=>'sirus-ai-research-v1','depth'=>(int)$row['depth'],
            'engines'=>explode(',',$row['engines'])],JSON_THROW_ON_ERROR|JSON_PRETTY_PRINT);
        exit;
    }
    throw new InvalidArgumentException('Unknown AI import action.');
}
function cms_ai_import_panel(PDO $db):void {
    $settings=$db->query('SELECT depth,engines FROM cms_ai_research_config WHERE id=1')
                 ->fetch(PDO::FETCH_ASSOC);
    $selected=explode(',',$settings['engines']);
    $pdfs=$db->query('SELECT id,profile,original_name,bytes,status,created_at
        FROM cms_ai_pdf_uploads ORDER BY id DESC LIMIT 100')->fetchAll(PDO::FETCH_ASSOC);
    ?>
    <div class="admin-panel"><h2>Upload service manuals (PDF)</h2>
      <p>Upload up to ten PDF files per batch, each at most 25 MiB. Files are stored privately on Ubuntu, never in the public website. Files are staged, not executed, analyzed, approved, or sent automatically.</p>
      <p class="muted">PHP post_max_size, upload_max_filesize and Nginx client_max_body_size may be lower; verify those limits before uploading large manuals.</p>
      <form method="post" enctype="multipart/form-data" action="/admin/ai">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_upload_pdfs">
        <label>Expertise<select name="profile"><?php foreach(cms_ai_expertise() as $slug=>$title):?>
          <option value="<?=cms_esc($slug)?>" <?=$slug==='mechanics'?'selected':''?>><?=cms_esc($title)?></option>
          <?php endforeach;?></select></label>
        <label>PDF manuals<input type="file" name="manuals[]" accept="application/pdf,.pdf" multiple required></label>
        <button type="submit" class="btn primary">Stage PDFs privately</button>
      </form>
      <p class="muted">Gentoo extraction, page-level indexing, and an AI summary are separate, reviewed processing steps. Do not treat a staged file as searchable knowledge.</p>
      <div class="table-scroll"><table><thead><tr><th>Manual</th><th>Expertise</th><th>Size</th><th>Status</th><th>Download</th></tr></thead><tbody>
        <?php foreach($pdfs as $pdf):?><tr><td><?=cms_esc($pdf['original_name'])?></td>
        <td><?=cms_esc($pdf['profile'])?></td><td><?=(int)$pdf['bytes']?> B</td>
        <td><?=cms_esc($pdf['status'])?></td><td>
        <form method="post" action="/admin/ai" class="inline-action">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="ai_download_pdf">
          <input type="hidden" name="id" value="<?=(int)$pdf['id']?>">
          <button class="btn small" type="submit">Download</button>
        </form></td></tr><?php endforeach;?></tbody></table></div>
    </div>
    <div class="admin-panel"><h2>Multi-engine research settings</h2>
      <p>Search depth is the desired number of unique candidate URLs, not recursive crawling depth. Default 50, maximum 5,000. Educational and primary sources rank first; engine limits may return fewer candidates.</p>
      <form method="post" action="/admin/ai">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_research_settings">
        <label>Search depth (1–5000)<input type="number" name="depth" min="1" max="5000" value="<?=(int)$settings['depth']?>" required></label>
        <fieldset><legend>Search engines</legend>
        <?php foreach(cms_ai_engine_names() as $slug=>$title):?>
          <label><input type="checkbox" name="engines[]" value="<?=cms_esc($slug)?>" <?=in_array($slug,$selected,true)?'checked':''?>><?=cms_esc($title)?></label>
        <?php endforeach;?></fieldset>
        <button class="btn primary" type="submit">Save research settings</button>
      </form>
      <form method="post" action="/admin/ai">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="ai_export_research_config">
        <button class="btn small" type="submit">Export Gentoo research settings JSON</button>
      </form>
      <p class="muted">These preferences are saved in the private CMS database. Web research requires a separately configured private Gentoo SearXNG service; this page does not crawl, summarize, or contact search engines yet.</p>
    </div>
    <?php
}
