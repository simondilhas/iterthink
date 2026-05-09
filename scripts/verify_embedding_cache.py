"""CI helper: assert that ONNX weights are present in the bundle directory.

Fails fast (exit 1) if the prefetch step was skipped or incomplete.
"""

import sys
from iterthink.ai.local_embedding import bundled_embedding_models_root

root = bundled_embedding_models_root()
onnx_files = list(root.rglob("*.onnx"))

if not onnx_files:
    print(f"ERROR: no .onnx files found under {root}", file=sys.stderr)
    sys.exit(1)

print(f"OK: {len(onnx_files)} .onnx file(s) found under {root}")
for f in onnx_files:
    print(f"  {f.relative_to(root)}")
