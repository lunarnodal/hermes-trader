# trading-ai

Local LLM pipeline for financial news aggregation, sentiment analysis,
and market signal generation.

## Stack
- OpenClaw  — orchestration and feed ingestion (GPU Pool 2)
- Hermes    — prediction reasoning layer (DGX Spark)
- DeepSeek-R1-70B Q4  — DGX Spark deep reasoning
- DeepSeek-R1-32B Q8  — GPU Pool 1 mid-tier reasoning
- Qwen3-30B-A3B Q4    — GPU Pool 2 entity extraction
- FinGPT-v3 7B        — GPU Pool 2 sentiment scoring
- BGE-M3              — GPU Pool 2 embeddings
- Qdrant              — vector store
- QNAP ai_shared 5TB  — model weights and data storage

## Hardware
- 4x RTX 3090 FE (Bykski TC-V2) — 2x NVLink pairs (48GB each)
- DGX Spark — 128GB unified memory

## Repo Layout
- openclaw/     Skills, SOUL.md, gateway config
- hermes/       Agent config, auto-generated trajectories (versioned)
- pipeline/     Feed ingestion, sentiment, embedding, signal scripts
- infra/        Docker compose, Ollama config
- docs/         Architecture notes

## Storage
Model weights and runtime data live on QNAP at /mnt/qnap/
Symlinked into repo root for transparent access.
See docs/architecture.md for full data flow.
