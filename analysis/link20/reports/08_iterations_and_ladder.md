# 08 — Per-iteration table and recommendation ladder (20-iteration DLR-F25 study)

For the paper. All numbers come from `scripts/ladder_and_table.py`, run on aipexws8. It reads:
- `links.csv` (`out_20261028`);
- `physics/links_physics.csv`, for the `converged` flag of the mission each iteration's fuel comes from;
- the Q7 item table `methods_q7/rec_scoring_items.csv`, scored by the single definition proposed in
  `07_methods_answers.md` Q7.

Output files sit next to this report in `data/`, with copies on ws8 under `~/Jessica/avion_analysis/reports_20261028/ladder/`:
- `iterations.csv`, `iterations_latex_rows.tex`
- `scored_items.csv`, `split_by_param.csv`, `feasibility.csv`
- `effective_pairs.csv`, `effective_summary.csv`
- `run.log`

## 1. Per-iteration table

- **Files:** CSV `data/iterations.csv` (160 rows) and the LaTeX table body `data/iterations_latex_rows.tex`. The LaTeX has
  rows of the form `S\_SP & 1 & 23,077 & ... \\`, columns in the order below, and a `% <combo>` comment before each block.
- **Fuel, GTOW, wing mass:** from the last successful mission in the iteration (the runner's rule). They are "–" when
  the iteration did not fly (no mission above 100 kg).
- **Converged:** SLSQP's flag for that same mission; "–" when the iteration did not fly.
- **Duration:** `result.json` wall time. It is "–" for the 4 failed iterations, which have no `result.json`. Those are
  O_IF 8 and 19 and N_GR 6 and 8; O_IF 19, N_GR 6 and N_GR 8 still flew a mission before the timeout.
- **Output tokens:** summed from the step logs (B215: `result.json` totals are wrong; input tokens are not saved).
- **Agent steps:** step-log steps of the attempt that counted.

#### S_SP

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 23,077 | 102,314 | 11,392 | y | n | 59 | 0.49 | 18,179 |
| 2 | 19,355 | 100,103 | 10,614 | y | n | 59 | 0.56 | 21,476 |
| 3 | 30,495 | 114,425 | 12,362 | y | n | 47 | 0.43 | 15,103 |
| 4 | 23,554 | 103,555 | 11,756 | y | y | 66 | 0.43 | 14,428 |
| 5 | 24,545 | 104,402 | 11,372 | y | y | 68 | 0.53 | 19,940 |
| 6 | 18,592 | 100,070 | 10,644 | y | n | 54 | 0.51 | 18,024 |
| 7 | 26,728 | 107,993 | 11,690 | y | n | 48 | 0.36 | 12,723 |
| 8 | 32,488 | 119,318 | 12,792 | y | n | 61 | 0.47 | 16,997 |
| 9 | 25,406 | 105,670 | 11,485 | y | y | 62 | 0.40 | 13,482 |
| 10 | 19,248 | 100,191 | 10,655 | y | n | 43 | 0.26 | 8,299 |
| 11 | 11,824 | 92,676 | 9,466 | y | n | 68 | 0.57 | 20,494 |
| 12 | 16,586 | 97,400 | 10,200 | y | n | 63 | 0.70 | 25,371 |
| 13 | 16,563 | 97,389 | 10,199 | y | n | 71 | 0.55 | 17,723 |
| 14 | 24,880 | 105,815 | 10,893 | y | n | 50 | 0.39 | 12,692 |
| 15 | 30,285 | 114,453 | 12,367 | y | n | 69 | 0.88 | 32,956 |
| 16 | 31,790 | 115,424 | 12,632 | y | n | 55 | 0.47 | 18,021 |
| 17 | 36,415 | 122,774 | 13,718 | y | y | 79 | 0.82 | 30,639 |
| 18 | 26,593 | 109,647 | 13,107 | y | y | 68 | 0.49 | 17,138 |
| 19 | 31,494 | 118,345 | 13,035 | y | n | 67 | 0.39 | 12,747 |
| 20 | 31,501 | 118,357 | 13,036 | y | n | 59 | 0.52 | 18,967 |

#### S_IF

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 23,078 | 102,316 | 11,392 | y | n | 69 | 0.67 | 25,228 |
| 2 | 23,077 | 102,314 | 11,392 | y | n | 55 | 0.39 | 12,934 |
| 3 | 19,355 | 100,103 | 10,614 | y | n | 71 | 0.87 | 36,055 |
| 4 | 20,145 | 101,894 | 11,138 | y | n | 59 | 0.50 | 19,395 |
| 5 | 21,122 | 104,171 | 11,726 | y | n | 57 | 0.51 | 18,787 |
| 6 | 21,904 | 102,655 | 11,358 | y | n | 83 | 0.63 | 22,940 |
| 7 | 21,734 | 102,631 | 11,356 | y | n | 63 | 0.53 | 19,355 |
| 8 | 21,850 | 102,635 | 11,356 | y | n | 74 | 0.62 | 23,540 |
| 9 | 21,850 | 102,635 | 11,356 | y | n | 58 | 0.63 | 24,313 |
| 10 | 21,850 | 102,635 | 11,356 | y | n | 62 | 0.55 | 18,849 |
| 11 | 21,850 | 102,635 | 11,356 | y | n | 61 | 0.66 | 24,369 |
| 12 | 21,850 | 102,635 | 11,356 | y | n | 60 | 0.68 | 26,627 |
| 13 | 21,850 | 102,635 | 11,356 | y | n | 67 | 0.43 | 15,129 |
| 14 | 21,850 | 102,635 | 11,356 | y | n | 81 | 0.82 | 31,612 |
| 15 | 21,850 | 102,635 | 11,356 | y | n | 79 | 0.87 | 32,069 |
| 16 | 21,850 | 102,635 | 11,356 | y | n | 58 | 0.54 | 19,531 |
| 17 | 21,850 | 102,635 | 11,356 | y | n | 62 | 0.49 | 17,236 |
| 18 | 20,740 | 97,984 | 11,227 | y | y | 84 | 0.69 | 26,797 |
| 19 | 24,622 | 104,239 | 11,297 | y | n | 80 | 0.71 | 27,172 |
| 20 | 26,155 | 109,382 | 12,559 | y | n | 66 | 0.65 | 23,586 |

#### S_GR

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 22,810 | 102,672 | 12,067 | y | y | 271 | 2.27 | 81,020 |
| 2 | 19,867 | 95,958 | 11,436 | y | n | 246 | 2.08 | 78,051 |
| 3 | 21,188 | 103,932 | 11,704 | y | n | 144 | 1.35 | 50,463 |
| 4 | 21,188 | 103,932 | 11,704 | y | n | 216 | 2.28 | 55,593 |
| 5 | 24,458 | 109,758 | 12,821 | y | n | 228 | 2.44 | 78,979 |
| 6 | 24,632 | 109,990 | 12,843 | y | n | 141 | 1.32 | 46,480 |
| 7 | – | – | – | n | – | 138 | 1.16 | 38,918 |
| 8 | 28,291 | 114,098 | 13,231 | y | n | 135 | 1.29 | 52,909 |
| 9 | 28,291 | 114,098 | 13,231 | y | n | 141 | 1.23 | 50,818 |
| 10 | 28,291 | 114,098 | 13,231 | y | n | 267 | 2.56 | 100,445 |
| 11 | 28,291 | 114,098 | 13,231 | y | n | 156 | 1.34 | 52,289 |
| 12 | 28,291 | 114,098 | 13,231 | y | n | 122 | 1.31 | 53,565 |
| 13 | – | – | – | n | – | 144 | 0.99 | 35,356 |
| 14 | – | – | – | n | – | 162 | 1.28 | 47,352 |
| 15 | – | – | – | n | – | 75 | 1.01 | 41,492 |
| 16 | 28,291 | 114,098 | 13,231 | y | n | 117 | 1.04 | 37,909 |
| 17 | 28,291 | 114,098 | 13,231 | y | n | 211 | 1.49 | 46,094 |
| 18 | 27,678 | 115,048 | 13,525 | y | y | 216 | 2.08 | 73,959 |
| 19 | 19,281 | 97,655 | 11,847 | y | n | 212 | 2.07 | 73,578 |
| 20 | 28,162 | 113,993 | 14,631 | y | n | 215 | 1.98 | 65,805 |

#### O_SP

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 22,974 | 102,149 | 11,378 | y | n | 72 | 1.65 | 66,517 |
| 2 | 22,998 | 102,187 | 11,381 | y | n | 53 | 1.01 | 41,429 |
| 3 | 22,914 | 102,052 | 11,369 | y | n | 76 | 1.65 | 67,313 |
| 4 | 18,521 | 94,094 | 10,657 | y | n | 76 | 1.66 | 66,624 |
| 5 | 37,668 | 126,154 | 14,305 | y | n | 76 | 1.21 | 46,673 |
| 6 | 37,668 | 126,154 | 14,305 | y | n | 78 | 1.42 | 59,410 |
| 7 | 27,865 | 110,101 | 12,371 | y | y | 81 | 0.52 | 22,094 |
| 8 | 27,239 | 109,177 | 12,286 | y | y | 70 | 0.89 | 34,972 |
| 9 | 26,953 | 108,754 | 12,247 | y | y | 79 | 1.28 | 52,894 |
| 10 | 41,138 | 129,217 | 13,962 | y | n | 83 | 0.57 | 21,930 |
| 11 | 34,628 | 121,038 | 13,083 | y | n | 73 | 0.60 | 20,237 |
| 12 | 34,851 | 120,966 | 13,077 | y | n | 59 | 0.92 | 38,794 |
| 13 | 34,682 | 121,029 | 13,082 | y | n | 82 | 1.30 | 53,054 |
| 14 | 34,628 | 121,038 | 13,083 | y | n | 85 | 1.39 | 57,241 |
| 15 | 34,624 | 121,039 | 13,083 | y | n | 81 | 1.68 | 71,117 |
| 16 | 37,935 | 128,067 | 14,821 | y | n | 81 | 1.35 | 55,077 |
| 17 | 36,410 | 122,345 | 13,412 | y | n | 79 | 1.19 | 47,798 |
| 18 | 36,410 | 122,345 | 13,412 | y | n | 82 | 1.35 | 59,830 |
| 19 | 34,032 | 118,902 | 13,101 | y | y | 60 | 0.75 | 27,734 |
| 20 | 34,022 | 118,887 | 13,099 | y | y | 90 | 0.99 | 37,230 |

#### O_IF

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 22,914 | 102,052 | 11,369 | y | n | 73 | 1.73 | 75,423 |
| 2 | 19,772 | 101,446 | 11,364 | y | n | 107 | 1.61 | 61,770 |
| 3 | 23,554 | 108,315 | 12,593 | y | n | 89 | 1.14 | 36,725 |
| 4 | 23,615 | 108,348 | 12,596 | y | n | 87 | 1.74 | 74,616 |
| 5 | 24,698 | 107,861 | 12,550 | y | n | 83 | 1.63 | 70,118 |
| 6 | 17,302 | 95,406 | 10,390 | y | n | 90 | 1.02 | 42,203 |
| 7 | 24,679 | 108,258 | 12,596 | y | n | 186 | 2.04 | 74,545 |
| 8 (failed) | – | – | – | n | – | 180 | – | 37,338 |
| 9 | 23,178 | 108,239 | 12,594 | y | n | 519 | 4.63 | 149,838 |
| 10 | 40,172 | 139,872 | 15,944 | y | y | 164 | 3.35 | 144,335 |
| 11 | 41,808 | 144,549 | 16,386 | y | n | 96 | 0.93 | 37,861 |
| 12 | 28,513 | 112,433 | 13,327 | y | y | 74 | 0.72 | 27,162 |
| 13 | 27,627 | 112,818 | 13,365 | y | y | 137 | 1.43 | 59,617 |
| 14 | 27,637 | 112,833 | 13,366 | y | y | 68 | 1.37 | 57,668 |
| 15 | 29,159 | 115,075 | 13,582 | y | y | 70 | 0.52 | 20,259 |
| 16 | 20,451 | 102,364 | 12,647 | y | y | 104 | 1.13 | 47,473 |
| 17 | 19,672 | 99,357 | 12,346 | y | y | 73 | 1.36 | 58,217 |
| 18 | 19,676 | 99,364 | 12,346 | y | y | 74 | 1.40 | 56,131 |
| 19 (failed) | 18,807 | 97,543 | 11,881 | y | y | 726 | – | 248,280 |
| 20 | 21,824 | 102,266 | 12,340 | y | y | 496 | 3.48 | 116,056 |

#### O_GR

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 22,664 | 101,649 | 11,333 | y | n | 98 | 1.47 | 85,776 |
| 2 | 27,440 | 110,185 | 12,676 | y | n | 182 | 3.09 | 190,299 |
| 3 | 31,279 | 119,044 | 13,496 | y | n | 125 | 1.52 | 88,602 |
| 4 | 31,279 | 119,044 | 13,496 | y | n | 106 | 1.20 | 64,141 |
| 5 | 31,279 | 119,044 | 13,496 | y | n | 108 | 1.48 | 82,318 |
| 6 | 24,228 | 105,227 | 12,214 | y | y | 125 | 0.96 | 49,293 |
| 7 | 32,757 | 120,916 | 13,906 | y | n | 155 | 1.97 | 89,885 |
| 8 | 18,126 | 96,128 | 11,548 | y | y | 247 | 1.98 | 82,841 |
| 9 | 35,764 | 127,668 | 14,941 | y | n | 58 | 0.83 | 44,390 |
| 10 | 40,918 | 135,623 | 15,990 | y | y | 133 | 1.80 | 91,489 |
| 11 | 36,753 | 126,907 | 15,137 | y | n | 84 | 1.30 | 66,443 |
| 12 | 27,708 | 113,446 | 13,810 | y | y | 106 | 1.53 | 92,604 |
| 13 | 36,313 | 126,136 | 14,917 | y | n | 229 | 2.67 | 135,588 |
| 14 | 33,616 | 122,139 | 14,528 | y | y | 111 | 1.02 | 46,373 |
| 15 | 33,649 | 122,187 | 14,532 | y | y | 108 | 1.57 | 88,976 |
| 16 | 33,673 | 122,222 | 14,536 | y | y | 130 | 1.77 | 101,905 |
| 17 | 33,028 | 121,289 | 14,445 | y | y | 166 | 2.11 | 115,191 |
| 18 | 33,028 | 121,289 | 14,445 | y | y | 189 | 2.52 | 146,288 |
| 19 | 33,039 | 121,304 | 14,446 | y | y | 119 | 1.66 | 97,727 |
| 20 | 22,848 | 105,873 | 12,931 | y | y | 97 | 1.09 | 60,708 |

#### N_IF

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | – | – | – | n | – | 129 | 0.85 | 24,208 |
| 2 | – | – | – | n | – | 231 | 1.70 | 25,735 |
| 3 | 18,452 | 94,349 | 10,680 | y | n | 169 | 1.25 | 24,491 |
| 4 | 20,423 | 97,128 | 11,456 | y | n | 129 | 1.43 | 26,466 |
| 5 | – | – | – | n | – | 164 | 1.54 | 28,876 |
| 6 | 19,001 | 94,362 | 10,764 | y | n | 131 | 0.84 | 25,455 |
| 7 | 17,846 | 93,308 | 10,825 | y | y | 130 | 0.87 | 27,755 |
| 8 | – | – | – | n | – | 220 | 2.21 | 43,714 |
| 9 | – | – | – | n | – | 209 | 2.00 | 36,403 |
| 10 | 18,423 | 93,427 | 10,402 | y | n | 192 | 1.17 | 30,548 |
| 11 | – | – | – | n | – | 204 | 1.54 | 39,081 |
| 12 | – | – | – | n | – | 119 | 0.90 | 22,539 |
| 13 | – | – | – | n | – | 176 | 1.23 | 33,156 |
| 14 | – | – | – | n | – | 82 | 0.53 | 15,115 |
| 15 | – | – | – | n | – | 192 | 1.50 | 32,700 |
| 16 | – | – | – | n | – | 189 | 1.58 | 27,791 |
| 17 | – | – | – | n | – | 186 | 2.24 | 27,759 |
| 18 | 17,520 | 91,701 | 8,995 | y | y | 110 | 0.74 | 22,970 |
| 19 | – | – | – | n | – | 224 | 1.83 | 41,849 |
| 20 | 25,196 | 109,693 | 12,759 | y | y | 113 | 0.75 | 22,702 |

#### N_GR

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 40,022 | 137,414 | 16,060 | y | n | 488 | 4.99 | 98,120 |
| 2 | 39,074 | 139,101 | 14,922 | y | n | 186 | 2.26 | 39,195 |
| 3 | 35,250 | 132,605 | 12,902 | y | n | 448 | 4.97 | 101,109 |
| 4 | 17,562 | 105,914 | 10,426 | y | n | 195 | 2.10 | 43,109 |
| 5 | – | – | – | n | – | 388 | 5.46 | 156,241 |
| 6 (failed) | 48,811 | 155,300 | 14,005 | y | n | 230 | – | 53,483 |
| 7 | 48,758 | 154,967 | 13,732 | y | n | 232 | 2.95 | 36,102 |
| 8 (failed) | 30,004 | 122,354 | 9,878 | y | n | 399 | – | 64,200 |
| 9 | 6,669 | 108,395 | 9,482 | y | n | 174 | 1.37 | 30,130 |
| 10 | 41,561 | 145,407 | 11,474 | y | n | 296 | 2.48 | 54,272 |
| 11 | – | – | – | n | – | 284 | 2.43 | 83,258 |
| 12 | 29,271 | 121,012 | 11,321 | y | n | 294 | 2.71 | 43,442 |
| 13 | 34,225 | 128,372 | 11,674 | y | n | 525 | 6.61 | 116,430 |
| 14 | 22,693 | 111,490 | 9,395 | y | n | 490 | 5.63 | 141,109 |
| 15 | 9,506 | 93,126 | 7,835 | y | n | 452 | 5.77 | 83,750 |
| 16 | 8,355 | 91,434 | 7,711 | y | n | 428 | 4.32 | 79,924 |
| 17 | 48,737 | 156,606 | 13,743 | y | n | 510 | 5.56 | 101,662 |
| 18 | 54,356 | 163,887 | 15,296 | y | n | 446 | 6.14 | 94,348 |
| 19 | 49,615 | 155,364 | 13,332 | y | n | 242 | 2.09 | 43,795 |
| 20 | 29,511 | 121,991 | 8,438 | y | n | 479 | 4.82 | 92,833 |

## 2. Recommendation ladder (proposed Q7 definition)

The 116 parsed recommendation items include 92 scored ones: 57 followed, 27 unchanged and 8 opposite. The other 24
are excluded: 18 were already met at the end of iteration k, and 6 have no recorded actual value.

### 2a. Actionable vs. not actionable: **split confirmed**

| Group | Variables | Scored | Followed | Unchanged | Opposite |
|---|---|---|---|---|---|
| Actionable (agent-set in the prompt table) | wing area, AR, sweep, BPR, fan PR, HPC PR, T4 | **65** | **51** | 11 | 3 |
| Not actionable (framework-set or follows geometry) | engine scale factor, wing mass scaler, taper | **27** | **6** | 16 | 5 |
| **All** | | **92** | **57** | 27 | 8 |

Per variable (`data/split_by_param.csv`), shown as followed / unchanged / opposite:

| Variable | Followed / unchanged / opposite |
|---|---|
| Wing AR | 29 / 2 / 0 |
| Wing area | 21 / 3 / 1 |
| BPR | 1 / 3 / 0 |
| HPC PR | 0 / 2 / 0 |
| Fan PR | 0 / 1 / 0 |
| Sweep | 0 / 0 / 1 |
| T4 | 0 / 0 / 1 |
| Engine scale factor | 5 / 8 / 5 |
| Wing mass scaler | 1 / 2 / 0 |
| Taper | 0 / 6 / 0 |

- The 3 design-CL items among the 116 are all excluded (already met or no actual), so none is scored.
- "Not actionable" follows the prompt's own design table (`config/mdo_f25_sequential_agents.yaml:29-71 @117b55e`):
  - Engine scale factor and wing mass scaler are "set by framework".
  - Taper "follows geometry", and tigl ignores it (B222).
- Their 6 "followed" moves were made by the framework, not by an agent acting on the recommendation.

### 2b. Level 4 (feasible): target inside the prompt's design range

Ranges from the prompt table:

| Variable | Range |
|---|---|
| Wing area | 100–160 m² |
| AR | 7–17 |
| Sweep | 15–40° |
| BPR | ≤ 10 |
| T4 | ≥ 2,857 °R |
| Fan PR | 1.4–1.7 |
| HPC PR | ≤ 30 |

The test depends on the item type:
- A value item counts as inside if its target is in the range.
- A range item counts as inside if both of its ends are.
- A direction-only item ("increase AR") has no target to test.

| Scored items | n | Followed / unchanged / opposite |
|---|---|---|
| Actionable, target **inside** the range | **34** | 29 / 4 / 1 |
| Actionable, target **outside** the range | 1 | 0 / 0 / 1 (O_SP iteration 19: T4 2,700 °R, below the 2,857 °R floor) |
| Actionable, direction only (no target) | 30 | 22 / 7 / 1 |
| Not actionable (no agent range exists) | 27 | 6 / 16 / 5 |
| **Total** | **92** | 57 / 27 / 8 |

**Answer: 34 of the 92 scored items have a target inside the stated design range.** That is 34 of the 35 actionable
items that state a target.

One caution about sweep: the prompt range applies to tigl's `sweep` key, but the item's variable is aviary's
quarter-chord sweep. There is only one sweep item, and its test is on the value as written.

### 2c. Level 6 (effective): fuel and GTOW change from iteration k to k+1

Each scored item belongs to the iteration that acts on it (k+1). The change is value(k+1) − value(k), and it is
counted only when both iterations flew. "Improved" means the value went down.

| Pairs | Group | Metric | n | Mean (kg) | Median (kg) | Improved |
|---|---|---|---|---|---|---|
| All (both flew) | followed | fuel | 51 | −380.5 | −948.6 | 27 of 51 |
| All (both flew) | not followed | fuel | 31 | +164.8 | +10.7 | 8 of 31 |
| All (both flew) | followed | GTOW | 51 | −785.2 | −134.3 | 26 of 51 |
| All (both flew) | not followed | GTOW | 31 | +307.4 | 0.0 | 11 of 31 |
| Converged only | followed | fuel | 5 | −7,510.0 | −9,821.4 | 4 of 5 |
| Converged only | not followed | fuel | 6 | −94.9 | +5.4 | 2 of 6 |
| Converged only | followed | GTOW | 5 | −10,710.0 | −13,127.2 | 4 of 5 |
| Converged only | not followed | GTOW | 6 | −140.0 | +7.8 | 2 of 6 |

"Not followed" means unchanged or opposite. Of the 92 scored items, 82 pairs have both iterations flown and 11 have
both missions converged.

How to read it:
- **Items are not independent.** One iteration step can carry several items, and each item counts that step's change
  again. The 82 item pairs cover 70 distinct steps: 45 have followed items and 26 have not-followed items, one step
  having both.
- **Followed means the parameter moved the recommended way,** not that the move caused the fuel change. The
  framework-written factors (engine scale, drag factor, wing mass scaler) move on every iteration whether or not
  anything was recommended.
- **The converged-only rows are too small to conclude from** (5 vs. 6 pairs). The followed mean is set by three large
  drops: S_SP iteration 18 (−9,821 kg, counted twice because AR and wing area were both followed), O_IF 16 (−8,708 kg)
  and O_GR 20 (−10,191 kg).
- **Most fuel figures come from unconverged missions** (37 of 140 flown iterations are converged; see 01).

## Reproduce

On aipexws8, from `~/Jessica/avion_analysis/code` with the Avion venv:

```
python -m analysis.link20.reports.scripts.ladder_and_table --root ../reports_20261028 \
    --links ../out_20261028/links.csv --out ../reports_20261028/ladder
```

The markdown tables in section 1 are rendered from `iterations.csv` by `md_tables.py`, which is in the same ws8 folder.
