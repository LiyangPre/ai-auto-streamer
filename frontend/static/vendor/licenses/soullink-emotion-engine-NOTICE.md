# Soullink expression engine

This application bundles `@soullink-emotion/engine` **0.1.0-beta.1** from the
official npm registry. That published package carries the **MIT License**;
the complete license accompanies this notice in
`soullink-emotion-engine-LICENSE.txt`.

Copyright (c) 2026 Soullink Emotion contributors.

Upstream: https://github.com/nanlingyin/soullink-emotion-sdk

Pinned package: https://www.npmjs.com/package/@soullink-emotion/engine/v/0.1.0-beta.1

Registry integrity:
`sha512-eXVMTldsFqEEsKadvkpb6DDEJ0qewnMqc8jdxCBrPTET/w4D6oIyJRxF7QtNT0swOT3er/gZgUO9qFPIlCrtzQ==`

Upstream's newer `main` branch uses Apache-2.0 and version 0.2.0-beta.1. This
integration uses the pinned published MIT package, not that newer source tree.

The bundled SDK is unmodified; `expression-engine.js` is the application's own
adapter. It disables SDK speech synthesis and lip-sync, constrains face/head
output, pairs eyelids, and leaves mouth opening to the host's real audio.
No planner, embedding, runtime orchestration, remote service, Live2D runtime,
model, or UI package is included by this dependency.

Rebuild: `npm ci --ignore-scripts` followed by `npm run build:expressions`.
