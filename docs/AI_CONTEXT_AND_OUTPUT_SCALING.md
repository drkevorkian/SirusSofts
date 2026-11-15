# SirusSofts AI — configurable model context and long output transport

## Current limits versus desired scale

The first Gentoo worker scripts pinned --ctx-size 2048, and the AI queue
router requested max_tokens=768. The output router then truncated model
answers after 16,000 characters, while the Ubuntu output gateway and web
client assumed every job completed in exactly one event (sequence 3).

These are **deployment/application limits**, not hardware or llama.cpp
architectural maximums. Model architecture and working memory still impose
finite effective context and generated-output capacity.

## Feature branch changes

- GPU service templates no longer include a fixed --ctx-size 2048.
  SIRUS_AI_CTX_SIZE=NNN optionally specifies context for *new* services.
  SIRUS_AI_CTX_SIZE=0 selects model-native context, at the cost of memory.
- Gentoo context-config.py --show / --plan / --apply supports the existing
  CPU/AMD/NVIDIA OpenRC service files. It does nothing until --apply and
  backs up/rolls back on service failure.
- The independent progressive_benchmark.py runs 3–25 response cycles,
  default 5, automatically sends NEXT, uses the user-specified test
  prompt, and measures real usage with the live server's /props context.
- The Gentoo AI task router no longer injects max_tokens=768.
- Longer inference outputs are divided into *durable 8192-character chunks*
  with incremental seq numbers, followed by a completed event with
  empty content. Short replies retain the legacy completed event at seq3.
- Ubuntu gateway accepts bounded chunk events, stores them in SQLite,
  and streams them through the existing authenticated output channel.
- Web app reassembles chunks into the final response, avoiding the
  previous 16,000-character silent clipping.
- Internal authentication, loopback bindings, separate SSH tunnels,
  worker selection and durable outbox remain intact.

## Important deployment order (NOT automatically deployed)

These changes span two machines. **Do not update Gentoo's live input router
before Ubuntu's gateway and web UI**: an older Ubuntu gateway rejects
chunk events, causing large answers to remain queued without delivery.

1. Run all Python tests locally from the checkout:

       python3 -m unittest discover -s sirus-ai-dual-tunnel/tests -p 'test_stack.py' -v
       python3 -m unittest discover -s sirus-ai-dual-tunnel/benchmarks -p 'test*.py' -v
       python3 -m unittest discover -s sirus-ai-dual-tunnel/gentoo -p 'test_context_config.py' -v

2. On Ubuntu, back up the installed
   /opt/sirus-ai-dual/ubuntu/gateway.py and
   /opt/sirus-ai-dual/ubuntu/web/app.js. Copy the two tested new files
   from the matching repository checkout; validate Python syntax and
   explicitly restart only the sirus-ai-gateway systemd service after
   administrator review. Verify /api/out/health and a short chat.

3. On Gentoo, back up the installed
   /opt/sirus-ai-dual/gentoo/input_server.py. Copy the tested new file
   from the matching repository checkout; validate Python syntax; then
   restart only sirus-ai-input OpenRC after administrator review. Do not
   touch CPU, AMD, NVIDIA or SSH tunnel services.

4. Send a short request and a long (>16,000 characters) request through
   /Gentoo/AI/. Verify short-result compatibility, no truncation, chunk
   order and reconnect behavior. Roll back from backups if either fails.

This update only changes GitHub source. It does not install or restart
the production services on either Gentoo or Ubuntu.

## Safety and scaling caveats

- The output transport allows an arbitrary number of bounded chunks,
  but the website input API **still validates** request size, message
  count, per-message size and the permitted roles. Those remain safety
  limits until authenticated per-node resource policies are added.
- The browser uses localStorage for conversations. Very large replies
  may exceed browser storage quota even though they're displayed.
  Durable server-side conversation storage belongs in the farm coordinator.
- Gentoo's router still uses a non-streaming model request. A very large
  completion is assembled in memory before chunking; streaming inference
  with incremental persistence is a later scalability improvement.
- Model context of 0 is *not* infinite: it means "use the model's native
  configured context". A request is bounded by its model's trained
  context window, KV-cache budget, worker hardware, and resource policy.
- Automatic long outputs may take minutes or hours. Gentoo's model request
  timeout now defaults to 1,800 seconds and can be set by the service
  environment variable SIRUS_AI_REQUEST_TIMEOUT, rather than
  prematurely capping the model at 768 generated tokens.
- Future farm admission control should use the per-worker /props n_ctx,
  available memory, model hash, queue and prompt-token count, not one
  global hardcoded token ceiling.

## Progression schedule

The user-supplied growth formula is exact:
ceil(1500 * (7/4)**(cycle - 1)).

Cycles 1–5: 1,500; 2,625; 4,594; 8,040; 14,069 tokens.
The cycle count is configurable from 3 to 25. Effective requests are
limited to the **actual** available server context after the prompt,
and both the desired target and observed completion count are recorded.
