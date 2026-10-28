# Instagram Reel cover update

## Included
- `cover_maker.py`: generates a 1080x1920 cover using the original product image, Pillow layout/text, and an optional text-free Gemini background; saves `cover.png` and `cover_prompt.txt` in the run folder.
- `llm.py`: existing LLM router plus `generate_image()` for a configured Gemini image model.
- `main_link.py`: generates and verifies the cover before opening Instagram for posting; passes the verified image into the posting guard.
- `post_guard.py`: selects the cover between video selection and caption entry. If the custom cover fails, it recovers to the New Reel screen and attempts to avoid a black default frame. The AI still cannot tap Share.
- `index.html`: adds an optional cover upload for “Only post a Reel” and display hooks for cover history.
- `test_cover.py`: builds and verifies a cover from an existing run folder without connecting to a phone.
- `assets/fonts/`: bundled DejaVu Sans fonts for readable text on Windows and Linux.
- `.env.cover.example`: optional image-model setting.
- `server.js.cover-integration.md`: integration notes for backend changes.

## Install
1. Back up your current files.
2. Replace `main_link.py`, `llm.py`, `post_guard.py`, and `index.html` with the versions in this package.
3. Add `cover_maker.py`, `test_cover.py`, and the `assets/fonts` directory beside `main_link.py`.
4. Copy the optional `IMAGE_MODEL` line from `.env.cover.example` into `.env` only if your Gemini API key has access to an image-output model. Model availability depends on the API account. If it is missing/unsupported, the generator uses a Pillow pastel gradient.
5. Install/confirm Pillow and your existing `google-genai`, `python-dotenv`, and `requests` dependencies.
6. Run the offline layout test: `python test_cover.py outputs_test/<existing-run-folder>`. This test calls your configured AI for text/vision; if the API is unavailable it exits as a failed verification, but still writes `cover.png` and `cover_prompt.txt` for visual inspection.
7. Test on a spare/draft Reel first. The final Share action remains owned by the existing guarded code.

## Important limitation
`server.js` was not included in the uploaded files. Therefore I have not overwritten or guessed your existing server. The updated HTML sends the optional upload as base64 and expects History cover routes/fields, but these need to be merged into your actual server using `server.js.cover-integration.md` before those two UI pieces work end-to-end. The generated cover for product-link post mode is wired through the supplied Python files.

## Image providers
The existing `llm.py` adapter supports text/vision with Gemini and configured OpenAI-compatible providers. The new image-generation adapter uses Gemini only, when `IMAGE_MODEL` names an image-output-capable model. The current adapters for Groq, NVIDIA, Cloudflare, Z.AI, OpenRouter and Mistral are not treated as image-generation APIs here. If Gemini image generation is unavailable, the Pillow gradient fallback keeps the run moving.
