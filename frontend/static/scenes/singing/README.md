# Astra's night music room

Generated with the built-in imagegen tool on 2026-10-28 and copied into the project.

- `music-room-v2.png`: 1448 × 1086 environment, with a visible gaming chair,
  keyboard, mouse, notebook, headphones and audio interface.
- `astra-singer-v2.png`: 1086 × 1448 RGBA seated Astra, holding one microphone,
  wearing an ivory knit top and a lilac gaming hoodie. The other hand stays below
  the tabletop and is not rendered. Wings remain inside the scene.
- `happy-seated-dolls-v1.png`: 1774 × 887 RGBA pair: DeepSeek in the left half,
  Claude in the right. Both sit happily toward the camera; the shared companions
  setting controls their visibility. Earlier v1 artwork is retained as a variant.

Reference roles: the supplied blue-haired music-desk picture guides the room mood
and seated singing composition; the supplied silver-haired car picture defines
Astra's identity. Clothes may vary, but hair, horns, eyes and silver ornament stay
consistent. No generated words, logo or watermark are needed in the scene.

Prompt set:

1. **Character** — transparent waist-up seated silver-dragon Astra; preserve the
   car reference's white-silver hair, violet eyes, curved ivory horns, pointed ears,
   silver knot ornament and tassel. Ivory knit top and lilac/charcoal gaming hoodie,
   gentle closed smile, open eyes. Her right hand grips one black/silver handheld
   microphone near her mouth without covering it; the other hand is below the desk
   and invisible. Anatomically connected shoulders, elbows, wrists and fingers;
   compact lavender wings fully in frame. Delicate
   anime painting, soft lilac room light, no room or desk baked into the cutout.
2. **Room** — empty cozy 4:3 anime music/computer room at night; indigo/lavender
   tones, amber desk lamp, star fairy lights, city window, monitor with abstract
   audio waveform at left, centered ivory/lilac chair. Broad wooden desktop in the
   lower portion, keyboard/notebook at left, mouse/interface at right and clear
   spaces for separate doll layers. Wider visible chair behind the singer.
   No people, dolls, microphone, readable words or watermark.
3. **Dolls** — preserve the two reference companions: navy/blue whale-maid
   DeepSeek and orange-haired Claude with her ribbon, flower and book. Both sit
   facing the camera, eyes open and happy, hands in lap, legs dangling naturally.
   Each entire doll fits one half of a transparent horizontal sprite pair.

Runtime calibration and constrained deformation are in `../../singing-scene.js`.
The scene uses one character texture; mouth and eyelids animate locally. Song
choreography follows the real recording clock. It is a limited painted rig, not a
new arbitrary-pose or phoneme-recognition model.

The 2026-10-28 motion revision lifts the connected upper body by up to 10 source
pixels over a 4.8-second breath and adds bounded recording-clock sway around a
seated pivot. Head, microphone and grip inherit that parent; the lower seat stays
anchored and desktop occlusion stays fixed. Eye closing follows each painted
eye's own angle. Semantic emotion targets preserve paired eyelids and adjustable
strength; audio alone opens the jaw. Use `/static/previews/singing-motion.html`
for a silent preview with expression, blink, breathing and rhythm controls.
Runtime texture preparation makes almost-opaque interior texels opaque once,
retaining feathered alpha edges and the unmodified source PNG. Uniform triangle
edge overlap then prevents a visible grid when this translucent painting moves.
