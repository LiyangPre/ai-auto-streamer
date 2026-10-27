# Silver dragon sprite

- `reference.png`: original turnaround supplied by the user.
- `neutral.png`: transparent front-view sprite, produced using the built-in image generation tool. RGBA, 1024 × 1536. No external asset URL is required at runtime.
- Mouth anchor: x=0.494, y=0.157, width=0.018, height=0.009 in normalized image coordinates. Configured in `/api/config` for this sprite directory.
- The original `neutral.png` remains a fallback. The default 2D character now uses the layered rig below.

## Joint rig

- `rig.json`: part rectangles, rest pivots and lengths.
- `rig-parts.png`: separately painted head, back hair, two wings, two upper arms with sleeves, and two forearms with hands. RGBA, 1254 × 1254.
- `rig-body.png`: a clean sleeveless, armless neck-to-feet body under the moving arm layers. RGBA, 1024 × 1536.
- Both new assets were made with the built-in image-generation tool using the original character as the reference. Originals are preserved. The final prompt set is in [rig-generation-prompts.md](rig-generation-prompts.md).

`cutout-rig.js` caches the painted parts and eye regions once. The supported poses are `idle`, `nod`, `think`, `shy`, `laugh`, and `present`. Shoulder and elbow rotations are deliberately small; the wrist/hand artwork stays intact. Neck placement is calibrated to overlap the collar. Feet, skirt and torso remain planted. No pose-specific PNG switching occurs.

`rig.json` also stores the eye rectangles and mouth center in original atlas pixels. `character-face.js` controls smooth expressions, irregular blinking and audio-reactive mouth opening/roundness. Facial landmarks follow the head transform, including in the close-up view. Runtime skin masks and eyelid compression reuse the existing artwork; this revision generated no new raster assets.

Open `/rig-preview` to inspect all six poses, expressions and blinking. Click **Close-up**, then **Test current voice** to hear the configured voice with lip sync. The test plays only in this page. Pauses and playback stops close the mouth. The demo keeps a 30fps cap.

This is a front-view cutout, not a full anatomical or cloth simulation. It cannot perform large turns, raised-arm poses or individual finger movements. Those unsupported poses have been removed from the controller, and old saved mappings are migrated. Lip motion is driven by RMS and spectral balance, not phoneme alignment.

Generation prompt (built-in tool, transparent_background=true):

> Use case: background-extraction / identity-preserve. Asset type: transparent full-body sprite for a low-compute VTuber livestream application. Input image is the character reference and edit target. Isolate ONLY the FRONT view character (leftmost figure) into one clean centered front-facing full-body sprite, true transparent alpha background. Preserve her exact identity, face, closed neutral mouth, silver lavender long hair, curved horns, pointed ears, lavender dragon wings, silver-white embroidered dress, bare-shoulder design, detached bell sleeves, shoes, and original illustration style. Include both wing tips and full dress hem with comfortable transparent margins. Remove side and back figures, text labels, and the pale background. Only one person, no additional accessories or scenery. Use a 1024x1536 portrait canvas or similar portrait framing. Keep the character symmetrical and the face centered for later audio-reactive mouth animation.
