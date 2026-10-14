# Throttling log, MacBook Air M3 16 GB (fanless)

Side table to the results: one line per run. `thermal` = worst macOS thermal state during the run (`—` = logger not running yet). GPU clock-capped = GPU busy ≥ 50% at < 2 W median.

| started (UTC) | run | result | GPU W med / max | pkg W med / max | GPU busy % | thermal | verdict |
|---|---|---|---|---|---|---|---|
| 20:38:57 | s1-clean-air-qwen4b-mlx4-1k-r1 | 30.988 tok/s | 6.6 / 11.6 | 7.9 / 12.6 | 99.88 | — | ok |
| 20:41:35 | s1-clean-air-qwen4b-ollama-1k-r1 | 14.739 tok/s | 1.3 / 7.4 | 1.8 / 8.7 | 88.22 | — | GPU clock-capped |
| 20:44:03 | s1-clean-air-qwen4b-q4km-1k-r1 | 6.579 tok/s | 1.2 / 2.5 | 1.8 / 3.3 | 79.3 | — | GPU clock-capped |
| 20:48:25 | s1-clean-air-qwen9b-mlx4-1k-r1 | 5.277 tok/s | 0.9 / 2.7 | 1.7 / 4.4 | 60.93 | — | GPU clock-capped |
| 20:53:23 | s1-clean-air-qwen9b-ollama-1k-r1 | 4.586 tok/s | 1.2 / 3.4 | 1.7 / 4.1 | 69.03 | — | GPU clock-capped |
| 20:58:19 | s1-clean-air-qwen9b-q4km-1k-r1 | 3.469 tok/s | 1.2 / 2.5 | 1.6 / 3.2 | 71.6 | — | GPU clock-capped |
| 21:10:02 | s1ctl-clean-air-qwen4b-mlx4-1k-r1 | 25.293 tok/s | 3.4 / 9.5 | 4.5 / 13.3 | 99.59 | fair | throttled (thermal: fair) |
| 21:11:35 | s2-clean-voice-chatterbox-mlx-refA-scene | RTF 4.127 (yes) | 1.1 / 3.2 | 1.7 / 5.2 | 76.21 | fair | throttled (thermal: fair) |
| 21:17:04 | s2-clean-voice-qwen3tts-0.6b-refA-scene | RTF 1.3 (yes) | 1.2 / 4.3 | 2.6 / 7.0 | 58.17 | fair | throttled (thermal: fair) |
| 21:50:31 | s1t-clean-air-qwen4b-mlx4-1k-r1 | 32.746 tok/s | 6.7 / 11.4 | 7.3 / 11.9 | 99.88 | nominal | ok |
| 21:52:48 | s1t-clean-air-qwen4b-ollama-1k-r1 | 23.554 tok/s | 6.0 / 11.3 | 6.8 / 12.2 | 99.95 | fair | throttled (thermal: fair) |
| 21:54:30 | s1t-clean-air-qwen4b-q4km-1k-r1 | 14.998 tok/s | 3.1 / 8.5 | 3.9 / 10.9 | 94.92 | fair | throttled (thermal: fair) |
| 21:57:22 | s1t-clean-air-qwen9b-mlx4-1k-r1 | 7.667 tok/s | 1.3 / 7.5 | 1.9 / 8.3 | 78.79 | fair | throttled (thermal: fair) |
| 22:01:17 | s1t-clean-air-qwen9b-ollama-1k-r1 | 6.819 tok/s | 1.1 / 9.9 | 1.6 / 11.1 | 64.88 | fair | throttled (thermal: fair) |
| 22:04:53 | s1t-clean-air-qwen9b-q4km-1k-r1 | 4.491 tok/s | 1.4 / 5.3 | 2.0 / 5.9 | 85.25 | fair | throttled (thermal: fair) |
| 22:07:49 | s2-clean-voice-qwen3tts-1.7b-refA-scene | RTF 2.309 (yes) | 1.1 / 6.0 | 1.9 / 10.6 | 76.65 | fair | throttled (thermal: fair) |

## Thermal state changes

- 21:07:07 UTC → nominal
- 21:10:01 UTC → fair
- 21:16:38 UTC → fair
- 21:19:55 UTC → nominal
- 21:48:55 UTC → nominal
- 21:53:00 UTC → fair
- 21:56:41 UTC → nominal
- 21:57:00 UTC → fair
- 22:00:00 UTC → nominal
- 22:01:10 UTC → fair
- 22:04:06 UTC → nominal
- 22:04:19 UTC → fair
