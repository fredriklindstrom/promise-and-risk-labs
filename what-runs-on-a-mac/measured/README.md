# Measured results

Mac Studio (Apple M2 Max, 38-core GPU, 96 GB) and MacBook Air (Apple M3, 10-core GPU, 16 GB). Everyday machines with ordinary apps open, not dedicated AI nodes: your numbers will differ. Every figure traces to a run ID plus machine in `summary.csv` / `media.csv` (the like-for-like runs reuse the same IDs on both Macs).

## Test set 1: language models

### Qwen 3.8 27B

| Runner | Quant | Context | Decode tok/s | Prefill tok/s | TTFT s | Peak GB | System mem peak GB | Avg / peak W (package) | Thermal worst · GPU MHz avg (min) · die max °C | Load s | Fit | Reps | Load condition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MLX | MLX 4-bit | 1K | 21.25 (21.24–21.27) | 132.6 (132.5–132.7) | 3.94 (3.94–3.95) | 16.4 (16.4–16.4) | 32.6 (32.6–32.7) | — / — | — | 3.0 | yes | 3 | clean |
| MLX | MLX 4-bit | 8K | 20.20 (20.13–20.22) | 143.3 (143.3–143.3) | 53.66 (53.65–53.67) | 18.9 | 36.0 (35.8–36.1) | — / — | — | 1.3 | yes | 3 | clean |
| MLX | MLX 4-bit | 32K | 17.66 (17.66–17.66) | 133.5 (133.5–133.5) | 241.74 (241.74–241.74) | 22.9 | 40.2 (40.0–40.2) | — / — | — | 1.4 | yes | 3 | clean |
| MLX | MLX 4-bit | 128K | 12.19 (12.18–12.20) | 100.6 (100.6–100.7) | 1297.74 (1297.11–1297.88) | 39.4 | 61.5 (61.2–61.5) | 56 (55–56) / 82 (80–96) | — | 1.5 | yes | 3 | clean |
| MLX | MLX 8-bit | 1K | 12.21 (12.19–12.25) | 131.9 (131.8–132.0) | 3.96 (3.95–3.97) | 29.7 (29.7–29.7) | 45.9 (45.9–45.9) | 31 (31–31) / 60 (60–60) | — | 4.9 | yes | 3 | clean |
| MLX | MLX 8-bit | 8K | 11.94 (11.93–11.94) | 145.4 (145.4–145.4) | 52.88 (52.87–52.88) | 32.3 | 49.3 (49.2–49.4) | 46 (46–46) / 62 (61–65) | — | 1.7 | yes | 3 | clean |
| MLX | MLX 8-bit | 32K | 10.90 (10.83–10.92) | 135.4 (135.4–135.4) | 238.29 (238.28–238.38) | 36.4 | 53.4 (53.4–53.6) | 54 (54–54) / 64 (62–65) | — | 1.7 | yes | 3 | clean |
| MTPLX | MTPLX 4-bit (FP16) | 1K | 21.60 (20.16–21.67) | 178.6 (177.9–178.8) | 2.93 (2.93–2.93) | 20.5 (19.0–21.4) | 39.7 (38.4–40.9) | 55 (55–56) / 66 (64–67) | — | 27.5 | yes | 3 | clean |
| MTPLX | MTPLX 4-bit (FP16) | 8K | 19.40 (18.74–19.53) | 149.7 (149.7–149.7) | 51.35 (51.35–51.36) | 35.4 (32.8–37.5) | 54.6 (52.2–56.9) | 56 (56–56) / 67 (67–89) | — | 11.1 | yes | 3 | clean |
| MTPLX | MTPLX 4-bit (FP16) | 32K | 16.94 (16.46–17.42) | 178.3 (178.3–178.5) | 180.91 (180.75–180.93) | 38.5 (33.5–44.8) | 60.3 (55.1–66.6) | 61 (61–61) / 108 (86–121) | nominal · die max 62 °C | 10.7 | yes | 3 | clean |
| Ollama | NVFP4 (Ollama default) | 1K | 25.88 (25.72–26.43) | 134.3 (132.4–134.4) | 3.88 (3.88–3.93) | 19.5 (19.1–20.0) | 39.4 (39.1–39.9) | 51 (51–54) / 57 (57–67) | — | 7.8 | yes | 3 | clean |
| Ollama | NVFP4 (Ollama default) | 8K | — | — | — | 18.2 | 40.7 (40.6–40.7) | 54 (54–54) / 58 (58–58) | — | 1.7 | yes | 3 | clean |
| Ollama | NVFP4 (Ollama default) | 32K | 21.50 | 123.6 | 261.00 | 18.2 (18.2–21.3) | 48.6 (48.5–50.9) | 55 (55–55) / 59 (59–60) | — | 1.6 | yes | 3 | clean |
| llama.cpp | GGUF Q4_K_M | 1K | 17.72 (17.69–17.73) | 170.0 (169.8–170.0) | 2.64 (2.64–2.64) | 16.8 (16.8–16.8) | 33.0 (32.6–33.4) | 50 (50–50) / 66 (64–66) | — | 6.5 | yes | 3 | clean |
| llama.cpp | GGUF Q4_K_M | 8K | 17.16 (17.11–17.16) | 171.8 (171.8–171.8) | 44.33 (44.32–44.35) | 17.1 (17.1–17.1) | 34.9 (33.9–35.9) | 57 (57–58) / 66 (65–67) | — | 1.8 | yes | 3 | clean |
| llama.cpp | GGUF Q4_K_M | 32K | 15.34 (15.34–15.35) | 150.7 (150.7–150.7) | 213.58 (213.57–213.60) | 18.7 (18.7–18.8) | 40.0 (37.4–42.5) | 60 (60–60) / 66 (66–73) | — | 2.1 | yes | 3 | clean |
| llama.cpp | GGUF Q4_K_M | 128K | 10.01 (9.99–10.01) | 99.4 (99.4–99.4) | 1313.04 (1312.95–1313.09) | 25.3 (25.3–25.3) | 43.4 (43.2–44.3) | 58 (57–58) / 70 (66–99) | — | 2.6 | yes | 3 | clean |
| llama.cpp | GGUF Q8_0 | 1K | 12.38 (12.38–12.38) | 191.7 (191.6–191.7) | 2.34 (2.34–2.34) | 27.9 (27.9–27.9) | 46.4 (45.9–46.7) | 38 (38–38) / 61 (61–61) | — | 10.3 | yes | 3 | clean |
| llama.cpp | GGUF Q8_0 | 8K | 12.06 | 193.1 (193.1–193.1) | 39.45 (39.44–39.45) | 28.3 (28.3–28.3) | 48.0 (47.1–49.1) | 49 (49–49) / 63 (63–63) | — | 2.3 | yes | 3 | clean |
| llama.cpp | GGUF Q8_0 | 32K | 11.10 (11.10–11.10) | 166.8 (166.8–166.8) | 193.02 (193.00–193.03) | 29.8 (29.8–29.8) | 52.8 (51.2–55.3) | 56 (56–56) / 68 (64–68) | — | 2.3 | yes | 3 | clean |

### Qwen 3.6 35B-A3B

| Runner | Quant | Context | Decode tok/s | Prefill tok/s | TTFT s | Peak GB | System mem peak GB | Avg / peak W (package) | Thermal worst · GPU MHz avg (min) · die max °C | Load s | Fit | Reps | Load condition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MLX | MLX 4-bit | 1K | 91.23 (91.06–91.24) | 707.1 (658.7–715.0) | 0.74 (0.73–0.79) | 20.2 (20.2–20.2) | 39.6 (39.1–39.7) | 31 (31–31) / 39 (37–40) | — | 3.6 | yes | 3 | clean |
| MLX | MLX 4-bit | 32K | 65.99 (65.78–66.03) | 825.7 (825.1–825.8) | 39.08 (39.07–39.10) | 23.8 | 43.3 (43.1–43.4) | 52 (52–53) / 77 (60–91) | — | 1.5 | yes | 3 | clean |
| llama.cpp | GGUF Q4_K_M | 1K | 79.24 (78.94–79.63) | 1016.9 (1014.4–1024.1) | 0.44 (0.44–0.44) | 20.9 (20.9–20.9) | 39.3 (39.2–39.5) | 33 (32–33) / 38 (35–38) | — | 7.5 | yes | 3 | clean |
| llama.cpp | GGUF Q4_K_M | 32K | 61.23 (61.17–61.43) | 751.8 (751.7–751.9) | 42.82 (42.82–42.83) | 21.6 (21.6–21.6) | 41.3 (40.6–42.1) | 52 (51–52) / 57 (56–58) | — | 1.8 | yes | 3 | clean |

### Llama 3.3 70B Instruct

| Runner | Quant | Context | Decode tok/s | Prefill tok/s | TTFT s | Peak GB | System mem peak GB | Avg / peak W (package) | Thermal worst · GPU MHz avg (min) · die max °C | Load s | Fit | Reps | Load condition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MLX | MLX 4-bit | 1K | 8.99 (8.99–8.99) | 71.3 (71.0–71.7) | 7.49 (7.48–7.49) | 40.4 (40.4–40.4) | 57.9 (57.9–57.9) | 43 (43–43) / 67 (65–99) | — | 6.6 | yes | 3 | clean |
| MLX | MLX 4-bit | 32K | 6.51 (6.51–6.51) | 58.1 | 555.59 (555.57–555.60) | 51.1 (51.1–51.1) | 78.2 (78.0–79.0) | 60 (59–60) / 96 (93–124) | — | 3.0 | yes | 3 | clean |
| llama.cpp | GGUF Q4_K_M | 1K | 7.46 (7.46–7.46) | 62.9 (62.9–63.0) | 7.14 (7.13–7.14) | 40.7 (40.6–40.8) | 59.4 (59.2–59.7) | 55 (55–55) / 66 (65–68) | — | 14.6 | yes | 3 | clean |
| llama.cpp | GGUF Q4_K_M | 32K | 5.53 (5.53–5.54) | 46.5 (46.5–46.5) | 692.70 (692.11–692.76) | 50.4 (50.4–50.5) | 69.0 (68.9–69.5) | 62 (62–63) / 72 (71–73) | — | 3.9 | yes | 3 | clean |

### gpt-oss-120b

| Runner | Quant | Context | Decode tok/s | Prefill tok/s | TTFT s | Peak GB | System mem peak GB | Avg / peak W (package) | Thermal worst · GPU MHz avg (min) · die max °C | Load s | Fit | Reps | Load condition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MLX | MXFP4 native | 1K | 49.34 (49.28–49.43) | 384.9 (376.4–386.6) | 1.48 (1.47–1.51) | 65.8 | 81.3 (81.2–81.4) | 33 (32–33) / 52 (50–53) | — | 13.1 | yes | 3 | clean |
| MLX | MXFP4 native | 8K | 42.68 (42.65–42.75) | 524.4 (524.2–524.6) | 14.75 (14.75–14.76) | 66.4 | 80.4 (80.2–80.9) | 45 (45–45) / 60 (58–60) | — | 13.0 | yes | 3 | clean |
| MLX | MXFP4 native | 32K | 30.95 (30.92–30.98) | 438.1 (438.0–438.1) | 73.75 (73.75–73.77) | 67.2 | 81.1 (81.1–81.2) | 53 (53–53) / 62 (60–64) | — | 12.2 | yes | 3 | clean |
| MLX | MXFP4 native, GPU limit raised to 91 GB | 128K | — | — | — | — | — | — / — | — | — | no: macOS stopped the GPU job (Impacting Interactivity) after 8 min; GPU 71.3 of 91  | 0 | clean |
| llama.cpp | MXFP4 native (GGUF) | 1K | 65.94 (65.92–66.40) | 366.4 (365.9–367.5) | 1.22 (1.22–1.22) | 60.5 (60.4–60.5) | 74.8 (74.7–74.8) | 41 (41–42) / 55 (53–55) | — | 21.1 | yes | 3 | clean |
| llama.cpp | MXFP4 native (GGUF) | 8K | 59.89 (59.82–59.98) | 487.8 (487.0–487.9) | 15.62 (15.61–15.64) | 60.7 (60.7–60.8) | 75.6 (75.3–75.8) | 51 (51–52) / 58 (58–59) | — | 3.6 | yes | 3 | clean |
| llama.cpp | MXFP4 native (GGUF) | 32K | 45.66 (45.65–45.66) | 377.8 (377.8–377.9) | 85.20 (85.20–85.21) | 61.7 (61.6–61.7) | 79.4 (77.5–81.2) | 56 (55–57) / 60 (59–64) | — | 3.9 | yes | 3 | clean |
| llama.cpp | MXFP4 native (GGUF) | 128K | 24.21 (24.20–24.25) | 190.6 (190.6–190.6) | 684.83 (684.74–684.84) | 65.3 (65.3–65.3) | 85.6 (85.5–85.6) | 56 (56–56) / 74 (62–74) | — | 4.1 | yes | 3 | clean |

### Qwen 3.5 4B

| Runner | Quant | Context | Decode tok/s | Prefill tok/s | TTFT s | Peak GB | System mem peak GB | Avg / peak W (package) | Thermal worst · GPU MHz avg (min) · die max °C | Load s | Fit | Reps | Load condition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MLX | MLX 4-bit | 1K | 107.61 | 730.0 | 0.71 | 3.4 | 35.0 | 33 / 43 | — | 1.2 | yes | 1 | clean |
| Ollama | Ollama default (qwen3.5:4b) | 1K | 83.34 | 758.0 | 0.69 | 4.3 | 36.5 | 33 / 41 | — | 3.2 | yes | 1 | clean |
| llama.cpp | GGUF Q4_K_M | 1K | 81.05 | 1017.6 | 0.44 | 3.6 | 34.4 | 54 / 56 | — | 5.9 | yes | 1 | clean |

### Qwen 3.5 9B

| Runner | Quant | Context | Decode tok/s | Prefill tok/s | TTFT s | Peak GB | System mem peak GB | Avg / peak W (package) | Thermal worst · GPU MHz avg (min) · die max °C | Load s | Fit | Reps | Load condition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MLX | MLX 4-bit | 1K | 63.92 | 431.3 | 1.21 | 5.9 | 37.3 | 42 / 54 | — | 1.5 | yes | 1 | clean |
| Ollama | Ollama default (qwen3.5:9b) | 1K | 58.69 | 449.9 | 1.16 | 8.0 | 39.9 | 47 / 52 | — | 1.0 | yes | 1 | clean |
| llama.cpp | GGUF Q4_K_M | 1K | 52.20 | 576.4 | 0.78 | 6.3 | 36.3 | 54 / 62 | — | 1.0 | yes | 1 | clean |

### Qwen3.8-Flash-Next

| Runner | Quant | Context | Decode tok/s | Prefill tok/s | TTFT s | Peak GB | System mem peak GB | Avg / peak W (package) | Thermal worst · GPU MHz avg (min) · die max °C | Load s | Fit | Reps | Load condition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MTPLX | MTPLX 4-bit Bare Speed, n-gram table on SSD | 1K | 30.21 (28.82–30.43) | 297.3 (295.1–302.9) | 1.75 (1.72–1.76) | 71.8 (71.8–71.9) | 87.3 (87.2–87.3) | 39 (39–39) / 55 (52–57) | nominal · die max 54 °C | 45.9 | yes | 3 | clean |
| mlx-serve | DS4 Q4 via mlx-serve's embedded ds4 engine, n-gram table on SSD | 1K | 31.95 (31.42–32.70) | 274.9 (274.4–275.8) | 1.88 (1.88–1.88) | 73.0 (73.0–73.0) | 84.3 (84.2–84.4) | 32 (32–33) / 46 (45–46) | nominal · die max 57 °C | 33.4 | yes | 3 | clean |
| mlx-serve | DS4 Q4 via mlx-serve's embedded ds4 engine, n-gram table on SSD | 32K | 16.24 (15.78–16.32) | 382.8 (380.8–383.2) | 84.27 (84.19–84.72) | 77.4 (77.4–77.4) | 91.2 (91.0–91.5) | 39 (39–39) / 54 (53–58) | nominal · die max 60 °C | 7.6 | yes | 3 | clean |
| mlx-serve | mlx-serve iQ-MLX 3.3 bpw, n-gram table on SSD | 1K | 36.59 (36.16–36.75) | 324.8 (323.1–325.2) | 1.61 (1.60–1.61) | 53.9 (53.8–54.3) | 67.5 (67.3–67.7) | 33 (32–34) / 51 (48–55) | nominal · die max 55 °C | 17.0 | yes | 3 | clean |
| mlx-serve | mlx-serve iQ-MLX 3.3 bpw, n-gram table on SSD | 32K | 31.47 (29.13–32.70) | 440.4 (440.4–440.4) | 73.26 (73.26–73.26) | 60.7 (60.6–60.7) | 74.3 (74.3–74.5) | 47 (46–47) / 58 (57–82) | nominal · die max 60 °C | 16.0 | yes | 3 | clean |
| mlx-serve | mlx-serve iQ-MLX 4.7 bpw, n-gram table on SSD | 1K | 33.46 (32.82–34.02) | 324.9 (322.9–325.9) | 1.60 (1.59–1.62) | 73.0 (73.0–73.1) | 84.7 (84.5–85.2) | 30 (30–33) / 55 (51–57) | nominal · die max 57 °C | 49.6 | yes | 3 | clean |
| mlx-serve | mlx-serve iQ-MLX 4.7 bpw, n-gram table on SSD | 32K | 16.66 (16.06–18.55) | 183.8 (168.3–184.0) | 175.54 (175.38–191.69) | 75.2 (74.7–75.2) | 86.6 (86.5–86.8) | 25 (24–26) / 58 (58–59) | nominal · die max 56 °C | 36.8 | yes | 3 | clean |
| oMLX | MLX 4-bit, PLE on SSD | 1K | — | — | — | — | — | — / — | — | — | no: oMLX HTTP 507: 77.76 GB (PLE on SSD) vs 63.60 GB ceiling; see note below | 0 | clean |
| oMLX | MLX 4-bit, PLE on SSD | 32K | — | — | — | — | — | — / — | — | — | skipped: 1K already did not fit | 0 | clean |
| oMLX | MLX 4-bit, PLE on SSD, GPU limit raised to 91 GB, oMLX ceiling 86 GB | 1K | — | — | — | — | — | — / — | — | — | no: raised limit: loaded 22 s, then memory pressure critical, swap +8 GB; stopped | 0 | clean |
| oMLX | MLX 4-bit, PLE on SSD, GPU limit raised to 91 GB, oMLX ceiling 86 GB | 32K | — | — | — | — | — | — / — | — | — | skipped: 1K already did not fit | 0 | clean |
| oMLX | MLX 4-bit, PLE on SSD, memory guard aggressive | 1K | — | — | — | — | — | — / — | — | — | no: oMLX HTTP 507: 77.76 GB vs 77.37 GB ceiling (aggressive, all apps closed) | 0 | clean |
| oMLX | MLX 4-bit, PLE on SSD, memory guard aggressive | 32K | — | — | — | — | — | — / — | — | — | skipped: 1K already did not fit | 0 | clean |
| oMLX | MLX 4-bit, PLE on SSD, memory guard custom 80 GB | 1K | — | — | — | — | — | — / — | — | — | no: oMLX HTTP 507: projected 77.90 GB vs Metal cap 77.90 GB (iogpu.wired_limit_mb) | 0 | clean |
| oMLX | MLX 4-bit, PLE on SSD, memory guard custom 80 GB | 32K | — | — | — | — | — | — / — | — | — | skipped: 1K already did not fit | 0 | clean |

### Retrieval (needle)

| Model | Runner | Quant | Context | 0% | 25% | 50% | 75% | 100% |
|---|---|---|---|---|---|---|---|---|
| Qwen 3.8 27B | MLX | MLX 4-bit | 32K | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Qwen 3.8 27B | llama.cpp | GGUF Q4_K_M | 32K | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| gpt-oss-120b | MLX | MXFP4 native | 32K | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| gpt-oss-120b | llama.cpp | MXFP4 native (GGUF) | 32K | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Qwen 3.8 27B | MLX | MLX 4-bit | 128K | 1/1 | — | — | — | — |
| Qwen 3.8 27B | llama.cpp | GGUF Q4_K_M | 128K | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| gpt-oss-120b | MLX | MXFP4 native | 128K | — | — | — | — | — |
| gpt-oss-120b | llama.cpp | MXFP4 native (GGUF) | 128K | 3/3 | 3/3 | 3/3 | 1/3 | 3/3 |
| Qwen3.8-Flash-Next | mlx-serve | mlx-serve iQ-MLX 3.3 bpw, n-gram table on SSD | 32K | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Qwen3.8-Flash-Next | mlx-serve | DS4 Q4 via mlx-serve's embedded ds4 engine, n-gram table on SSD | 32K | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Qwen3.8-Flash-Next | mlx-serve | mlx-serve iQ-MLX 4.7 bpw, n-gram table on SSD | 32K | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |

Exact match on the final answer; raw answers stay in each run's folder on the machine that ran it (not published).

### Runner comparison: Qwen 3.8 27B, 1K context

| Runner | Quant | Decode tok/s | Prefill tok/s | TTFT s | Peak GB |
|---|---|---|---|---|---|
| Ollama | NVFP4 (Ollama default) | 25.88 (25.72–26.43) | 134.3 (132.4–134.4) | 3.88 (3.88–3.93) | 19.5 (19.1–20.0) |
| MTPLX | MTPLX 4-bit (FP16) | 21.60 (20.16–21.67) | 178.6 (177.9–178.8) | 2.93 (2.93–2.93) | 20.5 (19.0–21.4) |
| MLX | MLX 4-bit | 21.25 (21.24–21.27) | 132.6 (132.5–132.7) | 3.94 (3.94–3.95) | 16.4 (16.4–16.4) |
| llama.cpp | GGUF Q4_K_M | 17.72 (17.69–17.73) | 170.0 (169.8–170.0) | 2.64 (2.64–2.64) | 16.8 (16.8–16.8) |
| llama.cpp | GGUF Q8_0 | 12.38 (12.38–12.38) | 191.7 (191.6–191.7) | 2.34 (2.34–2.34) | 27.9 (27.9–27.9) |
| MLX | MLX 8-bit | 12.21 (12.19–12.25) | 131.9 (131.8–132.0) | 3.96 (3.95–3.97) | 29.7 (29.7–29.7) |

Matched quantization levels, not identical files (MLX 4-bit, GGUF Q4_K_M, Ollama NVFP4 and the MTPLX pack differ). Splash: not supported on M2 Max (requires M3 or newer).

### Qwen3.8-Flash-Next: what happened on each runner

oMLX, six tries on 96 GB. The first five were refused with HTTP 507 before loading. `summary.csv` holds the last run of each setting.

| Date (UTC) | Setting | oMLX's size estimate | Its ceiling | Result |
|---|---|---|---|---|
| 2026-10-12 | Balanced guard | 109.05 GB | 71.29–71.34 GB | Refused |
| 2026-10-21 | N-gram table on SSD, balanced guard | 77.76 GB | 63.59–63.60 GB | Refused |
| 2026-10-22 | Table on SSD, aggressive guard | 77.76 GB | 77.45–77.63 GB | Refused |
| 2026-10-22 | Same, every app closed | 77.76 GB | 77.33–77.37 GB | Refused |
| 2026-10-22 | Table on SSD, custom 80 GB ceiling | 77.90 GB | 77.90 GB (Metal cap) | Refused |
| 2026-10-22 | GPU limit raised to 91 GB (`iogpu.wired_limit_mb`), ceiling 86 GB | | | Loaded in 22 s, memory pressure critical, swap up 8 GB. Stopped by hand |

The other runners ran at the default GPU limit on 2026-11-05 and loaded every build. MTPLX finished the 1K runs and stopped its own 32K runs with an insufficient-memory error during prefill (6.3 GiB left free and reclaimable, 4.9 GiB needed for the next chunk, 5.0 GiB floor), so those three runs have no rows. mlx-serve finished 1K and 32K on all three builds. Swap stood at 11–14 GB during these runs.

## Test set 2: production stack

| Model / runtime | Setting | Clips | Min per clip | Forwards | s per full forward | Wh per clip | Peak W (package) | System mem peak GB | Kept | Min per usable clip | Hours / episode (10 / 15 clips) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MiniMax H3 · mlx-serve (fast mode) | 1312×736 5s 13 steps (clean) | 1/1 | 44.2 | 5 full + 3 partial + 5 cached of 13 | 432.7 | 39.0 | 103 | 58.1 | — | — | — |
| MiniMax H3 · mlx-serve (Turbo LoRA, no step cache) | 1312×736 5s 4 steps + turbo-4step (clean) | 3/3 | 34.9 (34.6–34.9) | 4 full of 4; 4 full of 4; 4 full of 4 | 465.8 (462.3–467.8) | 29.4 (29.3–29.6) | 98 (87–100) | 53.9 (45.3–54.2) | — | — | — |
| MiniMax H3 · mlx-serve (full compute) | 1312×736 5s 13 steps (clean) | 1/1 | 97.4 | 13 full of 13 | 431.9 | 86.4 | 100 | 41.4 | — | — | — |
| MiniMax H3 · mlx-serve (fast mode + ANE) | 1312×736 5s 13 steps (clean) | 1/1 | 40.6 | 5 full + 3 partial + 5 cached of 13 | 404.7 | 35.1 | 100 | 68.2 | — | — | — |
| MiniMax H3 · PipeNetwork minimax_h3_mlx (full compute) | 1312×736 5s 13 steps (clean) | 0/1 | — | — | — | — | — | — | — | — | — |
| MiniMax H3 · mlx-serve (Turbo LoRA, no step cache) | 1312×736 10s 4 steps + turbo-4step (clean) | 3/3 | 98.4 (98.4–98.6) | 4 full of 4; 4 full of 4; 4 full of 4 | 1366.7 (1366.2–1369.2) | 83.0 (83.0–83.1) | 100 (94–105) | 63.5 (63.3–63.9) | — | — | — |

One night = 8 hours. Hours per episode = minutes per usable clip × clips ÷ 60. mlx-serve's default fast mode runs a full transformer pass on only some steps, a partial pass (attention reused) on others and a cached velocity on the rest, so compare builds on seconds per FULL forward and on minutes per clip, never on an average over all scheduled steps.

### Narration

| Engine | Runner | Reference | Audio s | Wall s | RTF | Peak GB | Peak W | Wh |
|---|---|---|---|---|---|---|---|---|
| Chatterbox 0.1.7 (PyTorch/MPS) | PyTorch/MPS | reference A (a synthetic voice) | 46.68 | 170.6 | 1.848 | 36.93 | 39.04 | 0.3335 |
| Chatterbox 0.1.7 (PyTorch/MPS) | PyTorch/MPS | reference B (a real recorded voice) | 52.8 | 142.5 | 2.049 | 49.9 | 26.19 | 0.3418 |
| Chatterbox Multilingual (MLX) | MLX (mlx-audio) | reference A (a synthetic voice) | 50.36 | 43.4 | 0.467 | 5.34 | 40.76 | 0.1994 |
| Chatterbox Multilingual (MLX) | MLX (mlx-audio) | reference B (a real recorded voice) | 49.0 | 28.8 | 0.468 | 5.47 | 35.8 | 0.1956 |
| Qwen3-TTS 1.7B Base (MLX) | MLX (mlx-audio) | reference A (a synthetic voice) | 37.92 | 50.4 | 0.417 | 11.74 | 32.08 | 0.1093 |
| Qwen3-TTS 1.7B Base (MLX) | MLX (mlx-audio) | reference B (a real recorded voice) | 46.48 | 26.3 | 0.413 | 13.23 | 38.77 | 0.1079 |
| MOSS-TTS v1.5 8B | MLX (mlx-audio) | reference A (a synthetic voice) | 50.16 | 54.0 | 0.766 | 25.88 | 50.55 | 0.2541 |
| MOSS-TTS v1.5 8B | MLX (mlx-audio) | reference B (a real recorded voice) | 56.24 | 55.7 | 0.757 | 25.75 | 49.03 | 0.2832 |
| MOSS Local-Transformer v1.5 (MLX) | MLX (mlx-audio) | reference A (a synthetic voice) | 50.48 | 50.0 | 0.69 | 20.94 | 46.36 | 0.2965 |
| MOSS Local-Transformer v1.5 (MLX) | MLX (mlx-audio) | reference B (a real recorded voice) | 69.68 | 61.5 | 0.703 | 23.04 | 50.68 | 0.4004 |
| Qwen3-TTS 0.6B Base (MLX) | MLX (mlx-audio) | reference A (a synthetic voice) | 41.76 | 52.4 | 0.346 | 10.42 | 36.11 | 0.1263 |

## Same test, other Macs

**MacBook Air M3 16 GB** (MacBook Air · Apple M3 · 10-core GPU · 16 GB) against this machine, decode tokens/sec, 1K context:

| Variant | This machine | Air, first pass | Air, thermal-logged pass | Air, after cool-down | Other machine run IDs |
|---|---|---|---|---|---|
| air-qwen4b-mlx4 | 107.6 | 31.0 | 32.7 | 25.3 | `s1-clean-air-qwen4b-mlx4-1k-r1, s1t-clean-air-qwen4b-mlx4-1k-r1, s1ctl-clean-air-qwen4b-mlx4-1k-r1` |
| air-qwen4b-ollama | 83.3 | 14.7 | 23.6 | — | `s1-clean-air-qwen4b-ollama-1k-r1, s1t-clean-air-qwen4b-ollama-1k-r1` |
| air-qwen4b-q4km | 81.0 | 6.6 | 15.0 | — | `s1-clean-air-qwen4b-q4km-1k-r1, s1t-clean-air-qwen4b-q4km-1k-r1` |
| air-qwen9b-mlx4 | 63.9 | 5.3 | 7.7 | — | `s1-clean-air-qwen9b-mlx4-1k-r1, s1t-clean-air-qwen9b-mlx4-1k-r1` |
| air-qwen9b-ollama | 58.7 | 4.6 | 6.8 | — | `s1-clean-air-qwen9b-ollama-1k-r1, s1t-clean-air-qwen9b-ollama-1k-r1` |
| air-qwen9b-q4km | 52.2 | 3.5 | 4.5 | — | `s1-clean-air-qwen9b-q4km-1k-r1, s1t-clean-air-qwen9b-q4km-1k-r1` |

| Voice engine | Real-time factor here | there | Peak GB there | Fit there |
|---|---|---|---|---|
| Chatterbox Multilingual (MLX) | 0.467 | 4.127 | 4.88 | yes |
| Qwen3-TTS 0.6B Base (MLX) | 0.346 | 1.3 | 9.69 | yes |
| Qwen3-TTS 1.7B Base (MLX) | 0.417 | 2.309 | 12.03 | yes |

The Air is fanless. In the first pass the GPU dropped to its lowest clock (about 1–2 W) after the first model; in the thermal-logged pass macOS reported thermal state 'fair' on every run after the first (thermal-macbook-air.md). One timed run per model, so read small gaps as ties.


## Agent loop: cache hit vs miss

A scripted coding-agent session sent to each runner's server: a 33,000-token system prompt (tool descriptions plus CPython stdlib source, `inputs/agent_loop/`), then about 3,000 tokens of tool output per turn, 256 output tokens per turn, temperature 0. The script is `inputs/agent_loop/script.json`. Qwen 3.8 27B (hybrid attention, thinking on) runs 10 turns in four conditions; Llama 3.3 70B (standard attention) runs 6 turns in A and B only, as planned. Two runs per condition, server restarted for every run. TTFT is client-side, request sent to first streamed token. Cached tokens come from each server's own report (column `cached_source`), never from timing.

| ID | Condition | What changes between turns |
|---|---|---|
| A | Cache-friendly | Append only |
| B | Broken at the top | A short request ID at the very start of the system prompt changes every turn |
| C | Broken mid-context | One line about halfway through the history is edited every turn |
| D | Thinking stripped | Reasoning is removed from earlier assistant turns before resending |


### Headline: last turn, cache broken vs intact

| Model | Runner | Turn | A: TTFT s · cached tokens | B: TTFT s · cached tokens | B ÷ A |
|---|---|---|---|---|---|
| Qwen 3.8 27B | mlx_lm.server · MLX 4-bit | 10 | 29.9 (29.8–29.9) · 59,666 | 543.1 (542.8–543.3) · 0 | 18.2× |
| Qwen 3.8 27B | llama-server · GGUF Q4_K_M | 10 | 30.3 (30.3–30.3) · 59,667 | 484.9 (484.9–485.0) · 0 | 16.0× |
| Qwen 3.8 27B | oMLX · MLX 4-bit | 10 | 45.6 (45.6–45.6) · 59,405 | 658.2 (657.0–659.4) · 0 | 14.4× |
| Qwen 3.8 27B | Ollama · NVFP4 (Ollama default) | 10 | 32.6 (32.5–32.7) · 59,629 | 562.0 (561.2–562.8) · 5 | 17.2× |
| Llama 3.3 70B Instruct | llama-server · GGUF Q4_K_M | 6 | 108.1 (108.0–108.3) · 46,457 | 1252.1 (1250.6–1253.5) · 29 | 11.6× |
| Llama 3.3 70B Instruct | mlx_lm.server · MLX 4-bit, prompt cache capped at 16 GB, no seed | 6 | 92.7 (92.7–92.7) · 46,461 | 1087.7 (1087.7–1087.7) · 29 | 11.7× |

Median of two runs, range in brackets. Turn 10 for Qwen, turn 6 (the last) for Llama.

### What each runner kept at the last turn

| Model | Runner | Version | A | B | C | D | Peak GB |
|---|---|---|---|---|---|---|---|
| Qwen 3.8 27B | mlx_lm.server · MLX 4-bit | mlx-lm 0.31.3 / mlx 0.32.2 | kept: 59,666 of 62,700 (95%), 2/2 runs | re-read all: 0 of 62,699 (0%), 2/2 runs | re-read all: 0 of 62,702 (0%), 2/2 runs | re-read all: 0 of 60,390 (0%), 2/2 runs | 58.5 |
| Qwen 3.8 27B | llama-server · GGUF Q4_K_M | llama.cpp 0.6.0 b11429 (d81235049) | kept: 59,667 of 62,702 (95%), 2/2 runs | re-read all: 0 of 62,700 (0%), 2/2 runs | re-read all: 0 of 62,702 (0%), 2/2 runs | kept: 57,353 of 60,390 (95%), 2/2 runs | 22.3 |
| Qwen 3.8 27B | oMLX · MLX 4-bit | omlx 0.7.0 | kept: 59,405 of 62,700 (95%), 2/2 runs | re-read all: 0 of 62,700 (0%), 2/2 runs | partial: 28,672 of 62,700 (46%), 2/2 runs | partial: 54,253 of 60,309 (90%), 2/2 runs | 27.6 |
| Qwen 3.8 27B | Ollama · NVFP4 (Ollama default) | ollama 0.40.6 | kept: 59,629 of 62,664 (95%), 2/2 runs | re-read all: 5 of 62,663 (0%), 2/2 runs | partial: 28,359 of 62,665 (45%), 2/2 runs | kept: 57,339 of 60,377 (95%), 2/2 runs | 51.0 |
| Llama 3.3 70B Instruct | llama-server · GGUF Q4_K_M | llama.cpp 0.6.0 b11429 (d81235049) | kept: 46,457 of 49,485 (94%), 2/2 runs | re-read all: 29 of 49,486 (0%), 2/2 runs | partial: 23,380 of 49,485 (47%), 2/2 runs | not applicable (no thinking output) | 61.3 |
| Llama 3.3 70B Instruct | mlx_lm.server · MLX 4-bit, prompt cache capped at 16 GB, no seed | mlx-lm 0.31.3 / mlx 0.32.2 | kept: 46,461 of 49,488 (94%), 2/2 runs | re-read all: 29 of 49,488 (0%), 2/2 runs | partial: 23,380 of 49,487 (47%), 2/2 runs | not applicable (no thinking output) | 75.5 |

"kept" = at least 90% of the prompt came from cache; "partial" = the server rewound to an earlier point; "re-read all" = under 5% cached.

### C and D on the hybrid model

- **C, broken mid-context, turn 10:** mlx_lm.server 516.7 (516.3–517.1) s with 0 cached; llama-server 484.5 (484.5–484.6) s with 0 cached; oMLX 408.0 (408.0–408.1) s with 28,672 cached; Ollama 333.9 (333.9–334.0) s with 28,359 cached.
- **D, thinking stripped, turn 10:** mlx_lm.server 493.7 (493.7–493.8) s with 0 cached; llama-server 29.8 (29.8–29.8) s with 57,353 cached; oMLX 82.2 (82.2–82.2) s with 54,253 cached; Ollama 32.2 (32.2–32.3) s with 57,339 cached.
- D does not apply to Llama 3.3 70B: it emits no separate reasoning to strip.

### C on both models, llama-server, turn 6

| Model | Attention | Context tokens | C: TTFT s | C: cached tokens | A: TTFT s | B: TTFT s | Source |
|---|---|---|---|---|---|---|---|
| Qwen 3.8 27B | hybrid | 49,536 | 360.2 (360.2–360.3) | 0 | 27.4 (27.4–27.4) | 360.2 (360.2–360.2) | `cache_test.csv` lines 127, 137 |
| Llama 3.3 70B Instruct | standard | 49,485 | 794.9 (794.9–794.9) | 23,380 | 108.1 (108.0–108.3) | 1252.1 (1250.6–1253.5) | `cache_test.csv` lines 375, 381 |

With one line edited about halfway back, Llama 3.3 70B kept the 23,380 tokens before the edit (47% of the prompt); Qwen 3.8 27B on the same server kept 0.

### Turn by turn

**Qwen 3.8 27B · mlx_lm.server · MLX 4-bit**: TTFT s, median (range) of two runs · cached tokens

| Turn | Context tokens | A: Cache-friendly | B: Broken at the top | C: Broken mid-context | D: Thinking stripped |
|---|---|---|---|---|---|
| 1 | 33,077 | 250.1 (249.6–250.6) · 0 | 250.6 (250.4–250.8) · 0 | 250.8 (250.7–251.0) · 0 | 251.7 (251.6–251.7) · 0 |
| 2 | 36,374 | 25.8 (25.8–25.8) · 33,333 | 277.5 (277.3–277.7) · 0 | 276.2 (276.1–276.2) · 0 | 274.3 (274.3–274.3) · 0 |
| 3 | 39,667 | 26.3 (26.3–26.3) · 36,630 | 305.5 (305.3–305.7) · 0 | 304.1 (304.1–304.1) · 0 | 300.0 (300.0–300.0) · 0 |
| 4 | 42,956 | 26.8 (26.7–26.8) · 39,923 | 338.0 (337.9–338.0) · 0 | 332.7 (332.7–332.7) · 0 | 326.2 (326.2–326.2) · 0 |
| 5 | 46,243 | 56.1 (56.1–56.1) · 39,923 | 364.5 (364.4–364.5) · 0 | 361.8 (361.8–361.9) · 0 | 353.0 (353.0–353.0) · 0 |
| 6 | 49,533 | 27.9 (27.9–27.9) · 46,499 | 402.9 (402.9–402.9) · 0 | 391.5 (391.5–391.5) · 0 | 379.9 (379.9–379.9) · 0 |
| 7 | 52,824 | 28.4 (28.4–28.4) · 49,789 | 425.4 (425.3–425.4) · 0 | 421.9 (421.9–422.0) · 0 | 407.9 (407.9–407.9) · 0 |
| 8 | 56,117 | 28.9 (28.8–28.9) · 53,080 | 469.2 (469.0–469.4) · 0 | 452.7 (452.7–452.8) · 0 | 435.9 (435.8–436.0) · 0 |
| 9 | 59,410 | 29.4 (29.4–29.4) · 56,373 | 506.1 (505.8–506.4) · 0 | 484.4 (484.4–484.5) · 0 | 464.8 (464.7–464.9) · 0 |
| 10 | 62,700 | 29.9 (29.8–29.9) · 59,666 | 543.1 (542.8–543.3) · 0 | 516.7 (516.3–517.1) · 0 | 493.7 (493.7–493.8) · 0 |

**Qwen 3.8 27B · llama-server · GGUF Q4_K_M**: TTFT s, median (range) of two runs · cached tokens

| Turn | Context tokens | A: Cache-friendly | B: Broken at the top | C: Broken mid-context | D: Thinking stripped |
|---|---|---|---|---|---|
| 1 | 33,077 | 221.3 (221.3–221.4) · 0 | 221.3 (221.3–221.3) · 0 | 221.3 (221.3–221.3) · 0 | 221.3 (221.3–221.4) · 0 |
| 2 | 36,375 | 24.5 (24.5–24.5) · 33,332 | 247.7 (247.7–247.7) · 0 | 247.4 (247.4–247.4) · 0 | 24.5 (24.5–24.5) · 33,073 |
| 3 | 39,668 | 25.2 (25.2–25.2) · 36,630 | 274.9 (274.9–274.9) · 0 | 274.6 (274.6–274.6) · 0 | 25.1 (25.1–25.1) · 36,114 |
| 4 | 42,957 | 25.9 (25.9–25.9) · 39,923 | 302.3 (302.3–302.4) · 0 | 302.1 (302.0–302.1) · 0 | 25.8 (25.7–25.8) · 39,150 |
| 5 | 46,246 | 26.6 (26.6–26.6) · 43,212 | 330.8 (330.8–330.9) · 0 | 330.6 (330.5–330.6) · 0 | 26.4 (26.4–26.4) · 42,182 |
| 6 | 49,536 | 27.4 (27.4–27.4) · 46,501 | 360.2 (360.2–360.2) · 0 | 360.2 (360.2–360.3) · 0 | 27.1 (27.1–27.1) · 45,214 |
| 7 | 52,827 | 28.1 (28.1–28.1) · 49,791 | 390.3 (390.3–390.3) · 0 | 390.2 (390.2–390.3) · 0 | 27.8 (27.8–27.8) · 48,247 |
| 8 | 56,120 | 28.8 (28.8–28.8) · 53,082 | 421.0 (421.0–421.0) · 0 | 420.6 (420.6–420.6) · 0 | 28.5 (28.5–28.5) · 51,281 |
| 9 | 59,412 | 29.5 (29.5–29.5) · 56,375 | 452.6 (452.6–452.6) · 0 | 452.3 (452.3–452.3) · 0 | 29.1 (29.1–29.1) · 54,317 |
| 10 | 62,702 | 30.3 (30.3–30.3) · 59,667 | 484.9 (484.9–485.0) · 0 | 484.5 (484.5–484.6) · 0 | 29.8 (29.8–29.8) · 57,353 |

**Qwen 3.8 27B · oMLX · MLX 4-bit**: TTFT s, median (range) of two runs · cached tokens

| Turn | Context tokens | A: Cache-friendly | B: Broken at the top | C: Broken mid-context | D: Thinking stripped |
|---|---|---|---|---|---|
| 1 | 33,077 | 303.9 (303.1–304.7) · 0 | 304.0 (303.2–304.7) · 0 | 304.8 (304.7–305.0) · 0 | 303.5 (302.0–305.1) · 0 |
| 2 | 36,374 | 37.3 (37.3–37.3) · 33,072 | 334.3 (334.1–334.5) · 0 | 201.4 (201.4–201.4) · 16,384 | 34.3 (34.3–34.4) · 33,072 |
| 3 | 39,667 | 38.5 (38.5–38.5) · 36,369 | 370.9 (370.8–371.1) · 0 | 237.9 (237.9–237.9) · 16,384 | 35.4 (35.2–35.7) · 36,104 |
| 4 | 42,954 | 38.3 (38.3–38.3) · 39,662 | 407.9 (407.8–408.0) · 0 | 237.2 (237.2–237.2) · 20,480 | 35.0 (35.0–35.1) · 39,131 |
| 5 | 46,243 | 40.3 (40.3–40.3) · 42,949 | 446.8 (446.7–447.0) · 0 | 276.4 (276.4–276.4) · 20,480 | 37.6 (37.6–37.6) · 42,154 |
| 6 | 49,533 | 42.3 (42.3–42.3) · 46,238 | 487.8 (487.5–488.0) · 0 | 317.2 (317.2–317.2) · 20,480 | 38.1 (38.1–38.1) · 45,177 |
| 7 | 52,824 | 42.8 (42.7–42.8) · 49,528 | 528.6 (527.7–529.4) · 0 | 358.5 (358.5–358.5) · 20,480 | 39.3 (39.3–39.3) · 48,201 |
| 8 | 56,117 | 44.2 (44.2–44.3) · 52,819 | 570.5 (569.4–571.5) · 0 | 361.4 (361.3–361.4) · 24,576 | 39.3 (39.3–39.3) · 51,226 |
| 9 | 59,410 | 45.6 (45.6–45.6) · 56,112 | 614.2 (613.1–615.4) · 0 | 405.5 (405.4–405.5) · 24,576 | 41.0 (41.0–41.0) · 54,253 |
| 10 | 62,700 | 45.6 (45.6–45.6) · 59,405 | 658.2 (657.0–659.4) · 0 | 408.0 (408.0–408.1) · 28,672 | 82.2 (82.2–82.2) · 54,253 |

**Qwen 3.8 27B · Ollama · NVFP4 (Ollama default)**: TTFT s, median (range) of two runs · cached tokens

| Turn | Context tokens | A: Cache-friendly | B: Broken at the top | C: Broken mid-context | D: Thinking stripped |
|---|---|---|---|---|---|
| 1 | 33,039 | 274.1 (271.4–276.8) · 0 | 276.0 (275.4–276.5) · 0 | 276.4 (276.3–276.5) · 0 | 273.3 (270.6–276.0) · 0 |
| 2 | 36,337 | 28.1 (28.1–28.1) · 33,294 | 298.4 (298.4–298.4) · 0 | 172.5 (172.4–172.5) · 16,383 | 28.1 (28.1–28.1) · 33,034 |
| 3 | 39,629 | 28.4 (28.4–28.4) · 36,592 | 329.2 (329.2–329.2) · 6 | 193.8 (193.8–193.8) · 17,496 | 28.8 (28.6–28.9) · 36,075 |
| 4 | 42,918 | 30.2 (28.9–31.4) · 39,754 | 360.2 (360.2–360.2) · 6 | 210.1 (210.1–210.1) · 19,282 | 28.8 (28.8–28.8) · 39,136 |
| 5 | 46,207 | 29.8 (29.7–29.8) · 43,173 | 392.0 (392.0–392.1) · 6 | 232.3 (232.2–232.3) · 20,453 | 29.5 (29.5–29.6) · 42,168 |
| 6 | 49,497 | 31.5 (30.3–32.7) · 46,332 | 425.1 (425.0–425.1) · 6 | 254.8 (254.8–254.9) · 21,673 | 30.1 (30.1–30.1) · 45,200 |
| 7 | 52,788 | 32.1 (30.9–33.4) · 49,622 | 457.9 (457.6–458.1) · 6 | 276.4 (276.4–276.4) · 23,015 | 30.6 (30.6–30.6) · 48,233 |
| 8 | 56,081 | 31.5 (31.5–31.5) · 53,043 | 492.0 (491.5–492.5) · 6 | 297.0 (297.0–297.1) · 24,575 | 31.1 (31.1–31.1) · 51,267 |
| 9 | 59,374 | 34.8 (34.8–34.8) · 56,076 | 526.6 (526.0–527.3) · 0 | 312.1 (312.1–312.1) · 26,847 | 31.6 (31.6–31.6) · 54,303 |
| 10 | 62,664 | 32.6 (32.5–32.7) · 59,629 | 562.0 (561.2–562.8) · 5 | 333.9 (333.9–334.0) · 28,359 | 32.2 (32.2–32.3) · 57,339 |

**Llama 3.3 70B Instruct · llama-server · GGUF Q4_K_M**: TTFT s, median (range) of two runs · cached tokens

| Turn | Context tokens | A: Cache-friendly | B: Broken at the top | C: Broken mid-context |
|---|---|---|---|---|
| 1 | 33,059 | 719.0 (718.9–719.0) · 0 | 719.3 (719.3–719.3) · 0 | 723.4 (719.2–727.6) · 0 |
| 2 | 36,351 | 90.4 (90.4–90.4) · 33,314 | 815.9 (815.1–816.6) · 29 | 499.0 (499.0–499.1) · 17,335 |
| 3 | 39,637 | 94.8 (94.7–94.8) · 36,606 | 917.9 (916.9–918.9) · 29 | 565.6 (565.5–565.7) · 18,926 |
| 4 | 42,919 | 99.3 (99.2–99.4) · 39,892 | 1024.5 (1023.3–1025.6) · 29 | 646.4 (646.4–646.5) · 20,065 |
| 5 | 46,202 | 103.5 (103.4–103.6) · 43,173 | 1135.6 (1134.3–1136.9) · 29 | 728.3 (728.3–728.4) · 21,330 |
| 6 | 49,485 | 108.1 (108.0–108.3) · 46,457 | 1252.1 (1250.6–1253.5) · 29 | 794.9 (794.9–794.9) · 23,380 |

**Llama 3.3 70B Instruct · mlx_lm.server · MLX 4-bit, prompt cache capped at 16 GB, no seed**: TTFT s, median (range) of two runs · cached tokens

| Turn | Context tokens | A: Cache-friendly | B: Broken at the top | C: Broken mid-context |
|---|---|---|---|---|
| 1 | 33,059 | 594.0 (593.8–594.2) · 0 | 593.7 (593.7–593.8) · 0 | 597.4 (596.2–598.6) · 0 |
| 2 | 36,352 | 77.3 (77.3–77.3) · 33,314 | 711.5 (711.5–711.6) · 29 | 430.5 (430.4–430.5) · 17,335 |
| 3 | 39,638 | 75.3 (75.2–75.3) · 36,608 | 799.8 (799.6–800.0) · 29 | 487.8 (487.8–487.8) · 18,926 |
| 4 | 42,921 | 88.2 (88.2–88.2) · 39,801 | 891.4 (891.4–891.4) · 29 | 556.0 (555.8–556.1) · 20,065 |
| 5 | 46,205 | 88.8 (88.7–88.8) · 43,177 | 985.5 (985.5–985.6) · 29 | 626.4 (626.4–626.4) · 21,330 |
| 6 | 49,488 | 92.7 (92.7–92.7) · 46,461 | 1087.7 (1087.7–1087.7) · 29 | 682.8 (682.4–683.1) · 23,380 |

Turns where the cache stopped growing for one turn, in both runs: mlx_lm.server A turn 5: 39,923 cached, the same as turn 4, 56.1 (56.1–56.1) s against 26.8 (26.7–26.8) s (`cache_test.csv` lines 6, 16); oMLX D turn 10: 54,253 cached, the same as turn 9, 82.2 (82.2–82.2) s against 41.0 (41.0–41.0) s (`cache_test.csv` lines 311, 321). The server matched only up to the previous turn's prompt; the logs do not say why.

### Runs that did not finish

- **4 runs, `llama70b-mlx4`:** Llama 3.3 70B on mlx_lm.server, default prompt cache: out of GPU memory (`[METAL] ... Insufficient Memory`) at turn 5 in A and turn 4 in B, all four runs.
- **4 runs, `llama70b-mlx4-cap16`:** Same with `--prompt-cache-bytes 16GB`: failed at the same turns. The harness sends a seed, and mlx_lm.server only applies the byte cap to requests without one (seeded requests take its single-request path).
- **8 runs, Ollama 0.40.3:** the MLX runner crashed past ~33K tokens (`Maximum threads per threadgroup is 896 but requested 960`, ollama/ollama#18940, fixed in 0.40.4). Rerun on 0.40.6.

Leaving out the seed puts requests on the server's batched path, which is a little slower: in A, turns 2–4 took 75–88 s without a seed against 69–77 s for the seeded runs before they failed (same prompts).

### Ollama's default context window

With no `num_ctx`, ollama 0.40.6 loaded qwen3.8:27b (47951e4a6661) with **262,144 tokens** of context; Ollama reports 18.49 GB on the GPU, GPU memory in use +20.52 GB (2026-10-21T03:44 UTC).

