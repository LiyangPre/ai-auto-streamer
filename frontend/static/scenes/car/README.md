# Astra's car / 银龙座驾

This is the project default scene. Use **Controller → Scene** to change it. Restart an older
server once after installing this update, then refresh browser sources.
The normal output URL remains `http://127.0.0.1:8000/?overlay=1&controls=0`.
For a temporary car-only preview, append `&scene=car` (does not save settings).

The two local illustrations are native **1448 × 1086 (4:3)**, suitable for the
project's 1440 × 1080 capture. Created with the built-in image_gen tool using
the user's references. Exact prompts are in `prompts.json`.

- `astra-car-v1.png`: complete painted anime scene, including Astra's fixed
  seated body, steering wheel, cropped foreground hand and seated companions.
- `astra-car-clean-v1.png`: matching clean facial plate. Only a feathered
  facial region is composited at runtime; the whole illustration is never swapped.

`car-scene.js` caches the painted eye crops, blends eight expressions, runs an
irregular blink timer, and draws the mouth from the existing real voice envelope.
Music does not drive the mouth. Blinking continues during silence. Expressions
relax after eight seconds. New chat emotions blend into the current expression.
The scene picker includes local expression audition buttons, including a blink
button; these affect only that editor iframe, not the live output.

All body and car contact points stay fixed. No independent character translation,
scaling, arm waving or standing companions are applied in this scene. These
controls are disabled while the car is selected; their saved values are retained
for the standing scenes. The two dashboard mascots are part of the illustration.
Weather is disabled inside the cabin. Dimming, bottom blur, subtitle hold and
music still work. `car-drive.js` scrolls a local countryside panorama through
the glass openings at 12 reference pixels per second (about a 97-second loop).
The loop ends are blended once in a cached texture. Only the alpha of
`window-cutout-v1.png` is used as a mask, preserving the original character and
interior pixels. The mirror reflections remain painted. `countryside-loop-v1.png`
is the exterior artwork; its generation prompts and the mask prompt are saved
in `driving-prompts.json`. Both were made with the built-in image_gen tool.

The entire shot shares a tiny camera vibration (less than one reference pixel,
up to 0.012° of roll), with 0.4% overscan to avoid exposed edges. No body part
moves independently. **Slow scenery loop** and **Gentle camera vibration** in
Scene control the two effects. Turning off the loop restores the original still
artwork. Reduced-motion preference freezes the new exterior and disables
vibration; hidden tabs pause it. Rendering is capped at 20 fps with cached
textures and a maximum of three scenery tiles per frame.

Artwork loads only when the car scene is selected. The still image is a fallback
if the animation plate fails. Hidden pages skip facial drawing; other scenes do
not render it. The car default and motion settings are saved in `config.yaml`,
`data/studio-settings.json` and `data/studio-defaults.json`. Voice settings are
unchanged. The user deferred facial-expression fixes on 2026-10-27; the existing
expression logic remains unchanged and the task is in `data/pending-tasks.json`.
