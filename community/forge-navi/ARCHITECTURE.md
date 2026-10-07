# Forge-Navi Community Architecture

## v0.2 split

Forge-Navi Community now has three public layers:

1. **Core** — `forge_navi_community.py`
   - parses Forge-style text scripts;
   - runs structural checks;
   - discovers token dependencies;
   - builds the token handoff;
   - enforces the packaging gate;
   - writes a SHA-256 package manifest.

2. **Desktop** — `forge_navi_desktop.py`
   - folder picker;
   - RED / YELLOW / GREEN findings;
   - open-the-bad-script workflow;
   - token-handoff button;
   - gated package button;
   - no third-party GUI dependency (Tkinter only).

3. **Token companion** — `token_navi_community.py`
   - validates token handoffs;
   - binds approval to exact PNG bytes;
   - verifies SHA-256 plus PNG dimensions.

The CLI remains available because both automation and the desktop UI should rely on the same core behavior.

## Private production boundary

The public Core is deliberately smaller than the private Forge-Navi / Token-Navi production environment. The private layer remains the R&D and high-confidence semantic system, including accumulated semantic precedent, failure/repair knowledge, active project state, upstream research, and private working content.

Reusable checks can graduate from private R&D into the public Core after they are generalized and proven.
