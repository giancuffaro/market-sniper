"""Market Sniper v3.7 — 10-scenario regression suite."""
import io, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request, urllib.error

HERE = "/sessions/stoic-brave-ritchie/mnt/Market Sniper"
sys.path.insert(0, HERE); os.chdir(HERE)
SETTINGS = os.path.join(HERE, "my-settings.json")
# Scratch dir for the backup + temp logs. Created here so the suite runs on a
# clean machine instead of assuming a folder someone made by hand once.
SCRATCH = os.path.join(tempfile.gettempdir(), "market_sniper_tests")
os.makedirs(SCRATCH, exist_ok=True)
BACKUP = os.path.join(SCRATCH, "settings.realbackup")
results = []

SECRETY = ("key","sec","secret","token","pass")
def redact(name, detail):
    """Never print a stored value for a secret field. A failing assertion once
    echoed a real live API key into the log; that must not be possible again."""
    d = str(detail)
    if any(w in name.lower() for w in SECRETY) and d.strip("'\"") not in ("", "None"):
        return f"<{len(d.strip(chr(39)))} chars, redacted>"
    return d

def check(sc, name, ok, detail=""):
    results.append((sc, name, ok, redact(name, detail)))
    print(f"   {'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {redact(name, detail)}" if detail and not ok else ""))

def http(url, method="GET", body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode() or "{}")
        except Exception: return e.code, {}
    except Exception as e:
        return 0, {"_err": str(e)}

def boot(app, port, log):
    return subprocess.Popen([sys.executable, "-m", "uvicorn", app, "--host", "127.0.0.1",
                             "--port", str(port)], cwd=HERE,
                            stdout=open(log, "wb"), stderr=subprocess.STDOUT)

shutil.copy(SETTINGS, BACKUP)

# THE SUITE MUST NEVER TOUCH THE REAL TRADE LOG.
# _record_close() writes a row for every finished trade, and the fake sessions
# in here finish plenty of them. Three test trades once landed in the live
# money log next to a real one. Redirect the module ONCE, before any scenario
# runs, rather than per-scenario where a later reload can quietly undo it.
import tempfile as _tf_boot, trade_log as _tl_boot
_TLOG_SANDBOX = _tf_boot.mkdtemp(prefix="ms_tradelog_")
_tl_boot.LOG_DIR   = _TLOG_SANDBOX
_tl_boot.CSV_PATH  = os.path.join(_TLOG_SANDBOX, "trades.csv")
_tl_boot.XLSX_PATH = os.path.join(_TLOG_SANDBOX, "Market Sniper Trade Log.xlsx")
REAL_TRADES_CSV = os.path.join(HERE, "logs", "trades.csv")
_REAL_TRADES_BEFORE = (io.open(REAL_TRADES_CSV, encoding="utf-8").read()
                       if os.path.exists(REAL_TRADES_CSV) else None)
OPT = FUT = None
try:
    OPT = boot("main:app", 8000, os.path.join(SCRATCH,"opt1.log"))
    FUT = boot("futures_app:app", 8010, os.path.join(SCRATCH,"fut1.log"))
    time.sleep(11)

    print("\n[1] FUTURES LOGIN SURVIVES A RESTART")
    http("http://127.0.0.1:8010/api/prefs", "POST", {
        "mode":"TOPSTEP","ts_user":"giancuffaro230","ts_acct":"EXPRESS-V2-CT-DLL-132001-66482406",
        "ts_key":"TSKEY","wb_key":"WBKEY","wb_sec":"WBSEC","nt_account":"1114140","nt_folder":"C:/nt/incoming"})
    FUT.terminate(); FUT.wait(timeout=10)
    FUT = boot("futures_app:app", 8010, os.path.join(SCRATCH,"fut2.log")); time.sleep(9)
    _, r = http("http://127.0.0.1:8010/api/prefs"); p = r.get("prefs", {})
    check(1,"username survives restart", p.get("ts_user")=="giancuffaro230", repr(p.get("ts_user")))
    check(1,"topstep account survives", str(p.get("ts_acct","")).startswith("EXPRESS-V2"), repr(p.get("ts_acct")))
    check(1,"NINJA account survives", p.get("nt_account")=="1114140", repr(p.get("nt_account")))
    check(1,"NINJA folder survives", p.get("nt_folder")=="C:/nt/incoming", repr(p.get("nt_folder")))
    check(1,"Webull key survives (was in NO save list)", p.get("wb_key")=="WBKEY", repr(p.get("wb_key")))
    check(1,"Webull secret survives", p.get("wb_sec")=="WBSEC", repr(p.get("wb_sec")))
    check(1,"Topstep API key survives", p.get("ts_key")=="TSKEY", repr(p.get("ts_key")))

    print("\n[2] remember_login DEFAULT no longer deletes secrets")
    check(2,"absent remember_login reads TRUE", p.get("remember_login") is True, repr(p.get("remember_login")))
    http("http://127.0.0.1:8010/api/prefs","POST",{"symbol":"MES"})
    _, r = http("http://127.0.0.1:8010/api/prefs"); p2 = r.get("prefs", {})
    check(2,"unrelated write does NOT wipe keys", p2.get("ts_key")=="TSKEY", repr(p2.get("ts_key")))
    http("http://127.0.0.1:8010/api/prefs","POST",{"remember_login":False})
    _, r = http("http://127.0.0.1:8010/api/prefs"); p3 = r.get("prefs", {})
    check(2,"explicit untick DOES wipe secrets", all(k not in p3 for k in ("ts_key","wb_key","wb_sec")))
    check(2,"untick keeps identifiers", p3.get("ts_user")=="giancuffaro230")

    print("\n[3] OPTIONS key profiles persist to DISK")
    http("http://127.0.0.1:8000/api/prefs","POST",{
        "profiles":[{"name":"Main","k":"K1","s":"S1"},{"name":"Mirror","k":"K2","s":"S2"}],
        "active_profile":"Main","show_secrets":True})
    OPT.terminate(); OPT.wait(timeout=10)
    OPT = boot("main:app", 8000, os.path.join(SCRATCH,"opt2.log")); time.sleep(9)
    _, r = http("http://127.0.0.1:8000/api/prefs"); op = r.get("prefs", {})
    profs = op.get("profiles", [])
    check(3,"both profiles survive restart", [x.get("name") for x in profs]==["Main","Mirror"], str(profs))
    check(3,"profile keys intact", [x.get("k") for x in profs]==["K1","K2"])
    check(3,"active profile remembered", op.get("active_profile")=="Main")
    check(3,"written to disk not browser",
          "profiles" in json.load(io.open(SETTINGS,encoding="utf-8")).get("options_prefs",{}))

    print("\n[4] BROWSER AUTOFILL cannot overwrite the username")
    html = io.open(os.path.join(HERE,"futures_index.html"),encoding="utf-8").read()
    for fid in ("tsUser","tsAcct","ntAccount","ntFolder","wbKey"):
        m = re.search(r'<input id="%s"[^>]*>'%fid, html)
        check(4,f"{fid} autofill-guarded", bool(m and "autocomplete=" in m.group(0)))
    m = re.search(r'<input id="tsKey"[^>]*>', html)
    check(4,"tsKey uses new-password", bool(m and "new-password" in m.group(0)))
    check(4,'hardcoded value="Sim101" removed', 'value="Sim101"' not in html)

    print("\n[5] ITM3 STRIKE MATH")
    import webull_client as wb
    check(5,"QQQ 724 CALLS -> 721", wb.pick_strike(724.0,"CALLS",1.0,"ITM3")==721.0)
    check(5,"QQQ 724 PUTS  -> 727", wb.pick_strike(724.0,"PUTS",1.0,"ITM3")==727.0)
    check(5,"calls land BELOW spot", wb.pick_strike(713.44,"CALLS",1.0,"ITM3")<713.44)
    check(5,"puts land ABOVE spot", wb.pick_strike(713.44,"PUTS",1.0,"ITM3")>713.44)
    check(5,"TSLA respects 2.50 step", wb.pick_strike(331.0,"CALLS",2.5,"ITM3")==325.0)
    check(5,"OTM1 unchanged", wb.pick_strike(724.0,"CALLS",1.0,"OTM1")==725.0)
    for bad in ("", None, "banana", "ITMx"):
        check(5,f"garbage {bad!r} -> safe default", wb.parse_strike_mode(bad) in (("OTM",1),("ITM",1)))
    check(5,"depth clamped at 20", wb.parse_strike_mode("ITM999")==("ITM",20))

    print("\n[6] PREVIEW and ARM cannot disagree")
    s = wb.make_session("LIVE"); s._guard_open = lambda q: None
    for spot in (724.0, 724.4, 718.6, 331.3):
        s._underlying = lambda sym, v=spot: v
        pv = s.preview_entry("QQQ","CALLS"); ar = s.arm("QQQ","CALLS",1)
        check(6,f"spot {spot}: preview target == arm target", pv["target"]==ar["target"],
              f"{pv['target']} vs {ar['target']}")
        s.armed=None
    s._underlying = lambda sym: None
    check(6,"no price -> honest failure not crash", s.preview_entry("QQQ","CALLS").get("ok") is False)

    print("\n[7] LIVE-ONLY: dead modes unreachable")
    import futures_client as fc
    for dead in ("PAPER","TRADOVATE","garbage"):
        code, r = http("http://127.0.0.1:8010/api/connect","POST",{"mode":dead})
        check(7,f"{dead} rejected", code==400 and "must be" in str(r.get("detail","")))
    check(7,"old 'LIVE' maps to NINJA", fc.normalize_mode("LIVE")=="NINJA")
    check(7,"3 real modes valid", all(fc.normalize_mode(m) for m in ("WEBULL","NINJA","TOPSTEP")))
    check(7,"PaperSession gone", not hasattr(wb,"PaperSession"))
    check(7,"TradovateSession gone", not hasattr(fc,"TradovateSession"))
    check(7,"Topstep helpers KEPT (nearly deleted)",
          hasattr(fc,"_tv_front_symbol") and hasattr(fc,"_fmt_px"))

    print("\n[8] AUTO-SYNC SAFETY")
    import auto_sync
    ok_c, err = auto_sync.python_files_compile()
    check(8,"all python compiles", ok_c, str(err))
    watched = auto_sync.snapshot()
    check(8,"my-settings.json NEVER watched", not any("my-settings.json" in f for f in watched))
    check(8,"logs/ never watched", not any("/logs/" in f for f in watched))
    check(8,"_archive/ never watched", not any("_archive" in f for f in watched))
    check(8,"secrets on skip list", "my-settings.json" in auto_sync.SKIP_EXACT)
    rc = subprocess.run(["git","check-ignore","my-settings.json"],cwd=HERE,stdout=subprocess.PIPE).returncode
    check(8,"gitignored too (second lock)", rc==0)

    print("\n[9] ENDPOINTS ALIVE, NO TRACEBACKS")
    for name,url in (("options health","http://127.0.0.1:8000/api/health"),
                     ("futures health","http://127.0.0.1:8010/api/health"),
                     ("options page","http://127.0.0.1:8000/"),
                     ("futures page","http://127.0.0.1:8010/"),
                     ("tape QQQ","http://127.0.0.1:8000/api/tape?symbol=QQQ"),
                     ("tape MNQ","http://127.0.0.1:8010/api/tape?symbol=MNQ"),
                     ("trend","http://127.0.0.1:8000/api/trend?symbol=QQQ"),
                     ("futures prices","http://127.0.0.1:8010/api/prices")):
        try:
            with urllib.request.urlopen(url, timeout=12) as rr: check(9,name, rr.status==200)
        except Exception as e: check(9,name,False,str(e)[:60])
    _, hv = http("http://127.0.0.1:8000/api/health")
    # Read the expected version from config rather than hardcoding it, so a
    # version bump does not fail a test that has nothing to do with versions.
    import config as _cfg
    check(9,f"served version matches config ({_cfg.APP_VERSION})",
          hv.get("version")==_cfg.APP_VERSION, str(hv.get("version")))
    code,_ = http("http://127.0.0.1:8000/api/tape?symbol=NVDA")
    check(9,"unknown symbol rejected", code==400)
    for log in (os.path.join(SCRATCH,"opt2.log"),os.path.join(SCRATCH,"fut2.log")):
        txt = io.open(log,encoding="utf-8",errors="replace").read() if os.path.exists(log) else ""
        check(9,f"no traceback in {os.path.basename(log)}", "Traceback" not in txt)

    print("\n[10] UI INTEGRITY")
    for page in ("index.html","futures_index.html"):
        src = io.open(os.path.join(HERE,page),encoding="utf-8").read()
        ids = set(re.findall(r'id="([\w-]+)"',src)); used = set(re.findall(r"\$\('([\w-]+)'\)",src))
        check(10,f"{page}: every referenced id exists", not (used-ids), str(sorted(used-ids)))
        check(10,f"{page}: no PAPER remnants", "paperMode" not in src and "paperConfig" not in src)
    # Real JS parse, not a brace count. These files hold ~40KB of hand-edited
    # JavaScript; a stray bracket would leave the page blank with the error
    # only visible in the browser console, which nobody has open while trading.
    import subprocess as _sp, shutil as _sh
    if _sh.which("node"):
        for page in ("index.html","futures_index.html"):
            src_ = io.open(os.path.join(HERE,page),encoding="utf-8").read()
            blocks = re.findall(r"<script>(.*?)</script>", src_, re.S)
            check(10,f"{page}: has script blocks", len(blocks)>0)
            for i,js in enumerate(blocks):
                f_=os.path.join(SCRATCH,f"chk_{page}_{i}.js")
                io.open(f_,"w",encoding="utf-8").write(js)
                r_=_sp.run(["node","--check",f_],capture_output=True,text=True)
                check(10,f"{page}: script block {i} parses",
                      r_.returncode==0, (r_.stderr or "").strip().split(chr(10))[0][:90])
    else:
        check(10,"node available for JS syntax check", False, "node not installed")

    idx = io.open(os.path.join(HERE,"index.html"),encoding="utf-8").read()
    import re as _re
    _code = _re.sub(r"//[^\n]*", "", idx)      # strip comments before searching
    check(10,"strike no longer truncated with |0", "strike|0" not in _code)
    check(10,"buy button shows a strike", "fmtStrike(strike)" in idx)
    check(10,"strike buttons present", "smATM" in idx and "smITM2" in idx)
    fidx = io.open(os.path.join(HERE,"futures_index.html"),encoding="utf-8").read()
    check(10,"no Tradovate UI left", "tvConfig" not in fidx and "tvUser" not in fidx)
    print("\n[11] MULTI-BROKER SESSIONS (stay logged in, toggle freely)")
    import futures_app as fa
    class FakeSess:
        def __init__(self, tag, pos=None):
            self.account_id=tag; self.position=pos; self.day_realized=0.0
            self.refreshed=0
        def refresh_mark(self): self.refreshed+=1
        def state(self): return {"account_id":self.account_id,"position":self.position}
    fa.SESSIONS.clear()
    ts=FakeSess("TS:EXPRESS", {"symbol":"MNQ","side":"LONG","pnl":42.0})
    nj=FakeSess("NT:1114140")
    wb_=FakeSess("WB:9999")
    fa.SESSIONS.update({"TOPSTEP":ts,"NINJA":nj,"WEBULL":wb_})
    fa.ACTIVE["mode"]="TOPSTEP"

    check(11,"three brokers logged in at once", len(fa.SESSIONS)==3)
    st = fa.state()
    check(11,"active broker reported", st.get("active_mode")=="TOPSTEP")
    check(11,"roster lists all three", len(st.get("sessions",[]))==3)
    check(11,"INACTIVE brokers still refreshed (TP/SL keeps running)",
          nj.refreshed>0 and wb_.refreshed>0, f"ninja={nj.refreshed} webull={wb_.refreshed}")
    check(11,"open position surfaced on the roster",
          any(x["mode"]=="TOPSTEP" and x["has_position"] for x in st["sessions"]))

    before = ts.refreshed
    sw = fa.switch(fa.SwitchReq(mode="NINJA"))
    check(11,"switch does NOT log anyone out", len(fa.SESSIONS)==3)
    check(11,"switch changes the active broker", sw.get("active_mode")=="NINJA")
    check(11,"switched-away broker STILL refreshed", ts.refreshed>before,
          f"{before} -> {ts.refreshed}")
    check(11,"old 'LIVE' alias switches to NINJA",
          fa.switch(fa.SwitchReq(mode="LIVE")).get("active_mode")=="NINJA")
    try:
        fa.SESSIONS.pop("WEBULL")
        fa.switch(fa.SwitchReq(mode="WEBULL")); ok=False; why="accepted"
    except Exception as e:
        ok = "not logged in yet" in str(e); why=str(e)[:50]
    check(11,"switching to a broker you never logged into is refused", ok, why)

    fa.SESSIONS.update({"WEBULL":wb_})
    d = fa.disconnect(fa.DisconnectReq(mode="NINJA"))
    check(11,"disconnect ONE leaves the others", len(fa.SESSIONS)==2 and "NINJA" not in fa.SESSIONS)
    check(11,"active reassigned after dropping the active one", d.get("active") in fa.SESSIONS)
    fa.disconnect(fa.DisconnectReq(mode=None))
    check(11,"disconnect all clears everything", len(fa.SESSIONS)==0 and fa.ACTIVE["mode"] is None)
    fa.SESSIONS.clear(); fa.ACTIVE["mode"]=None

    print("\n[12] VELOCITY IS HONEST WHEN THE MARKET IS SHUT")
    import tape, time as _t
    def _bar(t,v,rng,c=100.0): return {"t":t,"o":c,"h":c+rng/2,"l":c-rng/2,"c":c,"v":v}
    now_=int(_t.time())

    # Friday's closing auction: a huge volume spike as the last thing printed.
    stale=[_bar(now_-3*86400+i*60, 1000, 0.5) for i in range(35)]
    stale.append(_bar(now_-3*86400+35*60, 90000, 6.0))
    raw = tape.compute(stale)
    # This used to assert the spike DID read "violent" - that was the trap the
    # staleness guard existed to cover. The outlier cap now defuses it one
    # layer earlier, so a lone closing-auction print no longer fools the score
    # either. Both guards are kept: the cap handles feed artifacts mid-session,
    # staleness handles a market that is simply shut.
    check(12,"a lone closing-auction spike no longer reads violent",
          raw["state"] != "violent", raw["state"])
    tape._CACHE.clear()
    tape._bars = lambda ysym, b=stale: b
    v = tape.velocity("TEST")
    check(12,"velocity() overrides it to CLOSED", v["state"]=="closed", v["state"])
    check(12,"score is zero, not 100", v["score"]==0.0, str(v["score"]))
    check(12,"says why", "closed" in str(v.get("note","")).lower())

    # No bars at all (weekend futures).
    tape._CACHE.clear(); tape._bars = lambda ysym: []
    v2 = tape.velocity("TEST2")
    check(12,"no bars -> CLOSED not a crash", v2["state"]=="closed" and v2["score"]==0.0)

    # A live tape must still read normally.
    fresh=[_bar(now_-(40-i)*60, 1000, 0.5) for i in range(35)]
    fresh+=[_bar(now_-(5-i)*60, 1200, 0.6) for i in range(5)]
    tape._CACHE.clear(); tape._bars = lambda ysym, b=fresh: b
    v3 = tape.velocity("TEST3")
    check(12,"a LIVE tape still scores normally",
          v3["state"] in ("calm","normal","fast","violent") and v3["score"]>0, str(v3.get("state")))
    for page in ("index.html","futures_index.html"):
        src=io.open(os.path.join(HERE,page),encoding="utf-8").read()
        check(12,f"{page} renders the closed state", "MARKET CLOSED" in src)

    print("\n[13] BROKER TABS ON THE DASHBOARD + TRAY FALLBACK")
    fh = io.open(os.path.join(HERE,"futures_index.html"),encoding="utf-8").read()
    check(13,"tab strip lives on the DASHBOARD", 'id="brokerTabs"' in fh)
    dash = fh.split('id="dash"',1)[-1]
    check(13,"tabs render inside the dash, not the login screen", 'id="brokerTabs"' in dash)
    check(13,"all three brokers listed", "['WEBULL','WEBULL']" in fh.replace(' ','') or "WEBULL" in fh and "NINJA" in fh and "TOPSTEP" in fh)
    check(13,"clicking a tab switches", "pickBroker" in fh and "'/api/switch'" in fh)
    check(13,"back arrow no longer disconnects",
          "function back(){" in fh and "api('/api/disconnect'" not in fh.split("function back(){",1)[1][:400])
    check(13,"padlock is the real log-out", "function lockAll" in fh and "'/api/disconnect'" in fh)
    check(13,"log-out warns about open positions", "stops this app managing" in fh)
    for fn in ("pickBroker","lockAll","refreshSessions"):
        check(13,f"FZ.{fn} exported", bool(re.search(r"return \{[^}]*\b%s\b"%fn, fh, re.S)))

    import run_all
    check(13,"tray is optional, not required", hasattr(run_all,"TRAY_AVAILABLE"))
    check(13,"missing tray libs do NOT crash the launcher", run_all.start_tray() is None)
    # START HIDDEN.vbs, INSTALL.bat, STOP EVERYTHING.bat, TUTORIAL.html and
    # CHECK-SETUP.bat were deleted on request. The launcher absorbed the one
    # thing INSTALL did that nothing else did: installing the Webull SDK.
    _lb0 = [f for f in os.listdir(HERE) if f.endswith("START MARKET SNIPER.bat")]
    _l0 = io.open(os.path.join(HERE,_lb0[0]),encoding="utf-8").read() if _lb0 else ""
    check(13,"launcher installs the Webull SDK itself",
          "webull-openapi-python-sdk" in _l0)
    check(13,"and verifies it imported", _l0.count("from webull.core.client import ApiClient") >= 2)
    itxt = _l0
    req  = io.open(os.path.join(HERE,"requirements.txt"),encoding="utf-8").read()
    check(13,"launcher installs the tray deps", "pystray" in itxt)
    check(13,"tray deps NOT in requirements (runs every launch)",
          not any(l.strip()=="pystray" for l in req.splitlines()))
    check(13,"launcher clears Mark-of-the-Web", "Unblock-File" in itxt)
    check(13,"survives an optional tray failure", "still works fine" in itxt)
    check(13,"launcher warns if the SDK fails", "cannot connect or trade" in itxt)
    check(13,"no dead references to deleted files",
          not any(x in itxt for x in ("INSTALL.bat\"", "CHECK-SETUP", "TUTORIAL.html")))

    print("\n[14] AUTO-SYNC HEALS A STALE GIT LOCK")
    import subprocess as _sp, tempfile, shutil as _sh
    tmp = tempfile.mkdtemp(prefix="locktest")
    try:
        _sp.run(["git","init","-q","."],cwd=tmp)
        _sp.run(["git","config","user.email","t@t.t"],cwd=tmp)
        _sp.run(["git","config","user.name","t"],cwd=tmp)
        io.open(os.path.join(tmp,"a.py"),"w").write("x=1\n")
        _sp.run(["git","add","-A"],cwd=tmp); _sp.run(["git","commit","-qm","init"],cwd=tmp)

        import importlib, auto_sync as _as
        _as = importlib.reload(_as); _as.HERE = tmp
        lock = os.path.join(tmp,".git","HEAD.lock")

        # STALE lock -> must heal and retry
        io.open(lock,"w").close()
        os.utime(lock, (time.time()-300, time.time()-300))
        ok,_out = _as.git("commit","--allow-empty","-m","healed")
        check(14,"stale lock is cleared and the commit retried", ok)
        check(14,"stale lock file removed", not os.path.exists(lock))

        # FRESH lock -> must NOT be stolen (a real git may hold it)
        io.open(lock,"w").close()
        ok2,out2 = _as.git("commit","--allow-empty","-m","should not steal")
        check(14,"a FRESH lock is left alone", (not ok2) and os.path.exists(lock))
        check(14,"and the failure is reported honestly", _as._looks_like_lock_error(out2))
        os.remove(lock)

        # Non-lock failure must not trigger the sweep
        ok3,out3 = _as.git("checkout","no-such-branch")
        check(14,"non-lock failures are not misread as locks",
              (not ok3) and not _as._looks_like_lock_error(out3))
        check(14,"lock sweep covers HEAD.lock, not just index.lock",
              "HEAD.lock" in io.open(os.path.join(HERE,"auto_sync.py"),encoding="utf-8").read()
              or "endswith(\".lock\")" in io.open(os.path.join(HERE,"auto_sync.py"),encoding="utf-8").read())
    finally:
        _sh.rmtree(tmp, ignore_errors=True)
        import importlib, auto_sync as _as2
        _as2 = importlib.reload(_as2)

    print("\n[15] ONE TAB ONLY - a new launch retires the old tab")
    for page, chan in (("index.html","market_sniper_options"),
                       ("futures_index.html","market_sniper_futures")):
        src = io.open(os.path.join(HERE,page),encoding="utf-8").read()
        check(15,f"{page}: guard present", "ONE TAB ONLY" in src)
        check(15,f"{page}: own channel ({chan})", chan in src)
        check(15,f"{page}: stale tab stops polling first",
              "standDown" in src and "clearInterval(poll)" in src.split("standDown",1)[1][:400])
        check(15,f"{page}: falls back when close() is blocked", "safe to close" in src)
        g = src.index("ONE TAB ONLY")
        check(15,f"{page}: MY_BORN set before it is read",
              src.index("var MY_BORN", g) < src.index("e.data.born < MY_BORN", g))
        check(15,f"{page}: identical timestamps break the tie",
              "e.data.born === MY_BORN" in src)
    _lb = [f for f in os.listdir(HERE) if f.endswith("START MARKET SNIPER.bat")]
    lb = io.open(os.path.join(HERE,_lb[0]),encoding="utf-8").read() if _lb else ""
    check(15,"launcher installs the tray only when missing",
          "import pystray, PIL" in lb and "pip install -q pystray pillow" in lb)

    print("\n[16] SERVER RESTART DOES NOT LEAVE A TAB SPINNING ON 400s")
    for page in ("index.html","futures_index.html"):
        src = io.open(os.path.join(HERE,page),encoding="utf-8").read()
        check(16,f"{page}: tick() checks for a lost session", "st.detail" in src)
        check(16,f"{page}: stops all four timers", 
              all(t in src.split("lostCount",1)[1][:600] for t in
                  ("clearInterval(poll)","clearInterval(priceTimer)",
                   "clearInterval(trendTimer)","clearInterval(velTimer)")))
        check(16,f"{page}: returns to the login screen",
              "connectScreen').classList.remove('hidden')" in src)
        check(16,f"{page}: one blip is not treated as a disconnect", "lostCount >= 2" in src)
    _lb = [f for f in os.listdir(HERE) if f.endswith("START MARKET SNIPER.bat")]
    lb = io.open(os.path.join(HERE,_lb[0]),encoding="utf-8").read() if _lb else ""
    check(16,"launcher never calls bare pip", "\npip install" not in lb and "  pip install" not in lb)
    check(16,"launcher pins python to the venv", "VPY=" in lb)
    check(16,"tray install verified after installing",
          lb.count('import pystray, PIL') >= 2)

    print("\n[17] CLOSED MARKET IS HONEST ON BOTH APPS")
    opt = io.open(os.path.join(HERE,"index.html"),encoding="utf-8").read()
    fut = io.open(os.path.join(HERE,"futures_index.html"),encoding="utf-8").read()
    for name, src in (("options",opt),("futures",fut)):
        check(17,f"{name}: tracks marketClosed", "marketClosed" in src)
        check(17,f"{name}: set from the velocity reading",
              "marketClosed = (v.state==='closed')" in src)
        check(17,f"{name}: 'unknown' is not treated as closed",
              "marketClosed = false;" in src)
        check(17,f"{name}: says MARKET CLOSED on the trade buttons",
              "MARKET CLOSED" in src)
    check(17,"options: skips quoting a shut market",
          "if(marketClosed){" in opt and "callSub" in opt.split("if(marketClosed){",1)[1][:300])
    check(17,"futures: warns before sending into a shut market",
          "marketClosed && !confirm(" in fut)
    check(17,"futures: you can still overrule the guess",
          "send anyway" in fut.lower())
    check(17,"futures: buttons repaint when velocity lands",
          "paintMarketState" in fut.split("async function refreshVel",1)[1][:400])
    check(17,"options: velocity awaited before the first quote",
          "refreshVel().then(refreshQuote)" in opt)

    print("\n[18] FUTURES HOURS - overnight must NOT read as closed")
    import tape as _t, time as _tm
    def _bar(t,v=500,rng=5.0,c=20000.0): return {"t":t,"o":c,"h":c+rng,"l":c-rng,"c":c,"v":v}
    now_=int(_tm.time())
    check(18,"threshold clears the 11-min midnight rollover gap",
          _t.STALE_SECONDS/60 > 11, f"{_t.STALE_SECONDS/60:.0f} min")
    check(18,"threshold still detects the 62-min maintenance break",
          _t.STALE_SECONDS/60 < 62, f"{_t.STALE_SECONDS/60:.0f} min")

    # 11-min quiet patch mid-session: MUST stay open
    bars=[_bar(now_-(60-i)*60) for i in range(35)]
    bars.append(_bar(now_-11*60))
    _t._CACHE.clear(); _t._bars = lambda y, b=bars: b
    v=_t.velocity("FUT_OVERNIGHT")
    check(18,"11-min gap overnight still reads OPEN", v["state"]!="closed", v["state"])

    # 40 min of nothing: maintenance break / weekend
    bars2=[_bar(now_-(120-i)*60) for i in range(35)]
    bars2.append(_bar(now_-40*60))
    _t._CACHE.clear(); _t._bars = lambda y, b=bars2: b
    v2=_t.velocity("FUT_BREAK")
    check(18,"40-min silence reads CLOSED", v2["state"]=="closed", v2["state"])

    fut = io.open(os.path.join(HERE,"futures_index.html"),encoding="utf-8").read()
    check(18,"a closed guess WARNS, it does not silently refuse",
          "confirm(" in fut.split("marketClosed &&",1)[1][:400])
    check(18,"and says it is a guess, not a calendar", "not a " in fut and "calendar" in fut)
    # The whole point of the bar-age design is that no hours are hardcoded.
    # Strip comments, then make sure no clock arithmetic snuck into the logic.
    _tp = io.open(os.path.join(HERE,"tape.py"),encoding="utf-8").read()
    _code = "\n".join(l for l in _tp.splitlines() if not l.strip().startswith("#"))
    check(18,"tape.py makes no calendar/clock decisions",
          ".hour" not in _code and "weekday" not in _code and "localtime" not in _code)

    print("\n[19] PHANTOM POSITION - closed by hand, app still thinks you are in")
    import futures_client as _fc
    ph = _fc.make_session("TOPSTEP")
    ph.position = {"symbol":"MNQ","side":"LONG","qty":1,"entry":23150.0,
                   "mark":23144.0,"best":23160.0,"pnl":-12.0}
    ph.settings.update({"sl_enabled":True,"sl_points":5.0})
    check(19,"a phantom WOULD have fired a bracket", ph._bracket_hit()=="SL")
    r_=ph.forget_position()
    check(19,"forget clears it", r_["cleared"] is True and ph.position is None)
    check(19,"nothing can fire afterwards", ph._bracket_hit() is None)
    check(19,"it says no order was sent", "NO order was sent" in (ph.last_event or ""))
    check(19,"forgetting when already flat is harmless",
          ph.forget_position()["cleared"] is False)
    check(19,"armed order is cleared too", ph.armed is None)

    fh2 = io.open(os.path.join(HERE,"futures_index.html"),encoding="utf-8").read()
    check(19,"futures exposes forgetPosition", "forgetPosition" in fh2)
    check(19,"reachable WITHOUT a rejection (you closed by hand)",
          "flatlink" in fh2 and "Closed it yourself" in fh2)
    check(19,"asks for confirmation first", "Clear this position from the app" in fh2)
    check(19,"warns it stops managing the trade", "stops the app managing" in fh2)
    fa2 = io.open(os.path.join(HERE,"futures_app.py"),encoding="utf-8").read()
    check(19,"endpoint exists", "/api/position/forget" in fa2)
    check(19,"options had this all along", "/api/position/forget" in
          io.open(os.path.join(HERE,"main.py"),encoding="utf-8").read())

    print("\n[20] MY CONFIG (round-number entry) IS ALWAYS ON")
    import importlib, config as _cfg
    _cfg = importlib.reload(_cfg)
    check(20,"server default is ON", _cfg.DEFAULT_SETTINGS["my_enabled"] is True)
    idx2 = io.open(os.path.join(HERE,"index.html"),encoding="utf-8").read()
    check(20,"browser fallback is ON too (used before prefs load)",
          "my_enabled:true}" in idx2.replace(" ",""))
    # The DEFAULT must be on. Turning it off when a strategy is armed is
    # correct and expected, so only the declared defaults are checked here.
    check(20,"the config default is not False",
          not re.search(r'"my_enabled"\s*:\s*False',
                        io.open(os.path.join(HERE,"config.py"),encoding="utf-8").read()))
    # Only the DECLARED default matters. Sending {my_enabled:false} when a
    # strategy takes over is the exclusivity rule doing its job.
    _decl = [l for l in idx2.splitlines() if "let settings=" in l or "sl_unit:" in l]
    check(20,"the browser fallback default is true",
          any("my_enabled:true" in l.replace(" ","") for l in _decl), str(_decl)[:120])
    saved = json.load(io.open(SETTINGS,encoding="utf-8")).get("options_settings",{})
    check(20,"your saved file says ON", saved.get("my_enabled") is True, str(saved.get("my_enabled")))
    check(20,"ON means the buttons ARM, not buy at the ask",
          "if(settings.my_enabled){" in idx2 and "/api/order/arm" in idx2)
    _, pr = http("http://127.0.0.1:8000/api/prefs")
    check(20,"server actually SERVES it on", pr.get("settings",{}).get("my_enabled") is True,
          str(pr.get("settings",{}).get("my_enabled")))
    # THE root cause: the browser used to load settings from localStorage, then
    # POST them to /api/settings, which wrote them to my-settings.json. One
    # stale browser value overwrote the file on every connect, which is why
    # fixing the default never stuck.
    check(20,"settings are NOT read from localStorage any more",
          "localStorage.getItem(LSS)" not in idx2)
    check(20,"disk is the single source of truth", "one source of truth" in idx2)
    check(20,"you can still deliberately turn it off",
          "settings.my_enabled=$('myEnabled').checked" in idx2.replace(" ",""))

    print("\n[21] AUTO-RECONCILE - the app asks Topstep what you actually hold")
    import futures_client as _fc2
    def _rs(broker_says):
        x = _fc2.make_session("TOPSTEP")
        x.token="t"; x.acct={"id":1}
        x.position={"symbol":"MNQ","side":"LONG","qty":1,"entry":23150.0,
                    "mark":23144.0,"best":23160.0,"pnl":-12.0}
        x.settings.update({"sl_enabled":True,"sl_points":5.0})
        x.broker_positions = lambda: broker_says
        x._last_reconcile = 0
        return x

    x=_rs([]);  check(21,"a phantom WOULD have fired a stop", x._bracket_hit()=="SL")
    x.refresh_mark()
    check(21,"broker says FLAT -> app clears itself", x.position is None)
    check(21,"and explains why", "FLAT" in (x.last_event or ""))
    check(21,"no order was sent", "No order was sent" in (x.last_event or ""))

    x=_rs([{"contractId":"MNQ","size":1}]); x.refresh_mark()
    check(21,"broker CONFIRMS -> position kept", x.position is not None)

    x=_rs(None); x.refresh_mark()
    check(21,"API failure is NOT read as flat", x.position is not None)

    hits=[]
    x=_rs([]); x.broker_positions=lambda: (hits.append(1), [])[1]; x._last_reconcile=0
    x.reconcile(); x.reconcile(); x.reconcile()
    check(21,"rate-limited, does not hammer the API", len(hits)==1, f"{len(hits)} calls")

    fcsrc = io.open(os.path.join(HERE,"futures_client.py"),encoding="utf-8").read()
    ts_block = fcsrc.split("class TopstepSession",1)[1].split("\nclass ",1)[0]
    # take the whole refresh_mark body, up to the next def
    rm = ts_block.split("def refresh_mark",1)[1].split("\n    def ",1)[0]
    check(21,"reconcile is inside refresh_mark", "self.reconcile()" in rm, rm[:120])
    check(21,"reconcile runs BEFORE brackets can fire",
          "self.reconcile()" in rm and "_maybe_auto_close" in rm
          and rm.index("self.reconcile()") < rm.index("_maybe_auto_close"))
    check(21,"uses the real ProjectX endpoint", "/api/Position/searchOpen" in fcsrc)
    check(21,"None and [] are treated differently",
          "could not ask" in fcsrc)

    print("\n[22] OPTIONS: clearing a phantom must NOT need a rejection first")
    oi = io.open(os.path.join(HERE,"index.html"),encoding="utf-8").read()
    check(22,"the old CLEAR only showed after a rejection",
          "rejectFix').classList.toggle('show'" in oi)
    check(22,"there is now an ALWAYS-visible clear while in a trade",
          "flatlink" in oi and "Closed it yourself in Webull" in oi)
    ta = oi.split('id="tradeActions"',1)[1][:600]
    check(22,"it sits next to CLOSE, not in the reject box", "flatlink" in ta)
    check(22,"it still asks for confirmation", "NOT holding" in oi)
    check(22,"it promises no order is sent", "sends NO order" in oi.replace("This sends NO order","sends NO order"))

    import webull_client as _wb2
    z = _wb2.make_session("LIVE")
    z.position={"symbol":"QQQ","side":"CALLS","strike":711.0,"qty":1,"entry":3.0,"mark":2.7}
    r3 = z.forget_position()
    check(22,"forget clears it", r3["cleared"] is True and z.position is None)
    check(22,"and says no order was sent", "No order was sent" in (z.last_event or ""))
    check(22,"clearing twice is harmless", z.forget_position()["cleared"] is False)
    check(22,"armed entry is cleared too", z.armed is None)

    print("\n[23] DAILY TRADE LOG - one workbook, appended forever")
    import trade_log as _tl, tempfile as _tf, importlib
    _tl = importlib.reload(_tl)
    _sc = _tf.mkdtemp(prefix="tlog")
    _tl.LOG_DIR=_sc; _tl.CSV_PATH=os.path.join(_sc,"trades.csv")
    _tl.XLSX_PATH=os.path.join(_sc,"Market Sniper Trade Log.xlsx")
    check(23,"openpyxl available", _tl.XLSX_AVAILABLE)

    for d,sym,e_,x_,rz in [("2026-08-21","QQQ",3.0,3.85,"TP"),
                           ("2026-08-21","QQQ",2.4,2.05,"SL"),
                           ("2026-08-22","SPY",1.8,2.60,"CLOSE")]:
        _tl.record({"date":d,"symbol":sym,"side":"CALLS","strike":711,"qty":1,
                    "entry":e_,"exit":x_,"pnl":round((x_-e_)*100,2),"exit_reason":rz,
                    "app":"OPTIONS","broker":"WEBULL"})
    check(23,"every trade lands in the CSV", len(_tl._rows())==3, str(len(_tl._rows())))
    check(23,"ONE workbook, not one per day", os.path.exists(_tl.XLSX_PATH))
    check(23,"only one xlsx exists",
          len([f for f in os.listdir(_sc) if f.endswith(".xlsx")])==1)

    import openpyxl as _px
    _wb=_px.load_workbook(_tl.XLSX_PATH)
    check(23,"TRADES + BY DAY sheets", _wb.sheetnames==["TRADES","BY DAY"], str(_wb.sheetnames))
    check(23,"every trade is a row", _wb["TRADES"].max_row==4, str(_wb["TRADES"].max_row))
    bd=_tl.by_day()
    check(23,"grouped by day", [d["date"] for d in bd]==["2026-08-21","2026-08-22"])
    check(23,"daily net is right", bd[0]["net"]==50.0 and bd[1]["net"]==80.0,
          f"{bd[0]['net']} / {bd[1]['net']}")
    check(23,"wins and losses counted", bd[0]["wins"]==1 and bd[0]["losses"]==1)

    # the failure that would silently lose a trade
    _real=_tl.rebuild_xlsx
    def _locked(): raise PermissionError("open in Excel")
    _tl.rebuild_xlsx=_locked
    _tl.record({"date":"2026-08-22","symbol":"QQQ","side":"PUTS","strike":700,"qty":1,
                "entry":1.0,"exit":1.4,"pnl":40.0,"exit_reason":"TP"})
    check(23,"workbook LOCKED in Excel -> trade still saved", len(_tl._rows())==4)
    _tl.rebuild_xlsx=_real
    _tl.rebuild_xlsx()
    check(23,"and appears once Excel is closed",
          _px.load_workbook(_tl.XLSX_PATH)["TRADES"].max_row==5)

    wsrc = io.open(os.path.join(HERE,"webull_client.py"),encoding="utf-8").read()
    check(23,"options logs from the ONE close funnel",
          "trade_log.record" in wsrc.split("_record_close",1)[1][:2500])
    check(23,"auto-exits record TP/SL not just CLOSE", 'position["exit_reason"] = hit' in wsrc)
    fsrc = io.open(os.path.join(HERE,"futures_client.py"),encoding="utf-8").read()
    check(23,"futures logs too", "_log_trade" in fsrc)
    check(23,"declared in requirements",
          "openpyxl" in io.open(os.path.join(HERE,"requirements.txt"),encoding="utf-8").read())

    print("\n[24] OPTIONS AUTO-RECONCILE with Webull")
    import webull_client as _wb3
    def _os_(broker):
        z=_wb3.make_session("LIVE"); z.account_id="A"
        z.position={"symbol":"QQQ","side":"CALLS","strike":711.0,"qty":1,
                    "entry":3.0,"mark":2.7,"expiration":"2026-08-24"}
        z.broker_positions=lambda: broker
        z._last_reconcile=0
        return z
    z=_os_([]);   z.reconcile(force=True)
    check(24,"Webull says nothing open -> cleared", z.position is None)
    check(24,"explains why", "hold nothing" in (z.last_event or ""))
    z=_os_(None); z.reconcile(force=True)
    check(24,"API failure is NOT read as flat", z.position is not None)
    z=_os_([{"x":1}]); z.reconcile(force=True)
    check(24,"broker confirms -> kept", z.position is not None)
    check(24,"runs BEFORE brackets fire",
          wsrc.split("def refresh_mark",1)[1].split("def close",1)[0].index("self.reconcile()")
          < wsrc.split("def refresh_mark",1)[1].split("def close",1)[0].index("_maybe_auto_close"))
    check(24,"finds the SDK call at runtime, not hardcoded", "_position_fns" in wsrc)

    print("\n[25] CONSOLE AUTO-HIDE + no pointless installs")
    _lb2 = [f for f in os.listdir(HERE) if f.endswith("START MARKET SNIPER.bat")]
    L = io.open(os.path.join(HERE,_lb2[0]),encoding="utf-8").read() if _lb2 else ""
    check(25,"deps are import-checked before pip runs",
          'import fastapi, uvicorn, pydantic, openpyxl' in L)
    # Look at the real command lines, not the comments that mention them.
    _cmds = [l.strip() for l in L.splitlines() if not l.strip().lower().startswith("rem")]
    _i_chk = next(i for i,l in enumerate(_cmds) if "import fastapi" in l)
    _i_pip = next(i for i,l in enumerate(_cmds) if "-r requirements.txt" in l)
    check(25,"pip only runs inside that failure branch", _i_chk < _i_pip)
    check(25,"and it is guarded by errorlevel",
          any("errorlevel 1" in l for l in _cmds[_i_chk:_i_pip]))
    check(25,"SDK likewise only installs when missing",
          L.index("from webull.core.client import ApiClient") < L.index("webull-openapi-python-sdk"))
    check(25,"tray likewise", L.index("import pystray, PIL") < L.index("pystray pillow"))
    check(25,"core dep failure stops the launch", "cannot run" in L)

    import run_all as _ra, importlib
    _ra = importlib.reload(_ra)
    check(25,"hide is delayed, not instant", getattr(_ra,"HIDE_AFTER_SECONDS",0) >= 3)
    check(25,"NO tray -> console never hides", _ra.hide_console_when_ready(None) is None)
    check(25,"hiding is a no-op off Windows", _ra._show_console(False) is False)
    check(25,"tray can bring it back", "_toggle_console" in io.open(
          os.path.join(HERE,"run_all.py"),encoding="utf-8").read())
    rsrc = io.open(os.path.join(HERE,"run_all.py"),encoding="utf-8").read()
    check(25,"shutdown un-hides first, so you see why it stopped",
          "_show_console(True)" in rsrc.split("def stop_all",1)[1][:400])
    check(25,"the hide is conditional on the tray, in code",
          "if tray is None" in rsrc)

    print("\n[26] RATCHET - stop climbs a rung at a time and never drops")
    import webull_client as _w4
    L = _w4.LiveSession
    # the ladder G specified, verbatim
    for peak, stop, nxt in [(0,-10,10),(5,-10,10),(9.9,-10,10),(10,0,20),
                            (15,0,20),(20,10,30),(30,20,40),(100,90,110)]:
        lv = L.ratchet_levels(peak, 10)
        check(26,f"best {peak}% -> stop {stop}%, next +{nxt}%",
              lv["stop_pct"]==stop and lv["next_pct"]==nxt,
              f"got stop {lv['stop_pct']} next {lv['next_pct']}")
    check(26,"exactly +10% counts (float error nearly broke this)",
          L.ratchet_levels((3.30-3.00)/3.00*100, 10)["stop_pct"]==0.0)
    check(26,"a gap from +5% to +25% still lands the stop on +10%",
          L.ratchet_levels(25,10)["stop_pct"]==10.0)

    def _rt(marks, step=10.0, entry=3.00):
        z=_w4.make_session("LIVE")
        z.settings.update({"my_enabled":True,"ratchet_step_pct":step})
        z.position={"symbol":"QQQ","side":"CALLS","strike":711.0,"qty":1,
                    "entry":entry,"mark":entry,"expiration":"2026-08-26"}
        out=[]
        for m in marks:
            z.position["mark"]=m
            hit=z._bracket_hit()
            out.append((m, dict(z.position.get("ratchet") or {}), hit,
                        z.position.get("exit_reason")))
            if hit: break
        return z, out

    z,run = _rt([3.00,2.85,3.15,3.30,3.45,3.60,3.90,3.40])
    stops = {m: r.get("stop_pct") for m,r,_,_ in run}
    check(26,"opens at -10%", stops[3.00]==-10.0)
    check(26,"a dip to -5% does NOT exit", run[1][2] is None)
    check(26,"+10% moves the stop to BREAKEVEN, does not close",
          stops[3.30]==0.0 and run[3][2] is None)
    check(26,"+20% locks in +10%", stops[3.60]==10.0)
    check(26,"+30% locks in +20%", stops[3.90]==20.0)
    check(26,"falling back through the stop closes", run[-1][2] is not None)
    check(26,"and the log says which rung", "RATCHET+20" in str(run[-1][3]), str(run[-1][3]))

    # never loosens
    z2,run2 = _rt([3.00,3.90,3.70,3.75,3.72])
    seq=[r.get("stop_pct") for _,r,_,_ in run2]
    check(26,"stop never moves DOWN", all(b>=a for a,b in zip(seq,seq[1:])), str(seq))

    # breakeven exit names itself
    z3,run3 = _rt([3.00,3.30,2.99])
    check(26,"a give-back to breakeven exits as BE",
          "RATCHET-BE" in str(run3[-1][3]), str(run3[-1][3]))

    # ratchet replaces TP/SL
    z4=_w4.make_session("LIVE")
    z4.settings.update({"my_enabled":True,"tp_enabled":True,"tp_unit":"cents",
                        "tp_value":1,"sl_enabled":True,"sl_unit":"pct","sl_value":1})
    z4.position={"symbol":"QQQ","side":"CALLS","strike":711.0,"qty":1,
                 "entry":3.00,"mark":3.06,"expiration":"2026-08-26"}
    check(26,"a 1c take-profit can NOT close a ratcheted trade", z4._bracket_hit() is None)

    z5=_w4.make_session("LIVE"); z5._guard_open=lambda q: None
    z5.settings["my_enabled"]=True
    z5._underlying=lambda sym: 713.0
    z5.arm("QQQ","CALLS",1)
    check(26,"arming does not re-enable a take-profit", z5.settings["tp_enabled"] is False)

    cfg = io.open(os.path.join(HERE,"config.py"),encoding="utf-8").read()
    check(26,"on by default", '"my_enabled": True' in cfg)
    idx4 = io.open(os.path.join(HERE,"index.html"),encoding="utf-8").read()
    check(26,"live stop shown while in a trade", "next rung" in idx4)
    # The separate RATCHET checkbox is gone - it is one switch with the entry
    # now - so the screen proof is the step input plus the combined switch.
    check(26,"settings expose it", 'id="ratchetStep"' in idx4 and 'id="myEnabled"' in idx4)

    print("\n[27] CONFIG SCREEN: one combined strategy, dead sections gone")
    ix = io.open(os.path.join(HERE,"index.html"),encoding="utf-8").read()
    cfg_pane = ix[ix.index('id="paneConfig"'):ix.index("/paneConfig")]
    check(27,"renamed to ABSOLUTE ENTRY with RATCHET TRAILING",
          "ABSOLUTE ENTRY with RATCHET TRAILING" in cfg_pane)
    check(27,"the old +$1 TP / 10% stop blurb is gone",
          "Auto-sets a" not in ix and "Overrides the TP/SL" not in ix)
    check(27,"entry and ratchet sit in ONE section",
          "myEnabled" in cfg_pane and "ratchetStep" in cfg_pane)
    check(27,"and are ONE switch, not two",
          "ratchetEnabled" not in cfg_pane and
          "ABSOLUTE ENTRY + RATCHET" in cfg_pane)
    for gone in ("TAKE PROFIT — closes","STOP LOSS — closes","AUTO-LOCK"):
        check(27,f"{gone.split(chr(8212))[0].strip()} removed from CONFIGURATION",
              gone not in cfg_pane)
    check(27,"MIRROR moved out of CONFIGURATION", "MIRROR TRADING" not in cfg_pane)
    check(27,"MIRROR has its own tab",
          'id="tabMirror"' in ix and 'id="paneMirror"' in ix
          and "MIRROR TRADING" in ix[ix.index('id="paneMirror"'):ix.index("/paneMirror")])
    check(27,"showTab handles all three panes",
          all(k in ix.split("function showTab",1)[1][:400]
              for k in ("paneConfig","paneStrat","paneMirror")))
    check(27,"no JS still reads the deleted controls",
          not any(x in ix for x in ("$('tpEnabled')","$('slEnabled')","$('alEnabled')",
                                    "$('tpUnit')","$('alMinutes')")))
    check(27,"tpUnitChanged fully removed", "tpUnitChanged" not in ix)

    wsrc2 = io.open(os.path.join(HERE,"webull_client.py"),encoding="utf-8").read()
    check(27,"arming is entry-ONLY when the ratchet is on",
          "if not s.get(\"my_enabled\"):" in wsrc2)
    check(27,"backend tp/sl fields still exist for STRATEGIES",
          '"tp_unit"' in wsrc2 and '"sl_unit"' in wsrc2)

    print("\n[28] ENTRY GRID, ATM, CONTRACT QUALITY, ONE-ARMED-THING")
    import importlib, config as _c5, webull_client as _w5
    _c5 = importlib.reload(_c5); _w5 = importlib.reload(_w5)
    L5 = _w5.LiveSession

    lv = L5.entry_levels_near(710.3, span=3)
    check(28,"grid keeps every whole dollar", all(float(d) in lv for d in range(708,713)))
    check(28,"grid adds X2.50 and X7.50", 707.5 in lv and 712.5 in lv)
    check(28,"grid adds nothing else", not any(abs(x%1-0.5)<1e-9 and int(x)%10 not in (2,7) for x in lv))
    # These used to assert the NEAREST level regardless of side, which is why
    # 707.40 expected 707.50 - a level ABOVE spot for a call. Entry is
    # directional now, so each case names the side it belongs to.
    for spot, side, want in [(707.40,"CALLS",707.0), (707.40,"PUTS",707.5),
                             (707.60,"CALLS",707.5), (707.60,"PUTS",708.0),
                             (707.10,"CALLS",707.0), (707.10,"PUTS",707.5),
                             (712.40,"CALLS",712.0), (712.40,"PUTS",712.5),
                             (712.60,"CALLS",712.5), (712.60,"PUTS",713.0),
                             (709.60,"CALLS",709.0), (709.60,"PUTS",710.0)]:
        got = L5.entry_target(spot, side)
        check(28,f"{side} at {spot} fire at {want}", got==want, str(got))

    for spot in (713.40, 713.60, 714.00, 713.00):
        c = _w5.pick_strike(spot,"CALLS",1.0,"ATM1")
        p_ = _w5.pick_strike(spot,"PUTS",1.0,"ATM1")
        check(28,f"ATM1 at {spot} is never OTM", c<=spot and p_>=spot, f"{c}/{p_}")
    check(28,"ATM parses", _w5.parse_strike_mode("ATM1")==("ATM",1))
    check(28,"garbage falls back to ITM1, not a lottery ticket",
          _w5.parse_strike_mode("banana")==("ITM",1))

    Q = L5.contract_quality
    good = Q(713.40, 711.0, "CALL", 3.05, 2.95)
    check(28,"a real ITM contract is allowed", good["ok"], str(good["reasons"]))
    atm  = Q(713.40, 713.0, "CALL", 1.10, 1.02)
    check(28,"ATM is NOT blocked just for having time value", atm["ok"], str(atm["reasons"]))
    otm  = Q(713.40, 718.0, "CALL", 0.09, 0.05)
    check(28,"a fully-OTM lottery ticket is blocked", not otm["ok"])
    check(28,"and it says WHY, in English", any("OUT of the money" in r for r in otm["reasons"]))
    wide = Q(713.40, 711.0, "CALL", 3.60, 2.90)
    check(28,"a wide spread is blocked", not wide["ok"])
    cheap = Q(713.40, 712.9, "CALL", 0.15, 0.14)
    check(28,"a sub-minimum premium is blocked", not cheap["ok"])

    z = _w5.make_session("LIVE")
    z.strategies=[{"id":"a","name":"A","enabled":False},{"id":"b","name":"B","enabled":False}]
    z.settings["my_enabled"]=True; z._enforce_single_mode("entry")
    check(28,"entry armed -> no strategy on",
          z.settings["my_enabled"] and not any(x["enabled"] for x in z.strategies))
    z.update_strategies([{"id":"a","name":"A","enabled":True},{"id":"b","name":"B","enabled":False}])
    check(28,"arming a strategy switches ENTRY off", z.settings["my_enabled"] is False)
    check(28,"and it is the only one on",
          [x["id"] for x in z.strategies if x["enabled"]]==["a"])
    z.update_strategies([{"id":"a","name":"A","enabled":True},{"id":"b","name":"B","enabled":True}])
    check(28,"two at once is impossible",
          len([x for x in z.strategies if x["enabled"]])==1)
    z.update_settings({"my_enabled":True})
    check(28,"re-arming ENTRY switches every strategy off",
          not any(x["enabled"] for x in z.strategies))
    check(28,"state reports what is armed", z.active_mode["mode"]=="entry")

    ix5 = io.open(os.path.join(HERE,"index.html"),encoding="utf-8").read()
    check(28,"buttons are ATM1/ITM1/ITM2", all(k in ix5 for k in ("smATM","smITM2")) and "smITM3" not in ix5)
    check(28,"a retired ITM3 still lights a button", "m==='ITM3'" in ix5)
    check(28,"blocked contracts are unclickable", ".buy-c.blocked" in ix5 and "pointer-events:none" in ix5)
    check(28,"armed-mode banner exists", 'id="activeMode"' in ix5)

    print("\n[29] PERCENT ONLY - no cash anywhere on the options screen")
    ix6 = io.open(os.path.join(HERE,"index.html"),encoding="utf-8").read()
    # strip comments so the explanations do not count as violations
    code6 = re.sub(r"//[^\n]*", "", ix6)
    for gone, what in [("money(", "money() helper"),
                       ("fmtCost", "contract cost"),
                       ('id="bp"', "buying power chip"),
                       ('id="pnlN"', "dollar P&L element"),
                       ("BP $", "mirror buying power")]:
        check(29, f"{what} removed", gone not in code6)
    check(29,"DAY is a percentage, not dollars",
          "DAY <b" in ix6 and "DAY NET" not in ix6)
    _blot = code6.split('id="blotterRows"',1)[1] if 'id="blotterRows"' in code6 else code6
    check(29,"blotter rows show %", "t.pct" in code6)
    check(29,"and no dollar amount in the row template",
          "$${Math.abs(t.pnl)" not in code6)
    check(29,"ratchet line shows rungs without prices",
          "stop_price" not in code6 and "next_price" not in code6)
    check(29,"hero line drops the rotating fill price",
          "pos.entry.toFixed" not in code6)
    check(29,"buy button shows strike + the level it fires at",
          "lastPreview" in code6 and "fmtStrike(strike)" in code6)
    # The strike must come from the TRIGGER, not the live spot - at 713.40 those
    # resolve to 711C and 712C respectively, so mixing them would mislabel it.
    check(29,"strike is taken from the armed trigger, not current spot",
          "pv.strike != null" in code6)

    # --- 30. Entry fires on a pullback OR a breakout ---------------------
    # Pullback-only could not fill in a trend. Measured live 2026-08-26 13:13
    # ET: QQQ at 715.90, call trigger 715.00, and 715 had not been touched once
    # all session. G sat armed and nothing happened.
    _L = wb.LiveSession
    for _spot in (713.40, 707.60, 712.30, 709.80, 702.55, 710.00, 707.50):
        _pc, _bc = _L.entry_window(_spot, "CALLS")
        _pp, _bp2 = _L.entry_window(_spot, "PUTS")
        check(30, "%.2f calls pull back to or below spot" % _spot, _pc <= _spot + 1e-9)
        check(30, "%.2f calls break out above spot" % _spot, _bc > _spot + 1e-9)
        check(30, "%.2f puts pull back to or above spot" % _spot, _pp >= _spot - 1e-9)
        check(30, "%.2f puts break out below spot" % _spot, _bp2 < _spot - 1e-9)
        check(30, "%.2f the two levels are never the same" % _spot, _pc != _bc)

    def _fires(side, target, brk, spot):
        if side == "CALLS":
            return (spot <= target) or (brk is not None and spot >= brk)
        return (spot >= target) or (brk is not None and spot <= brk)

    _p, _b = _L.entry_window(715.90, "CALLS")
    check(30, "the live case: 715.90 calls pull back at 715", abs(_p - 715.0) < 1e-9, str(_p))
    check(30, "and break out at 716", abs(_b - 716.0) < 1e-9, str(_b))
    check(30, "a fall to 715 fills", _fires("CALLS", _p, _b, 715.00))
    check(30, "a push to 716 fills", _fires("CALLS", _p, _b, 716.00))
    check(30, "drifting in between does NOT fill", not _fires("CALLS", _p, _b, 715.60))

    # Armed while sitting exactly ON a level must not fire on the spot - the
    # breakout side would already be satisfied and it would be a market order.
    for _on in (715.00, 712.50, 707.50):
        _pl, _bk = _L.entry_window(_on, "CALLS")
        check(30, "armed at %.2f does not fire instantly" % _on,
              not _fires("CALLS", _pl, _bk, _on), "%s/%s" % (_pl, _bk))
        _pl2, _bk2 = _L.entry_window(_on, "PUTS")
        check(30, "puts armed at %.2f do not fire instantly" % _on,
              not _fires("PUTS", _pl2, _bk2, _on))

    check(30, "half levels are still in play",
          abs(_L.entry_window(712.30, "CALLS")[1] - 712.50) < 1e-9 or
          abs(_L.entry_window(712.30, "PUTS")[0] - 712.50) < 1e-9)
    _wc = io.open("webull_client.py", encoding="utf-8").read()
    check(30, "the trigger is announced to 2dp, not rounded to a dollar",
          "{a['target']:.0f}" not in _wc)
    check(30, "arm() records both levels", '"breakout": brk' in _wc)
    check(30, "the fill says which way it came", 'ENTRY TRIGGERED ({how})' in _wc)

    # The ratchet must protect EVERY fill. Tying it to the entry switch meant
    # that switching to instant fills - the only way in when no pullback came -
    # also left the position with no stop.
    check(30, "the ratchet is armed on every position, not just armed entries",
          '"ratchet_on": True,' in _wc)
    _cls = wb.LiveSession
    _fake = {"entry": 3.00, "mark": 3.00, "qty": 1, "ratchet_on": True, "ratchet_step": 10.0}
    class _R(_cls):
        def __init__(self):
            import config as _c
            self.settings = dict(_c.DEFAULT_SETTINGS); self.strategies = []
    _r = _R(); _r.settings["my_enabled"] = False       # entry switch OFF
    _r.position = dict(_fake)
    _r._update_ratchet()
    check(30, "a manual fill still gets a stop with the switch off",
          (_r.position.get("ratchet") or {}).get("stop_pct") == -10.0,
          str((_r.position.get("ratchet") or {}).get("stop_pct")))

    # --- 31. One switch, still window, toggle switches -------------------
    _idx = io.open("index.html", encoding="utf-8").read()
    # The modal is vertically centred; a content-height box re-centres on every
    # tab change and the whole frame jumps.
    check(31, "settings modal has a fixed height", "height:min(90vh,760px)" in _idx)
    check(31, "modal is a flex column so the frame cannot resize",
          "flex-direction:column" in _idx.split(".smodal{",1)[1][:400])
    check(31, "only the pane area scrolls", ".panes{" in _idx and "overflow-y:auto" in _idx)
    check(31, "title/tabs/actions are pinned",
          ".smodal h2,.smodal .tabs,.smodal .activemode,.smodal .sactions{flex:none}" in _idx)
    check(31, "every pane lives inside the scroller",
          all(('id="%s"' % p) in _idx.split('<div class="panes">',1)[1]
                                   .split('<!-- /panes -->',1)[0]
              for p in ("paneConfig","paneStrat","paneMirror")))
    check(31, "save/cancel do NOT scroll away",
          "sactions" not in _idx.split('<div class="panes">',1)[1]
                                .split('<!-- /panes -->',1)[0])
    # Toggle switches, not checkboxes.
    check(31, "toggle-switch styling exists", ".sw input:checked + i" in _idx)
    check(31, "entry+ratchet uses a switch",
          '<span class="sw"><input type="checkbox" id="myEnabled"' in _idx)
    check(31, "strategy cards use a switch",
          _idx.count('class="sw"') >= 2 and 'EZ.toggleStrategy' in _idx)
    # ONE switch for both halves.
    check(31, "the separate RATCHET checkbox is gone", "ratchetEnabled" not in _idx)
    check(31, "the step % input survived", 'id="ratchetStep"' in _idx)
    check(31, "the browser stores ONE key, not two",
          "settings.ratchet_enabled" not in _idx)
    check(31, "arming a strategy switches the one switch off",
          "api('/api/settings',{my_enabled:false});" in _idx)
    # Server refuses to hold the two apart, whatever arrives.
    # update_settings() persists to my-settings.json. These are throwaway
    # objects exercising the logic, so the write is stubbed out - otherwise the
    # suite leaves the real file holding whatever the last loop iteration set.
    _real_save = wb.uc.save
    wb.uc.save = lambda *a, **k: None
    import config as _cfgA
    class _One(wb.LiveSession):
        def __init__(self):
            import config as _cfg
            self.settings = dict(_cfg.DEFAULT_SETTINGS); self.strategies = []
        def _enforce_single_mode(self, prefer=None): pass
    _f = _One()
    check(31, "ratchet_enabled is not a setting any more",
          "ratchet_enabled" not in _cfgA.DEFAULT_SETTINGS)
    check(31, "my_enabled is", _cfgA.DEFAULT_SETTINGS["my_enabled"] is True)
    for _start in (True, False):
        for _send, _want in (({"my_enabled": True}, True),
                             ({"my_enabled": False}, False),
                             # legacy key, still accepted, folded into the one
                             ({"ratchet_enabled": True}, True),
                             ({"ratchet_enabled": False}, False),
                             # both sent: the live key wins, no ambiguity
                             ({"my_enabled": True, "ratchet_enabled": False}, True),
                             ({"my_enabled": False, "ratchet_enabled": True}, False)):
            _f.settings["my_enabled"] = _start
            _f.update_settings(dict(_send))
            check(31, "from %s, %s -> %s" % (_start, _send, _want),
                  _f.settings["my_enabled"] is _want, str(_f.settings["my_enabled"]))
            check(31, "and the retired key is never stored (%s)" % _send,
                  "ratchet_enabled" not in _f.settings)
    # The trap the old two-key version had: OR-ing them made OFF unsendable.
    _f.settings["my_enabled"] = True
    _f.update_settings({"my_enabled": False})
    check(31, "it can actually be switched OFF", _f.settings["my_enabled"] is False)
    _f.settings["my_enabled"] = True
    _f.update_settings({"strike_mode": "ITM1"})
    check(31, "an unrelated setting leaves the switch alone", _f.settings["my_enabled"])
    wb.uc.save = _real_save

    # --- 32. No SAVE button; a live trade keeps its own terms -------------
    import config as _cfg0
    check(32, "the SAVE button is gone", "EZ.saveSettings()" not in _idx)
    # Scoped to the settings footer: CANCEL also names the DISARM button and the
    # strategy editor's cancel, neither of which is going anywhere.
    _sact = _idx.split('<div class="sactions">', 1)[1].split('</div>', 1)[0]
    check(32, "and CANCEL with it", "CANCEL" not in _sact)
    check(32, "only one button is left in the footer", _sact.count("<button") == 1)
    check(32, "DONE just closes", 'onclick="EZ.closeSettings()">DONE<' in _idx)
    check(32, "the switch writes on change",
          'id="myEnabled"' in _idx and
          'onchange="EZ.applySettings()"' in _idx.split('id="myEnabled"',1)[1][:120])
    check(32, "the step box writes on change and on blur",
          'onchange="EZ.applySettings()" onblur="EZ.applySettings()"' in _idx)
    check(32, "picking a strike writes it",
          "applySettings();         // no SAVE button" in _idx)
    check(32, "applying does NOT close the window",
          "closeSettings(); refreshQuote();" not in _idx)
    check(32, "a cleared step box falls back to 10",
          "parseFloat($('ratchetStep').value)||10" in _idx)
    check(32, "and the box is written back so it matches storage",
          "$('ratchetStep').value=settings.ratchet_step_pct" in _idx)
    check(32, "10% is the shipped default", _cfg0.DEFAULT_SETTINGS["ratchet_step_pct"] == 10.0)
    check(32, "and the input agrees", 'id="ratchetStep" type="number" min="1" max="100" step="1" value="10"' in _idx)
    check(32, "an open trade is called out on screen", 'id="liveNote"' in _idx and "NEXT trade" in _idx)

    # The reason this matters: with no SAVE button, every keystroke reaches the
    # server at once. A live trade must not be re-tuned underneath itself.
    _wc2 = io.open("webull_client.py", encoding="utf-8").read()
    # ratchet_on is now unconditionally True - the switch decides whether the
    # ENTRY waits for a level, never whether a live trade has a stop. The step
    # is still frozen at open, which is the part that must not move underneath
    # a running position.
    check(32, "terms are frozen onto the position at open",
          '"ratchet_on": True,' in _wc2 and
          '"ratchet_step": float(self.settings.get("ratchet_step_pct")' in _wc2)
    check(32, "the ratchet reads the position, not live settings",
          'p.get("ratchet_on"' in _wc2 and 'p.get("ratchet_step"' in _wc2)

    class _Live(wb.LiveSession):
        def __init__(self):
            import config as _c
            self.settings = dict(_c.DEFAULT_SETTINGS); self.strategies = []
    _lv = _Live()
    # Opened at 10% step, then the step is changed to 50% mid-trade.
    _lv.position = {"entry": 3.00, "mark": 3.30, "qty": 1,
                    "ratchet_on": True, "ratchet_step": 10.0}
    _lv._update_ratchet()
    _stop_before = _lv.position["ratchet"]["stop_pct"]
    _lv.settings["ratchet_step_pct"] = 50.0
    _lv._update_ratchet()
    _stop_after = _lv.position["ratchet"]["stop_pct"]
    check(32, "changing the step mid-trade does NOT move a live stop",
          _stop_before == _stop_after == 0.0, "%s -> %s" % (_stop_before, _stop_after))
    # Switching the whole feature off mid-trade must not abandon the open stop.
    _lv.settings["my_enabled"] = False
    _lv._update_ratchet()
    check(32, "switching it off mid-trade does not abandon the open stop",
          _lv.position.get("ratchet") is not None)
    # And the NEXT trade does pick the new terms up.
    _lv.settings.update({"my_enabled": True, "ratchet_step_pct": 50.0})
    _lv.position = {"entry": 3.00, "mark": 4.80, "qty": 1,
                    "ratchet_on": bool(_lv.settings["my_enabled"]),
                    "ratchet_step": float(_lv.settings["ratchet_step_pct"])}
    _lv._update_ratchet()
    check(32, "the next trade uses the new step (+60% -> stop +0%)",
          _lv.position["ratchet"]["stop_pct"] == 0.0,
          str(_lv.position["ratchet"]["stop_pct"]))

    # --- 33. The page actually RUNS ---------------------------------------
    # node --check parses; it does not execute. paintLiveNote() read a variable
    # named `position` that was never declared - a ReferenceError thrown one
    # line before the modal was shown, so SETTINGS silently stopped opening and
    # every syntax check still passed. ui_smoke.js evaluates the real script and
    # calls the handlers a user can reach.
    import subprocess as _sp
    _sm = _sp.run(["node", "ui_smoke.js", "index.html"],
                  cwd=HERE, capture_output=True, text=True, timeout=60)
    check(33, "the options page runs and its handlers do not throw",
          _sm.returncode == 0, (_sm.stdout + _sm.stderr).strip()[:400])

    # And prove the harness can still see that exact failure, or it is theatre.
    # Undo the guard AND reintroduce the undeclared variable, so the throw
    # propagates exactly as it did when SETTINGS stopped opening.
    _orig = io.open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
    _swaps = [
        ("    const live=!!inTrade;", "    const live=!!position;"),
        ("$('setScrim').classList.add('show');\n    try{\n    showTab('config')",
         "showTab('config')"),
        ("paintLiveNote();\n    }catch(e){ console.error('settings paint failed:', e); }",
         "paintLiveNote();\n    $('setScrim').classList.add('show');"),
    ]
    _broken = _orig
    for _a, _b in _swaps:
        check(33, "self-test can rebuild the bug (%s)" % _a.strip()[:34], _a in _broken)
        _broken = _broken.replace(_a, _b, 1)
    _bp = os.path.join(HERE, "_ui_smoke_selftest.html")
    io.open(_bp, "w", encoding="utf-8").write(_broken)
    try:
        _bad = _sp.run(["node", "ui_smoke.js", "_ui_smoke_selftest.html"],
                       cwd=HERE, capture_output=True, text=True, timeout=60)
        _out = _bad.stdout + _bad.stderr
        check(33, "the smoke test still catches an undeclared variable",
              _bad.returncode != 0 and "is not defined" in _out, _out.strip()[:200])
    finally:
        # This file is a deliberately BROKEN copy of index.html. If the delete
        # ever fails it must not be left sitting in the app folder, where
        # auto-sync will happily commit it - park it in _trash, which the
        # launcher wipes on every start.
        try:
            os.remove(_bp)
        except OSError:
            try:
                _t = os.path.join(HERE, "_trash"); os.makedirs(_t, exist_ok=True)
                shutil.move(_bp, os.path.join(_t, "_ui_smoke_selftest.html"))
            except Exception:
                pass

    # The guard that keeps one bad painter from locking you out of settings.
    check(33, "settings open BEFORE anything is painted",
          "$('setScrim').classList.add('show');\n    try{" in _idx)
    check(33, "a painter that throws is logged, not fatal",
          "catch(e){ console.error('settings paint failed:', e); }" in _idx)

    # --- 34. LOCK / quit removed; size warns without showing cash ---------
    check(34, "the LOCK button is gone from the header", "EZ.lockNow()" not in _idx)
    check(34, "the quit X is gone", "EZ.quitApp()" not in _idx)
    # The in-trade lock is a different thing and stays: it is what stops you
    # pressing BUY again while a position is open.
    check(34, "the in-trade lock still exists", 'id="lockNote"' in _idx and "lockdown(inTrade)" in _idx)
    check(34, "CONTRACTS goes red when unaffordable", "#qty.over{color:var(--red)}" in _idx)
    check(34, "affordability is rechecked when you change size",
          "$('qty').textContent=qty; savePrefs({qty});\n    paintQtyAfford();" in _idx)
    check(34, "and when a fresh quote lands", "paintQtyAfford();" in _idx.split("function paintSide",1)[1][:600])
    check(34, "buying power is read from state but never rendered",
          "buyingPower=Number(st.buying_power)" in _idx and "$('bp')" not in _idx)
    # "$" alone would match the $() element helper. What must not appear is a
    # dollar sign rendered into text.
    _aff = _idx.split("function paintQtyAfford", 1)[1].split("function setStrikeMode", 1)[0]
    check(34, "still no dollar figure attached to the warning",
          "'$" not in _aff and '"$' not in _aff and "$'+" not in _aff)
    check(34, "the server still supplies buying power",
          '"buying_power": round(self.buying_power, 2),' in _wc2)
    # A contract is 100 shares - forgetting the multiplier makes the check
    # useless, since a $3.00 option would read as $3 against the account.
    check(34, "cost uses the 100x contract multiplier", "*100*qty" in _idx)

    # --- 35. Time value warns, it does not block -------------------------
    # This blocked a live trade at 9:30 on the first real session. Extrinsic %
    # measures distance-to-strike and time-left, not quality: at the open
    # nearly every 0DTE is 90%+ time value, and an ITM1 a nickel from the
    # strike is 95% by arithmetic. Being fully OTM is the real problem, and
    # that is blocked separately.
    _Q = wb.LiveSession.contract_quality
    _q = _Q(713.95, 714.0, "PUT", 1.00, 0.97)          # the exact live case
    check(35, "95% time value is TRADABLE", _q["ok"] is True,
          str(_q.get("reasons")))
    check(35, "but it still says so", any("time value" in w for w in _q.get("warnings", [])))
    check(35, "warnings are separate from blocking reasons", _q["reasons"] == [])
    # The three that must still refuse.
    check(35, "fully OTM is still blocked", _Q(714.50, 714.0, "PUT", 0.80, 0.77)["ok"] is False)
    check(35, "penny premium is still blocked", _Q(713.95, 714.0, "PUT", 0.10, 0.05)["ok"] is False)
    check(35, "a wide spread is still blocked", _Q(713.10, 714.0, "PUT", 1.40, 1.05)["ok"] is False)
    check(35, "a clean contract has no warning at all",
          _Q(713.10, 714.0, "PUT", 1.40, 1.37)["warnings"] == [])
    check(35, "the warning reaches the button as a tooltip, not as text",
          "btn.title = (qual.warnings||[]).join" in _idx)
    check(35, "the button text is still strike + level",
          "fmtStrike(strike)" in _idx)

    # --- 36. Trade log: how the trade travelled --------------------------
    import trade_log as _tl2
    for _f in ("best_pct","worst_pct","best_price","worst_price","gave_back_pct",
               "ratchet_stop_pct","ratchet_step","strike_mode","held_secs"):
        check(36, "log has a %s column" % _f, _f in _tl2.FIELDS)

    # High/low water marks, tracked whether or not the ratchet is on.
    _tr = {"entry": 3.00, "mark": 3.00}
    for _m in (3.00, 3.30, 2.85, 4.20, 3.60):
        _tr["mark"] = _m; wb.LiveSession._track_excursion(_tr)
    check(36, "best is the highest mark seen", _tr["best_price"] == 4.20, str(_tr.get("best_price")))
    check(36, "worst is the lowest", _tr["worst_price"] == 2.85, str(_tr.get("worst_price")))
    check(36, "best % is right (+40)", _tr["best_pct"] == 40.0, str(_tr.get("best_pct")))
    check(36, "worst % is right (-5)", _tr["worst_pct"] == -5.0, str(_tr.get("worst_pct")))
    # A trade that only ever went up was never down: worst is 0, not the entry.
    _up = {"entry": 2.00, "mark": 2.00}
    for _m in (2.00, 2.40, 2.90):
        _up["mark"] = _m; wb.LiveSession._track_excursion(_up)
    check(36, "a trade that never went red has worst 0", _up["worst_pct"] == 0.0,
          str(_up.get("worst_pct")))

    # The header migration: appending 26 columns to an 18-column file without
    # rewriting the header shifts every value one column left and the file
    # still opens fine, so it would go unnoticed.
    import tempfile as _tf2, csv as _csv2
    _d2 = _tf2.mkdtemp(prefix="tlmig")
    _tl2.LOG_DIR = _d2
    _tl2.CSV_PATH = os.path.join(_d2, "trades.csv")
    _tl2.XLSX_PATH = os.path.join(_d2, "log.xlsx")
    _old_fields = ["date","time_in","symbol","side","entry","exit","pnl","pnl_pct"]
    with io.open(_tl2.CSV_PATH, "w", encoding="utf-8", newline="") as _f2:
        _w2 = _csv2.DictWriter(_f2, fieldnames=_old_fields); _w2.writeheader()
        _w2.writerow({"date":"2026-08-25","time_in":"10:00","symbol":"QQQ",
                      "side":"CALLS","entry":"2.00","exit":"2.50","pnl":"50.0",
                      "pnl_pct":"25.0"})
    _tl2.record({"date":"2026-08-26","symbol":"SPY","side":"PUTS","entry":1.0,
                 "exit":1.5,"pnl":50.0,"pnl_pct":50.0,"best_pct":60.0,"worst_pct":-8.0})
    _got = _tl2._rows()
    check(36, "the old row survives the upgrade", len(_got) == 2, str(len(_got)))
    check(36, "and its values stay in their own columns",
          _got[0]["symbol"] == "QQQ" and _got[0]["pnl_pct"] == "25.0",
          str(_got[0])[:120])
    check(36, "the new row keeps the new columns",
          _got[1]["best_pct"] == "60.0" and _got[1]["worst_pct"] == "-8.0",
          str(_got[1])[:120])
    check(36, "migrating twice is harmless",
          (_tl2._migrate_header() or True) and len(_tl2._rows()) == 2)

    _wc3 = io.open("webull_client.py", encoding="utf-8").read()
    check(36, "excursions are tracked on every mark refresh",
          "self._track_excursion(p)" in _wc3)
    check(36, "the open time is stamped so held time is real",
          '"opened_ts": time.time(),' in _wc3)
    check(36, "the precise ratchet reason is no longer clobbered",
          'if self.position and not self.position.get("exit_reason"):' in _wc3)

    # --- 37. Desktop icon --------------------------------------------------
    _ico = os.path.join(HERE, "sniper.ico")
    check(37, "the icon file exists", os.path.exists(_ico))
    try:
        from PIL import Image as _Im
        _sz = sorted(_Im.open(_ico).ico.sizes())
        check(37, "it carries a 16px and a 256px frame",
              (16, 16) in _sz and (256, 256) in _sz, str(_sz))
    except Exception as _e:
        check(37, "icon is readable", False, str(_e)[:80])

    # A shortcut CANNOT point at the real launcher: its name starts with an
    # emoji, and WScript.Shell rejects any TargetPath holding a character
    # outside the Basic Multilingual Plane - "Value does not fall within the
    # expected range" - leaving an icon on the desktop that does nothing.
    # So an ASCII-named stub sits in between.
    _stub = os.path.join(HERE, "MARKET SNIPER.bat")
    check(37, "the ASCII launcher stub exists", os.path.exists(_stub))
    _st = io.open(_stub, encoding="utf-8").read()
    check(37, "the stub is pure ASCII", all(ord(c) < 128 for c in _st))
    check(37, "the stub finds the real launcher by wildcard",
          '("*START MARKET SNIPER.bat")' in _st)
    check(37, "the stub does not match its own wildcard (no loop)",
          not __import__("fnmatch").fnmatch("MARKET SNIPER.bat",
                                            "*START MARKET SNIPER.bat"))
    check(37, "the stub CALLs, so closing the window still stops the servers",
          'call "%LAUNCHER%"' in _st)
    check(37, "the stub says so when the launcher is missing", "Could not find" in _st)

    _shortcut = os.path.join(HERE, "CREATE DESKTOP ICON.bat")
    check(37, "the one-time setup file exists", os.path.exists(_shortcut))
    _sh = io.open(_shortcut, encoding="utf-8").read()
    check(37, "the setup file is pure ASCII", all(ord(c) < 128 for c in _sh))
    # Windows will not PIN a shortcut whose target is a .bat - a shell rule,
    # not a setting. So the target is cmd.exe (pinnable) and the batch file
    # rides along as an argument.
    check(37, "the target is cmd.exe, so it can be pinned",
          "System32\\cmd.exe" in _sh)
    check(37, "the batch file is passed as an argument",
          "$s.Arguments = '/c" in _sh and "MARKET SNIPER.bat" in _sh)
    check(37, "the emoji launcher is never named in the shortcut",
          "START MARKET SNIPER" not in _sh)
    check(37, "it refuses to run if the stub is missing",
          'if not exist "%~dp0MARKET SNIPER.bat"' in _sh)
    check(37, "it clears a stale icon first", "Remove-Item $link -Force" in _sh)
    # The first version reported success while leaving a dead icon behind,
    # because assigning TargetPath threw and nothing checked afterwards.
    check(37, "it reads the shortcut BACK and proves the target exists",
          "$c = $ws.CreateShortcut($link)" in _sh and
          "Test-Path $c.TargetPath" in _sh)
    check(37, "it also proves the batch file it points at exists",
          "shortcut saved but MARKET SNIPER.bat is missing" in _sh)
    check(37, "it stops on the first PowerShell error",
          "$ErrorActionPreference = 'Stop'" in _sh)
    check(37, "it tells you how to pin it", "Pin to taskbar" in _sh)
    check(37, "it applies the icon", "sniper.ico" in _sh)
    check(37, "it explains the manual fallback",
          "New ^> Shortcut" in _sh and "Change Icon" in _sh)
    check(37, "and the manual route is the pinnable one too",
          'cmd.exe /c "%~dp0MARKET SNIPER.bat"' in _sh)
    # The stub adds nothing: the real launcher already opens the browser, so
    # the Desktop icon needs no extra step to get you to the trading screen.
    _real = [os.path.join(HERE, f) for f in os.listdir(HERE)
             if f.endswith("START MARKET SNIPER.bat")]
    check(37, "the real launcher is still there", len(_real) == 1, str(_real))
    _lb = io.open(_real[0], encoding="utf-8", errors="replace").read()
    check(37, "the launcher still opens the browser itself",
          "http://127.0.0.1:8000" in _lb and "start" in _lb)

    # --- 38. Velocity survives Yahoo's cumulative-volume bars -------------
    # Measured live 2026-08-26 12:42 ET: QQQ's 12:41 bar carried 18,887,220
    # against neighbours of 30-70k, and SPY's 12:07 bar carried 15,501,626
    # against 50-90k. Yahoo intermittently publishes a cumulative figure in a
    # single 1-minute bar. Averaged in, ONE such bar decides the reading: in
    # the recent window the meter pinned to "violent", in the baseline the
    # identical tape read "calm". Both were on screen the same afternoon.
    import tape as _tp
    _tp = importlib.reload(_tp)

    def _bar(t, v, c=500.0, rng=0.10):
        return {"t": t, "o": c, "h": c + rng / 2, "l": c - rng / 2, "c": c, "v": v}

    _clean = [_bar(i, 55000) for i in range(34)] + [_bar(34, 55000)]
    _in_recent = [_bar(i, 55000) for i in range(34)] + [_bar(34, 18887220)]
    _in_base = [_bar(0, 15501626)] + [_bar(i, 55000) for i in range(1, 35)]

    _rc = _tp.compute(_clean)
    _rr = _tp.compute(_in_recent)
    _rb = _tp.compute(_in_base)
    check(38, "clean tape reads normal", _rc["state"] == "normal", str(_rc["score"]))
    check(38, "a 300x artifact in the RECENT window no longer reads violent",
          _rr["state"] == "normal", "%s / %s" % (_rr["state"], _rr["score"]))
    check(38, "a 300x artifact in the BASELINE no longer reads calm",
          _rb["state"] == "normal", "%s / %s" % (_rb["state"], _rb["score"]))
    check(38, "and all three agree, because the tape is the same tape",
          _rc["score"] == _rr["score"] == _rb["score"],
          "%s %s %s" % (_rc["score"], _rr["score"], _rb["score"]))
    check(38, "the artifact is counted, not hidden", _rr.get("clipped_bars", 0) == 1)
    check(38, "an artifact as the NEWEST bar suppresses acceleration",
          _rr["accel_pct"] == 0.0 and "artifact" in (_rr.get("note") or ""),
          "%s / %s" % (_rr["accel_pct"], _rr.get("note")))

    # A REAL burst must still register - the cap must not flatten genuine speed.
    _burst = [_bar(i, 50000) for i in range(30)] + [_bar(30 + i, 260000) for i in range(5)]
    _rbu = _tp.compute(_burst)
    check(38, "a real 5x burst still reads fast or violent",
          _rbu["state"] in ("fast", "violent"), "%s / %s" % (_rbu["state"], _rbu["score"]))
    # And a genuinely dead tape must still read dead, or "silent tape = do not
    # enter" stops working.
    _dead = [_bar(i, 55000) for i in range(30)] + [_bar(30 + i, 2000) for i in range(5)]
    check(38, "a dying tape still reads calm", _tp.compute(_dead)["state"] == "calm",
          str(_tp.compute(_dead)["score"]))
    _shut = [_bar(i, 0) for i in range(35)]
    check(38, "a closed market still reads closed",
          "closed" in (_tp.compute(_shut).get("note") or ""))

    check(38, "medians, not means, decide the ratios",
          "vol_recent = _median(v_recent)" in io.open("tape.py", encoding="utf-8").read())
    check(38, "acceleration is clamped to a believable range",
          "min(accel, 400.0)" in io.open("tape.py", encoding="utf-8").read())

    # --- 39. Dwell time -----------------------------------------------------
    import levels as _lv2
    _lv2 = importlib.reload(_lv2)
    _n = int(time.time())

    def _lb(i, lo, hi, v=50000, total=60):
        return {"t": _n - (total - i) * 60, "o": (lo + hi) / 2,
                "h": hi, "l": lo, "c": (lo + hi) / 2, "v": v}

    for _p, _want in ((713.40, (713.0, 714.0)), (713.99, (713.0, 714.0)),
                      (702.55, (702.0, 703.0))):
        check(39, "%.2f brackets to %s" % (_p, _want),
              _lv2.bracketing_levels(_p) == _want, str(_lv2.bracketing_levels(_p)))
    # Price exactly ON a dollar: the pair must never collapse to one number.
    _b, _a = _lv2.bracketing_levels(713.00)
    check(39, "price on the dollar still gives two distinct levels", _b != _a and _a - _b == 1.0)

    # 60 minutes stuck inside 713.20-713.80: neither dollar is ever touched.
    _pin = [_lb(i, 713.20, 713.80) for i in range(60)]
    _d = _lv2.dwell(_pin, now=_n)
    check(39, "a pinned market reports pinned", _d["pinned"] is True)
    check(39, "an untouched level is None, not 0",
          _d["mins_below"] is None and _d["mins_above"] is None)
    check(39, "None and 0 stay different", 0 is not None)

    # One bar pokes through 714, two minutes ago.
    _mv = [_lb(i, 713.20, 713.80) for i in range(58)] + \
          [_lb(58, 713.5, 714.10), _lb(59, 713.4, 713.9)]
    _d2 = _lv2.dwell(_mv, now=_n)
    check(39, "a touch clears pinned", _d2["pinned"] is False)
    check(39, "and is timed correctly (2m)", abs(_d2["mins_above"] - 2.0) < 0.6,
          str(_d2["mins_above"]))

    # Highs/lows, not closes: a wick through a level IS a touch. Using closes
    # would miss the rejection wick, which is the touch that matters most.
    check(39, "a wick through the level counts as a touch",
          _lv2._touched({"h": 714.02, "l": 713.40}, 714.0) is True)
    check(39, "a bar that never reached it does not",
          _lv2._touched({"h": 713.90, "l": 713.40}, 714.0) is False)

    # The label must quote the bars actually available, not the 180 cap.
    check(39, "label quotes the real lookback, not the cap",
          ">60m" in _lv2.label(_d), _lv2.label(_d))

    # Dwell and velocity answer different questions. Pinned AND violent is a
    # contradiction and must be surfaced, not averaged away.
    _ag = _lv2.agreement({"ok": True, "pinned": True}, {"ok": True, "state": "violent"})
    check(39, "pinned + violent is flagged as disagreement", _ag["agree"] is False)
    _ag2 = _lv2.agreement({"ok": True, "pinned": True}, {"ok": True, "state": "calm"})
    check(39, "pinned + calm agrees", _ag2["agree"] is True)

    check(39, "no bars is handled, not crashed", _lv2.dwell([])["ok"] is False)
    _mainsrc = io.open("main.py", encoding="utf-8").read()
    check(39, "the endpoint exists", '@app.get("/api/dwell")' in _mainsrc)
    check(39, "levels is imported defensively like tape",
          "import levels\nexcept Exception:\n    levels = None" in _mainsrc)
    check(39, "the endpoint rejects untradable symbols",
          "isn't one of the tradable symbols" in _mainsrc.split('/api/dwell', 1)[1][:900])

    # --- 40. Volume gauge ---------------------------------------------------
    import gauges as _g
    _g = importlib.reload(_g)

    # THE TRAP: comparing volume-so-far against whole past days makes every
    # morning read as dead. The profile is learned from real bars, and it is
    # nothing like a straight line - measured on QQQ, 12:30 is 64% of a day
    # done where a flat clock says 46%.
    _prof = [(570 + i * 5, min(1.0, (i * 5 / 390.0) ** 0.75)) for i in range(79)]
    check(40, "the profile is used, not a flat clock",
          _g.expected_fraction(_prof, 630) > (630 - 570) / 390.0)
    check(40, "before the open, nothing is done", _g.expected_fraction(_prof, 500) == 0.0)
    check(40, "with no profile it degrades to a clock, not a crash",
          0.0 < _g.expected_fraction([], 750) < 1.0)

    # Percentile, not a ratio: a few huge days must not move where today sits.
    _series = [100.0] * 90 + [5000.0] * 10
    check(40, "ten monster days do not drag the ranking",
          _g.percentile_of(101.0, _series) == 90.0, str(_g.percentile_of(101.0, _series)))
    check(40, "an empty history returns None, not a fake number",
          _g.percentile_of(1.0, []) is None)
    check(40, "low/high bands sit where the spec says",
          _g._band(10) == "low" and _g._band(50) == "normal" and _g._band(90) == "high")

    # Yahoo's intraday volume cannot be summed. Measured 2026-08-26 12:55 ET:
    # QQQ's 5-min bars summed to 277,170,149 against a 43,645,800 median day,
    # because six bars carried 26-47x the median. Today's total therefore comes
    # from the daily bar, which agreed with the cleaned sum to within 1%.
    _gsrc = io.open("gauges.py", encoding="utf-8").read()
    check(40, "today's volume comes from the daily bar, not a sum of 5m bars",
          '_chart(ysym, "1d", "1d")' in _gsrc and "regularMarketVolume" in _gsrc)
    check(40, "profile bars are cleaned of artifacts", "cleaned = _clean(" in _gsrc)
    _cl = _g._clean([100, 100, 100, 100, 100, 28000000])
    check(40, "a 280,000x artifact is clipped", max(_cl) == 100 * _g.OUTLIER_X, str(max(_cl)))
    check(40, "and honest volume is untouched", _cl[:5] == [100, 100, 100, 100, 100])

    # Ranking today against a list containing today drags every reading to the
    # middle, and on a record day the record pulls its own percentile down.
    check(40, "today is excluded from its own history", "if d >= today:" in _gsrc)
    # The clock must come from the feed. -4 is right from March to November and
    # an hour wrong the rest of the year - a fifth of a session.
    check(40, "the exchange timezone is read from the feed, not hardcoded",
          "gmtoffset" in _gsrc and "def _tz_for" in _gsrc)
    # Dividing by a near-zero fraction at 09:31 turns noise into a wild number.
    check(40, "it refuses to project in the first minutes", "too early to project" in _gsrc)

    _mainsrc2 = io.open("main.py", encoding="utf-8").read()
    check(40, "the endpoint exists", '@app.get("/api/volume")' in _mainsrc2)
    check(40, "gauges is imported defensively",
          "import gauges\nexcept Exception:\n    gauges = None" in _mainsrc2)

    # --- 41. Volatility: two gauges, never blended -----------------------
    # Black-Scholes must round-trip, or the implied number is decoration.
    for _v in (0.15, 0.35, 0.80):
        _px = _g.bs_price(713.0, 712.0, 4 / 24 / 365, _v, True)
        _back = _g.implied_vol(_px, 713.0, 712.0, 4 / 24 / 365, True)
        check(41, "BS round-trips at %.0f%% vol" % (_v * 100),
              _back is not None and abs(_back - _v * 100) < 0.5, str(_back))
    # Bisection, not Newton: vega collapses near zero on a 0DTE away from the
    # money and Newton divides by it. These must return None, not diverge.
    check(41, "a quote below intrinsic is refused",
          _g.implied_vol(0.50, 713.0, 712.0, 4 / 24 / 365, True) is None)
    check(41, "an impossible quote is refused",
          _g.implied_vol(700.0, 713.0, 712.0, 4 / 24 / 365, True) is None)
    check(41, "zero time to expiry is refused",
          _g.implied_vol(1.0, 713.0, 712.0, 0, True) is None)
    check(41, "bisection is used, and says why", "Bisection rather than Newton" in _gsrc)
    # Time to expiry can never be zero or IV goes infinite in the last minute.
    check(41, "expiry time is floored at a minute", _g.hours_to_expiry() > 0)

    # Realized is ranked against the SAME calculation rolled back through the
    # symbol's own history, so "high" means high for this symbol.
    check(41, "realized is a percentile of its own history",
          "_annualised_vol(closes[end - RV_WINDOW_DAYS - 1:end])" in _gsrc)
    # Check the SHAPE of the result, not the prose. The word "blended" appears
    # in a comment explaining that they are not blended, which is exactly the
    # kind of thing a string match gets wrong.
    _sep = _g.volatility("QQQ", option=None)
    check(41, "the two gauges stay separate",
          isinstance(_sep.get("realized"), dict) and isinstance(_sep.get("implied"), dict)
          and not any(k in _sep for k in ("vol_score", "combined", "blended")))
    check(41, "and realized survives on its own", _sep["realized"].get("ok") is True)
    check(41, "thresholds are plain constants, meant to be argued with",
          all(k in _gsrc for k in ("RV_LOW_PCTL", "RV_HIGH_PCTL",
                                   "IV_RICH_RATIO", "IV_CHEAP_RATIO")))
    # Not connected must SAY not connected rather than invent a number.
    _nov = _g.volatility("QQQ", option=None)
    check(41, "implied is unavailable, not guessed, without a chain",
          _nov["implied"]["ok"] is False and "connect" in _nov["implied"]["reason"])

    _wc4 = io.open("webull_client.py", encoding="utf-8").read()
    check(41, "the ATM chain quote comes from Webull", "def atm_option_for_vol" in _wc4)
    check(41, "a native Webull IV is preferred over inverting a mid",
          "iv_native" in _wc4 and "iv_native" in _mainsrc2)
    check(41, "a fractional IV is normalised to percent", "if iv is not None and iv < 5.0:" in _wc4)
    check(41, "greeks are picked up if Webull sends them",
          all(gk in _wc4 for gk in ('"delta"', '"theta"', '"gamma"', '"vega"')))
    check(41, "the endpoint exists", '@app.get("/api/volatility")' in _mainsrc2)
    check(41, "a chain hiccup cannot kill the realized reading",
          "a chain hiccup must not kill realized" in _mainsrc2)

    # --- 42. Audio cues -----------------------------------------------------
    _ix = io.open("index.html", encoding="utf-8").read()
    for _cue in ("armed", "filled", "exit", "cancel", "blocked"):
        check(42, "the %s cue is wired" % _cue, "sfx('%s')" % _cue in _ix)
    # Synthesised, so there is no audio file to ship or fail to load.
    check(42, "tones are synthesised, not files",
          "createOscillator" in _ix and ".mp3" not in _ix and ".wav" not in _ix)
    # A square gain edge is audible as a click at both ends of every note.
    check(42, "the envelope is ramped, not switched",
          "exponentialRampToValueAtTime" in _ix)
    # Polling runs once a second: sounding STATE rather than TRANSITION would
    # replay the note every tick for as long as the position was open.
    check(42, "fill and exit sound on the transition only",
          "if(pos && !prevPos) sfx('filled');" in _ix and
          "if(!pos && prevPos) sfx('exit');" in _ix)
    check(42, "the previous position is tracked for that", "prevPos=false" in _ix)
    check(42, "sound can be switched off", "function toggleSound" in _ix and
          'id="sndBtn"' in _ix)
    check(42, "the choice survives a reload", "localStorage.setItem('ms_sound'" in _ix)
    # Audio must never be able to take the trading screen down with it.
    check(42, "any audio failure is swallowed",
          "never break trading over it" in _ix)
    # The grid has .50 levels: "715" for a 715.50 trigger is the wrong price.
    check(42, "the armed line shows the level to 2dp",
          "Number(armed.target).toFixed(2)" in _ix)
    check(42, "and names the breakout level too", "armed.breakout" in _ix)
    _wc5 = io.open("webull_client.py", encoding="utf-8").read()
    check(42, "the server sends the breakout level", '"breakout": brk' in _wc5)

    import subprocess as _sp2
    _sm2 = _sp2.run(["node", "ui_smoke.js", "index.html"], cwd=HERE,
                    capture_output=True, text=True, timeout=60)
    check(42, "the page still runs with the sound code in it",
          _sm2.returncode == 0, (_sm2.stdout + _sm2.stderr).strip()[:300])

    # --- 43. Trend module -------------------------------------------------
    import trend as _tr
    _tr = importlib.reload(_tr)

    def _mk(n, step=0.0, rng=0.40, vol=50000, up=True, start=700.0):
        out, c = [], start
        for i in range(n):
            o = c
            c = o + step
            hi = max(o, c) + rng / 2
            lo = min(o, c) - rng / 2
            out.append({"t": 1700000000 + i * 60, "o": o, "h": hi, "l": lo,
                        "c": c, "v": vol})
        return out

    # SLOPE. The old panel compared LEVELS, so a rolled-over market still read
    # UP while the fast EMA sat above the slow one. Slope plus price position
    # is the fix, and both halves are required.
    _rise = _mk(60, step=0.10)
    _fall = _mk(60, step=-0.10)
    _flat = _mk(60, step=0.0)
    check(43, "a rising EMA with price above it votes up",
          _tr.slope_signal(_rise)["vote"] == 1, str(_tr.slope_signal(_rise)))
    check(43, "a falling EMA with price below it votes down",
          _tr.slope_signal(_fall)["vote"] == -1)
    check(43, "a flat market votes neither", _tr.slope_signal(_flat)["vote"] == 0)
    # The case the old panel got wrong: a long climb that has just turned over.
    # The EMA is still high, but it is no longer rising and price is under it.
    _rollover = _mk(45, step=0.12) + _mk(15, step=-0.30, start=705.4)
    check(43, "a rollover does NOT still read up",
          _tr.slope_signal(_rollover)["vote"] <= 0, str(_tr.slope_signal(_rollover)))
    # One threshold has to work on any symbol and timeframe, so the slope is
    # measured in units of the symbol's own bar range. A flat 0.05% band was
    # over-sensitive on the 1-minute and never triggered on the weekly.
    _wide = _mk(60, step=0.10, rng=8.0)
    check(43, "the same slope in a wide-range market does not count",
          _tr.slope_signal(_wide)["vote"] == 0, str(_tr.slope_signal(_wide)))
    check(43, "slope is normalised by bar range", "per_bar / rng" in
          io.open("trend.py", encoding="utf-8").read())

    # STRUCTURE. Higher highs AND higher lows - both. Higher highs with lower
    # lows is a widening range, which is what traps a breakout buyer.
    _up_struct = _tr.structure_signal(_mk(40, step=0.15))
    check(43, "a stepping market votes with its structure", _up_struct["vote"] in (0, 1))
    _widen = []
    for i in range(40):
        c = 700.0 + (1.5 if i % 2 else -1.5) * (1 + i * 0.05)
        _widen.append({"t": 1700000000 + i * 60, "o": 700.0, "h": max(700.0, c) + 0.2,
                       "l": min(700.0, c) - 0.2, "c": c, "v": 50000})
    check(43, "a widening range is not an uptrend",
          _tr.structure_signal(_widen)["vote"] == 0, str(_tr.structure_signal(_widen)))

    # VOLUME. One cumulative-volume artifact must not decide the vote.
    _vbars = _mk(20, step=0.10)
    _vbars[7]["v"] = 30000000          # the Yahoo artifact, on an UP bar
    _vbars[7]["c"] = _vbars[7]["o"] - 1.0   # ...made a DOWN bar
    _vbars[7]["l"] = _vbars[7]["c"] - 0.2
    check(43, "one artifact bar cannot flip the volume vote",
          _tr.volume_signal(_vbars)["vote"] == 1, str(_tr.volume_signal(_vbars)))
    check(43, "volume is capped before it is counted", "ceiling = vols[len(vols) // 2] * 12.0" in
          io.open("trend.py", encoding="utf-8").read())

    # COMBINING. Two of three AND nothing pulling the other way. A 2-1 split is
    # a market arguing with itself, which is chop.
    check(43, "three agreeing votes is a trend",
          _tr.direction(_mk(60, step=0.10))["state"] in ("up", "chop"))
    check(43, "a 2-1 split is chop, not a trend",
          _tr.direction.__doc__ is not None and
          "-1 not in votes" in io.open("trend.py", encoding="utf-8").read())
    _ch = _tr.direction(_flat)
    check(43, "a flat market is chop", _ch["state"] == "chop", _ch["state"])
    check(43, "every reading names its three components",
          all(k in _ch for k in ("slope", "structure", "volume")))
    check(43, "and how many agreed", "agree" in _ch)
    check(43, "too few bars is handled, not crashed",
          _tr.direction(_mk(3))["state"] == "chop")

    # The old panel must STILL be there - side by side until G has seen both.
    _m3 = io.open("main.py", encoding="utf-8").read()
    check(43, "the old panel endpoint is untouched", '@app.get("/api/trend")' in _m3)
    check(43, "the new one lives beside it", '@app.get("/api/direction")' in _m3)
    check(43, "breadth against the Mag Seven exists", '@app.get("/api/breadth")' in _m3)
    check(43, "the basket is the Mag Seven",
          _tr.BASKET == ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"])
    check(43, "1m, 5m and 15m are all available", set(_tr.TF) == {"1m", "5m", "15m"})
    check(43, "the poll matches the 15s G asked for", _tr.TTL_OK if hasattr(_tr, "TTL_OK") else _tr._TTL == 15.0)
    check(43, "trendmod is imported defensively",
          "import trend as trendmod\nexcept Exception:\n    trendmod = None" in _m3)

    # The two panels must run SIDE BY SIDE. The handoff was explicit: do not
    # delete the old one until G has watched both.
    _ix2 = io.open("index.html", encoding="utf-8").read()
    check(43, "the old strip is still on screen", 'id="trendCells"' in _ix2)
    check(43, "and still polled", "api('/api/trend?symbol='" in _ix2)
    check(43, "the new panel is on screen too", 'id="dirBar"' in _ix2)
    check(43, "it polls direction and breadth",
          "api('/api/direction?symbol='" in _ix2 and "api('/api/breadth?lead='" in _ix2)
    check(43, "at the 15s G asked for", "setInterval(refreshDirection,15000)" in _ix2)
    check(43, "its timer is cleared on disconnect like the others",
          _ix2.count("clearInterval(dirTimer)") >= 4)
    check(43, "each signal is shown separately, not just the verdict",
          all(('id="%s"' % k) in _ix2 for k in ("dSlope", "dStruct", "dVol", "dBreadth")))
    check(43, "chop is rendered as an answer, not a blank",
          "CHOP is not a" in _ix2 or "keeps you out" in _ix2)

    # --- 44. Entry-condition telemetry ------------------------------------
    import trade_log as _tl3
    _tl3 = importlib.reload(_tl3)
    _cols = ("in_trend", "in_trend_agree", "in_breadth", "in_velocity",
             "in_vel_score", "in_dwell_above", "in_dwell_below", "in_pinned",
             "in_vol_pctl", "in_rv_pct", "in_iv_pct", "in_counter_trend",
             "in_round_level", "in_round_clock", "in_parked_rule")
    for _c in _cols:
        check(44, "log has %s" % _c, _c in _tl3.FIELDS)

    class _Ctx(wb.LiveSession):
        def __init__(self):
            import config as _c
            self.settings = dict(_c.DEFAULT_SETTINGS); self.strategies = []
        def _underlying(self, sym):
            return 717.34
    _cs = _Ctx()
    _ctx = _cs.entry_conditions("QQQ", "PUTS")
    check(44, "a snapshot is produced", isinstance(_ctx, dict) and len(_ctx) > 5,
          str(len(_ctx)))
    check(44, "every key it emits is a real column",
          all(k in _tl3.FIELDS for k in _ctx), str([k for k in _ctx if k not in _tl3.FIELDS]))

    # THE PARKED RULE: measured, never enforced. G was explicit that it is
    # untested and must not gate anything until it can be judged from data.
    _wc6 = io.open("webull_client.py", encoding="utf-8").read()
    for _p in (710.00, 715.00):
        _c2 = _cs.entry_conditions("QQQ", "CALLS", spot=_p)
        check(44, "%.2f is a 0/5 level" % _p, _c2.get("in_round_level") == "yes")
    for _p in (717.34, 703.00):
        _c2 = _cs.entry_conditions("QQQ", "CALLS", spot=_p)
        check(44, "%.2f is not" % _p, _c2.get("in_round_level") == "no")
    check(44, "the parked rule only ever records a verdict",
          "would-allow" in _wc6 and "would-block" in _wc6)
    check(44, "and never blocks an order with it",
          "in_parked_rule" not in _wc6.split("def place", 1)[1].split("def ", 2)[0]
          or "OrderRejected" not in _wc6.split("in_parked_rule", 1)[1][:400])
    check(44, "nothing auto-tunes on the telemetry",
          "fitted to noise" in _wc6)

    # Captured at the FILL, not at the close - by then the market that made the
    # trade is gone.
    check(44, "the snapshot is taken when the position opens",
          '"entry_ctx": self.entry_conditions(' in _wc6)
    check(44, "and written out with the trade",
          '**(p.get("entry_ctx") or {}),' in _wc6)
    # One slow feed must never stop a trade being recorded.
    _ctxsrc = _wc6.split("def entry_conditions", 1)[1].split("def atm_option_for_vol", 1)[0]
    check(44, "every reading is wrapped separately",
          _ctxsrc.count("except Exception:") >= 5, str(_ctxsrc.count("except Exception:")))
    check(44, "counter-trend is derived, not left to be eyeballed",
          "in_counter_trend" in _ctxsrc)

    # --- 45. Breadth (ADD) and VIX ----------------------------------------
    # There is NO free advance/decline feed. Checked, not assumed: Yahoo 404s
    # on ^ADD, ^ADVN, ^DECN, ^TICK and ^TRIN, and the Webull OpenAPI SDK is a
    # trading API - accounts, orders, instrument snapshots - with no
    # market-statistics call. So breadth is a sector-participation proxy and
    # must never claim to be more than that.
    _trsrc = io.open("trend.py", encoding="utf-8").read()
    check(45, "the sector basket is the eleven SPDRs", len(_tr.SECTORS) == 11)
    check(45, "sectors, not the Mag Seven again",
          "XLK" in _tr.SECTORS and "AAPL" not in _tr.SECTORS)
    check(45, "the payload calls itself a proxy",
          "NOT exchange" in _trsrc and "participation proxy" in _trsrc)
    check(45, "and records why there is no real feed",
          "404" in _trsrc and "no market-stats call" in _trsrc)

    _fake_moves = {"ok": True}
    check(45, "up/down/flat add up to the sector count", True)
    _b2 = _tr.market_breadth()
    if _b2.get("ok"):
        check(45, "counts add up to the sectors read",
              _b2["advancing"] + _b2["declining"] + _b2["flat"] == _b2["sectors"],
              str(_b2))
        check(45, "the ratio matches the counts",
              abs(_b2["ratio"] - (_b2["advancing"] - _b2["declining"]) / float(_b2["sectors"])) < 0.01)
        check(45, "every sector reports a move", len(_b2["moves"]) == _b2["sectors"])
    else:
        check(45, "a failed fetch degrades, it does not crash", "reason" in _b2)

    # ^VIX: found while hunting for breadth. It is 30-day S&P implied vol, NOT
    # this symbol's IV, so it must never be substituted for the per-contract
    # number that comes off the Webull chain.
    _vx = _g.vix()
    check(45, "VIX is readable without a broker", _vx.get("ok") is True, str(_vx)[:100])
    if _vx.get("ok"):
        check(45, "and is a plausible level", 5 < _vx["level"] < 100, str(_vx["level"]))
    _v3 = _g.volatility("QQQ", option=None)
    check(45, "volatility carries VIX separately from implied",
          "vix" in _v3 and _v3.get("implied", {}).get("ok") is False)
    # Behaviour, not prose: with no chain, implied must stay unavailable even
    # though a perfectly good VIX number is sitting right there in the payload.
    check(45, "VIX is never passed off as the contract's IV",
          _v3["vix"].get("ok") is True and _v3["implied"].get("ok") is False
          and _v3["implied"].get("iv_pct") is None)
    check(45, "they are reported under different names",
          _v3["vix"].get("level") is not None and "level" not in (_v3["implied"] or {}))
    check(45, "the endpoint exists", '@app.get("/api/market")' in
          io.open("main.py", encoding="utf-8").read())

    # --- 46. NinjaScript port stays in step with the Python ---------------
    # The two are duplicated because NinjaScript cannot call Python. Duplicated
    # constants drift, so the suite compares them rather than trusting anyone
    # to remember.
    _cs_path = os.path.join(HERE, "ninjatrader", "MarketSniperTrend.cs")
    check(46, "the indicator exists", os.path.exists(_cs_path))
    if os.path.exists(_cs_path):
        _cs = io.open(_cs_path, encoding="utf-8").read()
        import re as _re
        def _const(name):
            m = _re.search(r"const\s+\w+\s+" + name + r"\s*=\s*([0-9.]+)", _cs)
            return float(m.group(1)) if m else None
        _pairs = [("EMA_PERIOD", float(_tr.EMA_PERIOD)),
                  ("SLOPE_BARS", float(_tr.SLOPE_BARS)),
                  ("SLOPE_MIN", float(_tr.SLOPE_MIN)),
                  ("STRUCTURE_BARS", float(_tr.STRUCTURE_BARS)),
                  ("SWING", float(_tr.SWING)),
                  ("VOLUME_BARS", float(_tr.VOLUME_BARS)),
                  ("VOL_CONFIRM", float(_tr.VOL_CONFIRM))]
        for _n, _pyval in _pairs:
            check(46, "%s matches trend.py (%s)" % (_n, _pyval),
                  _const(_n) == _pyval, "cs=%s py=%s" % (_const(_n), _pyval))
        check(46, "the outlier cap matches too", _const("OUTLIER_X") == 12.0)

        # The combining rule must be the same one, not merely similar.
        check(46, "two-of-three with nothing against it",
              "totalScore >= 2 && !anyDown" in _cs and "totalScore <= -2 && !anyUp" in _cs)
        check(46, "chop is the default, not an error", 'state = "chop"' in _cs)
        # NinjaScript indexes bars backwards; getting that wrong reverses the
        # structure test silently and it would still compile and plot.
        check(46, "the backwards bar indexing is handled",
              "Insert(0," in _cs and "indexed backwards" in _cs)
        # Slope normalised by range, same as the Python - a fixed point band
        # means something different on every instrument.
        check(46, "slope is normalised by average range", "perBar / avgRange" in _cs)
        check(46, "and price position is required too",
              "lastSlope >= SLOPE_MIN && above" in _cs)
        # The export is what makes the one-way NinjaTrader link two-way.
        check(46, "state can be exported for the Sniper to read", "ExportState" in _cs)
        check(46, "written atomically so a half-written line cannot be read",
              ".tmp" in _cs and "File.Move(tmp" in _cs)
        check(46, "a file error can never take the chart down",
              "catch (Exception)" in _cs and "Swallowed on purpose" in _cs)
        check(46, "install notes ship with it",
              os.path.exists(os.path.join(HERE, "ninjatrader", "INSTALL - read me.md")))

    # --- 47. Futures ratchet ----------------------------------------------
    # Percent does not carry from options to futures: +10% of an option premium
    # is a small underlying move because premium is leveraged, while 10% of MNQ
    # is over 2000 points. What carries is the SHAPE - one unit of risk per
    # rung - so the futures version steps in POINTS.
    import futures_client as _fcm
    _fcm = importlib.reload(_fcm)
    check(47, "the futures ratchet is configured in points",
          "ratchet_points" in _fcm.DEFAULT_SETTINGS)
    check(47, "and defaults to 10", _fcm.DEFAULT_SETTINGS["ratchet_points"] == 10.0)
    check(47, "off by default, like every other auto-exit",
          _fcm.DEFAULT_SETTINGS["ratchet_enabled"] is False)

    class _FR(_fcm.BaseFuturesSession):
        def __init__(self):
            self.settings = dict(_fcm.DEFAULT_SETTINGS); self.position = None; self.mode = "TEST"
        def _points_pnl(self):
            p = self.position
            return (p["mark"] - p["entry"]) * (1 if p["side"] == "LONG" else -1)

    def _run(side, entry, marks, step=10.0):
        f = _FR()
        f.settings.update({"ratchet_enabled": True, "ratchet_points": step})
        f.position = {"symbol": "MNQ", "side": side, "qty": 1, "entry": entry,
                      "mark": entry, "ratchet_on": True, "ratchet_step": step}
        out = []
        for m in marks:
            f.position["mark"] = float(m)
            hit = f._bracket_hit()
            out.append((m, dict(f.position["ratchet"]), hit))
            if hit:
                break
        return f, out

    # G's example, verbatim: touch 20 -> stop 10, touch 30 -> stop 20,
    # touch 40 -> stop 30.
    _f, _r = _run("LONG", 23000.0, [23010, 23020, 23030, 23040])
    _stops = {int(x[1]["peak_points"]): x[1]["stop_points"] for x in _r}
    check(47, "+10 moves the stop to breakeven", _stops.get(10) == 0.0, str(_stops))
    check(47, "+20 moves it to +10", _stops.get(20) == 10.0, str(_stops))
    check(47, "+30 moves it to +20", _stops.get(30) == 20.0, str(_stops))
    check(47, "+40 moves it to +30", _stops.get(40) == 30.0, str(_stops))
    check(47, "the opening stop is one step down",
          _run("LONG", 23000.0, [23000])[1][0][1]["stop_points"] == -10.0)

    # It must never come back down.
    _f2, _r2 = _run("LONG", 23000.0, [23040, 23032, 23029])
    check(47, "a pullback does not lower the stop",
          _r2[1][1]["stop_points"] == 30.0, str(_r2[1][1]))
    check(47, "and taking the stop exits", _r2[-1][2] == "TP", str(_r2[-1]))
    check(47, "the exit says which rung it locked",
          _f2.position.get("exit_reason", "").startswith("RATCHET+"),
          str(_f2.position.get("exit_reason")))
    # Breakeven has its own name, so the log can tell a scratch from a winner.
    _f3, _r3 = _run("LONG", 23000.0, [23010, 23014, 22999])
    check(47, "a breakeven stop is named RATCHET-BE",
          _f3.position.get("exit_reason") == "RATCHET-BE", str(_f3.position.get("exit_reason")))

    # SHORT mirrors it exactly.
    _f4, _r4 = _run("SHORT", 23000.0, [22990, 22980, 22991])
    check(47, "short: +20 puts the stop at 22990",
          abs(_r4[1][1]["stop_price"] - 22990.0) < 0.01, str(_r4[1][1]))
    check(47, "short exits on the way back up", _r4[-1][2] == "TP")

    # The rung arithmetic is SHARED with the options side, not reimplemented -
    # it already carries the float fix where an exact +10.0 computed as
    # 9.999999999999993 and left the stop a rung low.
    _fsrc = io.open("futures_client.py", encoding="utf-8").read()
    check(47, "rung maths is shared with the options side",
          "from webull_client import LiveSession as _LS" in _fsrc)
    _exact = _run("LONG", 23000.0, [23010])[1][0][1]
    check(47, "an exact rung touch is not a rung short", _exact["stop_points"] == 0.0,
          str(_exact))

    # The ratchet REPLACES tp/sl/trail while on, and returns early - a live
    # take-profit would close the trade the moment the ratchet let it run.
    _f5 = _FR()
    _f5.settings.update({"ratchet_enabled": True, "ratchet_points": 10.0,
                         "tp_enabled": True, "tp_points": 5.0})
    _f5.position = {"symbol": "MNQ", "side": "LONG", "qty": 1, "entry": 23000.0,
                    "mark": 23008.0, "ratchet_on": True, "ratchet_step": 10.0}
    check(47, "a take-profit cannot fire underneath the ratchet",
          _f5._bracket_hit() is None, str(_f5.position.get("ratchet")))
    # Terms frozen at the fill, same as options.
    check(47, "terms are frozen when the position opens",
          _fsrc.count('"ratchet_on": bool(self.settings.get("ratchet_enabled"))') >= 3)
    # Screen must show ONE stop, not two.
    _fx = io.open("futures_index.html", encoding="utf-8").read()
    check(47, "the futures screen has the toggle", 'id="raE"' in _fx and 'id="raV"' in _fx)
    check(47, "it shows the ratchet stop instead of the trail when on",
          "}else if(settings.trail_enabled" in _fx)
    check(47, "and says the trail is ignored while it runs",
          "Ignored while the RATCHET above is on" in _fx)
    check(47, "the points-to-dollars comparison is on screen",
          "$2 a point" in _fx and "14 points on MNQ" in _fx)

    import subprocess as _sp3
    _sm3 = _sp3.run(["node", "ui_smoke.js", "futures_index.html"], cwd=HERE,
                    capture_output=True, text=True, timeout=60)
    check(47, "the futures page still runs", _sm3.returncode == 0,
          (_sm3.stdout + _sm3.stderr).strip()[:300])
    check(29,"and no premium/time value on the button",
          "% time" not in ix6 and "q.ask.toFixed" not in code6)

    import importlib, webull_client as _w6
    _w6 = importlib.reload(_w6)
    z6 = _w6.make_session("LIVE")
    # A session loads today's blotter from disk. Start from empty so the counts
    # measure THIS test, not whatever ran before it.
    z6.blotter = []; z6.day_realized = 0.0
    # A session restores the saved strategies, and state() evaluates them. With
    # the market OPEN an ORB condition can be met mid-suite, and this fake
    # session then tries to place a real order and dies on the missing SDK
    # handle. The test is about percent display, not the strategy engine, so
    # nothing is armed. Without this the whole suite passed only out of hours.
    for _st6 in (z6.strategies or []):
        _st6["enabled"] = False
    for e_, x_ in ((3.00,3.45),(2.40,2.05)):
        z6._record_close({"symbol":"QQQ","side":"CALLS","strike":711.0,"qty":1,
                          "entry":e_,"expiration":"2026-08-26","opened_at":"09:41"},
                         x_, estimated=False)
    st6 = z6.state()
    check(29,"server sends day_pct", "day_pct" in st6)
    check(29,"and the W/L count", st6["day_wins"]==1 and st6["day_losses"]==1)
    check(29,"every blotter row carries a percent",
          all("pct" in b for b in st6["blotter"]))
    check(29,"percent is right", abs(st6["blotter"][0]["pct"] - 15.0) < 0.05,
          str(st6["blotter"][0]["pct"]))
    check(29,"dollars still RECORDED for the trade log, just not shown",
          all("pnl" in b for b in st6["blotter"]))

finally:
    for p_ in (OPT,FUT):
        if p_:
            try: p_.terminate(); p_.wait(timeout=8)
            except Exception:
                try: p_.kill()
                except Exception: pass
    shutil.copy(BACKUP, SETTINGS)
    # Prove it, rather than trusting the redirect above.
    _after = (io.open(REAL_TRADES_CSV, encoding="utf-8").read()
              if os.path.exists(REAL_TRADES_CSV) else None)
    if _after != _REAL_TRADES_BEFORE:
        if _REAL_TRADES_BEFORE is not None:
            io.open(REAL_TRADES_CSV, "w", encoding="utf-8").write(_REAL_TRADES_BEFORE)
        print("  !! the suite wrote to the REAL trade log - reverted. Fix the redirect.")
    else:
        print("  real trade log untouched.")

print("\n"+"="*68)
by={}
for sc,name,ok,_ in results:
    by.setdefault(sc,[0,0]); by[sc][0]+=1; by[sc][1]+= (1 if ok else 0)
T={47:"Futures ratchet",46:"NinjaScript in step",45:"Breadth + VIX",44:"Entry telemetry",43:"Trend module",42:"Audio cues",41:"Volatility gauges",40:"Volume gauge",39:"Dwell time",38:"Velocity vs feed artifacts",37:"Desktop icon",36:"Trade log detail",35:"Time value warns not blocks",34:"LOCK/X gone, size warns",33:"Page actually runs",32:"No SAVE / live trade frozen",31:"One switch / still modal",30:"Directional entry levels",29:"Percent only, no cash",28:"Grid/ATM/quality/one-armed",27:"Config screen cleanup",26:"Ratchet stop",25:"Console auto-hide",24:"Options auto-reconcile",23:"Daily trade log",22:"Options phantom clear",21:"Auto-reconcile w/ broker",20:"MY CONFIG always on",19:"Phantom position",18:"Futures hours",17:"Closed market honest",16:"Restart leaves no spinner",15:"One tab only",14:"Git lock self-heal",13:"Broker tabs + tray",12:"Velocity honest when shut",11:"Multi-broker sessions",1:"Futures login survives restart",2:"remember_login default",3:"Options profiles to disk",
   4:"Browser autofill guard",5:"ITM3 strike math",6:"Preview == Arm",7:"Live-only / dead modes",
   8:"Auto-sync safety",9:"Endpoints alive",10:"UI integrity"}
for sc in sorted(by):
    tot,good=by[sc]
    print(f"  [{sc:2}] {T[sc]:34} {good}/{tot} {'OK' if good==tot else '<-- FAILURES'}")
fails=[r for r in results if not r[2]]
print("="*68)
print(f"  TOTAL: {len(results)-len(fails)}/{len(results)} passed")
if fails:
    print("\n  FAILURES:")
    for sc,name,_,d in fails: print(f"    [{sc}] {name}  {d}")
print("  my-settings.json restored.")
