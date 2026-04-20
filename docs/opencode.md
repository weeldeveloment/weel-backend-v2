# OpenCode + Ollama (local)

This repo ships an `opencode.jsonc` that configures OpenCode to use Ollama via its OpenAI-compatible API.

## Setup

1. Start Ollama:

   - `ollama serve`

2. Pull/run the model once (this will download it):

   - `ollama run hf.co/mradermacher/Qwen3-8B-heretic-GGUF:Q4_K_M`

3. From the repo root, run OpenCode:

   - `opencode`

OpenCode will pick up `opencode.jsonc` automatically and default to:

- `ollama/hf.co/mradermacher/Qwen3-8B-heretic-GGUF:Q4_K_M`

