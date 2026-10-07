# Forge-Navi + Token-Navi Community

A sanitized, runnable public demonstration of the workflow behind Joshua Phillips' private Forge-Navi and Token-Navi tooling.

This is intentionally not a dump of the production repositories. It contains the reusable public-facing ideas: structural QA, token dependency handoff, exact-asset approval, verification, and gated packaging. The private production system adds a much larger semantic knowledge corpus, upstream research, repair history, project state, and active set data.

## Requirements

- Python 3.10+
- Windows PowerShell for the optional installer
- No third-party Python packages

## Install

Download install.ps1 from this folder and run:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
~~~

By default it installs the standalone EXE under `%LOCALAPPDATA%\\Forge-Navi-Community`, verifies its SHA-256 hash, and creates a desktop shortcut. You can choose another destination:

~~~powershell
.\install.ps1 -Destination "D:\Tools\Forge-Navi-Community"
~~~

### Direct EXE

If you do not want to use the installer, use the **Download EXE Directly** button on the Community page. The download is served from the repository's raw file host rather than GitHub Pages. The EXE is built automatically on Windows with PyInstaller and includes the Python runtime and Tk GUI dependencies.

Because this beta is not code-signed yet, Windows SmartScreen may show an unknown-publisher warning.

## Desktop app

After installation, double-click **Launch Forge-Navi.cmd** or the **Forge-Navi Community** desktop shortcut.

The desktop beta lets you:

- choose a Forge `custom` folder or a project folder containing `custom`;
- run an audit and see RED / YELLOW / GREEN results;
- double-click or open the script behind a finding;
- generate the Token-Navi handoff;
- build a ZIP only when structural blockers are gone.

The CLI remains available underneath for automation and advanced users.

## 60-second CLI demo

From the installed folder:

~~~powershell
py -3 forge_navi_community.py audit demo/custom --report demo/audit-report.json
py -3 forge_navi_community.py handoff demo/custom demo/generated-token-requirements.json
py -3 token_navi_community.py validate demo/generated-token-requirements.json
py -3 forge_navi_community.py package demo/custom dist/demo-forge-package.zip
~~~

The sample should audit cleanly. Packaging is blocked automatically if structural errors exist.

## Using it on your own Forge custom content

Point audit at a folder containing your Forge-style cards and tokens directories:

~~~powershell
py -3 forge_navi_community.py audit "C:\path\to\custom" --report audit.json
~~~

The Community tool currently checks:

- missing Name / Types fields;
- duplicate card/token names;
- malformed and duplicate SVar declarations;
- common local SVar references that are not defined in the same file;
- TODO / FIXME markers;
- TokenScript$ references with no matching token script;
- basic line structure.

Then build a token-production handoff:

~~~powershell
py -3 forge_navi_community.py handoff "C:\path\to\custom" token-requirements.json
~~~

## Token-Navi side

Validate the handoff:

~~~powershell
py -3 token_navi_community.py validate token-requirements.json
~~~

After you create a finished token PNG, bind approval to its exact bytes:

~~~powershell
py -3 token_navi_community.py approve token-requirements.json my_token assets\my_token.png approvals\my_token.json
~~~

Later, verify that the PNG has not changed:

~~~powershell
py -3 token_navi_community.py verify approvals\my_token.json assets\my_token.png
~~~

Approval uses SHA-256 plus PNG dimensions, so replacing or modifying the approved file fails verification.

## Packaging gate

~~~powershell
py -3 forge_navi_community.py package "C:\path\to\custom" dist\my-set.zip
~~~

The package command reruns the audit and refuses to package when errors remain. A manifest containing SHA-256 hashes is written into the ZIP.

## What is private-only

The production Forge-Navi/Token-Navi environment contains substantially more than this demo, including accumulated semantic precedent, failure classes, repair knowledge, active project state, upstream compatibility research, visual runtime configuration, and set-specific working material.

The production rule is simple: prove the content works; do not merely prove that it parses. This Community Demo demonstrates the workflow and safety rails without publishing the private corpus that makes the production system much deeper.

## Status

Community v0.2.1 — standalone Windows desktop beta + optional CLI.

Forge and Magic: The Gathering are third-party projects/properties. This demo is unofficial and is not affiliated with or endorsed by their respective maintainers or rights holders.
