Add order type classification and enhanced Confluence formatting to bom_scanner.
Read tasks/bom_scanner/main.py and tasks/bom_scanner/publish.py first.

---

PART 1: MITBAL ORDER TYPE CLASSIFICATION

Each article number extracted from a container description can be either:
- SPI = the primary article being produced in this container
- SNO = a reference article already released
- Other = skip entirely

Query:
    SELECT MBORTY FROM PFODS.MITBAL
    WHERE MBITNO = ? AND MBWHLO = 'MF1'

Add SQL constant MITBAL_ORTY_SQL in main.py. For each article:
- MBORTY = 'SPI' → tag as SPI (primary)
- MBORTY = 'SNO' → tag as SNO (reference)
- No row or other value → skip, log "article XXXXXX: order type ZZZ, skipping"
- Mock mode: default to SPI if no fixture exists.

Add MITBAL capture in capture.py (m3_mitbal_XXXXXX.json per article).

Console output table should show the type column:
  Container  Source  Article   Type  Flagged  Reporter  Action

Comment body (logic.py): group SPI articles first under "Primary (SPI)",
then SNO under "Reference (SNO)".

---

PART 2: ENHANCED CONFLUENCE OUTPUT (publish.py)

Rewrite the Confluence page rendering with these requirements:

A) PAGE STRUCTURE — use separate sections so the operator can focus on what
   they need:

   1. Summary banner at top: scan timestamp, total containers, total flagged,
      target status used, counts by source (JIRA vs Confluence).

   2. Section: "Primary Articles (SPI)" — table of all SPI flagged results.
   3. Section: "Reference Articles (SNO)" — table of all SNO flagged results.
   4. Section: "Clean Containers" — collapsed expand macro listing containers
      with no flagged components (just keys + article numbers, minimal).
   5. Section: "Skipped Containers" — collapsed expand macro listing containers
      with no article number or no BOM.
   6. Footer: code-macro with re-run command and comment-command template.

   Use Confluence h2 headers for each section. Use ac:structured-macro with
   ac:name="expand" for sections 4 and 5 so they're collapsed by default.

B) TABLE COLUMNS for SPI and SNO sections:
   | Container | Source | Article # | Order Type | Component | PLC Status | Description |

C) FILTERING BY PLC STATUS — since Confluence has no native column filter,
   structure the data so it's easy to scan:
   - Sort rows by PLC status within each table (group all NEW together,
     all INT together, all 200 together, etc.)
   - Add a PLC status sub-header row when the status changes, spanning all
     columns, with a colored background:
     e.g. a full-width row "PLC: NEW (3 components)" in orange background
     followed by the 3 rows with NEW status, then
     "PLC: INT (2 components)" in yellow background, etc.

D) COLOR CODING — use inline styles (Confluence storage format supports them):
   - PLC status cell colors:
     * "NEW" → background orange (#F39C12), white text
     * "INT" → background yellow (#F1C40F), dark text
     * "200" → background red (#E74C3C), white text
     * "201"-"299" → background red (#E74C3C), white text (all development)
     * "300"-"309" → background blue (#3498DB), white text (active but restricted)
     * "311"-"399" → background blue (#3498DB), white text
     * "4xx" → background purple (#8E44AD), white text (still available)
     * "5xx" → background dark red (#C0392B), white text (phase out)
     * "6xx" → background grey (#7F8C8D), white text (terminated)
     * blank/empty → background black (#2C3E50), white text
     * any other → background grey (#95A5A6), dark text
   - Order Type badge:
     * SPI → green background (#27AE60), white text, bold
     * SNO → blue background (#2980B9), white text

   - Source badge:
     * JIRA → blue badge (#3498DB)
     * Confluence → purple badge (#8E44AD)
     * Both → green badge (#27AE60)

E) CLICKABLE JIRA LINKS — Container column should be a hyperlink:
   <a href="https://pfjira.pepperl-fuchs.com/browse/POSX-6558">POSX-6558</a>

F) SOURCE DIFFERENTIATION — the Source column should clearly show where each
   container came from. Use the Confluence status macro for visual badges:
   <ac:structured-macro ac:name="status">
     <ac:parameter ac:name="colour">Blue</ac:parameter>
     <ac:parameter ac:name="title">JIRA</ac:parameter>
   </ac:structured-macro>

   Or for Confluence source:
   <ac:structured-macro ac:name="status">
     <ac:parameter ac:name="colour">Purple</ac:parameter>
     <ac:parameter ac:name="title">CONFLUENCE</ac:parameter>
   </ac:structured-macro>

   Or for containers found in both:
   <ac:structured-macro ac:name="status">
     <ac:parameter ac:name="colour">Green</ac:parameter>
     <ac:parameter ac:name="title">BOTH</ac:parameter>
   </ac:structured-macro>

---

PART 3: DO NOT CHANGE
- capture.py structure (only add MITBAL capture)
- main.py scan/comment subcommand structure
- MPDHED gate logic
- BOM flagged SQL query
- --mock/--live split
- Comment body format (except adding SPI/SNO grouping)
