# MIDUS Monte Carlo Collection

Collection code and prompt templates for comparing eight LLM participant-simulation
methods (M1–M8). This repository contains no participant data, generated results or
API keys. Statistical analysis scripts are separate.

## Study setup

- 1,000 repetitions per method; 100 participants in one request per dataset.
- Model: `gpt-5.6-luna`; reasoning: `medium`; output allowance: 128,000 tokens.
- Seed: `20260831`; individual-level 20% calibration split, without family grouping.
- M5/M6 draw separate 50-person pilots each repetition. M5 receives pilot data plus
  computed summaries; M6 receives pilot data and instructions to analyse it.
- M7/M8 draw separate 100-person profiles each repetition. M7 fixes psychological
  and demographic predictors; M8 fixes demographics. Fixed values are attached locally.
- M4 uses demographic quotas from the full eligible cohort. Generated values use
  at most four decimal places; fixed values retain their original precision.

Prompts: [`prompts/methods.json`](prompts/methods.json). Settings and variable
definitions: [`config/`](config/). Collection code: [`src/`](src/).

## Run on Windows

Install 64-bit Python 3.13 and supply the input CSV described in
[`data/README.md`](data/README.md). Open PowerShell in this repository folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
notepad .env
```

Add your own `OPENAI_API_KEY` to `.env`, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\check_setup.ps1
powershell -ExecutionPolicy Bypass -File .\run_collection.ps1
```

The setup check makes no API calls but saves local inputs and progress. The run
command starts paid collection with no script-imposed spending cap and requires
access to the configured model. Model availability has not been checked here.

## Resume and reproducibility

Outputs are saved in `outputs/monte_carlo_medium_v1/`. Resume with the same run
command and folder. Invalid or duplicate datasets receive up to three initial
attempts using the same pilot/fixed inputs. Other jobs continue when a dataset is
blocked; unresolved jobs cause a review pause at the end. Keep all attempt records.

Scientific source, prompts and configuration are unchanged from the collection
project. Input draws require the same source values, row order and seed. New model
outputs may differ; dependencies use version ranges. Exact reanalysis also needs
the original accepted datasets and analysis scripts.

Known limitation: auxiliary distinct-profile counts may be null for M4/M7/M8;
the separate core duplicate and fixed-input checks remain in place.

Keep `.env`, participant data and `outputs/` private. Git exclusions are supplied;
they do not filter files uploaded manually through a browser.
