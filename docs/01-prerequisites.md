# 1. Prerequisites

## Required

- **Docker Desktop** (macOS or Windows) or Docker Engine with the Compose v2
  plugin (Linux). Check: `docker compose version` prints a version.
- About **1 GB of disk** for images and data, plus room for your statements.
- A **web browser** for Actual Budget (http://localhost:5006 after setup).
- **Online-banking access** that can export statements as CSV. Supported today:
  Postbank (Deutsche Bank platform). Other banks work with the generic CSV
  format or a new format profile (see [Configuration](04-configuration.md)).

## Optional

- **n8n** running in Docker, if you want the scheduled refresh and the summary
  file. Any n8n ≥ 1.0 works; the workflow was built and tested on n8n 2.35.
  The pipeline joins n8n's Docker network, so note the network's name
  (`docker network ls`; with the standard n8n compose it is `n8n_default`).
  n8n docs: https://docs.n8n.io/hosting/
- **`uv`** (https://docs.astral.sh/uv/) only if you want to run tests or
  develop.

## What you do not need

- A bank API key, a FinTS/HBCI product registration, or any cloud account.
  The manual statement import works with what your online banking already
  offers.
