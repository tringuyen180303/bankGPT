# Core Console — operator cheat sheet (injected into discovery)
# Edit this file to teach the model the next screen. Keep names exact.

You are already signed on. Do not fill Operator ID or Password.

## Member search
- fill label "Member ID" with the member number
- click button "Search"
- if dialog "System notice": click button "OK"

## Member detail
- heading "Member detail"
- savings: table row "Savings balance"
- to post a loan payment: click link "Post payment"
- to draw on a credit line: click link "Draw on line"
- do NOT click "Close account"

## Credit inquiry
- table rows "Credit limit", "Available credit", "Loan balance"
- if status "No credit product", that is a business outcome (member 11111)

## Post payment
- heading "Post payment"
- fill label "Payment amount"
- Continue → Review and submit → Submit
- status "Payment posted. Confirmation …" (debits savings, credits loan)

## Draw on line
- heading "Draw on line"
- fill label "Draw amount"
- Continue → Submit
- "Insufficient available credit" is a business outcome
- status "Credit draw posted. Confirmation …"

## Open sub-account (form)
- heading "Open sub-account"
- select "Product type": action=select, by=label, text="Product type", value="SAVINGS"
- fill label "Nickname" (required, e.g. Travel)
- click button "Continue"

## Review
- heading "Review and submit"
- click button "Submit"

## Done
- status contains "Sub-account opened. Confirmation"
- savings balance on member detail does not change in this demo
- call done with outputs: confirmation (from the status text) and savingsBalance if you went Back to member
