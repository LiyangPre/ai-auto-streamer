# Rig artwork generation

Tool: built-in `image_gen.imagegen`, `transparent_background=true`. Both outputs were copied into this project; existing artwork was preserved.

## Parts atlas → rig-parts.png

Edit target: the project's original `neutral.png` silver dragon woman character.

Create a production 2D cutout animation part atlas with genuine transparent alpha. Preserve the same detailed silver-lavender anime illustration, adult face, curved horns, white embroidered dress, detached bell sleeves, silver wrist bracelets, long silver hair and lavender bat wings. Make her face gently sweeter and cute, still the same adult character. No background glow, shadows, text, grid lines, labels, or assembled duplicate figures.

Requested packing: a body in the left half, with eight isolated parts on the right in two columns and four rows: head with horns/ears/bangs; complete long back hair; viewer-left and viewer-right wings; viewer-left and viewer-right upper arms with complete flowing bell sleeves; viewer-left and viewer-right bare forearms, bracelets and relaxed open hands. Use rounded painted shoulder/elbow overlap areas. Separate all parts with transparent margins. Hands have five gently curved fingers. Front-view neutral arms allow rotating joints. This is a disconnected doll rig asset sheet, not poses or assembled people.

The generated packing was measured into normalized rectangles in `rig.json`. Its body was replaced with the separate body asset below to remove attached sleeve artwork. Atlas extraction keeps each region's largest connected alpha island so isolated neighbouring fragments cannot float beside the character.

## Armless core → rig-body.png

Edit target: the generated parts atlas.

Produce only one asset on a transparent portrait canvas: the headless, armless body from the atlas, isolated by itself from neck to shoes. Preserve its silver embroidered sleeveless halter gown, thigh slit, heels and full long dress train. Remove both arms entirely, including every detached bell sleeve and draping sleeve panel hanging from an upper arm. The upper torso is a clean sleeveless armless mannequin with small rounded shoulder stubs; no cloth extends from the shoulders. Expose a complete painted side of the bodice under each removed arm. Retain the complete gown skirt and train below the hips. No head, horns, hair, wings, other parts, labels, grid, or background. Preserve the same body and white-lavender embroidery. Center with transparent margins, clean alpha edges, no glow or white speckle. Separately animated shoulder and elbow layers attach to this body.
