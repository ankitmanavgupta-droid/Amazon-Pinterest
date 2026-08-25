---
name: garment-flatlay-generator
description: Recreate one clothing product reference from the project's incoming-clothes folder as a realistic vertical vintage-resale flat-lay photo, using the bundled fixed prompt verbatim and saving a same-stem PNG in generated-images and moving the successful source to processed-inputs. Use for queued garment-reference processing, Pinterest-pin-style garment flat lays, or the project's automatic incoming-clothes workflow. Do not use for unrelated image generation, general photo editing, or multiple images in one execution.
---

# Garment Flat-Lay Generator

Process exactly one queued garment reference per execution. Use the built-in image-generation tool exactly once, save non-destructively, and preserve the fixed prompt byte-for-byte.

## Project contract

- Read project settings from `.garment-flatlay.json` at the project root.
- Treat `incoming-clothes/` as the input queue.
- Treat `generated-images/` as the output directory.
- Move successfully processed references into `processed-inputs/` only after the output PNG verifies.
- Map each input to the configured output pattern. The current pattern is `{stem}.png`, so `incoming-clothes/autumn-cardigan-1.jpg` maps to `generated-images/autumn-cardigan-1.png`.
- Never overwrite an existing output.
- Accept one PNG, JPEG, or WebP reference per execution.
- Use the input as a **reference image** for a new generation, not as a pixel-preserving edit target.

## Fixed prompt integrity

Read `references/fixed-prompt.txt` completely immediately before generation. Pass its contents as the image-generation `prompt` unchanged.

Do not prepend, append, rewrite, normalize, summarize, expand, or structure the prompt. Do not insert the input path, output path, role labels, or Pinterest wording into it. Supply the local input separately through `referenced_image_paths`.

## Process one image

1. Resolve the project root from the current working directory.
2. Run:

   ```text
   python3 .agents/skills/garment-flatlay-generator/scripts/garment_queue.py init --project-root <absolute-project-root>
   ```

3. If the invoking prompt supplies an existing claim key, absolute input path, and absolute output path, use them after confirming that they match the project configuration. Otherwise claim exactly one ready input:

   ```text
   python3 .agents/skills/garment-flatlay-generator/scripts/garment_queue.py claim --project-root <absolute-project-root>
   ```

4. If the claim result is `empty`, report that no ready input exists and stop without invoking image generation.
5. If the claim result is `blocked`, report the existing output conflict and stop without invoking image generation.
6. Read `references/fixed-prompt.txt` without changing it.
7. Invoke the built-in `image_gen` tool exactly once with:

   - `prompt`: the exact file contents
   - `referenced_image_paths`: an array containing only the claimed absolute input path
   - omit `num_last_images_to_include`

8. Do not retry, iterate, or make a second image-generation call in the same execution, even if the result is imperfect.
9. Obtain the generated PNG's local source path from the tool result. If no usable local path is available, treat saving as failed; do not invoke another generator.
10. Save with the non-overwriting helper:

    ```text
    python3 .agents/skills/garment-flatlay-generator/scripts/save_png.py --source <generated-source-path> --destination <claimed-output-path>
    ```

11. Verify and complete the claim:

    ```text
    python3 .agents/skills/garment-flatlay-generator/scripts/garment_queue.py complete --project-root <absolute-project-root> --key <claim-key> --output <claimed-output-path>
    ```

12. Report the final absolute output path.

## Failure handling

On any generation, saving, or verification failure:

1. Do not call image generation again.
2. Record the failure when a claim key exists:

   ```text
   python3 .agents/skills/garment-flatlay-generator/scripts/garment_queue.py fail --project-root <absolute-project-root> --key <claim-key> --error <concise-error>
   ```

3. Report the error and the affected absolute input path.

Never delete the source reference image. Move it to the configured `processed-inputs/` directory only after the output PNG has been saved and verified. Refuse to overwrite an existing processed input.

## Folder watcher

Use `scripts/watch_folder.py` only to trigger this skill when a ready image appears. The watcher claims one file, attaches it to a non-interactive Codex run, waits for completion, verifies the expected PNG, and then continues with the next file. It is intentionally not registered as a background service by the skill.

Start it interactively from the project root with:

```text
python3 .agents/skills/garment-flatlay-generator/scripts/watch_folder.py --project-root <absolute-project-root>
```

Use `--once` to process at most one ready input, or `--dry-run` to inspect the next mapping without invoking Codex.

Use `--nested-sandbox-bypass` only for a one-off test when the watcher itself is already confined by an external managed sandbox. Never use that option for a normal terminal run or persistent background watcher.
