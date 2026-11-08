<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}
require_once __DIR__.'/vhosts.php';
require_once __DIR__.'/operations.php';
require_once __DIR__.'/github_projects.php';

/** Private CMS administration. No arbitrary PHP upload or shell command console. */
function cms_admin_post(PDO $db,string $path): void {
    cms_check_csrf();
    $action=(string)($_POST['action']??'');
    if($action==='login' && !cms_is_admin()){
        if(!cms_login($db,(string)($_POST['username']??''),(string)($_POST['password']??'')))
            throw new InvalidArgumentException('Incorrect username or password.');
        cms_flash('Welcome to your SirusSofts dashboard.');cms_go('/admin');
    }
    cms_need_admin();
    if(in_array($action,['vhost_preview','vhost_apply','vhost_rollback','vhost_settings'],true)&&$path==='/admin/hosting'){
        cms_vhost_post($db,$action);
    }
    if($action==='logout'){
        cms_audit($db,'sign-out','admin');
        $_SESSION=[];
        session_regenerate_id(true);
        cms_go('/admin/login');
    }
    if($action==='save_project'){
        $id=(string)($_POST['id']??'');
        $isNew=$id==='';
        $slug=$isNew?strtolower(cms_text($_POST['slug']??'',40,'Slug',true)):'';
        if($isNew && (!cms_safe_slug($slug)||$slug==='cpoint'))
            throw new InvalidArgumentException('Slug must start with a letter, use lowercase letters/numbers/hyphens, and avoid reserved routes.');
        $title=cms_text($_POST['title']??'',100,'Project title',true);
        $summary=cms_text($_POST['summary']??'',360,'Summary',true);
        $body=cms_text($_POST['body']??'',60000,'Page content');
        $category=cms_text($_POST['category']??'',60,'Category');
        $icon=cms_text($_POST['icon']??'◈',24,'Symbol');
        $kind=(string)($_POST['kind']??'page');
        $state=(string)($_POST['state']??'draft');
        if(!in_array($kind,['page','app'],true)||!in_array($state,['draft','published'],true))
            throw new InvalidArgumentException('Invalid project type or publication state.');
        $order=filter_var($_POST['sort_order']??'100',FILTER_VALIDATE_INT);
        if($order===false||$order < -100000||$order > 100000)
            throw new InvalidArgumentException('Project order must be an integer between -100000 and 100000.');
        if($isNew){
            $st=$db->prepare('INSERT INTO cms_projects
                (slug,title,summary,body,category,icon,kind,state,sort_order,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)');
            $st->execute([$slug,$title,$summary,$body,$category,$icon,$kind,$state,$order,cms_now(),cms_now()]);
            cms_audit($db,'create-project',$slug);
        }else{
            $num=cms_get_int($id);
            $st=$db->prepare('SELECT slug,kind FROM cms_projects WHERE id=?');$st->execute([$num]);
            $old=$st->fetch();
            if(!$old)throw new InvalidArgumentException('Project no longer exists.');
            if($old['slug']==='cpoint')$kind='app'; // Don't break the live app's routing.
            $st=$db->prepare('UPDATE cms_projects SET title=?,summary=?,body=?,category=?,
                icon=?,kind=?,state=?,sort_order=?,updated_at=? WHERE id=?');
            $st->execute([$title,$summary,$body,$category,$icon,$kind,$state,$order,cms_now(),$num]);
            $slug=(string)$old['slug'];
            cms_audit($db,'edit-project',$slug);
        }
        cms_flash('Project saved. Subdomain DNS and hosting still require provisioning.');
        cms_go('/admin/projects');
    }
    if($action==='delete_project'){
        $num=cms_get_int($_POST['id']??'');
        if((string)($_POST['confirm']??'')!=='DELETE')
            throw new InvalidArgumentException('Type DELETE to confirm.');
        $st=$db->prepare('SELECT slug FROM cms_projects WHERE id=?');$st->execute([$num]);
        $slug=$st->fetchColumn();
        if($slug===false)throw new InvalidArgumentException('Project not found.');
        if($slug==='cpoint')throw new InvalidArgumentException('cPoint is a protected integrated project. Unpublish it instead.');
        $db->prepare('DELETE FROM cms_projects WHERE id=?')->execute([$num]);
        cms_audit($db,'delete-project',(string)$slug);
        cms_flash('Project registry entry deleted. Files, DNS and databases were not deleted.');
        cms_go('/admin/projects');
    }
    if($action==='save_page'){
        $id=(string)($_POST['id']??'');
        $isNew=$id==='';
        $slug=$isNew?strtolower(cms_text($_POST['slug']??'',40,'Page slug',true)):'';
        if($isNew&&!cms_safe_slug($slug))
            throw new InvalidArgumentException('Page slug must be lowercase letters, numbers and hyphens.');
        $title=cms_text($_POST['title']??'',120,'Page title',true);
        $body=cms_text($_POST['body']??'',80000,'Body');
        $state=(string)($_POST['state']??'draft');
        if(!in_array($state,['published','draft'],true))
            throw new InvalidArgumentException('Invalid publication state.');
        if($isNew){
            $st=$db->prepare('INSERT INTO cms_pages(slug,title,body,state,created_at,updated_at) VALUES(?,?,?,?,?,?)');
            $st->execute([$slug,$title,$body,$state,cms_now(),cms_now()]);
        }else{
            $num=cms_get_int($id);
            $st=$db->prepare('SELECT slug FROM cms_pages WHERE id=?');$st->execute([$num]);
            $slug=$st->fetchColumn();
            if($slug===false)throw new InvalidArgumentException('Page not found.');
            $db->prepare('UPDATE cms_pages SET title=?,body=?,state=?,updated_at=? WHERE id=?')
               ->execute([$title,$body,$state,cms_now(),$num]);
        }
        cms_audit($db,$isNew?'create-page':'edit-page',(string)$slug);
        cms_flash('Content page saved.');cms_go('/admin/pages');
    }
    if($action==='delete_page'){
        $num=cms_get_int($_POST['id']??'');
        if((string)($_POST['confirm']??'')!=='DELETE')
            throw new InvalidArgumentException('Type DELETE to confirm.');
        $st=$db->prepare('SELECT slug FROM cms_pages WHERE id=?');$st->execute([$num]);
        $slug=$st->fetchColumn();
        if($slug===false)throw new InvalidArgumentException('Page not found.');
        $db->prepare('DELETE FROM cms_pages WHERE id=?')->execute([$num]);
        cms_audit($db,'delete-page',(string)$slug);
        cms_flash('Content page deleted.');cms_go('/admin/pages');
    }
    if($action==='save_settings'){
        $limits=['site_name'=>80,'tagline'=>140,'hero_title'=>160,'hero_body'=>2000,
                 'about'=>30000,'footer'=>200,'accent'=>10];
        $db->beginTransaction();
        try{
            $st=$db->prepare('INSERT INTO cms_settings(name,value) VALUES(?,?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value');
            foreach($limits as $key=>$limit){
                $value=cms_text($_POST[$key]??'',$limit,$key,$key==='site_name');
                if($key==='accent'&&!in_array($value,['blue','cyan','violet'],true))
                    throw new InvalidArgumentException('Choose a supported accent color.');
                $st->execute([$key,$value]);
            }
            cms_audit($db,'update-settings','site');
            $db->commit();
        }catch(Throwable $error){if($db->inTransaction())$db->rollBack();throw $error;}
        cms_flash('Public website settings updated.');cms_go('/admin/settings');
    }
    if($action==='change_password'){
        $old=(string)($_POST['old_password']??'');
        $new=(string)($_POST['new_password']??'');
        $confirm=(string)($_POST['confirm_password']??'');
        if(strlen($new)<14 || strlen($new)>512||!hash_equals($new,$confirm))
            throw new InvalidArgumentException('New password must match and contain at least 14 characters.');
        $st=$db->prepare('SELECT password_hash FROM cms_admins WHERE id=?');
        $st->execute([$_SESSION['cms_admin_id']]);$hash=$st->fetchColumn();
        if($hash===false || !password_verify($old,(string)$hash))
            throw new InvalidArgumentException('Current password is incorrect.');
        $db->prepare('UPDATE cms_admins SET password_hash=? WHERE id=?')
           ->execute([password_hash($new,PASSWORD_DEFAULT),$_SESSION['cms_admin_id']]);
        cms_audit($db,'change-password','admin');
        session_regenerate_id(true);
        $_SESSION['cms_csrf']=bin2hex(random_bytes(32));
        cms_flash('Administrator password changed.');cms_go('/admin/security');
    }
    throw new InvalidArgumentException('Unknown administrative operation.');
}
function cms_admin_shell_start(PDO $db,string $title): void {
    cms_layout_start($db,$title,true);
    ?><section class="admin-header"><span class="eyebrow">SIRUSSOFTS CONTROL CENTER</span>
      <h1><?=cms_esc($title)?></h1><p>Manage the public site and your project collection.</p></section>
      <div class="admin-layout"><aside class="admin-sidebar">
        <?php foreach([
           '/admin'=>'Overview','/admin/projects'=>'Projects','/admin/pages'=>'Content pages',
           '/admin/hosting'=>'Virtual hosts','/admin/infrastructure'=>'Infrastructure',
           '/admin/github'=>'GitHub projects','/admin/settings'=>'Site settings','/admin/security'=>'Security',
           '/admin/activity'=>'Activity log'
        ] as $url=>$label):?><a href="<?=$url?>"><?=cms_esc($label)?></a><?php endforeach;?>
        <form method="post" action="/admin/logout">
          <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
          <input type="hidden" name="action" value="logout">
          <button class="sidebar-logout" type="submit">Sign out</button>
        </form>
      </aside><div class="admin-work">
<?php
}
function cms_admin_shell_end(PDO $db): void {
    echo '</div></div>';
    cms_layout_end($db);
}
function cms_admin_login(PDO $db,?string $error): void {
    cms_layout_start($db,'Administrator sign-in');
    ?><section class="auth-layout"><div class="auth-copy">
      <span class="eyebrow">PRIVATE WORKSPACE</span><h1>Manage the work behind the scenes.</h1>
      <p>The website is public. Administration is private. Sign in to manage projects, content and settings.</p>
    </div><div class="admin-panel login-panel"><h2>Administrator sign-in</h2>
      <?php if($error):?><p class="form-error"><?=cms_esc($error)?></p><?php endif;?>
      <form method="post" action="/admin/login">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="login">
        <label>Username<input name="username" autocomplete="username" required maxlength="64" value="admin"></label>
        <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
        <button class="btn primary" type="submit">Sign in</button>
      </form><small>Only available over HTTPS.</small>
    </div></section>
    <?php cms_layout_end($db);
}
function cms_admin_project_editor(PDO $db,?array $project): void {
    $new=$project===null;
    $project??=['id'=>'','slug'=>'','title'=>'','summary'=>'','body'=>'',
                'category'=>'Software','icon'=>'◈','kind'=>'page','state'=>'draft','sort_order'=>100];
    cms_admin_shell_start($db,$new?'New project':'Edit project');
    ?><div class="admin-panel">
      <p class="muted">Create a landing page immediately. For an executable PHP/Linux application, deploy its code separately; the CMS never runs shell commands supplied through this form. For an already-deployed application, Draft hides only its CMS listing: its direct URL and API remain active until separately disabled in Plesk.</p>
      <form method="post" action="<?=$new?'/admin/projects/new':'/admin/projects/'.(int)$project['id']?>">
        <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
        <input type="hidden" name="action" value="save_project">
        <input type="hidden" name="id" value="<?=cms_esc($project['id'])?>">
        <div class="form-grid">
        <label>Project slug <?php if(!$new):?><small>Immutable after creation</small><?php endif;?>
          <input name="slug" value="<?=cms_esc($project['slug'])?>" maxlength="40"
                 pattern="[a-z][a-z0-9-]{1,39}" <?=$new?'required':'disabled'?>></label>
        <label>Title<input name="title" required maxlength="100" value="<?=cms_esc($project['title'])?>"></label>
        <label>Category<input name="category" maxlength="60" value="<?=cms_esc($project['category'])?>"></label>
        <label>Symbol / emoji<input name="icon" maxlength="24" value="<?=cms_esc($project['icon'])?>"></label>
        <label>Project type<select name="kind">
          <option value="page" <?=$project['kind']==='page'?'selected':''?>>CMS content project</option>
          <option value="app" <?=$project['kind']==='app'?'selected':''?>>Separate application (deployed on server)</option>
        </select></label>
        <label>Visibility<select name="state">
          <option value="draft" <?=$project['state']==='draft'?'selected':''?>>Draft / hidden</option>
          <option value="published" <?=$project['state']==='published'?'selected':''?>>Published</option>
        </select></label>
        <label>Homepage order<input type="number" name="sort_order" min="-100000" max="100000" value="<?=cms_esc($project['sort_order'])?>"></label>
        </div>
        <label>Short description<textarea name="summary" rows="3" maxlength="360" required><?=cms_esc($project['summary'])?></textarea></label>
        <label>Project description (plain text, ## headings supported)
          <textarea name="body" rows="12" maxlength="60000"><?=cms_esc($project['body'])?></textarea></label>
        <div class="form-actions"><button class="btn primary" type="submit">Save project</button>
          <a class="btn subtle" href="/admin/projects">Cancel</a></div>
      </form>
      <?php if(!$new):?><div class="address-panel"><h3>Routes for this project</h3>
        <code>https://sirussofts.com/<?=cms_esc($project['slug'])?></code>
        <code>https://<?=cms_esc($project['slug'])?>.sirussofts.com/</code>
        <p class="muted small">The directory route is handled by the CMS for content projects or the application's own directory. The subdomain also requires a Plesk host and DNS/TLS provisioning; saving does not configure DNS.</p>
      </div><?php endif;?>
    </div><?php cms_admin_shell_end($db);
}
function cms_admin_page_editor(PDO $db,?array $page): void {
    $new=$page===null;
    $page??=['id'=>'','slug'=>'','title'=>'','body'=>'','state'=>'draft'];
    cms_admin_shell_start($db,$new?'New content page':'Edit content page');
    ?><div class="admin-panel"><form method="post" action="<?=$new?'/admin/pages/new':'/admin/pages/'.(int)$page['id']?>">
      <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
      <input type="hidden" name="action" value="save_page">
      <input type="hidden" name="id" value="<?=cms_esc($page['id'])?>">
      <label>Page slug <small>(available under /pages/slug)</small>
        <input name="slug" maxlength="40" value="<?=cms_esc($page['slug'])?>"
               <?=$new?'required':'disabled'?>>&nbsp;</label>
      <label>Title<input name="title" maxlength="120" required value="<?=cms_esc($page['title'])?>"></label>
      <label>Status<select name="state">
        <option value="draft" <?=$page['state']==='draft'?'selected':''?>>Draft</option>
        <option value="published" <?=$page['state']==='published'?'selected':''?>>Published</option>
      </select></label>
      <label>Content<textarea name="body" rows="18" maxlength="80000"><?=cms_esc($page['body'])?></textarea></label>
      <div class="form-actions"><button class="btn primary" type="submit">Save page</button>
        <a class="btn subtle" href="/admin/pages">Cancel</a></div>
    </form></div><?php cms_admin_shell_end($db);
}
function cms_admin_run(PDO $db,string $path): never {
    if(!cms_https()&&!cms_local()){
        http_response_code(403);header('Content-Type: text/plain; charset=utf-8');
        exit('Administrator pages require HTTPS.');
    }
    header('Cache-Control: no-store, max-age=0');
    cms_session();
    $error=null;
    if(($_SERVER['REQUEST_METHOD']??'GET')==='POST'){
        try{cms_admin_post($db,$path);}
        catch(InvalidArgumentException|PDOException $e){
            $error=$e instanceof PDOException?'A database constraint prevented that change.':$e->getMessage();
        }
    }
    if(!cms_is_admin()){
        cms_admin_login($db,$error);exit;
    }
    if($path==='/admin/login')cms_go('/admin');
    if($path==='/admin/logout'){cms_go('/admin');}
    if($path==='/admin/hosting'){cms_vhost_page($db,$error);exit;}
    if($path==='/admin/infrastructure'){cms_operations_page($db);exit;}
    if($path==='/admin/github'){cms_github_projects_page($db);exit;}
    if($path==='/admin/projects/new'){cms_admin_project_editor($db,null);exit;}
    if(preg_match('#^/admin/projects/([1-9][0-9]*)$#D',$path,$mt)){
        $st=$db->prepare('SELECT * FROM cms_projects WHERE id=?');$st->execute([(int)$mt[1]]);
        $project=$st->fetch();if(!$project){cms_go('/admin/projects');}
        cms_admin_project_editor($db,$project);exit;
    }
    if($path==='/admin/pages/new'){cms_admin_page_editor($db,null);exit;}
    if(preg_match('#^/admin/pages/([1-9][0-9]*)$#D',$path,$mt)){
        $st=$db->prepare('SELECT * FROM cms_pages WHERE id=?');$st->execute([(int)$mt[1]]);
        $page=$st->fetch();if(!$page)cms_go('/admin/pages');
        cms_admin_page_editor($db,$page);exit;
    }
    $titles=[
        '/admin'=>'Overview','/admin/projects'=>'Projects','/admin/pages'=>'Content pages',
        '/admin/settings'=>'Site settings','/admin/security'=>'Security','/admin/activity'=>'Activity log'
    ];
    if(!isset($titles[$path])){http_response_code(404);cms_go('/admin');}
    cms_admin_shell_start($db,$titles[$path]);
    $flash=cms_take_flash();
    if($flash):?><div class="flash-message"><?=cms_esc($flash['message']??'')?></div><?php endif;?>
    <?php if($error):?><div class="form-error"><?=cms_esc($error)?></div><?php endif;?>
    <?php
    if($path==='/admin'){
        $projects=(int)$db->query('SELECT COUNT(*) FROM cms_projects')->fetchColumn();
        $published=(int)$db->query("SELECT COUNT(*) FROM cms_projects WHERE state='published'")->fetchColumn();
        $pages=(int)$db->query('SELECT COUNT(*) FROM cms_pages')->fetchColumn();
        ?><div class="admin-stats"><div class="admin-panel"><span>Projects</span><strong><?=$projects?></strong></div>
            <div class="admin-panel"><span>Published</span><strong><?=$published?></strong></div>
            <div class="admin-panel"><span>Content pages</span><strong><?=$pages?></strong></div></div>
          <div class="admin-panel"><h2>Your platform is ready.</h2>
            <p>Build your public homepage, publish projects, and maintain each project under its own directory and subdomain.</p>
            <div class="form-actions"><a class="btn primary" href="/admin/projects/new">Add a project ↗</a>
            <a class="btn subtle" href="/" target="_blank" rel="noopener">View public site</a></div>
          </div>
          <div class="admin-panel"><h2>Deployment boundary</h2>
            <p>The CMS manages content and URLs. DNS, HTTPS certificates, and application runtime deployments remain server-operator tasks. PHP will never execute a command typed into the website editor.</p>
          </div><?php
    }elseif($path==='/admin/projects'){
        $rows=cms_projects($db,false);
        ?><div class="toolbar"><p>Manage project landing pages and publication state.</p>
          <a class="btn primary" href="/admin/projects/new">+ New project</a></div>
          <div class="admin-panel table-scroll"><table><thead><tr><th>Project</th><th>Kind</th><th>Visibility</th><th>Routes</th><th>Actions</th></tr></thead><tbody>
          <?php foreach($rows as $project):?><tr><td><strong><?=cms_esc($project['title'])?></strong><br>
             <code><?=cms_esc($project['slug'])?></code></td>
             <td><?=cms_esc($project['kind'])?></td>
             <td><span class="state-pill <?=cms_esc($project['state'])?>"><?=cms_esc($project['state'])?></span></td>
             <td><small>/<?=cms_esc($project['slug'])?><br><?=cms_esc($project['slug'])?>.sirussofts.com</small></td>
             <td><a class="btn small" href="/admin/projects/<?=(int)$project['id']?>">Edit</a>
               <?php if($project['slug']!=='cpoint'):?>
               <form class="inline-action" method="post" action="/admin/projects" data-confirm="Delete project registry entry? App files and DNS will not be removed.">
                 <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
                 <input type="hidden" name="action" value="delete_project">
                 <input type="hidden" name="id" value="<?=(int)$project['id']?>">
                 <input type="hidden" name="confirm" value="DELETE">
                 <button class="btn small danger" type="submit">Delete</button>
               </form><?php endif;?></td></tr><?php endforeach;?>
          </tbody></table></div><?php
    }elseif($path==='/admin/pages'){
        $pages=$db->query('SELECT * FROM cms_pages ORDER BY updated_at DESC')->fetchAll();
        ?><div class="toolbar"><p>Manage ordinary content at <code>/pages/slug</code>.</p>
          <a class="btn primary" href="/admin/pages/new">+ New page</a></div>
          <div class="admin-panel table-scroll"><table><thead><tr><th>Page</th><th>Status</th><th>Updated</th><th>Actions</th></tr></thead><tbody>
          <?php foreach($pages as $page):?><tr><td><?=cms_esc($page['title'])?><br>
            <code>/pages/<?=cms_esc($page['slug'])?></code></td>
            <td><?=cms_esc($page['state'])?></td><td><?=cms_esc($page['updated_at'])?></td>
            <td><a class="btn small" href="/admin/pages/<?=(int)$page['id']?>">Edit</a>
              <form class="inline-action" method="post" action="/admin/pages" data-confirm="Permanently delete this content page?">
                <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
                <input type="hidden" name="action" value="delete_page">
                <input type="hidden" name="id" value="<?=(int)$page['id']?>">
                <input type="hidden" name="confirm" value="DELETE">
                <button class="btn danger small" type="submit">Delete</button>
              </form></td></tr><?php endforeach;?>
          </tbody></table></div><?php
    }elseif($path==='/admin/settings'){
        ?><div class="admin-panel"><h2>Public site identity</h2>
          <form method="post" action="/admin/settings">
            <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
            <input type="hidden" name="action" value="save_settings">
            <?php foreach([
              'site_name'=>['Site name',80],'tagline'=>['Tagline',140],
              'hero_title'=>['Homepage headline',160],
              'hero_body'=>['Homepage introduction',2000],
              'about'=>['About-page content',30000],
              'footer'=>['Footer',200]
            ] as $key=>$field):?>
              <label><?=cms_esc($field[0])?>
                <?php if(in_array($key,['hero_body','about'],true)):?>
                  <textarea name="<?=cms_esc($key)?>" rows="<?=$key==='about'?9:4?>" maxlength="<?=(int)$field[1]?>"><?=cms_esc(cms_setting($db,$key))?></textarea>
                <?php else:?>
                  <input name="<?=cms_esc($key)?>" maxlength="<?=(int)$field[1]?>" value="<?=cms_esc(cms_setting($db,$key))?>">
                <?php endif;?>
              </label>
            <?php endforeach;?>
            <label>Accent color<select name="accent">
            <?php foreach(['blue'=>'Blizzard blue','cyan'=>'Cyan','violet'=>'Violet'] as $key=>$name):?>
            <option value="<?=$key?>" <?=cms_setting($db,'accent')===$key?'selected':''?>><?=cms_esc($name)?></option>
            <?php endforeach;?></select></label>
            <button class="btn primary" type="submit">Save site settings</button>
          </form>
        </div><?php
    }elseif($path==='/admin/security'){
        ?><div class="admin-panel"><h2>Change administrator password</h2>
          <p>Use a unique password of at least 14 characters. The CMS administrator is separate from cPoint's existing administrator.</p>
          <form method="post" action="/admin/security">
            <input type="hidden" name="csrf" value="<?=cms_esc(cms_csrf())?>">
            <input type="hidden" name="action" value="change_password">
            <label>Current password<input type="password" name="old_password" autocomplete="current-password" required></label>
            <label>New password<input type="password" name="new_password" autocomplete="new-password" minlength="14" required></label>
            <label>Confirm new password<input type="password" name="confirm_password" autocomplete="new-password" minlength="14" required></label>
            <button class="btn primary" type="submit">Change password</button>
          </form>
        </div><?php
    }elseif($path==='/admin/activity'){
        $rows=$db->query('SELECT at,username,action,reference FROM cms_audit ORDER BY id DESC LIMIT 100')->fetchAll();
        ?><div class="admin-panel table-scroll"><table><thead><tr><th>Time UTC</th><th>Admin</th><th>Action</th><th>Reference</th></tr></thead><tbody>
          <?php foreach($rows as $row):?><tr><td><?=cms_esc($row['at'])?></td><td><?=cms_esc($row['username'])?></td>
          <td><?=cms_esc($row['action'])?></td><td><?=cms_esc($row['reference'])?></td></tr><?php endforeach;?>
          </tbody></table></div><?php
    }
    cms_admin_shell_end($db);
    exit;
}
