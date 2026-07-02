"""R analyzer v2 — tuned to the REAL IFRS-9 scripts: Base-R, source()-graph,
data-flow paths, global-state coupling, duplicate-function detection."""
import re, os, glob, collections

def read(f):
    try: return open(f,encoding="utf-8",errors="ignore").read()
    except: return ""

RE_FUNC   = re.compile(r"^\s*([A-Za-z.][\w.]*)\s*(?:<-|=)\s*function\s*\(([^)]*)\)", re.M)
RE_SOURCE = re.compile(r"source\s*\(\s*(?:paste0?\s*\([^)]*\)|['\"][^'\"]*['\"])", re.I)
RE_SRCARG = re.compile(r"source\s*\(\s*(.+?)\)", re.I|re.S)
RE_READ   = re.compile(r"\b(read\.csv2?|read_csv|fread|read\.table|readRDS|read_excel|read\.delim)\s*\(\s*([^,)]+)", re.I)
RE_WRITE  = re.compile(r"\b(write\.csv2?|write\.table|write_csv|fwrite|saveRDS|write\.xlsx)\s*\([^,]*,\s*([^,)]+)", re.I)
RE_LIB    = re.compile(r"\b(?:library|require)\s*\(\s*['\"]?([A-Za-z.][\w.]*)", re.I)

def analyze(root):
    files = sorted(glob.glob(os.path.join(root,"**","*.R"),recursive=True)
                 + glob.glob(os.path.join(root,"**","*.r"),recursive=True))
    funcs=collections.defaultdict(list)   # name -> [(file,line,nargs)]
    file_funcs=collections.defaultdict(list)
    sources=[]                            # (file -> sourced target)
    reads=[]; writes=[]                   # (file, path-expr)
    libs=collections.Counter()
    text={}
    for fp in files:
        t=read(fp); rel=os.path.relpath(fp,root); text[rel]=t
        for m in RE_FUNC.finditer(t):
            name=m.group(1); nargs=len([a for a in m.group(2).split(",") if a.strip()])
            line=t[:m.start()].count("\n")+1
            funcs[name].append((rel,line,nargs)); file_funcs[rel].append(name)
        for m in RE_SRCARG.finditer(t):
            arg=m.group(1).strip()[:80]
            tgt=re.findall(r"['\"]([^'\"]+\.R)['\"]", arg, re.I)
            sources.append((rel, tgt[0] if tgt else arg))
        for fn,p in RE_READ.findall(t): reads.append((rel, fn, p.strip()[:90]))
        for fn,p in RE_WRITE.findall(t): writes.append((rel, fn, p.strip()[:90]))
        for l in RE_LIB.findall(t): libs[l]+=1
    # duplicate functions (same name defined in >1 file)
    dups={n:locs for n,locs in funcs.items() if len({l[0] for l in locs})>1}
    # call-frequency: count name( across corpus minus its own defs
    callcount=collections.Counter()
    for n in funcs:
        c=sum(len(re.findall(rf"(?<![\w.]){re.escape(n)}\s*\(", t)) for t in text.values())
        callcount[n]=c-len(funcs[n])  # subtract the definitions themselves
    # global-state coupling: top-level assigned names used INSIDE function bodies
    return dict(files=[os.path.relpath(f,root) for f in files], funcs=funcs,
                file_funcs=file_funcs, dups=dups, sources=sources, reads=reads,
                writes=writes, libs=libs, callcount=callcount)

if __name__=="__main__":
    import sys
    r=analyze(sys.argv[1])
    print(f"R files: {len(r['files'])}  |  Funktionen: {sum(len(v) for v in r['funcs'].values())}")
    print(f"\n=== FUNKTIONEN & AUFRUF-HÄUFIGKEIT ===")
    for n,c in r['callcount'].most_common(): 
        loc=r['funcs'][n][0]; print(f"  {c:2d}×  {n}  ({loc[0]}:{loc[1]}, {loc[2]} args)")
    print(f"\n=== DUPLIKATE (gleiche Funktion in mehreren Dateien) ===")
    for n,locs in r['dups'].items(): print(f"  ⚠ {n}: " + ", ".join(f"{f}:{l}" for f,l,_ in locs))
    print(f"\n=== source()-ABHÄNGIGKEITEN ===")
    for a,b in r['sources']: print(f"  {a}  →  {b}")
    print(f"\n=== DATEN-FLUSS ===")
    for f,fn,p in r['reads'][:6]: print(f"  READ  {f}: {fn}({p})")
    for f,fn,p in r['writes'][:6]: print(f"  WRITE {f}: {fn}(…,{p})")
