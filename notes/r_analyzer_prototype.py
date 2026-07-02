"""Prototype R analyzer — what aufwendige Auswertungen are feasible for R scripts.
Regex-based (R has no standard parser in our stack; tree-sitter-r exists in cbm but
only gives Function/Variable). This shows the R-specific structures we CAN extract."""
import re, os, glob, collections

def read(f):
    try: return open(f,encoding="utf-8",errors="ignore").read()
    except: return ""

files = glob.glob("rscripts/**/*.R", recursive=True) + glob.glob("rscripts/**/*.r", recursive=True)

# R-specific patterns
RE_FUNC   = re.compile(r"([A-Za-z.][\w.]*)\s*(?:<-|=)\s*function\s*\(([^)]*)\)")  # name <- function(args)
RE_LIB    = re.compile(r"\b(?:library|require)\s*\(\s*['\"]?([A-Za-z.][\w.]*)", re.I)
RE_PKGFN  = re.compile(r"\b([A-Za-z.][\w.]*)::([A-Za-z.][\w.]*)")                  # pkg::fn
RE_SOURCE = re.compile(r"\bsource\s*\(\s*['\"]([^'\"]+)")
RE_READ   = re.compile(r"\b(read\.csv|read_csv|read\.table|fread|readRDS|read_excel|dbGetQuery|read\.delim)\s*\(", re.I)
RE_WRITE  = re.compile(r"\b(write\.csv|write_csv|saveRDS|fwrite|write\.table|dbWriteTable)\s*\(", re.I)
RE_MODEL  = re.compile(r"\b(lm|glm|randomForest|xgboost|prcomp|kmeans|arima|gam|nnet|rpart)\s*\(", re.I)
RE_PIPE   = re.compile(r"%>%|\|>")
# call graph: any name( that matches a user-defined function
defs=set(); calls=collections.defaultdict(collections.Counter)
libs=collections.Counter(); pkgfns=collections.Counter(); reads=collections.Counter()
writes=collections.Counter(); models=collections.Counter(); pipes=0
func_table=[]  # (name, file, line, nargs)
file_funcs=collections.defaultdict(list)

for f in files:
    t=read(f); rel=os.path.basename(f)
    for m in RE_FUNC.finditer(t):
        name=m.group(1); nargs=len([a for a in m.group(2).split(",") if a.strip()])
        line=t[:m.start()].count("\n")+1
        defs.add(name); func_table.append((name,rel,line,nargs)); file_funcs[rel].append(name)
    for m in RE_LIB.findall(t): libs[m]+=1
    for a,b in RE_PKGFN.findall(t): pkgfns[f"{a}::{b}"]+=1
    for m in RE_READ.findall(t): reads[m.lower()]+=1
    for m in RE_WRITE.findall(t): writes[m.lower()]+=1
    for m in RE_MODEL.findall(t): models[m.lower()]+=1
    pipes+=len(RE_PIPE.findall(t))

# call graph: per file, count calls to user-defined funcs
callcount=collections.Counter()
for f in files:
    t=read(f)
    for d in defs:
        # count name( occurrences not preceded by 'function'/def
        c=len(re.findall(rf"(?<![\w.]){re.escape(d)}\s*\(", t))
        # subtract its own definition
        callcount[d]+=c
# subtract self-defs (each def has a "name <- function" not "name(")
print(f"R files: {len(files)}")
print(f"\nFUNKTIONEN definiert: {len(func_table)}")
for n,f,l,a in func_table: print(f"  {n}({a} args)  {f}:{l}")
print(f"\nMEISTGENUTZTE FUNKTIONEN (Aufruf-Häufigkeit):")
for n,c in callcount.most_common(8): print(f"  {c}×  {n}")
print(f"\nBIBLIOTHEKEN (library/require): {len(libs)}")
for n,c in libs.most_common(): print(f"  {c}×  {n}")
print(f"\nPAKET::FUNKTION-Aufrufe: {dict(pkgfns)}")
print(f"\nDATEN-I/O: lesen={dict(reads)}  schreiben={dict(writes)}")
print(f"STATISTISCHE MODELLE: {dict(models)}")
print(f"PIPES (%>% / |>): {pipes}")
