-- iks-dev — synthetic seed data (Task 0.3). NO real user data.
--
-- 3 threads, 15 sources, 2 sessions. Threads 1 (local LLM inference) and
-- 2 (self-hosted vector search) deliberately OVERLAP on embedding-model
-- topics so the Phase-5 suggestion worker has genuine cross-thread
-- relevance to find. Thread 3 (network hardening) is a clean negative
-- control — no cross-thread suggestions expected.
--
-- Embeddings are intentionally NULL here (SQL can't call the bge-m3
-- model). Run iks-dev/embed-seed.ps1 (Phase 5) to backfill real
-- embeddings before validating the suggestion worker.
--
-- Idempotent: fixed UUIDs + ON CONFLICT DO NOTHING, so this is safe to
-- re-apply to a scratch DB (the sandbox normally loads it once on a
-- fresh volume via /docker-entrypoint-initdb.d).

-- ── threads ────────────────────────────────────────────────────────────
INSERT INTO public.threads (id, name, description) VALUES
  ('11111111-1111-1111-1111-111111111111', 'Local LLM Inference Tuning',
     'Making local llama.cpp / llama-swap inference faster on a single GPU.'),
  ('22222222-2222-2222-2222-222222222222', 'Self-Hosted Vector Search',
     'Choosing and tuning a local vector store + embedding model for RAG.'),
  ('33333333-3333-3333-3333-333333333333', 'Home Network Hardening',
     'Locking down a self-hosted homelab: VPN, firewall egress, TLS.')
ON CONFLICT (id) DO NOTHING;

-- ── sources ────────────────────────────────────────────────────────────
-- content_hash is set to md5(content) to mirror find_or_create_source.
INSERT INTO public.sources (id, url, title, content, content_type, domain, content_hash, metadata) VALUES
  -- Thread 1 cluster: local LLM inference
  ('a0000000-0000-0000-0000-000000000001', 'https://example.test/llamacpp-quant',
     'Quantization formats in llama.cpp',
     'A practical comparison of GGUF quantization formats (Q4_K_M, Q5_K_M, Q6_K) and their effect on quality versus VRAM use for local inference.',
     'web_article', 'example.test', md5('llamacpp quant gguf q4 q5 q6 vram'), '{"seed":true}'),
  ('a0000000-0000-0000-0000-000000000002', 'https://example.test/gpu-vram-tuning',
     'Fitting a 27B model on a single 24GB GPU',
     'Techniques for fitting larger models on one consumer GPU: offload layers, KV cache quantization, and context-length tradeoffs.',
     'web_article', 'example.test', md5('gpu vram 24gb offload kv cache context length'), '{"seed":true}'),
  ('a0000000-0000-0000-0000-000000000003', 'https://example.test/kv-cache-context',
     'KV cache size and long-context inference',
     'How the key/value cache grows with context length and why long contexts dominate VRAM during local inference.',
     'web_article', 'example.test', md5('kv cache context length vram inference'), '{"seed":true}'),
  ('a0000000-0000-0000-0000-000000000004', 'https://example.test/speculative-decoding',
     'Speculative decoding for faster tokens',
     'Using a small draft model to accelerate a larger target model, with notes on acceptance rate and throughput gains.',
     'web_article', 'example.test', md5('speculative decoding draft model throughput'), '{"seed":true}'),
  -- s5 lives in Thread 1 but is ABOUT embeddings -> overlaps Thread 2.
  ('a0000000-0000-0000-0000-000000000005', 'https://example.test/embedding-models-overview',
     'A survey of open embedding models',
     'Comparison of open-weight text embedding models (bge, e5, gte) by dimension, MTEB score, and multilingual coverage for retrieval.',
     'web_article', 'example.test', md5('embedding models bge e5 gte mteb retrieval dimension'), '{"seed":true}'),

  -- Thread 2 cluster: self-hosted vector search
  ('a0000000-0000-0000-0000-000000000006', 'https://example.test/pgvector-hnsw',
     'Tuning HNSW indexes in pgvector',
     'How m and ef_construction affect recall and build time for HNSW indexes in pgvector, and when to prefer IVFFlat.',
     'web_article', 'example.test', md5('pgvector hnsw m ef_construction recall ivfflat'), '{"seed":true}'),
  -- s7 lives in Thread 2 but is ABOUT an embedding model -> overlaps Thread 1.
  ('a0000000-0000-0000-0000-000000000007', 'https://example.test/bge-m3',
     'The bge-m3 embedding model',
     'bge-m3 is a multilingual embedding model producing 1024-dimensional vectors, suitable for self-hosted retrieval and reranking.',
     'web_article', 'example.test', md5('bge-m3 embedding model 1024 multilingual retrieval'), '{"seed":true}'),
  ('a0000000-0000-0000-0000-000000000008', 'https://example.test/cosine-vs-dot',
     'Cosine similarity versus dot product',
     'When normalized embeddings make cosine and dot product equivalent, and how the distance metric interacts with the index.',
     'web_article', 'example.test', md5('cosine dot product normalized embeddings distance metric'), '{"seed":true}'),
  ('a0000000-0000-0000-0000-000000000009', 'https://example.test/rag-chunking',
     'Chunking strategies for RAG',
     'Fixed-size versus semantic chunking, overlap windows, and how chunk size changes retrieval quality and embedding cost.',
     'web_article', 'example.test', md5('rag chunking semantic overlap retrieval embedding'), '{"seed":true}'),
  ('a0000000-0000-0000-0000-00000000000a', 'https://example.test/reranking',
     'Cross-encoder reranking for retrieval',
     'Adding a cross-encoder reranking stage after vector search to improve precision before passing context to the LLM.',
     'web_article', 'example.test', md5('cross encoder reranking vector search precision'), '{"seed":true}'),

  -- Thread 3 cluster: home network hardening (negative control — no LLM/vector overlap)
  ('a0000000-0000-0000-0000-00000000000b', 'https://example.test/wireguard',
     'Setting up a WireGuard VPN',
     'A minimal WireGuard configuration for remote access to a homelab, including key generation and peer setup.',
     'web_article', 'example.test', md5('wireguard vpn homelab peer key remote access'), '{"seed":true}'),
  ('a0000000-0000-0000-0000-00000000000c', 'https://example.test/egress-allowlist',
     'Firewall egress allowlisting',
     'Restricting outbound connections from containers to an explicit allowlist of hosts to reduce exfiltration risk.',
     'web_article', 'example.test', md5('firewall egress allowlist outbound containers exfiltration'), '{"seed":true}'),
  ('a0000000-0000-0000-0000-00000000000d', 'https://example.test/tailscale-acls',
     'Tailscale ACLs for least privilege',
     'Writing Tailscale ACL policies so each device reaches only the services it needs.',
     'web_article', 'example.test', md5('tailscale acl least privilege device services'), '{"seed":true}'),
  ('a0000000-0000-0000-0000-00000000000e', 'https://example.test/fail2ban',
     'Blocking brute force with fail2ban',
     'Configuring fail2ban jails to ban IPs after repeated failed authentication attempts on exposed services.',
     'web_article', 'example.test', md5('fail2ban jail ban ip failed authentication'), '{"seed":true}'),
  ('a0000000-0000-0000-0000-00000000000f', 'https://example.test/reverse-proxy-tls',
     'TLS termination at a reverse proxy',
     'Terminating TLS at Caddy/nginx for internal services and automating certificate renewal.',
     'web_article', 'example.test', md5('tls reverse proxy caddy nginx certificate renewal'), '{"seed":true}')
ON CONFLICT (id) DO NOTHING;

-- ── thread_sources (primary, intra-thread links — all confirmed) ───────
INSERT INTO public.thread_sources (thread_id, source_id, link_type, status, confirmed_at) VALUES
  -- Thread 1
  ('11111111-1111-1111-1111-111111111111', 'a0000000-0000-0000-0000-000000000001', 'automatic',  'confirmed', now()),
  ('11111111-1111-1111-1111-111111111111', 'a0000000-0000-0000-0000-000000000002', 'automatic',  'confirmed', now()),
  ('11111111-1111-1111-1111-111111111111', 'a0000000-0000-0000-0000-000000000003', 'automatic',  'confirmed', now()),
  ('11111111-1111-1111-1111-111111111111', 'a0000000-0000-0000-0000-000000000004', 'automatic',  'confirmed', now()),
  ('11111111-1111-1111-1111-111111111111', 'a0000000-0000-0000-0000-000000000005', 'deliberate', 'confirmed', now()),
  -- Thread 2
  ('22222222-2222-2222-2222-222222222222', 'a0000000-0000-0000-0000-000000000006', 'automatic',  'confirmed', now()),
  ('22222222-2222-2222-2222-222222222222', 'a0000000-0000-0000-0000-000000000007', 'automatic',  'confirmed', now()),
  ('22222222-2222-2222-2222-222222222222', 'a0000000-0000-0000-0000-000000000008', 'automatic',  'confirmed', now()),
  ('22222222-2222-2222-2222-222222222222', 'a0000000-0000-0000-0000-000000000009', 'automatic',  'confirmed', now()),
  ('22222222-2222-2222-2222-222222222222', 'a0000000-0000-0000-0000-00000000000a', 'deliberate', 'confirmed', now()),
  -- Thread 3
  ('33333333-3333-3333-3333-333333333333', 'a0000000-0000-0000-0000-00000000000b', 'automatic',  'confirmed', now()),
  ('33333333-3333-3333-3333-333333333333', 'a0000000-0000-0000-0000-00000000000c', 'automatic',  'confirmed', now()),
  ('33333333-3333-3333-3333-333333333333', 'a0000000-0000-0000-0000-00000000000d', 'automatic',  'confirmed', now()),
  ('33333333-3333-3333-3333-333333333333', 'a0000000-0000-0000-0000-00000000000e', 'automatic',  'confirmed', now()),
  ('33333333-3333-3333-3333-333333333333', 'a0000000-0000-0000-0000-00000000000f', 'automatic',  'confirmed', now())
ON CONFLICT (thread_id, source_id) DO NOTHING;

-- ── sessions + session_sources (provenance) ────────────────────────────
INSERT INTO public.sessions (id, origin_tool, query_text, thread_id) VALUES
  ('5e551011-0000-0000-0000-000000000001', 'owui',
     'how to speed up local llama.cpp inference on one GPU',
     '11111111-1111-1111-1111-111111111111'),
  ('5e551011-0000-0000-0000-000000000002', 'open_notebook',
     'self-hosted vector search options for RAG',
     '22222222-2222-2222-2222-222222222222')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.session_sources (session_id, source_id) VALUES
  ('5e551011-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001'),
  ('5e551011-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000002'),
  ('5e551011-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000003'),
  ('5e551011-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000004'),
  ('5e551011-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000006'),
  ('5e551011-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000007'),
  ('5e551011-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000008'),
  ('5e551011-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000009')
ON CONFLICT (session_id, source_id) DO NOTHING;
