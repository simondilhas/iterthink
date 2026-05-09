"""CI helper: download the FastEmbed ONNX weights into the package bundle directory.

Run before `flet build` so the model ships inside the app and works offline.
"""

from iterthink.ai.local_embedding import ensure_bundle_model_downloaded

print("Prefetching embedding model into package bundle…")
ensure_bundle_model_downloaded()
print("Done.")
