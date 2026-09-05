# personal-budget — user documentation

This is the documentation for people who run the system. Development notes and
design decisions are kept elsewhere; nothing here assumes you want to change code.

| Page | Read it when |
|---|---|
| [1. Prerequisites](01-prerequisites.md) | before installing |
| [2. Setup](02-setup.md) | installing for the first time |
| [3. Daily use](03-daily-use.md) | importing statements, running the refresh, reading the summary |
| [4. Configuration reference](04-configuration.md) | `.env`, `sources.yaml`, statement formats, CLI, HTTP API |
| [5. Troubleshooting](05-troubleshooting.md) | something did not work |
| [6. Actual Budget](06-actual-budget.md) | how the pipeline uses Actual, and where Actual's own documentation is |
| [7. The n8n workflow](07-n8n-workflow.md) | what every node does; adding and removing sources; when the bank changes its export |
| [8. Categorisation assistant](08-categorization.md) | proposed rules from a local AI model, review and approval |

What the system does, in one paragraph: you export account statements from your
bank (CSV) and drop them into a folder. The pipeline parses them, gives every
row a stable identity, skips rows it has already imported, and writes the rest
into [Actual Budget](https://actualbudget.org) through Actual's API. Actual is
where you budget, categorise and report. An optional n8n workflow runs the
refresh on a schedule and writes a short summary. Nothing leaves your machine.

These pages are updated together with the software. If a page and the software
disagree, the software is newer than the page; please report it.
