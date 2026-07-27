# MR Status Report — setting up a second (backup) runner

This sets up the **MR Status Report** on a second person's laptop so it publishes
even when the primary runner is on leave or their run fails.

Both laptops publish to the **same Confluence page (560866215)**. That is
intentional and safe: each run reads the page, merges it with fresh Jira/EDM
data, and republishes. The later run simply refreshes what the earlier one wrote.

> **Stagger the times.** Primary runs at **10:00**, backup at **10:10**. Keep at
> least 10 minutes between them so the two runs never overlap on the same page.

You need about 20 minutes. Nothing here requires admin rights except, on some
machines, registering the scheduled task.

---

## What you need first

| | |
|---|---|
| **Python 3.12** | Installed for your user, typically `%LOCALAPPDATA%\Programs\Python\Python312`. Any 3.11–3.13 works. |
| **Network access** | To `pfjira.pepperl-fuchs.com`, `pfteamspace.pepperl-fuchs.com`, and the EDM Oracle host — same access the primary runner has. |
| **Your own JIRA PAT** | Read access. Do not reuse anyone else's. |
| **Your own Confluence PAT** | **Write** access to page 560866215. |
| **Oracle client** | Needed for EDM. If `oracledb` thick mode fails, ask the primary runner which client they have installed. |

You do **not** need git — it is not installed on these laptops and is not used.

---

## 1. Download the code

Open PowerShell and run this. It downloads the repo into
`Documents\AI\expressops-auto` under your own profile.

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -c "import io,os,urllib.request,zipfile;d=os.path.join(os.path.expanduser('~'),'Documents','AI');os.makedirs(d,exist_ok=True);r=urllib.request.urlopen(urllib.request.Request('https://github.com/neshnix89/expressops-auto/archive/refs/heads/main.zip',headers={'User-Agent':'expressops-sync'}),timeout=180);zipfile.ZipFile(io.BytesIO(r.read())).extractall(d);os.replace(os.path.join(d,'expressops-auto-main'),os.path.join(d,'expressops-auto'));print('OK ->',os.path.join(d,'expressops-auto'))"
```

It must print `OK -> C:\Users\<you>\Documents\AI\expressops-auto`.

*Why Python and not `Invoke-WebRequest`?* Python's `urllib` picks up the
corporate proxy from the Windows registry automatically. PowerShell's web client
does not, and fails with "Unable to connect to the remote server".

From now on, updates are just:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" "$env:USERPROFILE\Documents\AI\expressops-auto\scripts\sync_from_github.py"
```

The scheduled job does this itself before every run, so you normally never have
to.

---

## 2. Install the Python packages

```powershell
cd "$env:USERPROFILE\Documents\AI\expressops-auto"; if ($?) { & "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pip install -r requirements.txt }
```

---

## 3. Create your config

`config.yaml` holds credentials and is **never** in the repo. Copy the template:

```powershell
Copy-Item "$env:USERPROFILE\Documents\AI\expressops-auto\config\config.example.yaml" "$env:USERPROFILE\Documents\AI\expressops-auto\config\config.yaml"; notepad "$env:USERPROFILE\Documents\AI\expressops-auto\config\config.yaml"
```

Fill in:

- `mode:` → `live`
- `jira.pat` → your JIRA PAT
- `confluence.pat` → your Confluence PAT
- `edm.python_exe` → **leave empty**, step 4 fills it in
- `pages.mr_status_report` → leave as `560866215`

Leave the Tableau / Anthropic / M3 entries alone — the MR report does not use
them.

> **Never commit `config.yaml`,** and never paste your PATs into chat, email or
> a ticket. It is gitignored, so `sync_from_github.py` will not overwrite it.

---

## 4. Set up EDM access

EDM/Oracle refuses connections from a plain `python.exe` (there is a logon
trigger). The workaround is a renamed copy called `EDMAdmin.exe`. This creates it
and writes the path into your config:

```powershell
cd "$env:USERPROFILE\Documents\AI\expressops-auto"; if ($?) { & "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" scripts\setup_edmadmin.py }
```

It must report that it copied `python.exe -> ...\Python312\EDMAdmin.exe` and
updated the config.

> **It has to live inside the Python install directory**, beside
> `python3xx.dll`. A copy in your home folder dies in the Windows loader with
> exit code `0xC0000135` **producing no output at all** — no error, no
> traceback. If EDM ever goes quiet, check this first.
>
> Never run `EDMAdmin.exe` directly from a shell. Always run plain `python.exe`;
> the code spawns `EDMAdmin.exe` itself when it needs Oracle.

---

## 5. Test without publishing

```powershell
& "$env:USERPROFILE\Documents\AI\expressops-auto\run_mr_report.bat"
```

This reads live Jira, EDM and Confluence, builds the page, and **does not
publish**. It opens `logs\mr_status_report.log` at the end.

Check for:

```
Confluence: NN active rows | NNN completed | ... | page vNNN
EDM: Looking up NN PT numbers via EDMAdmin.exe...
EDM: N PRSG links (N released)
DRY-RUN: would publish ...
```

If `Confluence:` shows `0 active rows | 0 completed`, your Confluence PAT is
probably wrong — **stop and fix it before publishing.** The report will refuse to
publish on a failed read (that guard exists because a silent read failure once
wiped the page), but a bad token is worth fixing rather than working around.

---

## 6. Publish once, manually

Only after step 5 looks right:

```powershell
& "$env:USERPROFILE\Documents\AI\expressops-auto\publish_mr_report.bat"
```

Open page 560866215 and confirm it looks correct and `Last Updated` is now.

---

## 7. Schedule it for 10:10

```powershell
& "$env:USERPROFILE\Documents\AI\expressops-auto\setup_mr_schedule.bat" 10:10
```

This registers a daily task named `MR_Status_Report` pointing at
`scheduled_mr_publish.bat` **in your own folder**. In Task Scheduler, tick **Run
whether user is logged on or not** so it still runs when you are away.

Each run appends to `logs\mr_scheduled.log`.

---

## How the two runs interact

Nothing needs coordinating. The page is the source of truth: every run reads it,
preserves the manual columns (MR Status, Remarks, both tick-boxes and the
COMPLETED MR history), merges in fresh Jira/EDM data, and republishes.

- If the 10:00 run works, the 10:10 run just refreshes it.
- If the 10:00 run fails or nobody is in, the 10:10 run produces the report.
- If either run cannot read the page, **it refuses to publish** rather than
  writing a page rebuilt from nothing.

That last guard matters. On 2026-07-23 a transient network error was swallowed,
the page was rebuilt from nothing, and 28 completed containers were dumped back
into the active list with their closing notes erased. See
`tasks/mr_status_report/TASK.md` under "Publish guards" — do not remove them, and
never put `--allow-stale-page` on a scheduled run.

---

## If something breaks

| Symptom | Cause |
|---|---|
| `Python was not found` | Python is not on PATH — that is normal here. Use the full `...\Python312\python.exe` path, as every command above does. |
| Command produces **no output**, exit `-1073741515` | `0xC0000135`, a missing DLL — you ran `EDMAdmin.exe` directly. Run plain `python.exe` instead. |
| `Unable to connect to the remote server` on download | PowerShell's web client ignores the proxy. Use the Python command in step 1. |
| `Confluence: 0 active rows \| 0 completed` | Bad or expired Confluence PAT. |
| `Refusing to PUBLISH` | The page read failed. Re-run once the network is back. Do not pass `--allow-stale-page`. |
| `EDM unavailable` | `edm.python_exe` is wrong or `EDMAdmin.exe` is missing. Re-run step 4. |
| A `.bat` gets quarantined | Endpoint security flags newly saved batch files. Ask the primary runner — do not create new ones. |

Full behaviour spec: `tasks/mr_status_report/TASK.md`.
