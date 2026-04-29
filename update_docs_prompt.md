Update project documentation with discoveries from the bom_scanner build.
Read CLAUDE.md, PROJECT_STATUS.md, and M3_CONNECTIVITY_REFERENCE.md first.

1. CLAUDE.md — Add these confirmed M3 tables/fields under "M3 ERP (ODBC)":

   ### BOM / Product Structure Tables (confirmed 2026-04-20)
   - `MPDHED` — Product structure header. Key: PHPRNO (product number),
     PHSTRT (structure type, filter 'STD'). Use to verify a product exists
     before querying BOM materials.
   - `MPDMAT` — BOM materials/components (3.1M rows). Key: PMPRNO (parent),
     PMMTNO (component), PMSTRT ('STD'), PMFACI ('MF1').
     Same component appears at multiple positions (PMDWPO). Use DISTINCT.
   - `MPDSUM` — Product structure summary. EXISTS in PFODS but EMPTY (0 rows).
     Do not use.
   - `MITBAL` — Item/warehouse balance. Key: MBITNO, MBWHLO ('MF1').
     MBORTY = order type: 'SPI' (produced in Singapore), 'SNO' (reference).
   - `MITMAS_AP` — Item master. MMCFI3 = PLC status (string: '310', '200',
     'INT', 'NEW', blank). MMSTAT = item status. MMITDS = description.

   ### PLC Status Reference
   - PLC is stored in MITMAS_AP.MMCFI3 (Custom Field Information 3)
   - PLC 310 = "Without limitation" — full sales release
   - Values are strings, not always numeric ('INT', 'NEW', blank are valid)
   - PLC is an extension of MMS001 item status, documented in DPPF-A020001

   ### Article Number Extraction
   - customfield_13502 (M3 Article Number) is EMPTY on all containers
   - customfield_15805 (Component Part Number) is EMPTY on all containers
   - Extract from DESCRIPTION field (not Summary) using regex patterns:
     #(\d{6,8}), Y(\d{7,8}), \b(70\d{5,6})\b, PCB/PCBA#(\d{6,8})
   - Coverage: ~82% of SMT PCBA Singapore containers
   - Containers without article numbers are typically early-stage development

2. CLAUDE.md — Add under "Confluence":

   ### MR Status Report Page (560866215)
   Three tables: MR Week Schedule (purple, index 0), Active MR (blue, index 1),
   COMPLETED MR (index 2 — skip). Container keys are in <a> tags with
   href="/browse/KEY". Extract with BeautifulSoup find_all("a", href=True).

3. PROJECT_STATUS.md — Update Task Registry:

   | # | Task Name | Category | Status | Systems | Risk Level |
   | 1 | to_status_check | General | Phase A done, Phase B live-test pending | JIRA + M3 (Playwright) | Low |
   | 2 | bom_scanner | General | **Live ✓ — scheduled daily 9:30 AM** | JIRA + M3 (ODBC) + Confluence | Low (read-only scan, selective write) |

   Move bom_scanner from backlog to completed. It was originally listed as
   "bom_new_component_check" (#3) and "bom_routing_edm_check" (#8) — note
   that bom_scanner covers the BOM PLC check portion of these.

4. PROJECT_STATUS.md — Add to Completed Tasks section:

   ### Task 2 — bom_scanner — Live ✓ 2026-04-20
   - Scans Work Containers (JIRA SMT PCBA Singapore + Confluence MR page).
   - Extracts article numbers from Description field via regex.
   - Classifies articles as SPI (primary) or SNO (reference) via MITBAL.MBORTY.
   - Queries MPDMAT+MITMAS_AP for BOM components with PLC != 310.
   - Validates articles exist in MPDHED (STD structure at MF1) before BOM query.
   - Publishes color-coded results to Confluence page 572180443.
   - Selective JIRA comment posting via `comment --keys` subcommand.
   - Scheduled daily at 9:30 AM via Windows Task Scheduler.
   - Duplicate comment guard uses marker "#Ref: BOM-PLCCheck#".

5. PROJECT_STATUS.md — Add to Discovery Log:

   ### M3 BOM / PLC Tables (confirmed 2026-04-20)
   | Purpose | Table | Key Columns | Confirmed? |
   | BOM header | MPDHED | PHPRNO, PHSTRT | Yes |
   | BOM materials | MPDMAT | PMPRNO, PMMTNO, PMSTRT, PMFACI | Yes (3.1M rows) |
   | BOM summary | MPDSUM | — | Exists but EMPTY |
   | Item/warehouse | MITBAL | MBITNO, MBWHLO, MBORTY, MBSTAT | Yes |
   | PLC field | MITMAS_AP.MMCFI3 | String values | Yes |

6. PROJECT_STATUS.md — Add to Decisions Made:

   8. **2026-04-20:** PLC status is in MITMAS_AP.MMCFI3, not a PDS-specific
      table. Pure ODBC path — no Playwright needed for BOM PLC checks.
   9. **2026-04-20:** Article numbers extracted from Description field, not
      Summary or custom fields (both empty). Regex coverage ~82%.
   10. **2026-04-20:** MPDSUM is empty in PFODS. Use MPDMAT for BOM components.
   11. **2026-04-20:** scan/comment separation — scan publishes to Confluence
       only; operator selectively pushes JIRA comments via comment --keys.
   12. **2026-04-20:** MITBAL.MBORTY classifies articles: SPI=primary,
       SNO=reference. Others skipped.

7. M3_CONNECTIVITY_REFERENCE.md — Add to "Key Tables Confirmed":

   | MPDHED | Product Structure Header | PHPRNO, PHSTRT (filter 'STD') |
   | MPDMAT | BOM Materials (3.1M rows) | PMPRNO (parent), PMMTNO (component), PMSTRT, PMFACI |
   | MPDSUM | Product Structure Summary | EMPTY — do not use |
   | MITBAL | Item/Warehouse Balance | MBITNO, MBWHLO, MBORTY (SPI/SNO), MBSTAT |

   Add to "Summary: Which Method for Which Task":
   | BOM PLC check | **ODBC** (MPDMAT+MITMAS_AP) | Direct SQL join, fast |
   | Order type classification | **ODBC** (MITBAL) | MBORTY at MF1 |
