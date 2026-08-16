lab - feed traffic-cam frames to the VLMs on the Spark box (ollama), see what they say.
  ./ask.py -h                          usage
  ollama list                          models available (qwen2.5vl:7b, qwen3-vl:8b, ...)
  samples/                             ~20 SDOT frames: crowd / few / blocked / construction / night / wet / empty
  samples/mac_reads.json               what the Mac's Qwen2.5-VL said about each (reference)
  prompts/caption.txt                  the production JSON schema prompt (safe_walk/vision.py)
  prompts/people.txt                   boxes per person -> use with --json --draw, overlays land in out/
  log.jsonl                            every call appended (image, model, prompt, seconds, response)
