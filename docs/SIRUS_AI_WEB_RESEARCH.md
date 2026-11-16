# SirusSofts private multi-engine discovery (source staged)

Search source is available as a stand-alone Gentoo CLI, not yet wired to
the live chat queue or CMS admin job scheduler. Nothing was installed or
started on Ubuntu or Gentoo by this GitHub commit.

SearXNG is a metasearch gateway. Run it separately on Gentoo loopback
(e.g. 127.0.0.1:8888) with JSON results enabled, and configure engines
Google, Bing/MSN, Yahoo, DuckDuckGo, Brave and Mojeek as allowed by
their terms. Provider availability is best effort; rate limits and CAPTCHAs
are reported, not bypassed. The classic Bing Web Search API was retired;
Google Custom Search JSON API is closed to new customers.

Commands from the matching SirusSofts source checkout on Gentoo:

    python3 sirus-ai-dual-tunnel/gentoo/web_research.py --check
    python3 sirus-ai-dual-tunnel/gentoo/web_research.py \
      --query "Can-Am HD11 clutch service manual" \
      --profile mechanics --depth 50 \
      --searx-url http://127.0.0.1:8888/search

Depth is a candidate URL budget, not hyperlink-following recursion.
Default 50, maximum 5000. High-depth operator research can take many
sequential, rate-limited pages, and engines may not produce all 5000.
The tool deduplicates, attributes engines, classifies sources as
educational/government, curated primary, organization or unverified,
and reports engine failures. Source category is NOT proof of accuracy.

Security: only accesses an explicitly configured loopback metasearch API;
rejects redirects and never downloads discovered pages. No scraped
contents, summaries or candidate URLs become reviewed model knowledge
automatically. Source fetching, license checks, page citations, local
summarization and administrator approval require further integration.
Manuals uploaded into the private library must not be sent to public
search engines.
