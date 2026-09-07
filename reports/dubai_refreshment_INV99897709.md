# Dubai Refreshment receipt OCR update

Sample: `Subway_260976369_INV99897709.pdf`, invoice 99897709, dated 03/09/2026.
Vendor: Dubai Refreshment (P.J.S.C.). Subway is the customer location.
The one-page scan was rendered and visually checked: printed `TOTAL/GROSS(AED)` is **77.49**.

The original reader returned no final total. Perspective in the receipt places
the right-hand amounts above their corresponding labels, so ordinary horizontal
row grouping associates them with preceding labels.

The reader now recognizes the complete Dubai Refreshment five-row summary and
aligns the two columns in order. It requires the vendor, all five unique labels,
exactly five amounts to their right, and consistent vertical displacement.
Missing or ambiguous summaries are not repaired by guessing or arithmetic.
The final amount is copied from the printed gross amount; it is not calculated.

On this machine the original extraction took 13.33 seconds and returned no amount.
Updated extraction returned 77.49 in 3.89 seconds on the first run and 1.66 seconds
on a second run with the engine loaded. These are individual development runs,
not a general performance guarantee. Both runs performed OCR again; no result
cache was used.

This update tunes extraction rules using the existing pretrained OCR model.
It does not retrain neural model weights. Production approval still requires
exact equality with the original Excel payment amount, and exported payment
amounts remain unchanged.

Validation: 46 unit tests pass, including the sample's OCR box geometry and
missing final digits, incomplete labels, extra amounts, and missing vendor cases.
All five earlier supplied PDFs were reprocessed successfully: Abu Dhabi
Refreshments 50.17, Dubai Refreshment 94.47, Mohebi Logistics 1569.91,
M.H. Enterprises 673.58, and the two-page Barakat invoice 328.73.
