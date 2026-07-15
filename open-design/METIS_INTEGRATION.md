# Metis Design source integration

This directory contains the Open Design 0.15.1 product source integrated into
Metis. The upstream source is licensed under Apache License 2.0; see `LICENSE`.

- Upstream: https://github.com/nexu-io/open-design
- Integrated version: 0.15.1
- Product owner and executable host: Metis
- Design agent adapter: `apps/daemon/src/runtimes/metis-http.ts`

Metis builds the web editor, daemon modules, templates, design systems, skills,
and official plugin resources from this repository-local source. The shipped
application does not require a separate Open Design checkout or launch a second
Open Design desktop executable.

Local development is started by the Metis Electron host. Do not configure an
external `open-design-main` path or a local agent profile for Metis.
