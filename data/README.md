# Source data

Place your prepared linked human dataset at:

`data/MIDUS2_3_linked_cleaned.csv`

Participant records and the original survey-file linking/preparation script are
not distributed here. Obtain or prepare this input separately.

The collector requires unique, non-missing `M2ID` values, `B1STATUS = 2`, and
`C1STATUS` in `{4, 5, 6}`. It retains participants with valid `B1SSATIS` and
`C1SSATIS` outcomes; predictors may be missing. Missing codes, score ranges and
category definitions are in [`config/study.yaml`](../config/study.yaml).

Required columns:

```text
M2ID, M2FAMNUM, B1STATUS, C1STATUS, B1SORIEN
B1SPESSI, B1SMASTE, B1SCONST, B1SCTRL, B1SESTEE
B1SEXTRA, B1SOPEN, B1SCONS2, B1SAGREE, B1SNEURO
B1SACTIV, B1SPLAN, B1SREINT, B1SPRCOP, B1SDENIA
B1SDISEN, B1SFDCOP, B1SPOSAF, B1SNEGAF, B1PANXIE
B1SKINPO, B1SFDSPO, B1SSPEMP, B1SKINNE, B1SFDSNE
B1SSPCRI, B1SGENER, B1SSWBMS, B1SSWBSI, B1SSWBAO
B1SSWBSC, B1SSWBSA, B1SDAYDI, B1SNEGWF, B1SNEGFW
B1SPWBU2, B1SPWBS2, B1PRAGE_2019, B1PRSEX, B1PB1
B1PB19, B1PF7A, B1SN1A, B1SSATIS, C1SSATIS
```

Keep the original input values and row order to reproduce the same participant
draws. Keep participant data out of the repository.
