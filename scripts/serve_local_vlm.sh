#!/usr/bin/env bash
#
# Serve an on-device grading candidate locally, so the eval harness can put it to
# the same points as the hosted models.
#
# LEAP runs llama.cpp on device — every LFM2-VL manifest says
# "inference_type": "llama.cpp/image-to-text" — so serving the same GGUF and the
# same mmproj under llama-server here is the same engine the phone will run,
# not a stand-in for it. That is the whole reason the measurement is worth
# taking on a Mac: the sampling below is copied from the published manifest, and
# `vlm.py` sends it on every local call, so what is measured is what will ship.
#
# Usage:
#   scripts/serve_local_vlm.sh lfm-vl          # LFM2-VL-3B Q8_0   -> port 8081
#   scripts/serve_local_vlm.sh lfm-vl-q4       # LFM2-VL-3B Q4_K_M -> port 8082
#   scripts/serve_local_vlm.sh lfm-vl-small    # LFM2.5-VL-1.6B Q4 -> port 8083
#
# Ports match the registry in alcor_agents/inspector/vlm.py, so the arms can run
# concurrently in one grid. Weights are fetched from Hugging Face on first use
# and cached by llama.cpp.
#
set -euo pipefail

ARM="${1:-}"

case "$ARM" in
  lfm-vl)
    REPO="LiquidAI/LFM2-VL-3B-GGUF"
    MODEL="LFM2-VL-3B-Q8_0.gguf"
    MMPROJ="mmproj-LFM2-VL-3B-Q8_0.gguf"
    PORT=8081
    ;;
  lfm-vl-q4)
    REPO="LiquidAI/LFM2-VL-3B-GGUF"
    MODEL="LFM2-VL-3B-Q4_K_M.gguf"
    # Liquid ships no Q4 projector; their own Q4 manifests pair the Q4 language
    # model with the Q8 projector, so the vision tower is not what is being
    # cheapened here. Matching that keeps this arm a test of the language
    # quantisation alone.
    MMPROJ="mmproj-LFM2-VL-3B-Q8_0.gguf"
    PORT=8082
    ;;
  lfm-vl-small)
    REPO="LiquidAI/LFM2.5-VL-1.6B-GGUF"
    MODEL="LFM2.5-VL-1.6B-Q4_0.gguf"
    MMPROJ="mmproj-LFM2.5-VL-1.6b-Q8_0.gguf"
    PORT=8083
    ;;
  *)
    echo "usage: $0 {lfm-vl|lfm-vl-q4|lfm-vl-small}" >&2
    exit 2
    ;;
esac

if ! command -v llama-server >/dev/null 2>&1; then
  echo "llama-server not found. Install llama.cpp first:" >&2
  echo "  brew install llama.cpp" >&2
  echo "or, without Homebrew, take the upstream build — the Metal one, same" >&2
  echo "engine LEAP runs on device — and put it on PATH:" >&2
  echo "  https://github.com/ggml-org/llama.cpp/releases  (bin-macos-arm64)" >&2
  exit 1
fi

echo "$ARM -> $REPO/$MODEL on port $PORT"

# The context has to hold the whole prompt, and what that is depends on which
# eval is asking. A photo call sends one image; a video call sends the subtask's
# whole sampled span, and AM.I.D.S8's shortest is 43 frames. At the few hundred
# tokens the projector emits per frame that is tens of thousands of tokens, so
# the 8192 this served at — sized when only photo grading existed — truncated a
# sequence call before it had seen the work, and the model then graded whatever
# survived the cut. Overridable, because context is the memory cost of serving
# this arm and a photo-only run has no reason to pay for it.
CTX_SIZE="${LLAMA_CTX_SIZE:-32768}"

# One slot, and the context above is all of it. Left to itself llama-server opens
# four and gives each the full --ctx-size, so raising the context for video
# quadrupled into 131k tokens of KV cache, paged 25 GB of RAM out, and the server
# was killed mid-sequence — the run recorded three `request_failed`s and no
# reason. There is one GPU and one checkpoint here, so concurrent slots buy no
# throughput; they only multiply what is resident. The runner sends these calls
# one at a time to match.
SLOTS="${LLAMA_SLOTS:-1}"

# Sampling is verbatim from the model's leap/<quant>.json manifest. --jinja is
# required for the LFM2 chat template; without it the model is prompted in a
# format it was not trained on and grades worse for a reason that has nothing to
# do with the model.
exec llama-server \
  --hf-repo "$REPO" \
  --hf-file "$MODEL" \
  --mmproj-url "https://huggingface.co/$REPO/resolve/main/$MMPROJ" \
  --port "$PORT" \
  --host 127.0.0.1 \
  --jinja \
  --temp 0.1 \
  --min-p 0.15 \
  --repeat-penalty 1.05 \
  --ctx-size "$CTX_SIZE" \
  --parallel "$SLOTS" \
  "${@:2}"
