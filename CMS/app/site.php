<?php
declare(strict_types=1);
if(!defined('SIRUS_CMS_BOOTSTRAPPED')){http_response_code(404);exit;}


/** List every imported repository publicly, but never expose unreviewed draft text. */
function cms_public_directory_projects(PDO $db): array {
    $rows=$db->query("SELECT p.*,r.github_id,r.github_private,r.full_name
        FROM cms_projects p
        LEFT JOIN cms_github_repositories r ON r.project_id=p.id
        WHERE p.state='published' OR r.github_id IS NOT NULL
        ORDER BY p.sort_order ASC,p.title COLLATE NOCASE ASC")->fetchAll();
    foreach($rows as &$project){
        if($project['github_id']!==null && $project['state']!=='published'){
            // Draft project summaries and bodies are not approved for publication.
            $project['title']=(string)$project['full_name'];
            $project['summary']='GitHub project listing. Repository source is available only to authorized accounts.';
            $project['body']='';
            $project['category']='GitHub';
            $project['kind']='page';
        }
    }
    unset($project);
    return $rows;
}
function cms_public_directory_project(PDO $db,string $slug): ?array {
    foreach(cms_public_directory_projects($db) as $project){
        if($project['slug']===$slug)return $project;
    }
    return null;
}

/** Shared site shell. Public pages never require an administrator session. */
function cms_layout_start(PDO $db,string $title,bool $admin=false): void {
    $site=cms_setting($db,'site_name');
    $full=$title===$site?$site:$title.' · '.$site;
    $color=cms_setting($db,'accent');
    if(!array_key_exists($color,cms_accent_options()))$color='blue';
    $theme=cms_setting($db,'theme');
    if(!array_key_exists($theme,cms_theme_options()))$theme='midnight';
    // Links on a CMS-hosted project subdomain return to the main public site.
    $home=cms_project_host_slug(cms_request_host())===null?'':'https://'.cms_root_domain();
    ?><!doctype html>
<html lang="en" data-theme="<?=cms_esc($theme)?>" data-accent="<?=cms_esc($color)?>">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="<?=cms_esc(cms_setting($db,'tagline'))?>">
<title><?=cms_esc($full)?></title>
<link rel="stylesheet" href="/assets/cms.css">
<script defer src="/assets/cms.js"></script>
</head><body>
<div class="site-frame">
<header class="topbar">
  <div class="shell nav-shell">
    <a class="identity" href="<?=$home?>/" aria-label="<?=cms_esc($site)?> homepage">
      <span class="identity-mark" aria-hidden="true">S<span>✦</span></span>
      <span class="identity-copy"><strong><?=cms_esc($site)?></strong><small><?=cms_esc(cms_setting($db,'tagline'))?></small></span>
    </a>
    <button class="nav-toggle" type="button" aria-expanded="false" aria-controls="primaryNav" data-nav-toggle>Menu</button>
    <nav id="primaryNav" class="topnav" aria-label="Main navigation">
      <a href="<?=$home?>/">Home</a><a href="<?=$home?>/projects">Projects</a><a href="<?=$home?>/about">About</a>
      <?php if($admin):?><a href="/admin" class="nav-admin">Dashboard</a><?php endif;?>
    </nav>
  </div>
</header>
<main class="shell page-main">
<?php
}
function cms_layout_end(PDO $db): void {
    $home=cms_project_host_slug(cms_request_host())===null?'':'https://'.cms_root_domain();
    ?>
</main>
<footer class="site-footer"><div class="shell footer-row">
  <span><?=cms_esc(cms_setting($db,'footer'))?></span>
  <span><a href="<?=$home?>/projects">Explore projects</a> <span class="footer-divider">·</span> <a href="<?=$home?>/admin">Admin</a></span>
</div></footer></div></body></html>
<?php
}
function cms_project_card(array $project): void {
    $slug=cms_esc($project['slug']);
    $url='/'.$slug;
    ?>
    <article class="project-card">
      <a class="project-card-link" href="<?=$url?>" <?=$project['kind']==='page'?'data-cms-nav':'data-full-navigation'?>>
        <div class="project-icon"><?=cms_esc($project['icon'])?></div>
        <div class="project-tag"><?=cms_esc($project['category'])?> <span>·</span> <?=cms_esc($project['kind']==='app'?'Application':'Project')?></div>
        <h3><?=cms_esc($project['title'])?></h3>
        <p><?=cms_esc($project['summary'])?></p>
        <span class="project-arrow">Explore project <span aria-hidden="true">↗</span></span>
      </a>
    </article>
    <?php
}
function cms_public_home(PDO $db): void {
    $projects=cms_public_directory_projects($db);
    cms_layout_start($db,cms_setting($db,'site_name'));
    ?>
    <section class="hero">
      <div class="hero-copy">
        <div class="eyebrow"><span class="pulse"></span> INDEPENDENT DEVELOPMENT STUDIO</div>
        <h1><?=cms_esc(cms_setting($db,'hero_title'))?></h1>
        <p><?=cms_esc(cms_setting($db,'hero_body'))?></p>
        <div class="hero-actions">
          <a class="btn primary" href="/projects">Explore the projects <span aria-hidden="true">↗</span></a>
          <a class="btn subtle" href="/about">About SirusSofts</a>
        </div>
      </div>
      <div class="hero-art" aria-hidden="true">
        <div class="orbit orbit-one"></div><div class="orbit orbit-two"></div>
        <div class="art-center"><span class="art-symbol">S</span><span>BUILD // EXPLORE</span></div>
        <span class="orbit-point point-one"></span><span class="orbit-point point-two"></span>
        <span class="orbit-point point-three"></span>
      </div>
    </section>
    <section class="section-block">
      <div class="section-heading"><div>
        <div class="eyebrow">THE WORK</div><h2>Projects & platforms</h2>
        <p>Software, systems and experiments living under one roof.</p>
      </div><a href="/projects" class="inline-link">View all projects ↗</a></div>
      <div class="project-grid">
        <?php foreach(array_slice($projects,0,6) as $project)cms_project_card($project);?>
        <?php if(!$projects):?><p class="empty-state">The project collection is being prepared.</p><?php endif;?>
      </div>
    </section>
    <section class="statement"><span class="eyebrow">ONE HOME. MANY IDEAS.</span>
      <h2>Independent projects. Shared foundation.</h2>
      <p>Each published project can have its own directory and subdomain. The SirusSofts homepage stays open to everyone; project administration stays private.</p>
    </section>
    <?php
    cms_layout_end($db);
}
function cms_public_catalog(PDO $db): void {
    $projects=cms_public_directory_projects($db);
    cms_layout_start($db,'Projects');
    ?><section class="page-intro"><div class="eyebrow">PROJECT DIRECTORY</div>
      <h1>Explore our projects.</h1><p>Every application has a home here. Follow the links to see what's available.</p></section>
      <div class="project-grid">
        <?php foreach($projects as $project)cms_project_card($project);?>
        <?php if(!$projects):?><div class="panel empty-state">No projects have been imported yet.</div><?php endif;?>
      </div>
    <?php cms_layout_end($db);
}
function cms_public_project(PDO $db,array $project): void {
    cms_layout_start($db,(string)$project['title']);
    $slug=(string)$project['slug'];
    ?><section class="page-intro project-detail"><span class="eyebrow"><?=cms_esc($project['category'])?></span>
      <div class="detail-symbol"><?=cms_esc($project['icon'])?></div>
      <h1><?=cms_esc($project['title'])?></h1><p class="detail-lede"><?=cms_esc($project['summary'])?></p>
      <div class="project-addresses">
        <span class="url-tag">sirussofts.com/<?=cms_esc($slug)?></span>
        <span class="url-tag">Planned subdomain: <?=cms_esc($slug)?>.sirussofts.com</span>
      </div>
    </section>
    <section class="content-panel">
      <?php if(($project['github_id']??null)!==null):?>
        <p>This repository is listed publicly, but its source code is available only through
          the authenticated SirusSofts source browser to administrators and explicitly
          authorized accounts.</p>
        <p><a class="btn primary" href="/admin/source?repo=<?=(int)$project['github_id']?>">
          Sign in to view source</a></p>
      <?php endif;?>
      <?=cms_body((string)$project['body'])?>
      <?php if($project['kind']==='app'):?>
        <div class="notice">This application's runtime is deployed separately. Its runtime, domain or proxy, DNS, and TLS must be provisioned separately through Hestia; a CMS project record does not deploy executable code.</div>
      <?php endif;?>
    </section>
    <a class="inline-link back-link" href="/projects">← All projects</a>
    <?php cms_layout_end($db);
}
function cms_public_about(PDO $db): void {
    cms_layout_start($db,'About');
    ?><section class="page-intro"><div class="eyebrow">ABOUT SIRUSSOFTS</div>
      <h1>Building what's next.</h1><p><?=cms_esc(cms_setting($db,'tagline'))?></p></section>
      <article class="content-panel"><?=cms_body(cms_setting($db,'about'))?></article>
    <?php cms_layout_end($db);
}
function cms_public_page(PDO $db,array $page): void {
    cms_layout_start($db,(string)$page['title']);
    ?><section class="page-intro"><div class="eyebrow">SIRUSSOFTS</div>
      <h1><?=cms_esc($page['title'])?></h1></section>
      <article class="content-panel"><?=cms_body((string)$page['body'])?></article>
    <?php cms_layout_end($db);
}
function cms_public_not_found(PDO $db): void {
    http_response_code(404);
    cms_layout_start($db,'Not found');
    ?><section class="page-intro"><div class="eyebrow">404 / UNKNOWN ROUTE</div>
    <h1>Nothing lives here yet.</h1><p>Try the project directory to find your way.</p>
    <a class="btn primary" href="/projects">Browse projects ↗</a></section>
    <?php cms_layout_end($db);
}
