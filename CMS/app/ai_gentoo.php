<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}

/** Admin-only server-side bridge over the EXISTING Gentoo SSH reverse tunnel. */
function cms_gentoo_pdf_call(string $operation,array $fields=[]):array {
    $allowed=['status','preview','pause','resume','workers','retry','approve','unapprove','approve_all'];
    if(!in_array($operation,$allowed,true))
        throw new InvalidArgumentException('Unsupported Gentoo PDF operation.');
    $node=(string)(getenv('SIRUS_GENTOO_PDF_NODE_ID')?:'gentoo-home');
    if(!preg_match('/^[a-z][a-z0-9-]{1,63}$/D',$node))
        throw new RuntimeException('Invalid Gentoo node configuration.');
    $keyPath=cms_private_directory().'/nodes/keys/'.$node.'.key';
    if(is_link($keyPath)||!is_file($keyPath))
        throw new RuntimeException('Gentoo node signing key is not configured on Ubuntu.');
    $key=file_get_contents($keyPath);
    if(!is_string($key)||strlen($key)!==32)
        throw new RuntimeException('Gentoo node signing key must contain 32 binary bytes.');
    if(!function_exists('curl_init'))
        throw new RuntimeException('CMS PHP cURL extension is required.');
    $envelope=array_merge(['node_id'=>$node,'sent_at'=>time(),
                           'nonce'=>bin2hex(random_bytes(16)),'op'=>$operation],$fields);
    $body=json_encode($envelope,JSON_THROW_ON_ERROR|JSON_UNESCAPED_SLASHES);
    if(strlen($body)>4096)throw new InvalidArgumentException('Request is too large.');
    $signature=hash_hmac('sha256',"sirus-pdf-admin-v1\n".$body,$key);
    $curl=curl_init('http://127.0.0.1:18480/internal/pdf-admin');
    if($curl===false)throw new RuntimeException('PDF backend request setup failed.');
    try {
        curl_setopt_array($curl,[
            CURLOPT_POST=>true,CURLOPT_POSTFIELDS=>$body,CURLOPT_RETURNTRANSFER=>true,
            CURLOPT_FOLLOWLOCATION=>false,CURLOPT_CONNECTTIMEOUT=>2,CURLOPT_TIMEOUT=>7,
            CURLOPT_PROXY=>'',CURLOPT_MAXREDIRS=>0,
            CURLOPT_HTTPHEADER=>['Content-Type: application/json',
                                 'Accept: application/json',
                                 'X-Sirus-Pdf-Signature: '.$signature]]);
        $raw=curl_exec($curl);
        $http=(int)curl_getinfo($curl,CURLINFO_HTTP_CODE);
    } finally {curl_close($curl);}
    if(!is_string($raw)||strlen($raw)>120000||$http!==200)
        throw new RuntimeException('Gentoo PDF control service is unavailable or rejected this request.');
    $parsed=json_decode($raw,true,32,JSON_THROW_ON_ERROR);
    if(!is_array($parsed)||($parsed['ok']??null)!==true)
        throw new RuntimeException('Unexpected Gentoo PDF service response.');
    return $parsed;
}
function cms_gentoo_pdf_filters():array {
    $state=$_GET['pdf_state']??'';
    $query=$_GET['pdf_search']??'';
    $page=$_GET['pdf_page']??'1';
    if(!is_string($state)||!in_array($state,
        ['', 'queued','processing','ready','failed','approved'],true))$state='';
    if(!is_string($query))$query='';
    $query=substr(trim($query),0,120);
    if(preg_match('/[\x00-\x1f]/',$query))$query='';
    if(!is_string($page)||!preg_match('/^[1-9][0-9]{0,5}$/D',$page))$page='1';
    return [$state,$query,min(100000,(int)$page)];
}
function cms_gentoo_pdf_query_url(string $state,string $q,int $page,string $sha='',int $part=0):string {
    $vars=['pdf_state'=>$state,'pdf_search'=>$q,'pdf_page'=>$page];
    if($sha!=='')$vars['pdf_preview']=$sha;
    if($sha!=='' && $part>0)$vars['pdf_part']=$part;
    return '/admin/ai?'.http_build_query($vars,'','&',PHP_QUERY_RFC3986).'#gentoo-pdf';
}
function cms_gentoo_pdf_status_json():never {
    cms_need_admin();
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: private, no-store');
    if(($_SERVER['REQUEST_METHOD']??'GET')!=='GET'){
        http_response_code(405);exit('{"ok":false,"error":"GET required"}');
    }
    [$state,$q,$page]=cms_gentoo_pdf_filters();
    try {
        $result=cms_gentoo_pdf_call('status',[
            'limit'=>30,'offset'=>($page-1)*30,'filter'=>$state,'query'=>$q]);
        echo json_encode($result,JSON_THROW_ON_ERROR|JSON_INVALID_UTF8_SUBSTITUTE);
    }catch(Throwable $error){
        error_log('Gentoo PDF admin status: '.get_class($error));
        http_response_code(503);
        echo '{"ok":false,"error":"Gentoo PDF service unavailable; check tunnel and deployment."}';
    }
    exit;
}
function cms_gentoo_pdf_post(PDO $cms,string $action):never {
    cms_need_admin();
    $map=[
        'gentoo_pdf_pause'=>'pause','gentoo_pdf_resume'=>'resume',
        'gentoo_pdf_workers'=>'workers','gentoo_pdf_retry'=>'retry',
        'gentoo_pdf_approve'=>'approve','gentoo_pdf_unapprove'=>'unapprove',
        'gentoo_pdf_approve_all'=>'approve_all'];
    if(!isset($map[$action]))throw new InvalidArgumentException('Unsupported Gentoo PDF command.');
    $op=$map[$action];
    $params=[];
    if($op==='workers'){
        $value=$_POST['workers']??null;
        if(!is_string($value)||!preg_match('/^(?:[3-9]|[1-5][0-9]|6[0-4])$/D',$value))
            throw new InvalidArgumentException('Worker count must be between 3 and 64.');
        $params['workers']=(int)$value;
    }
    if(in_array($op,['retry','approve','unapprove'],true)){
        $sha=$_POST['sha256']??null;
        if(!is_string($sha)||!preg_match('/^[a-f0-9]{64}$/D',$sha))
            throw new InvalidArgumentException('Choose a valid PDF manual.');
        $params['sha256']=$sha;
    }
    if($op==='approve'){
        if(($_POST['confirm']??null)!=='APPROVE MANUAL')
            throw new InvalidArgumentException('Type APPROVE MANUAL before approving.');
        $params['confirm']='APPROVE MANUAL';
    }
    if($op==='approve_all'){
        $params['confirm']=cms_gentoo_pdf_bulk_confirmation(
            $_POST['confirm']??null, $_POST['bulk_ack']??null);
    }
    try {
        $result=cms_gentoo_pdf_call($op,$params);
    }catch(Throwable $error){
        error_log('Gentoo PDF admin command unavailable: '.get_class($error));
        throw new InvalidArgumentException('Gentoo PDF command could not be completed. Check connection and document state.',0,$error);
    }
    $target=$op==='approve_all'
        ? 'ready-approved:'.(int)($result['approved_count']??0)
        : substr((string)($params['sha256']??($params['workers']??'')),0,64);
    cms_audit($cms,'gentoo-pdf-'.$op,$target);
    cms_flash((string)($result['detail']??'Gentoo PDF control updated.'));
    cms_go('/admin/ai#gentoo-pdf');
}
/** Additional server-side safety gate independent of browser JavaScript. */
function cms_gentoo_pdf_bulk_confirmation(mixed $confirmation,mixed $ack):string {
    if($confirmation!=='APPROVE ALL READY'||$ack!=='yes')
        throw new InvalidArgumentException('Confirm bulk manual approval before submitting.');
    return 'APPROVE ALL READY';
}
function cms_gentoo_pdf_format_bytes(int $bytes):string {
    return number_format($bytes/1073741824,2).' GiB';
}
function cms_gentoo_pdf_panel(PDO $cms):void {
    // cms_ai_page has already verified administrator identity; do not
    // re-open a session after cms_admin_shell_start renders the page.
    [$state,$query,$page]=cms_gentoo_pdf_filters();
    $data=null;$error='';
    try {
        $data=cms_gentoo_pdf_call('status',[
            'limit'=>30,'offset'=>($page-1)*30,'filter'=>$state,'query'=>$query]);
    } catch(Throwable $caught){
        $error='Gentoo connection unavailable. The existing PDF queue and files are unchanged.';
        error_log('Gentoo PDF admin panel: '.get_class($caught));
    }
    $counts=is_array($data)?($data['counts']??[]):[];
    $rows=is_array($data)?($data['documents']??[]):[];
    $filtered=(int)($data['filtered']??0);
    $sha=$_GET['pdf_preview']??'';
    if(!is_string($sha)||!preg_match('/^[a-f0-9]{64}$/D',$sha))$sha='';
    $partOffset=$_GET['pdf_part']??'0';
    if(!is_string($partOffset)||!preg_match('/^(?:0|[1-9][0-9]{0,5})$/D',$partOffset))$partOffset='0';
    $partOffset=min(1000000,(int)$partOffset);
    $preview=null;
    if($data!==null&&$sha!==''){
        try{$preview=cms_gentoo_pdf_call('preview',['sha256'=>$sha,'offset'=>$partOffset]);}
        catch(Throwable $caught){$error='Selected PDF text preview unavailable.';}
    }
    ?>
    <section id="gentoo-pdf" class="admin-panel" data-gentoo-pdf>
      <h2>Gentoo PDF Library &amp; Worker Control</h2>
      <p class="muted">Authoritative library and extracted-text queue reside on the dedicated Gentoo SSD.
      These results are not the Ubuntu upload queue. Approval is recorded on Gentoo; it does not
      automatically train an LLM or publish text into the existing CMS knowledge export.</p>
      <?php if($error!==''):?><p class="form-error" role="alert"><?=cms_esc($error)?></p><?php endif;?>
      <div role="status" class="muted small" data-gentoo-live>
        <?=is_array($data)?'Connected through signed Gentoo SSH tunnel. Updating every 12 seconds.':'Waiting for Gentoo bridge installation.'?>
      </div>
      <?php if(is_array($data)):?>
      <div class="stats-grid">
        <div class="stat-card"><strong data-pdf-count="total"><?=(int)($data['total']??0)?></strong><span>Unique manuals</span></div>
        <?php foreach(['queued','processing','ready','failed','approved'] as $key):?>
        <div class="stat-card"><strong data-pdf-count="<?=cms_esc($key)?>"><?=(int)($counts[$key]??0)?></strong><span><?=cms_esc(ucfirst($key))?></span></div>
        <?php endforeach;?>
      </div>
      <p class="muted">SSD free: <strong data-pdf-free><?=cms_esc(cms_gentoo_pdf_format_bytes((int)($data['storage']['free_bytes']??0)))?></strong>.
        Worker dispatch: <strong data-pdf-state><?=!empty($data['paused'])?'Paused':'Running'?></strong>.
        Configured concurrent workers: <strong data-pdf-workers><?=(int)($data['workers']??3)?></strong>.</p>
      <div class="admin-panel">
        <h3>Batch approve all successfully extracted manuals</h3>
        <p><strong data-pdf-eligible><?=number_format((int)($data['eligible_ready']??0))?></strong>
        ready manuals have extracted text and qualify for bulk approval across the
        <strong>entire library</strong>, not just this page or current search.</p>
        <form method="post" action="/admin/ai"
          data-confirm="Bulk approve ALL ready manuals with extracted text, including manuals outside the current page and search results? This marks them reviewed without individual inspection.">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="gentoo_pdf_approve_all">
          <input type="hidden" name="confirm" value="APPROVE ALL READY">
          <label><input type="checkbox" name="bulk_ack" value="yes" required>
            I authorize approval of all successfully extracted manuals without individual review.</label>
          <button class="btn primary" type="submit" data-pdf-bulk-submit
            <?=(int)($data['eligible_ready']??0)===0?'disabled':''?>>
            Approve All Ready Manuals
          </button>
        </form>
        <p class="muted small">Failed, queued, processing and already-approved documents
        are excluded. Only manuals with stored, nonblank extracted text qualify.
        New manuals finishing later can be approved with another batch.
        Approval does not automatically train a model or publish these PDFs.</p>
      </div>
      <div class="form-actions">
        <form method="post" action="/admin/ai" class="inline-action">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="<?=!empty($data['paused'])?'gentoo_pdf_resume':'gentoo_pdf_pause'?>">
          <button class="btn small" type="submit"><?=!empty($data['paused'])?'Resume processing':'Pause new jobs'?></button>
        </form>
        <form method="post" action="/admin/ai" class="inline-action">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="gentoo_pdf_workers">
          <label>Workers (3–64)<input type="number" min="3" max="64" name="workers" value="<?=(int)$data['workers']?>" required></label>
          <button class="btn small" type="submit">Set workers</button>
        </form>
      </div>
      <p class="muted small">Pausing prevents new extraction jobs; already running jobs may finish.
      Counts are distinct SHA-256 manuals, not total filenames seen or duplicate files.</p>
      <form method="get" action="/admin/ai" class="form-actions">
        <label>Status <select name="pdf_state">
          <?php foreach([''=>'All statuses','queued'=>'Queued','processing'=>'Processing',
                          'ready'=>'Ready for review','failed'=>'Failed','approved'=>'Approved'] as $key=>$title):?>
          <option value="<?=cms_esc($key)?>" <?=$key===$state?'selected':''?>><?=cms_esc($title)?></option>
          <?php endforeach;?>
        </select></label>
        <label>Filename search <input type="search" name="pdf_search" maxlength="120" value="<?=cms_esc($query)?>" placeholder="Model or year"></label>
        <button class="btn small" type="submit">Filter manuals</button>
      </form>
      <p class="muted small">Showing <?=(int)count($rows)?> of <?=$filtered?> matching manuals; page <?=$page?>.</p>
      <div class="table-scroll"><table>
        <thead><tr><th>PDF manual</th><th>Extraction</th><th>Pages / excerpts</th><th>Actions</th></tr></thead>
        <tbody data-pdf-rows>
        <?php foreach($rows as $item):
          $digest=(string)($item['sha256']??'');
          $job=(string)($item['status']??'');
        ?>
          <tr data-pdf-sha="<?=cms_esc($digest)?>">
            <td><strong><?=cms_esc((string)($item['filename']??''))?></strong><small><?=cms_esc(substr($digest,0,12))?> · <?=number_format((int)($item['bytes']??0)/1048576,1)?> MiB</small></td>
            <td data-pdf-job><?=cms_esc(ucfirst($job))?>
              <?php if(!empty($item['error'])):?><small class="form-error"><?=cms_esc((string)$item['error'])?></small><?php endif;?>
            </td>
            <td><?= (int)($item['pages']??0) ?> / <?= (int)($item['excerpts']??0) ?></td>
            <td>
              <a href="<?=cms_esc(cms_gentoo_pdf_query_url($state,$query,$page,$digest))?>">Preview text</a>
              <?php if(in_array($job,['failed','ready','approved'],true)):?>
              <form method="post" action="/admin/ai" class="inline-action">
                <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
                <input type="hidden" name="sha256" value="<?=cms_esc($digest)?>">
                <input type="hidden" name="action" value="<?=$job==='failed'?'gentoo_pdf_retry':($job==='approved'?'gentoo_pdf_unapprove':'gentoo_pdf_approve')?>">
                <?php if($job==='ready'):?>
                <label>Confirmation <input type="text" name="confirm" placeholder="APPROVE MANUAL" required></label>
                <?php endif;?>
                <button class="btn small" type="submit"><?=$job==='failed'?'Retry':($job==='approved'?'Revoke approval':'Approve')?></button>
              </form>
              <?php endif;?>
            </td>
          </tr>
        <?php endforeach;?>
        </tbody>
      </table></div>
      <div class="form-actions">
        <?php if($page>1):?><a class="btn small" href="<?=cms_esc(cms_gentoo_pdf_query_url($state,$query,$page-1))?>">Previous page</a><?php endif;?>
        <?php if($page*30<$filtered):?><a class="btn small" href="<?=cms_esc(cms_gentoo_pdf_query_url($state,$query,$page+1))?>">Next page</a><?php endif;?>
      </div>
      <?php if(is_array($preview)):?>
        <details open class="admin-panel"><summary>Extracted PDF text preview — <?=cms_esc((string)($preview['document']['filename']??''))?></summary>
        <p class="muted small">The preview shows up to eight page-attributed excerpts. Compare with the original source PDF before approval.</p>
        <?php foreach(($preview['excerpts']??[]) as $part):?>
          <h4>Page <?=(int)($part['page']??0)?>, part <?=(int)($part['part']??0)?></h4>
          <pre style="white-space:pre-wrap;overflow-wrap:anywhere"><?=cms_esc((string)($part['text']??''))?></pre>
        <?php endforeach;?>
        <div class="form-actions">
          <?php if($partOffset>=8):?>
            <a class="btn small" href="<?=cms_esc(cms_gentoo_pdf_query_url($state,$query,$page,$sha,$partOffset-8))?>">Previous excerpts</a>
          <?php endif;?>
          <?php if($partOffset+8<(int)($preview['document']['excerpts']??0)):?>
            <a class="btn small" href="<?=cms_esc(cms_gentoo_pdf_query_url($state,$query,$page,$sha,$partOffset+8))?>">Next excerpts</a>
          <?php endif;?>
          <a href="<?=cms_esc(cms_gentoo_pdf_query_url($state,$query,$page))?>">Close preview</a>
        </div>
        </details>
      <?php endif;?>
      <script defer src="/assets/gentoo-pdf-admin.js"></script>
      <?php endif;?>
    </section>
    <?php
}
