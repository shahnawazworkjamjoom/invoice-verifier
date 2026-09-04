import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "D:/MyData/OneDrive - Kamal Osman Jamjoom Group LLC/Desktop/invoice verifier/outputs/01a06ab2-c9a9-79a0-94c1-f04769d538e9/KOJ_invoice_verification_result.xlsx";
const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const table = await workbook.inspect({
  kind: "table",
  range: "Sheet1!A1:X12",
  include: "values,formulas",
  tableMaxRows: 12,
  tableMaxCols: 24,
});
console.log(table.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 50 },
  summary: "formula error scan",
});
console.log(errors.ndjson);
const preview = await workbook.render({ sheetName: "Sheet1", range: "A1:X12", scale: 1.2 });
await fs.writeFile("artifact_verify/preview.png", new Uint8Array(await preview.arrayBuffer()));
