Yes, higher quantization would likely reduce several of the issues you're hitting. Here's why:

**What Q4 is costing you specifically:**

| Issue                                                              | Quantization-related?                                                                                                 | Why                                                       |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Hallucinated params (`required_context`, `old_text` on write_file) | **Yes** — Q4 degrades structured output precision. The model "fuzzes" the tool schema boundaries                      | Q6/Q8 retain more of the fine-tuned tool-calling behavior |
| Thinking tokens consuming all budget (0ch output)                  | **Partially** — lower quant models reason less efficiently, needing more internal tokens to reach the same conclusion | Q8 would think more concisely                             |
| `ast.parse` on `.js` files                                         | **Somewhat** — instruction following degrades at lower quant                                                          | But we've now blocked this in code                        |
| Literal `path/to/test`                                             | **Somewhat** — same instruction following issue                                                                       | Also blocked in code now                                  |
| Truncated write_file content                                       | **Yes** — Q4 loses coherence faster over long generations, especially structured content like complete file rewrites  | Q8 would produce more complete outputs                    |

**Practical recommendation:**

- **Q6 would be a meaningful improvement** — it's the sweet spot for tool-calling models. You'll see fewer hallucinated parameters and better instruction adherence.
- **Q8 would be even better** but uses ~2x the VRAM of Q4. If your GPU has the headroom, it's worth it for agentic work.
- **The bigger lever is model size.** gemma-4-e4b is a ~4B effective parameter model. Even at Q8, it's a small model being asked to do complex multi-step tool orchestration. A larger model like `qwen2.5-coder:32b` at Q4 would likely outperform gemma-4-e4b at Q8 for this task, because agentic tool calling depends heavily on model capacity.

**TL;DR:** Try Q6 first (easy win, moderate VRAM increase). If you can fit a larger model like qwen2.5-coder:14b+ or gemma-3:27b at Q4-Q6, that would be the biggest improvement for reliable tool calling. The code fixes we've applied will also help regardless of quantization — they catch the failures that any model at any quant might produce.
