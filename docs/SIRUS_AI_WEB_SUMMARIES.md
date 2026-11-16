# Review-gated web source reading and local summarization

This source-only Gentoo CLI processes a small subset of discovered URL
candidates, not 5000 pages per inference prompt. It is not deployed, not
connected to a live chat request, and has no automatic approval path.

    python3 sirus-ai-dual-tunnel/gentoo/web_source_summary.py --check
    python3 sirus-ai-dual-tunnel/gentoo/web_source_summary.py \
      --search-results /absolute/private/discovered.json \
      --top 5 --backend amd \
      --output /absolute/private/source-summaries.draft.json

Or supply --url https://www.example.org/article and --output.
Only source URLs with HTTPS are considered. The downloader checks all
resolved IPv4 addresses are public, pins the connection to a checked IP
with curl --resolve, validates the site certificate, disables proxy
settings and redirects, bounds the HTTP transfer, then parses only text.
No discovered HTML is executed in a browser or PHP service. PDF URLs
must use the separate PDF/manual pipeline. Some JavaScript-only pages
will yield little/no text and should be reported as unsupported.

Model summaries run ONLY through an existing Gentoo llama.cpp server
on loopback (CPU 18482, NVIDIA 18486, AMD 18487). They are generated
as unreviewed, hash-attributed source summaries; they can contain errors
and must be evaluated against the actual source. The program logs
individual fetch failures without pretending a source was verified.

The operator is responsible for honoring source licenses, robots/access
terms and copyright. No web source content, credentials, Can-Am manual
text or private knowledge is sent to third-party LLMs. Search queries
are sent to the configured SearXNG-backed engines only when an operator
explicitly runs research.
