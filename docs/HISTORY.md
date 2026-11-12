# Score history

Scores are public penalized accuracy, from uploads. Right and wrong counts are implied by the score and the number answered, not measured.

| File | Answered | Public score | About right / wrong | What changed |
|---|---|---|---|---|
| CHESS with metric layer | 823 | 0.241 | | CHESS as the fallback lost points on public |
| Metric layer only | | 0.352 | | Dropped CHESS as the fallback |
| v3 | | 0.398 | | Date and blank-answer fixes |
| v7 | 719 | 0.601 | 660 / 59 | Code rules for new shop types, debt, spoken forms |
| v8 | 789 | 0.667 | 728 / 61 | Raw ledger rules: cost, staff, supplier, Tet |
| v10 | 857 | 0.731 | 794 / 63 | Month lists, price rises, stock, expiry |
| v12 | 913 | 0.789 | 851 / 62 | `common.py`, product-name fix, not-recorded answers |
| v13 | 921 | 0.797 | 859 / 62 | Rules from odd train shops only |
| v14 | 996 | 0.854 | 925 / 71 | Public blanks by family: repair, phones, name variants, not-recorded answers |
| LoRA v3 | 1000 | 0.892 | | Rewrite step, LoRA slot filling, dual-verified SQL fallback |

The approach, "rules + metric layer", is our own and not a published method: code rules answer first, then the model fills slots and fixed SQL computes the answer after `check_evidence` accepts them; otherwise the question stays blank. It replaced CHESS after public scores of 0.241 with CHESS, 0.352 for the metric layer alone and 0.731 for rules + metric layer at v10; by estimate from those scores, CHESS got about 113 right and 224 wrong on the questions it answered.

Dropped methods, all worse on held-out or public questions: plain SQL agents, CHESS, DIN-SQL, Alpha-SQL.

Fine-tuning was added afterwards: a rank-16 LoRA on Qwen3-14B fills the slot form (see README, Training).

Kaggle run 15 on the final code: 920 answered in 312 s for 1000 questions on one Blackwell GPU. Code rules alone, with no model: 877 answered.
