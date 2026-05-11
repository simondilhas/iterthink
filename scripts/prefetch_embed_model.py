"""Developer helper: download FastEmbed ONNX weights into ``iterthink/embedded_models/``.

Use for local offline Flet builds or testing; desktop CI no longer prefetches here.
Run before ``flet build`` if you want the model inside the package tree.
"""

from iterthink.ai.local_embedding import ensure_bundle_model_downloaded

print("Prefetching embedding model into package bundle…")
ensure_bundle_model_downloaded()
print("Done.")
