# P3 · Union Budget since 2014-15: money that was budgeted but not spent

Every year Parliament is asked to vote money for each scheme, body and department of the Union
Government. This project reads, line by line, how much each line was given (the budget estimate),
how much the ministry expected to spend half-way through the year (the revised estimate), and how
much it actually spent, for twelve financial years, 2014-15 to 2025-26. It uses only the Ministry
of Finance's own published budget papers. There is no investigative work.

## What one check reads

Every Union Budget publishes, for each Demand for Grants, a statement called the **Notes on
Demands for Grants** (the Statement of Budget Estimates, `sbe<N>.pdf` on indiabudget.gov.in).
Section A of that statement is one table. Each numbered line prints four periods, three figures
each, in crore of rupees:

| Actual (two years back) | Budget (last year) | Revised (last year) | Budget (this year) |
| --- | --- | --- | --- |

So, for a financial year F:

* the **Union Budget presented in F+1** prints the budget estimate and the revised estimate of F;
* the **Union Budget presented in F+2** prints the actual expenditure of F.

A check pins both PDFs by their exact link and SHA-256 and reads the listed lines with the `UB1`
engine (`src/aiwatchdogs/engines/union_budget.py`). The CLI does this in code; no model tokens are
needed. Three other volunteers repeat every lead independently before it is shown as verified.

## The rule

* **Unspent = budget estimate − actual expenditure**, using the total of revenue and capital.
* A line is reported when the unspent amount is **10% or more of its budget estimate and at least
  ₹1 crore**. Everything else is recorded as cleared.
* For **2025-26**, whose actuals will only be printed in Union Budget 2027-28, and for **2015-16**,
  whose lines Union Budget 2017-18 regrouped when the Plan / Non-Plan split was dropped, the
  revised estimate is compared with the budget estimate instead.

## Keeping the comparison honest

* **The table must add up.** A statement is used only if its numbered lines add up to its printed
  Grand Total in all four periods. 1,173 of the 1,218 statements of Union Budgets 2015-16 to
  2026-27 pass; the rest are listed in `documents.csv` with the reason.
* **The two budgets must agree on the line.** The estimates row and the actuals row must carry the
  same printed name, and both budgets must print the same budget estimate for the year in between.
  Lines where neither prints that figure are matched by name only, and only inside statements that
  add up.
* **A scheme is compared as a whole.** Where a heading splits a scheme by source of funds (Gross
  Budgetary Support, National Investment Fund, cess-funded Koshes, CRIF, PMSSN) or by component
  (programme, externally aided, grant and loan parts), UB1 v2 adds its spending rows. It excludes
  numbered recoveries and loan-interest payments on both sides of the comparison. Interest
  subsidies remain spending. When a heading has other funded rows, the finding title identifies
  the budget lines included. A named top-level programme component can form its own unit.
* **Component coverage.** Each funded component in a grouped actuals comparison must have a
  counterpart by printed name in the later statement. Row numbers may change. Missing components
  make the actuals comparison unevaluable. Only evaluated lines contribute to the audited amount.
* **A printed nil is not read as "nothing spent"** unless the line continues into the next year in
  both budgets and its revised estimate had already been cut to a quarter or less.
* **Left out by design:** repayment of debt and interest payments; the Railways statement, whose
  works are printed net of the railway funds that pay for them; transfers into and out of reserve
  funds; suspense heads; loan servicing; lump-sum provisions; provisions paid in securities rather
  than cash; and headings whose costs are mostly recovered inside the heading.

## What a lead does not say

A lead compares figures printed by the Ministry of Finance. It is **not a finding of wrongdoing**.
Ordinary reasons for a shortfall include delayed approvals, tendering or land; vacant posts; lower
demand in a demand-driven scheme; money re-appropriated to another line; a scheme merged, renamed
or moved to another Demand; fresh releases held back while earlier releases are still unspent with
States or implementing agencies; and financing outside the budget for a year (for example loans to
a public sector undertaking). Every lead lists these and cites the page and printed row of every
figure in both documents.

## Before launch

Two independent audits read a stratified sample of the dry-run leads (330, then 190) against the
PDFs with a different text extractor. Neither found a wrong figure or a mismatched line. They
flagged leads that were correct but misleading in substance (one scheme split by source of funds,
a line netted against recoveries, lump sums, schemes moved to new lines); the rules above were
added in response, and the share fell from about one in nine to about one in sixteen. Known limits
that the statements cannot show are listed under "What a lead does not say".

## Files

* `../union-budget-unspent-funds.json`: the 10,000 tasks.
* `documents.csv`: every statement read, with its URL, SHA-256, page count and status
  (also served at `/api/missions/union-budget-unspent-funds/documents.csv`).
* `coverage.json`: lines, tasks and budget estimates per financial year, and what was left out.
* `scripts/build_union_budget_project.py`: rebuilds all three from the official PDFs. Re-run it
  when a new Union Budget is published: the current budget is served from the site root and moves
  to its own folder a year later, so tasks that pin it fail their checksum until the project is
  rebuilt (they are handed back, never misread).
