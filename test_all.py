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
    check(12,"raw compute on a close spike DOES read violent (the trap)",
          raw["state"]=="violent", raw["state"])
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

    # --- 30. Directional entry: calls below, puts above ------------------
    _L = wb.LiveSession
    for _spot in (713.40, 707.60, 712.30, 709.80, 702.55, 710.00, 707.50):
        _tc = _L.entry_target(_spot, "CALLS")
        _tp = _L.entry_target(_spot, "PUTS")
        check(30, "%.2f calls trigger at or below spot (%.2f)" % (_spot, _tc),
              _tc <= _spot + 1e-9)
        check(30, "%.2f puts trigger at or above spot (%.2f)" % (_spot, _tp),
              _tp >= _spot - 1e-9)
    # The bug this replaces: with a NEAREST target, arming calls at 709.80 set
    # the trigger to 710.00, and _maybe_trigger_entry asks spot <= target -
    # true on the spot, so it bought instantly instead of waiting for 709.
    check(30, "calls at 709.80 wait for 709.00, not 710.00",
          abs(_L.entry_target(709.80, "CALLS") - 709.00) < 1e-9)
    check(30, "puts at 709.80 wait for 710.00",
          abs(_L.entry_target(709.80, "PUTS") - 710.00) < 1e-9)
    # Half-levels count for BOTH sides, not just calls.
    check(30, "puts at 712.30 take the .50 level above (712.50)",
          abs(_L.entry_target(712.30, "PUTS") - 712.50) < 1e-9)
    check(30, "calls at 707.60 take the .50 level below (707.50)",
          abs(_L.entry_target(707.60, "CALLS") - 707.50) < 1e-9)
    # Sitting exactly ON a level triggers there, both ways - not one step away.
    check(30, "spot exactly on a level: calls fire there",
          abs(_L.entry_target(710.00, "CALLS") - 710.00) < 1e-9)
    check(30, "spot exactly on a level: puts fire there",
          abs(_L.entry_target(707.50, "PUTS") - 707.50) < 1e-9)
    # A .50 target printed with %.0f reads as a whole dollar it never was.
    _wc = io.open("webull_client.py", encoding="utf-8").read()
    check(30, "trigger is announced to 2dp, not rounded to a dollar",
          "{a['target']:.0f}" not in _wc)
    check(30, "arm() asks for a side-specific target",
          "self.entry_target(spot, side)" in _wc)

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
    check(32, "terms are frozen onto the position at open",
          '"ratchet_on": bool(self.settings.get("my_enabled"))' in _wc2 and
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
    check(29,"and no premium/time value on the button",
          "% time" not in ix6 and "q.ask.toFixed" not in code6)

    import importlib, webull_client as _w6
    _w6 = importlib.reload(_w6)
    z6 = _w6.make_session("LIVE")
    # A session loads today's blotter from disk. Start from empty so the counts
    # measure THIS test, not whatever ran before it.
    z6.blotter = []; z6.day_realized = 0.0
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

print("\n"+"="*68)
by={}
for sc,name,ok,_ in results:
    by.setdefault(sc,[0,0]); by[sc][0]+=1; by[sc][1]+= (1 if ok else 0)
T={34:"LOCK/X gone, size warns",33:"Page actually runs",32:"No SAVE / live trade frozen",31:"One switch / still modal",30:"Directional entry levels",29:"Percent only, no cash",28:"Grid/ATM/quality/one-armed",27:"Config screen cleanup",26:"Ratchet stop",25:"Console auto-hide",24:"Options auto-reconcile",23:"Daily trade log",22:"Options phantom clear",21:"Auto-reconcile w/ broker",20:"MY CONFIG always on",19:"Phantom position",18:"Futures hours",17:"Closed market honest",16:"Restart leaves no spinner",15:"One tab only",14:"Git lock self-heal",13:"Broker tabs + tray",12:"Velocity honest when shut",11:"Multi-broker sessions",1:"Futures login survives restart",2:"remember_login default",3:"Options profiles to disk",
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
