# Vendored browser libraries

The server page loads these unmodified files from this folder, so that it needs
no external script host. Only the woff2 fonts of KaTeX are kept, since every
current browser reads them.

| Library | Version | License | Source |
|---|---|---|---|
| Cytoscape.js | 3.30.2 | MIT | https://github.com/cytoscape/cytoscape.js |
| elkjs | 0.9.3 | EPL-2.0 | https://github.com/kieler/elkjs |
| KaTeX | 0.16.9 | MIT | https://github.com/KaTeX/KaTeX |

Files were taken from https://cdn.jsdelivr.net/npm/. The source code of elkjs
is available at the repository above, under the Eclipse Public License 2.0,
https://www.eclipse.org/legal/epl-2.0/.
