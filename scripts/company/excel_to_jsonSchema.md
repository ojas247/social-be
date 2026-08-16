---
name: excel-to-json-schema
description: Converts a pasted Excel/spreadsheet table (or tabular text, CSV, or screenshot of one) of company financial/operational line items into a structured JSON schema with periodicity, columns, and hierarchical line_items (Category/H1/H2/H3.../Item/units) — the same format used by AR_schema.json and QR_schema.json for RAG-based annual/quarterly report extraction. Use this skill any time the user pastes a table of line items (production volumes, financial statement rows, KPIs, segment data, etc.) and wants it turned into this schema, even if they don't say "schema" or "JSON" explicitly — phrases like "convert this table", "make a schema from this", "here's my line items list", or a raw pasted table followed by "in the usual format" should all trigger it.
---

# Excel → Line-Item JSON Schema

Converts a pasted spreadsheet/table of line items into the standardized JSON
schema consumed downstream by the RAG extraction pipeline (`co_embedding.py`
→ `extract_ar_schema.py`). The output defines *what to extract*, not the
extracted values themselves — no `Date` or `Value` data goes into this file,
only the shape of each line item.

## Output format (target shape)

```json
{
  "periodicity": "QUARTERLY",
  "columns": ["Date", "Value", "Item", "Category", "H1", "H2", "H3", "units"],
  "line_items": [
    {
      "Category": "Production",
      "H1": "Auto",
      "H2": "Passenger Vehicles",
      "H3": "--",
      "Item": "SUV Volumes",
      "units": "In Numbers"
    }
  ]
}
```

- `periodicity`: one of `MONTHLY`, `QUARTERLY`, `HALF_YEARLY`, `ANNUAL`. Use
  `ANNUAL` for annual-report-style tables (feeds `AR_schema.json`) and
  `QUARTERLY` for quarterly tables (feeds `QR_schema.json`), unless the
  pasted table clearly indicates otherwise (e.g. monthly production data).
- `columns`: always `["Date", "Value", "Item", "Category", "H1", ... "Hn", "units"]`
  — `Date` and `Value` are placeholders filled in later during extraction,
  not by this skill. The number of `Hn` columns equals the **deepest**
  hierarchy level found anywhere in the table (see below).
- `line_items`: one object per leaf-level metric. Every object has the same
  keys, in the same order, even if some `Hn` values are `"--"` for that item.

## Step-by-step process

### 1. Identify periodicity
Look at the table's column headers, title, or any date labels for cues:
- Monthly columns / "MTD" → `MONTHLY`
- Quarter labels (Q1, Q2, "3 months ended") → `QUARTERLY`
- "6 months ended", "H1", "H2" → `HALF_YEARLY`
- "FY", "Annual Report", 12-month periods → `ANNUAL`

If genuinely ambiguous (e.g. a bare list of line items with no period
markers at all), ask the user once rather than guessing silently.

### 2. Determine the hierarchy depth
Scan every row and figure out how many levels of grouping exist between the
broadest category and the actual leaf metric. Sources of hierarchy in a
pasted table, in rough order of how often they appear:
- Explicit indentation (rows nested under a bold/section header)
- Merged cells spanning a group of rows
- A multi-column layout where earlier columns repeat for grouped rows
- Section headers in ALL CAPS or bold, followed by indented sub-items
- Dash/bullet prefixes (`- `, `— `, `•`) indicating nesting depth

Find the **maximum depth** across the whole table — that determines how
many `H1..Hn` columns appear in the output. Every line item uses the same
set of columns, even the ones that don't go that deep (see step 4).

**Example depth-finding:** if most rows only need `H1`, but one section has
`Auto → Passenger Vehicles → SUV` (3 levels deep), the whole table needs
`H1`, `H2`, `H3` — every other item pads its unused deeper levels with `"--"`.

### 3. Assign Category, H1..Hn, and Item
- **Category**: the broadest/top-level grouping in the table — usually a
  sheet name, a top section header, or the first grouping column (e.g.
  "Production", "Financials", "Segment Revenue", "Balance Sheet").
- **H1, H2, ... Hn**: each successive level of nesting under Category, in
  order from broadest to narrowest, stopping one level above the leaf row.
- **Item**: the actual metric name on the leaf row — this is the specific
  thing that will be extracted later (e.g. "SUV Volumes", "Net Revenue",
  "EBITDA Margin"). Keep the item's own natural name; don't fold parent
  group names into it (that's what Category/H1..Hn are for).
- Any level that doesn't apply to a given item gets the literal string
  `"--"` (not `null`, not empty string) — see the example in the format
  above (`"H3": "--"`).

### 4. Determine units
Look for a units column, a footnote, or a header annotation (₹ Mn, %, In
Numbers, Days, x, bps, etc.). Common patterns:
- Volume/count metrics → `"In Numbers"` or `"In Units"`
- Currency line items → the stated currency + scale, e.g. `"INR Crores"`,
  `"USD Million"`
- Ratios/margins → `"%"`
- Per-share metrics → `"INR per share"` (or stated currency)
- Ratios like debt/equity → `"x"` (times)

If units genuinely aren't stated anywhere and can't be inferred from the
metric name (e.g. "SUV Volumes" clearly implies a count), use your best
judgement and flag the assumption in a short note after the JSON — don't
silently guess on something material like currency scale (Crores vs.
Millions vs. absolute) without flagging it.

### 5. Handle special row types
- **Subtotal/Total rows** ("Total Revenue", "Total Production"): include
  them as their own `Item` at the appropriate hierarchy level — they're
  legitimate line items to extract, just don't double-nest child items
  under them as if the total were a new Category/H-level.
- **Growth/YoY/QoQ rows**: include as separate items (e.g. `"Item": "SUV
  Volumes YoY Growth"`, `"units": "%"`) rather than merging into the base
  metric — extraction treats each as an independent number.
- **Blank/separator rows**: skip entirely, they're formatting only.
- **Repeated section headers with no data row of their own**: these become
  a `Category` or `Hn` value, not an `Item` — don't emit a line item for a
  pure header row.
- **Footnoted/starred items**: keep the clean metric name in `Item`; drop
  footnote markers (`*`, `¹`) unless the user says otherwise.

### 6. Assemble and validate before output
- Every `line_items` object has exactly the same keys, in the same order.
- Every `Hn` slot is filled — either a real group name or `"--"`.
- No `Date` or `Value` keys appear inside `line_items` (those are schema
  columns only, filled during extraction, not authored here).
- `columns` lists exactly `Date, Value, Item, Category, H1..Hn, units` —
  double check `n` matches the deepest hierarchy actually used.

## Output delivery

Produce the result as a `.json` file (not inline text) named descriptively,
e.g. `AR_schema.json` or `QR_schema.json` to match the downstream pipeline's
expected filenames, or `{topic}_schema.json` if the user hasn't specified
which. Save it to the outputs directory and present it as a downloadable
file — don't just print the JSON in chat, since the user pastes tables
repeatedly and wants a file each time.

If any assumption was made (periodicity, units, or hierarchy depth that
wasn't explicit in the table), add one short sentence after presenting the
file noting what was assumed, so the user can correct it if wrong.

## Worked example

**Input (pasted table):**

```
Production
  Auto
    Passenger Vehicles
      SUV Volumes (Numbers)
    Commercial Vehicles
      LCV <3.5T Volumes (Numbers)
      Trucks & Buses (Numbers)
```

**Output (`{topic}_schema.json`):**

```json
{
  "periodicity": "QUARTERLY",
  "columns": ["Date", "Value", "Item", "Category", "H1", "H2", "H3", "units"],
  "line_items": [
    {
      "Category": "Production",
      "H1": "Auto",
      "H2": "Passenger Vehicles",
      "H3": "--",
      "Item": "SUV Volumes",
      "units": "In Numbers"
    },
    {
      "Category": "Production",
      "H1": "Auto",
      "H2": "Commercial Vehicles",
      "H3": "--",
      "Item": "LCV <3.5T Volumes",
      "units": "In Numbers"
    },
    {
      "Category": "Production",
      "H1": "Auto",
      "H2": "Commercial Vehicles",
      "H3": "--",
      "Item": "Trucks & Buses",
      "units": "In Numbers"
    }
  ]
}
```

Note here the table only went 2 levels deep under Category (Auto →
Passenger/Commercial Vehicles) before hitting the leaf item, so `H3` exists
in `columns` only because some other part of a larger table needed it —
if this were the *entire* table, `H3` would be dropped and `columns` would
end at `H2`. Always size `Hn` to match the deepest level actually present
in what was pasted.
