# Bundled chart library

The report embeds Chart.js 4.5.1's UMD build so a generated HTML file works offline. It includes @kurkle/color 0.3.2. Both MIT license notices are embedded alongside the library.

Source: https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js

Upstream SHA-256: `48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a`

The only change to the upstream build is removal of its trailing `sourceMappingURL` comment, since the standalone report does not ship a source map. Retain both license files when upgrading, and validate chart rendering, tooltips, keyboard navigation, resizing, and instance cleanup in a browser.
