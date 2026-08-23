"""Market Sniper v3.7 — 10-scenario regression suite."""
import io, json, os, re, shutil, subprocess, sys, time, urllib.request, urllib.error

HERE = "/sessions/stoic-brave-ritchie/mnt/Market Sniper"
sys.path.insert(0, HERE); os.chdir(HERE)
SETTINGS = os.path.join(HERE, "my-settings.json"); BACKUP = "/tmp/ms/settings.realbackup"
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
    OPT = boot("main:app", 8000, "/tmp/ms/opt1.log")
    FUT = boot("futures_app:app", 8010, "/tmp/ms/fut1.log")
    time.sleep(11)

    print("\n[1] FUTURES LOGIN SURVIVES A RESTART")
    http("http://127.0.0.1:8010/api/prefs", "POST", {
        "mode":"TOPSTEP","ts_user":"giancuffaro230","ts_acct":"EXPRESS-V2-CT-DLL-132001-66482406",
        "ts_key":"TSKEY","wb_key":"WBKEY","wb_sec":"WBSEC","nt_account":"1114140","nt_folder":"C:/nt/incoming"})
    FUT.terminate(); FUT.wait(timeout=10)
    FUT = boot("futures_app:app", 8010, "/tmp/ms/fut2.log"); time.sleep(9)
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
    OPT = boot("main:app", 8000, "/tmp/ms/opt2.log"); time.sleep(9)
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
    for log in ("/tmp/ms/opt2.log","/tmp/ms/fut2.log"):
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
                f_=f"/tmp/_chk_{page}_{i}.js"
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
    check(10,"cost shown on buy buttons", "fmtCost" in idx)
    check(10,"ITM3 button present", "smITM3" in idx)
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
    check(13,"silent .vbs launcher exists",
          any("START HIDDEN" in f for f in os.listdir(HERE)))
    inst = [f for f in os.listdir(HERE) if f.endswith("INSTALL.bat")]
    itxt = io.open(os.path.join(HERE,inst[0]),encoding="utf-8").read() if inst else ""
    req  = io.open(os.path.join(HERE,"requirements.txt"),encoding="utf-8").read()
    check(13,"installer installs the tray deps", "pystray" in itxt)
    check(13,"tray deps NOT in requirements (runs every launch)",
          not any(l.strip()=="pystray" for l in req.splitlines()))
    check(13,"installer clears Mark-of-the-Web", "Unblock-File" in itxt)
    check(13,"installer survives optional tray failure",
          "app runs fine" in itxt or "Not a problem" in itxt)
    check(13,"installer fails LOUDLY on core deps", "coredeps_failed" in itxt)
    check(13,"installer warns about SmartScreen", "SmartScreen" in itxt)
    check(13,"installer names the real launcher files",
          "START MARKET SNIPER.bat" in itxt and "START HIDDEN" in itxt)

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
    check(17,"futures: refuses to SEND into a shut market",
          "No order was sent" in fut)
    check(17,"futures: buttons repaint when velocity lands",
          "paintMarketState" in fut.split("async function refreshVel",1)[1][:400])
    check(17,"options: velocity awaited before the first quote",
          "refreshVel().then(refreshQuote)" in opt)

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
T={17:"Closed market honest",16:"Restart leaves no spinner",15:"One tab only",14:"Git lock self-heal",13:"Broker tabs + tray",12:"Velocity honest when shut",11:"Multi-broker sessions",1:"Futures login survives restart",2:"remember_login default",3:"Options profiles to disk",
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
