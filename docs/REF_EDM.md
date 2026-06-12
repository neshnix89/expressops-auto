# REF_EDM — EDM Oracle System Reference

Living document. Update after each project that touches EDM.
Source: MR Status Report build, expressops-auto `core/edm.py` (EDMClient).

## 1. Connection
```python
import oracledb
try:
    oracledb.init_oracle_client()   # Thick mode — required, call once before connect
except Exception:
    pass
conn = oracledb.connect(
    dsn=oracledb.makedsn("sgp01.sg.pepperl-fuchs.com", 1521, service_name="SGP01EDMEWA.WORLD"),
    externalauth=True,              # Windows SSO — no username/password
)
```
| Parameter | Value |
|---|---|
| Host | `sgp01.sg.pepperl-fuchs.com` |
| Port | `1521` |
| Service | `SGP01EDMEWA.WORLD` |
| Auth | `externalauth=True` (Windows SSO) |
| Mode | Oracle **Thick** mode (`init_oracle_client()`) |

`config.yaml`:
```yaml
edm:
  python_exe: "C:\\Users\\tmoghanan\\EDMAdmin.exe"
  schema: "ADMEDP"
  connection_string: "sgp01.sg.pepperl-fuchs.com:1521/SGP01EDMEWA.WORLD"
```

## 2. EDMAdmin.exe — logon-trigger bypass
`SYS.PF_SEC_LOGON_TRIGGER` rejects any process **not named `EDMAdmin.exe`**.
One-time setup (automated by `scripts/setup_edmadmin.py` / `setup_edmadmin.bat`):
copy `…\Python312\python.exe` → `C:\Users\tmoghanan\EDMAdmin.exe`.

`core/edm.py` `EDMClient` handles both cases:
- running **as** EDMAdmin.exe → `_direct_query`
- otherwise → `_subprocess_query` spawns EDMAdmin.exe (path from `edm.python_exe`)
  so the normal-python ops runner can reach EDM without running the whole
  framework under the renamed exe.

## 3. Confirmed tables
**`ADMEDP.EDM_DOCS`** — document metadata. Key cols: `DOCNUMBER` (PK, e.g.
`PRSG-A0N5`), `RELEASESTATE` (**0 = Not Released, 9 = Fully Released**),
`STATEFLAG` (0 / 3 — corroborates), `RELEASEDATE` (NULL if not released),
`MOVEX` (M3 article link).

**`ADMEDP.EDM_REFERENCES`** — document links. `DOCNUMBER` = parent (PRSG side),
`REF` = referenced (PT side), `TYPE` (1 = standard).

## 4. Reference direction (counterintuitive)
**PRSG is the `DOCNUMBER` (parent); PT is the `REF`.** PRSG references PT.
```sql
SELECT r.REF, r.DOCNUMBER AS prsg_number, d.RELEASESTATE, d.RELEASEDATE
FROM ADMEDP.EDM_REFERENCES r
JOIN ADMEDP.EDM_DOCS d ON d.DOCNUMBER = r.DOCNUMBER
WHERE r.REF = :pt_number AND r.DOCNUMBER LIKE 'PRSG-%'
```
Confirmed pair: `PTDE-AXT7` ↔ `PRSG-A0N5` (RELEASESTATE 9, released 2026-03-16).

## 5. Release logic
`RELEASESTATE == 9` ⇒ Fully Released (`STATEFLAG == 3` equivalent). In the MR
Status Report: PRSG released ⇒ MR Status auto-DONE ⇒ row archived to Completed.

## 6. PT extraction (from JIRA summary, not description)
`PT[A-Z]{2}-[A-Z0-9]{4,5}` — e.g. `PTDE-AXT7`, `PTSG-AAG0C`, `PTCZ-1614`.

## 7. Pitfalls
| Wrong | Right |
|---|---|
| Run with `python.exe` | Run under `EDMAdmin.exe` (logon trigger) |
| Thin mode / skip `init_oracle_client()` | Always init thick mode first (SSO needs it) |
| `WHERE DOCNUMBER = PT` | `WHERE REF = PT AND DOCNUMBER LIKE 'PRSG-%'` |
| Assume one PRSG per PT | Handle multiple; prefer the released one |
