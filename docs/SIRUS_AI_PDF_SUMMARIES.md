# Offline PDF summarization stage (Gentoo)

This is an explicitly invoked local-only summary generator. It reads the
unreviewed page-attributed JSONL from manual_ingest.py and calls an already
running local llama.cpp server on 127.0.0.1 (CPU 18482, NVIDIA 18486, AMD 18487).
No OpenAI API, web search, external data transfer or server restart is involved.

    python3 sirus-ai-dual-tunnel/gentoo/manual_summary.py --check
    python3 sirus-ai-dual-tunnel/gentoo/manual_summary.py \
      --draft /absolute/private/hd11-pages-1-200.draft.jsonl \
      --backend amd \
      --output /absolute/private/hd11-pages-1-200.summary.md

The command writes a NEW (not overwritten) 0600 summary Markdown with
section/page attribution and clear DRAFT status. Source excerpts are treated
as untrusted text, not commands. The model may hallucinate or omit numerical
values, images, tables or safety warnings. A summary never automatically
approves or replaces the source PDF; examine original pages before approving
manually imported excerpts in the CMS.

Large manuals can take many minutes of inference. The command is run
deliberately by an operator, not within an HTTP request. The terminal
program is not a resident service and is not connected to the website
upload workflow until a separate reviewed job runner is deployed.
