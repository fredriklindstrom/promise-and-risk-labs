#!/bin/bash
H=${HF:-hf}
for r in mlx-community/clef-4bit TrevorJS/clef-flash-mlx-4bit mlx-community/clef-8bit Cloudflare/clef-flash Cloudflare/clef; do
  echo "$(date +%T) START $r"
  $H download "$r" --exclude "__pycache__/*" --exclude "*.pyc" --quiet && echo "$(date +%T) DONE $r" || echo "$(date +%T) FAIL $r"
done
echo ALLDONE
# Run 6/7 conversions (MLX BF16) are made locally with: python -m mlx_vlm convert --hf-path <Cloudflare snapshot> --mlx-path <out> --dtype bfloat16
# then copy joint_head.safetensors (from Cloudflare) and clef_mlx.py (from mlx-community/clef-4bit) into <out>.
