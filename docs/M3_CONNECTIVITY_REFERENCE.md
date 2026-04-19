# M3 Connectivity Reference — Pepperl+Fuchs

> Documented: 2026-04-19
> From: Phase B discovery session (TO status lookup for XDRX800)

---

## Architecture Overview

P+F runs two M3-related servers:

| Server | URL | Purpose | Auth |
|--------|-----|---------|------|
| **MNE/EAS** | `pfeash5live.pepperl-fuchs.com` | MNE web frontend (generic.do) | Windows Integrated (SSPI) |
| **M3 Backend** | `pfde-m3-auth.eu.p-f.biz` | H5 Client, REST API, MvxMCSvt | ADFS/SAML via `pfde-adfsfarm.eu.p-f.biz` |

ADFS farm: `pfde-adfsfarm.eu.p-f.biz` — uses WIA (Windows Integrated Auth) endpoint at `/adfs/ls/wia`.

---

## Connection Method 1: ODBC to PFODS (ODS Replica)

**Status: WORKING — primary method for standard M3 tables**

```python
import pyodbc
conn = pyodbc.connect("DSN=ODSSG", timeout=30)
cursor = conn.cursor()
```

- **DSN:** `ODSSG`
- **Schema:** `PFODS`
- **Backend:** Oracle (uses Oracle SQL syntax: `ROWNUM`, `ALL_SYNONYMS`, `ALL_TAB_COLUMNS`)
- **Tables:** 343 tables, all synonyms to underlying Oracle tables
- **Table naming:** `TABLENAME_AP` (Asia Pacific), `_100`, `_120` variants

### Key Tables Confirmed

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `MGHEAD_AP` | Goods Movement Header | `MGTRNR` (10-digit padded, e.g., `'0020578312'`), `MGTRSL`/`MGTRSH` (status), `MGRESP`, `MGFACI`, `MGTRDT` |
| `MGLINE_AP` | Goods Movement Lines | `MRTRNR` |
| `MHDISH_AP` | Delivery Head (Shipment) | `OQDLIX` (delivery no), `OQRIDN`, `OQPGRS`, `OQPIST`, `OQWHLO` |
| `MHDISL` | Delivery Lines | `URDLIX`, `URRIDN`, `URSTCD` |
| `MITTRA_AP` | Item Transactions | `MTNSTQ` |
| `MPDHED_AP` | MO Header | For MO status checks |
| `MPDOPE_AP` | MO Operations | For MO operation status |
| `OOLINE_AP` | Customer Order Lines | `OBORNO`, `OBORST` |
| `XDOPAH` | Custom table (XDRX prefix) | `XDTRNR`, `XDSTAT`, `XDRESP`, `XDFACI`, `XDTWHL`, `XDITNO` |
| `KBXHED_AP` | Kit Box Header | `KBTRNR`, `KBTRSL`, `KBTRSH` |
| `CSYTAB` | System Constants | `CTSTCO`, `CTSTKY`, `CTTX40` |

### Oracle SQL Notes
- Use `ROWNUM <= N` (not `TOP N`)
- String columns are often padded (e.g., MGTRNR is 10-digit zero-padded: `'0000147715'`)
- `SELECT *` can cause `ORA-01722` (invalid number) on some views — select specific columns instead
- `ORDER BY` must be in a subquery: `SELECT * FROM (SELECT ... ORDER BY x) WHERE ROWNUM <= 5`
- Column metadata: query `ALL_SYNONYMS` joined with `ALL_TAB_COLUMNS` for Oracle catalog

### What's NOT in PFODS ODS
- **XDRX800 custom tables** (DTH-prefixed fields: DTHID, DTHSTAT, etc.)
- M3 program metadata/catalog tables (CSYPRG, etc.)
- Any table with DTH, DRX, or EXTD prefix columns

### Working Query Examples

```python
# Recent TOs for a user
cursor.execute("""
    SELECT * FROM (
        SELECT MGTRNR, MGTRTP, MGTRSL, MGTRSH, MGRESP, MGFACI, MGTRDT
        FROM PFODS.MGHEAD_AP
        WHERE MGRESP = 'TMOGHANAN' ORDER BY MGTRDT DESC
    ) WHERE ROWNUM <= 10
""")

# EDM document references
cursor.execute("""
    SELECT * FROM ADMEDP.EDM_REFERENCES
    WHERE PRSG = ? AND RELEASESTATE = 'Released'
""", some_value)
```

---

## Connection Method 2: M3 REST API (MI Programs)

**Status: WORKING — passwordless auth via Kerberos + ADFS SAML**

### Auth Flow (fully automated, no password)

```python
import requests
from requests_kerberos import HTTPKerberosAuth, OPTIONAL
import re, urllib3
urllib3.disable_warnings()

M3_BASE = "https://pfde-m3-auth.eu.p-f.biz"

def get_m3_session():
    """Authenticate to M3 REST API. No password needed."""
    s = requests.Session()
    s.verify = False

    # 1: Hit any MI endpoint → ADFS redirect
    r = s.get(f"{M3_BASE}/m3api-rest/execute/CRS610MI/LstByNumber",
              timeout=15, allow_redirects=False)
    adfs_url = r.headers["location"]

    # 2: Follow to WIA endpoint
    r = s.get(adfs_url, timeout=15, allow_redirects=False)
    wia_url = r.headers["location"]

    # 3: Kerberos auth at WIA (automatic, uses Windows login)
    s_auth = requests.Session()
    s_auth.verify = False
    s_auth.auth = HTTPKerberosAuth(mutual_authentication=OPTIONAL)
    r = s_auth.get(wia_url, timeout=30, allow_redirects=False)

    # 4: Extract SAML token
    saml = re.search(r'name="SAMLResponse"\s+value="([^"]+)"', r.text).group(1)
    relay = re.search(r'name="RelayState"\s+value="([^"]*)"', r.text)
    action = re.search(r'<form[^>]+action="([^"]+)"', r.text).group(1).replace("&amp;", "&")

    post_data = {"SAMLResponse": saml}
    if relay:
        post_data["RelayState"] = relay.group(1)

    # 5: Post SAML to M3 → authenticated session
    s_m3 = requests.Session()
    s_m3.verify = False
    s_m3.post(action, data=post_data, timeout=15, allow_redirects=True)
    return s_m3
```

### Requirements
```
pip install requests requests-kerberos
```
- `requests_negotiate_sspi` has a bug (TypeError on ADFS redirect) — do NOT use
- `requests_ntlm` works but requires password — fallback only
- `requests-kerberos` is the correct library (passwordless)

### API Endpoints

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `/m3api-rest/metadata` | SAML | List all 683 MI programs |
| `/m3api-rest/metadata/{PROGRAM}` | SAML | Program transaction list + field definitions |
| `/m3api-rest/execute/{PROGRAM}/{TRANSACTION}` | SAML | Execute MI transaction |
| `/m3api-rest/application.wadl` | None | API schema (no auth needed) |

### Confirmed Working MI Programs

| Program | Description | Key Transactions |
|---------|-------------|------------------|
| `MWS410MI` | Delivery number toolbox | `GetHead`, `GetAdr`, `SearchDelLines`, `ConnectShipment` |
| `MWS411MI` | Delivery Lines | `GetDeliveryLine`, `LstDelLnByOrd`, `LstDeliveryLine` |
| `MMS100MI` | DO Lines (was Distribution Order, not Item Master) | `GetHead`, `GetLine`, `LstLines`, `SearchHead` |
| `MMS200MI` | — | — |
| `DRS100MI` | Shipment management | `GetShipment`, `LstShipment`, `LstByDelivery`, `CloseShipment` |
| `DRS021MI` | Route stops | `Add`, `Get`, `List`, `Update`, `Delete` |
| `CRS610MI` | Customer | `GetBasicData`, `LstByNumber` |
| `CRS620MI` | Customer addresses | — |
| `MDBREADMI` | **Generic table reader** | Pre-built readers: `GetMITBAL00`, `LstOOHEAD00`, etc. (45 transactions) |
| `EXPORTMI` | **Data export** | `Select`, `SelectPad`, `LstFields` — **LOCKED DOWN ("Not allowed")** |
| `CRS990MI` | **Generic Browse** | `InitBrowse`, `LstBrowse`, `LstRows` — needs PGNM + FLDI params |
| `BROWSEMI` | Browse for clients | `GetBrowseParms`, `LstBrowseParms`, `LstBrowseVals` |
| `PPS001MI` | Purchase order | — |
| `OIS100MI` | Customer order | — |
| `OIS320MI` | — | — |

### API Response Format
- XML: `<MIRecord><RowIndex>0</RowIndex><NameValue><Name>FIELD</Name><Value>data</Value></NameValue>...</MIRecord>`
- Errors: `<ErrorMessage type="TransactionNotFound"><Message>...</Message></ErrorMessage>`

### Limitations
- `EXPORTMI/Select` is locked down — returns "Not allowed" for all table queries
- No custom MI for XDRX800 (no DRX-prefixed MI programs exist)
- `MWS410MI` uses DLIX (delivery number), not TO number — no direct TO lookup

---

## Connection Method 3: MvxMCSvt (H5 Client Session API)

**Status: WORKING — session established, but cannot read XDRX800 panel data**

### Auth + Session Flow

```python
# After SAML auth (same as REST API), establish M3 session:
r = s_m3.post(MVX_URL, data={
    "CMDTP": "LOGON", "CMDVAL": "",
    "cono": "1", "dession": "100", "lng": "GB"
})
# Returns: <SID>hexstring</SID>, <Result>0</Result>
# SID is the M3 session ID for subsequent calls
```

### Endpoint
`https://pfde-m3-auth.eu.p-f.biz/mne/servlet/MvxMCSvt`

### Commands

| CMDTP | Purpose | Notes |
|-------|---------|-------|
| `LOGON` | Establish session | Params: `cono`, `dession`, `lng`. Returns SID. |
| `CMD` | Run program | `CMDVAL=DRX800` — works, returns `Result=0` |
| `FLD` | Set field | `CMDVAL=fieldname`, `DATA=value` |
| `KEY` | Press key | `CMDVAL=ENTER`, `CMDVAL=F3`, etc. |
| `LOGOUT` | End session | |

### Key Finding
MvxMCSvt can START programs (CMD) and SET fields (FLD), but **all responses return only `<Result>0</Result>` with no panel data**. XDRX800 is an MNE-only application — its UI renders exclusively through MNE's `generic.do`, not through MvxMCSvt panels.

### M3 Version
`10.3.1.0.611` (from LOGON response: `<Root ver="10.3.1.0.611" mcv="1.0">`)

---

## Connection Method 4: MNE generic.do (XDRX800 web interface)

**Status: WORKING — but requires secToken (CSRF) or existing browser session**

### Endpoint
`https://pfeash5live.pepperl-fuchs.com/mwp/jsp/component/pfmodules/generic.do`

### Init (requires secToken)
```
POST generic.do?keytype=DEF&applicationtype=DRX800B&application=drx800&H5=true&IID=NEW&secToken=...
```
- secToken is generated by M3 H5 Client when launching a program through the portal
- Cannot be generated programmatically — only through portal navigation

### Search (uses existing session)
```
POST generic.do
Content-Type: application/x-www-form-urlencoded

DTHID=147715+&DTHSNAC=TMOGHANAN&DTHSTAT1=&DTHSTAT2=89&
applicationtype=DRX800B&application=drx800&keytype=ENTER&
SID={jsessionid}&IID=0&CMDTP=KEY&CMDVAL=ENTER&BROWSEINQTYPE=10&H5=true
```

### Response Format (XML)
```xml
<LRows>
  <LR name="R1">
    <LC name="R1C0">147715</LC>          <!-- TO No -->
    <LC name="R1C1">0</LC>               <!-- Main TO -->
    <LC name="R1C2">1</LC>               <!-- Lines -->
    <LC name="R1C3">44 - Shipped...</LC>  <!-- Status -->
    <LC name="R1C4">Express</LC>          <!-- Delivery Service -->
    <LC name="R1C5">TMOGHANAN</LC>        <!-- Responsible -->
    <LC name="R1C6">AP-SG-MF</LC>         <!-- Sending Site -->
    <LC name="R1C7">GESCHAEFER</LC>       <!-- Receiver -->
    <LC name="R1C8">EU-DE-MH</LC>         <!-- Receiving site -->
    <LC name="R1C9">DE</LC>               <!-- Rec. country -->
    <LC name="R1C10">EU</LC>              <!-- FTA -->
    <LC name="R1C11">SP001003</LC>        <!-- Customer No -->
    <LC name="R1C12">NPIOTHER-4371...</LC><!-- Remark -->
    <LC name="R1C13">2026-04-16</LC>      <!-- Creation Date -->
    <LC name="R1C14">2026-04-17</LC>      <!-- Arrived at logistics -->
    <LC name="R1C15">ExpressOPS</LC>      <!-- Matter of Delivery -->
    <LC name="R1C16"></LC>                 <!-- Temp.CO -->
    <LC name="R1C17"></LC>                 <!-- CO -->
    <LC name="R1C18"></LC>                 <!-- Delivery -->
    <LC name="R1C19"></LC>                 <!-- Reference Order -->
  </LR>
</LRows>
```

### Column Index Map
| Index | Field | Description |
|-------|-------|-------------|
| C0 | DTHID | TO Number |
| C1 | DTHMAIN | Main TO |
| C2 | DTHLINES | Lines |
| C3 | DTHSTAT | Status (code + description) |
| C4 | DTDSRV | Delivery Service |
| C5 | DTHSNAC1 | Responsible |
| C6 | DTHSNSB | Sending Site |
| C7 | DTHTRAC | Receiver |
| C8 | DTHTRSB | Receiving Site |
| C9 | PARM | Rec. country (ISO) |
| C10 | FTA | FTA |
| C11 | DTHCUNO | Customer No. |
| C12 | DTHDSC | Remark |
| C13 | DTHRGDT1 | Creation Date |
| C14 | DTHDATE | Arrived at logistics |
| C15 | DTMAOD | Matter of Delivery |
| C16 | DTORNO | Temp.CO |
| C17 | EVORNR | CO |
| C18 | DTHDLIX | Delivery |
| C19 | DTHORNO | Reference Order |

---

## Connection Method 5: Playwright (Browser Automation)

**Status: WORKING — confirmed TO data retrieval, production-ready**

### Flow
1. Launch Edge (`channel="msedge"`, headed mode for SSO)
2. Navigate to `https://pfde-m3-auth.eu.p-f.biz/mne/`
3. Wait for portal to load (SSO happens automatically)
4. Open Search and Start: click `#cmdText`, type program name, click OK
5. Click result link (e.g., "Transport Orders")
6. XDRX800 loads in iframe (`pfeash5live.pepperl-fuchs.com`)
7. Fill fields in iframe: `input[name="DTHSNAC"]`, `input[name="DTHID"]`, etc.
8. Press Enter → data loads via XHR to `generic.do`
9. Capture XHR responses (XML with `<LR>` rows)

### Requirements
```
pip install playwright
python -m playwright install chromium
```

### Key Notes
- Headless mode does NOT work for ADFS SSO — must use headed (`headless=False`)
- `Ctrl+R` for search dialog doesn't work in Playwright (triggers browser refresh) — click `#cmdText` directly
- XDRX800 runs in an iframe — use `page.frames` to find it
- Response listener: don't call `r.text()` on redirect responses (causes crash)
- XHR capture: filter for `"generic.do" in response.url`

### Production Pattern
```python
# Safe XHR capture (handles redirects)
def on_response(resp):
    if "generic.do" in resp.url:
        try:
            body = resp.text()
            if "<LR" in body:
                captured.append(body)
        except:
            pass  # Skip redirect responses
page.on("response", on_response)
```

---

## Connection Method 6: EDM Oracle Direct

**Status: WORKING (from previous ALMA-T sessions)**

```python
import oracledb
conn = oracledb.connect(dsn="sgp01.sg.pepperl-fuchs.com:1521/...", ...)
```
- Schema: `ADMEDP`
- Key table: `EDM_REFERENCES` (PRSG, PT, RELEASESTATE)
- Auth: External (OS authentication)

---

## Summary: Which Method for Which Task

| Task | Best Method | Why |
|------|-------------|-----|
| TO status (XDRX800) | **Playwright** | Only method that works — MNE-only program |
| MO status | **ODBC** (`MPDHED_AP`) | Direct SQL, fast |
| Item data | **ODBC** (`MITMAS_AP`) or **REST API** (`MMS200MI`) | Both work |
| Delivery status | **REST API** (`MWS410MI/GetHead`) | Uses DLIX |
| Customer data | **REST API** (`CRS610MI`) | |
| EDM documents | **ODBC** to `ADMEDP` | Direct Oracle |
| Generic table read | **ODBC** to `PFODS` | If table exists in ODS |
| Custom program data | **Playwright** | For any MNE-only program |

---

## Python Dependencies

```
# ODBC
pyodbc

# REST API (Kerberos auth)
requests
requests-kerberos

# Browser automation
playwright

# Cookie decryption (if ever needed)
pywin32
pycryptodome

# DO NOT USE (buggy):
# requests_negotiate_sspi — TypeError on ADFS redirect
```

---

## Environment

- Company laptop: `C:\Users\tmoghanan\Documents\AI\expressops-auto`
- Python: `C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe`
- ODBC DSN: `ODSSG` (registered on company laptop)
- M3 User: `TMOGHANAN`
- Domain: `AP`
- Company: `1`
- Division: `100`
- Facility: `MF1`
- Warehouse: `MF1`
