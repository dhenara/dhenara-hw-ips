# Channel implementation

The CK-domain `lpddr5_mc_channel` composes the scheduler, physical backend, integrity and lifecycle owners. Sources are listed explicitly in [files.f](files.f). See [architecture and integration limits](../../docs/architecture/channel.md) and [owning checks](../../docs/verification/channel.md). The owning fixture exercises the composed CK-domain implementation; combined host/CDC integration, independent system qualification and measured throughput remain downstream checks.
