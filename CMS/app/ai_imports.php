<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/** Admin-only staging: PDFs are never served from public_html or executed. */
const CMS_AI_PDF_MAX_BYTES = 200 * 1024 * 1024;
// Bound aggregate requests on the 2 GiB VPS; upload large manuals in separate batches.
const CMS_AI_PDF_BATCH_MAX_BYTES = 200 * 1024 * 1024;
function cms_ai_pdf_size_valid(mixed $size):bool {
    return is_int($size)&&$size>=32&&$size<=CMS_AI_PDF_MAX_BYTES;
}

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
    $db->exec("CREATE TABLE IF NOT EXISTS cms_ai_manual_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,
        document_count INTEGER NOT NULL,approved INTEGER NOT NULL DEFAULT 0
        CHECK(approved IN (0,1)),created_at TEXT NOT NULL)");
    $db->exec("CREATE TABLE IF NOT EXISTS cms_ai_manual_batch_docs (
        batch_id INTEGER NOT NULL REFERENCES cms_ai_manual_batches(id) ON DELETE CASCADE,
        document_id INTEGER NOT NULL REFERENCES cms_ai_documents(id) ON DELETE CASCADE,
        PRIMARY KEY(batch_id,document_id))");

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
    if(!cms_ai_pdf_size_valid($bytes))
        throw new InvalidArgumentException('Each PDF must be 32 bytes to 200 MiB.');
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
    // Check the full request before moving any file; the batch is also bounded.
    $batchBytes=0;
    foreach(range(0,$count-1) as $i){
        foreach($fileKeys as $key){
            if(!isset($source[$key][$i]))
                throw new InvalidArgumentException('Malformed PDF upload.');
        }
        cms_ai_pdf_filename($source['name'][$i]);
        if($source['error'][$i]!==UPLOAD_ERR_OK)
            throw new InvalidArgumentException('PDF upload rejected by PHP/Nginx; check size limits.');
        if(!cms_ai_pdf_size_valid($source['size'][$i]))
            throw new InvalidArgumentException('Each PDF must be 32 bytes to 200 MiB.');
        $batchBytes+=$source['size'][$i];
        if($batchBytes>CMS_AI_PDF_BATCH_MAX_BYTES)
            throw new InvalidArgumentException('Maximum 200 MiB total per upload batch; submit large manuals separately.');
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
/** Parse Gentoo page-extraction drafts; every imported row is UNREVIEWED. */
function cms_ai_parse_draft(string $raw):array {
    if($raw===''||strlen($raw)>8*1024*1024)
        throw new InvalidArgumentException('Draft JSONL must be 1 byte to 8 MiB.');
    $lines=explode("\n",$raw);$out=[];
    foreach($lines as $line){
        if(trim($line)==='')continue;
        if(count($out)>=1000)throw new InvalidArgumentException('At most 1000 excerpts per import.');
        try{$row=json_decode($line,true,12,JSON_THROW_ON_ERROR);}
        catch(JsonException $e){throw new InvalidArgumentException('Invalid draft JSONL.');}
        if(!is_array($row)||($row['kind']??null)!=='sirus-ai-document-v1'
           ||($row['reviewed']??null)!==false
           ||!is_int($row['pdf_page']??null)||$row['pdf_page']<1)
            throw new InvalidArgumentException('Only unapproved, page-attributed manual records can be imported.');
        $fields=cms_ai_valid_document(['profile'=>$row['profile']??null,
            'title'=>$row['title']??null,'source_url'=>$row['source']??'',
            'content'=>$row['text']??null,'reviewed'=>'0']);
        if(!is_string($row['sha256']??null)
           ||!hash_equals(hash('sha256',$fields[3]),$row['sha256']))
            throw new InvalidArgumentException('Manual excerpt integrity check failed.');
        $out[]=$fields;
    }
    if(!$out)throw new InvalidArgumentException('Draft contains no excerpts.');
    return $out;
}
function cms_ai_import_manual_draft(PDO $db):never {
    $file=$_FILES['manual_draft']??null;
    if(!is_array($file)||($file['error']??null)!==UPLOAD_ERR_OK
       ||!is_int($file['size']??null)||$file['size']>8*1024*1024
       ||!is_uploaded_file((string)($file['tmp_name']??'')))
        throw new InvalidArgumentException('Upload a valid Gentoo draft JSONL under 8 MiB.');
    $raw=file_get_contents($file['tmp_name']);
    if(!is_string($raw))throw new RuntimeException('Could not read draft.');
    $records=cms_ai_parse_draft($raw);
    $title=cms_text($_POST['batch_title']??null,160,'manual batch title',true);
    $db->beginTransaction();
    try{
        $db->prepare('INSERT INTO cms_ai_manual_batches(title,document_count,created_at)
            VALUES(?,?,?)')->execute([$title,count($records),cms_now()]);
        $batch=(int)$db->lastInsertId();
        $doc=$db->prepare('INSERT INTO cms_ai_documents
            (profile,title,source_url,content,reviewed,created_at)
            VALUES(?,?,?,?,0,?)');
        $relation=$db->prepare('INSERT INTO cms_ai_manual_batch_docs(batch_id,document_id) VALUES(?,?)');
        foreach($records as $fields){
            $doc->execute([$fields[0],$fields[1],$fields[2],$fields[3],cms_now()]);
            $relation->execute([$batch,$db->lastInsertId()]);
        }
        $db->commit();
    }catch(Throwable $e){if($db->inTransaction())$db->rollBack();throw $e;}
    cms_audit($db,'ai-manual-import',$batch.':'.count($records));
    cms_flash(count($records).' unreviewed manual excerpts imported. Review their source pages before approving the batch.');
    cms_go('/admin/ai');
}
function cms_ai_approve_manual_batch(PDO $db):never {
    $id=cms_get_int($_POST['batch_id']??null);
    if(($_POST['confirm']??null)!=='APPROVE MANUAL')
        throw new InvalidArgumentException('Explicit APPROVE MANUAL confirmation is required.');
    $db->beginTransaction();
    try{
        $st=$db->prepare('UPDATE cms_ai_manual_batches SET approved=1 WHERE id=? AND approved=0');
        $st->execute([$id]);
        if($st->rowCount()!==1)throw new InvalidArgumentException('Batch not found or already approved.');
        $db->prepare('UPDATE cms_ai_documents SET reviewed=1 WHERE id IN
            (SELECT document_id FROM cms_ai_manual_batch_docs WHERE batch_id=?)')->execute([$id]);
        $db->commit();
    }catch(Throwable $e){if($db->inTransaction())$db->rollBack();throw $e;}
    cms_audit($db,'ai-manual-approve',(string)$id);
    cms_flash('Reviewed manual batch approved. Export Knowledge for RAG to update Gentoo.');
    cms_go('/admin/ai');
}

function cms_ai_import_post(PDO $db,string $action):never {
    cms_need_admin();
    if($action==='ai_upload_pdfs')cms_ai_upload_manuals($db);
    if($action==='ai_import_manual_draft')cms_ai_import_manual_draft($db);
    if($action==='ai_approve_manual_batch')cms_ai_approve_manual_batch($db);
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
      <p>Upload up to ten PDF files per batch; each PDF may be up to 200 MiB, with a maximum of 200 MiB total per request. Files are stored privately on Ubuntu, never in the public website. Files are staged, not executed, analyzed, approved, or sent automatically.</p>
      <p class="muted">For 200 MiB uploads, configure the site's PHP-FPM upload_max_filesize to 200M and post_max_size to at least 220M; verify effective Hestia Nginx/Apache request limits. Large manuals should be uploaded one at a time.</p>
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
      <div class="admin-panel"><h2>Import extracted manual text for review</h2>
        <p>After transferring a PDF to Gentoo and running manual_ingest.py, upload its unapproved JSONL here. The file is validated, hashed and inserted as unreviewed page-attributed knowledge. Up to 1000 excerpts / 8 MiB per batch.</p>
        <form method="post" enctype="multipart/form-data" action="/admin/ai">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="ai_import_manual_draft">
          <label>Manual batch label<input type="text" name="batch_title" maxlength="160" required placeholder="Can-Am HD11 workshop manual, pages 1–200"></label>
          <label>Unapproved extraction JSONL<input type="file" name="manual_draft" accept=".jsonl,application/x-ndjson" required></label>
          <button class="btn primary" type="submit">Import as unreviewed</button>
        </form>
      </div>
      <div class="admin-panel"><h2>Manual review batches</h2>
        <p>Review the original PDF pages and the extracted references before bulk approval. Scanned diagrams, torque units, and tables may be missing or misread.</p>
        <div class="table-scroll"><table><thead><tr><th>Batch</th><th>Excerpts</th><th>Approved</th><th>Action</th></tr></thead><tbody>
        <?php foreach($db->query('SELECT * FROM cms_ai_manual_batches ORDER BY id DESC LIMIT 100')->fetchAll(PDO::FETCH_ASSOC) as $batch):?>
          <tr><td><?=cms_esc($batch['title'])?></td><td><?=(int)$batch['document_count']?></td>
          <td><?=(int)$batch['approved']===1?'Yes':'No'?></td><td>
            <?php if((int)$batch['approved']===0):?>
            <form method="post" action="/admin/ai" class="inline-action">
              <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
              <input type="hidden" name="action" value="ai_approve_manual_batch">
              <input type="hidden" name="batch_id" value="<?=(int)$batch['id']?>">
              <label>Confirmation<input type="text" name="confirm" placeholder="APPROVE MANUAL" required></label>
              <button class="btn small" type="submit">Approve this batch</button>
            </form>
            <?php else:?>Approved for next knowledge export<?php endif;?>
          </td></tr>
        <?php endforeach;?></tbody></table></div>
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
