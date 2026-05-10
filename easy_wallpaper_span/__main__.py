import sys, os, re, math, copy, shutil, time, shlex, subprocess, json, argparse
from pathlib import Path

def detect_session():
    if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        return 'hyprland'
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
    if 'kde' in desktop or 'plasma' in desktop:
        return 'kde'
    return 'x11'

from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *

SNAP_PX   = 16
HANDLE_R  = 7
MODES     = ["Respect Aspect Ratio", "Warp"]
WALL_DIR  = Path.home() / '.local/share/easy-wallpaper-span'
MONS_CFG  = WALL_DIR / 'monitors.json'
LAST_CFG  = WALL_DIR / 'last.conf'
PROFILES_DIR = WALL_DIR / 'profiles'
LAST_PROFILE_FILE = WALL_DIR / 'last_profile.txt'
DEF_COLORS = [
    QColor(70,130,210), QColor(210,90,70),  QColor(70,190,110),
    QColor(210,170,40), QColor(150,70,210), QColor(40,190,190),
]
# FACE_CORNERS[fi] = (ca, cb) into get_corners() [TL,TR,BL,BR]; fi: 0=top 1=right 2=bottom 3=left
FACE_CORNERS = [(0,1),(1,3),(2,3),(0,2)]
# fields copied into/out of saved monitor records
MON_SAVE = ('x','y','w','h','pts','orig_rect','locked','color','group','override','disabled')


# ── quad helpers ──────────────────────────────────────────────────────────────

def get_corners(m):
    if m.get('pts'): return [list(p) for p in m['pts']]
    x,y,w,h = m['x'],m['y'],m['w'],m['h']
    return [[x,y],[x+w,y],[x,y+h],[x+w,y+h]]

def corners_to_rect(pts):
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    x,y=min(xs),min(ys); return x,y,max(1,max(xs)-x),max(1,max(ys)-y)

def is_rect(pts):
    return (abs(pts[0][0]-pts[2][0])<1 and abs(pts[1][0]-pts[3][0])<1 and
            abs(pts[0][1]-pts[1][1])<1 and abs(pts[2][1]-pts[3][1])<1)

def mon_color(m, idx):
    c = m.get('color')
    if c:
        col = QColor(c)
        if col.isValid(): return col
    return DEF_COLORS[idx % len(DEF_COLORS)]

def _poly_path(poly):
    path = QPainterPath(); path.addPolygon(poly); path.closeSubpath(); return path

def _mon_disabled(m, groups_map):
    """True if monitor is effectively disabled (individual flag OR group flag)."""
    if m.get('disabled'): return True
    grp = m.get('group')
    if grp and groups_map.get(grp, {}).get('disabled'): return True
    return False


# ── data dir migration ────────────────────────────────────────────────────────

def _migrate_data_dir():
    old = Path.home() / '.local/share/wallpaper-span'
    if old.exists() and not WALL_DIR.exists():
        shutil.copytree(str(old), str(WALL_DIR))


# ── monitor helpers ───────────────────────────────────────────────────────────

def read_monitors_xrandr():
    rotations={}; native_mm={}
    for line in subprocess.check_output(["xrandr"]).decode().splitlines():
        m = re.match(r'^(\S+) connected(?: primary)?\s+\d+x\d+\+\d+\+\d+\s+'
                     r'(normal|left|right|inverted)\s.*?(\d+)mm x (\d+)mm', line)
        if m:
            name,rot,wmm,hmm = m.group(1),m.group(2),int(m.group(3)),int(m.group(4))
            rotations[name]=rot; native_mm[name]=(wmm,hmm)
    mons=[]
    for line in subprocess.check_output(["xrandr","--listmonitors"]).decode().splitlines()[1:]:
        m = re.match(r'\s*(\d+):\s+[+*]*(\S+)\s+(\d+)/(\d+)x(\d+)/(\d+)\+(\d+)\+(\d+)', line)
        if m:
            _,dname,pw,pmw,ph,pmh,px,py = m.groups()
            rot=rotations.get(dname,'normal')
            wmm,hmm=native_mm.get(dname,(float(pmw),float(pmh)))
            if rot in ('left','right'): wmm,hmm=hmm,wmm
            mons.append(dict(name=dname,x=int(px),y=int(py),screen_x=int(px),screen_y=int(py),
                             w=int(pw),h=int(ph),phys_w=int(pw),phys_h=int(ph),
                             phys_w_mm=float(wmm),phys_h_mm=float(hmm),rotation=rot))
    return mons

def read_monitors_hyprland():
    data=json.loads(subprocess.check_output(['hyprctl','monitors','-j']).decode())
    mons=[]
    for m in data:
        scale=float(m.get('scale',1.0))
        pw,ph=m['width'],m['height']
        # transforms 1,3,5,7 are 90/270° rotations — swap logical dimensions
        if m.get('transform',0) in (1,3,5,7): pw,ph=ph,pw
        lw=round(pw/scale); lh=round(ph/scale)
        mons.append(dict(name=m['name'],x=m['x'],y=m['y'],
                         screen_x=m['x'],screen_y=m['y'],
                         w=lw,h=lh,phys_w=pw,phys_h=ph,
                         phys_w_mm=0.0,phys_h_mm=0.0,
                         scale=scale,rotation='normal'))
    return mons

def read_monitors():
    if detect_session()=='hyprland': return read_monitors_hyprland()
    return read_monitors_xrandr()

def merge_monitors(sys_mons, saved_list):
    saved={d['name']:d for d in saved_list}
    def apply(m):
        s=saved.get(m['name'])
        if not s: return m
        r={**m}
        for f in MON_SAVE:
            if f in s: r[f]=s[f]
        return r
    return [apply(m) for m in sys_mons]

def load_saved_monitors(sys_mons):
    if not MONS_CFG.exists(): return sys_mons
    try: return merge_monitors(sys_mons, json.loads(MONS_CFG.read_text()))
    except Exception: return sys_mons

def save_monitors(mons):
    WALL_DIR.mkdir(parents=True,exist_ok=True)
    records=[]
    for m in mons:
        r={'name':m['name']}
        for f in MON_SAVE:
            v=m.get(f)
            if v is not None: r[f]=v
        records.append(r)
    MONS_CFG.write_text(json.dumps(records,indent=2))

def read_last_conf():
    cfg={}
    for line in LAST_CFG.read_text().splitlines():
        if '=' in line:
            k,_,v=line.partition('=')
            try: cfg[k.strip()]=shlex.split(v.strip())[0]
            except Exception: cfg[k.strip()]=v.strip()
    return cfg


# ── profile helpers ───────────────────────────────────────────────────────────

def list_profiles():
    if not PROFILES_DIR.exists(): return []
    return sorted(p.stem for p in PROFILES_DIR.glob('*.json'))

def load_profile(name):
    p=PROFILES_DIR/f'{name}.json'
    return json.loads(p.read_text()) if p.exists() else None

def save_profile(name, state):
    PROFILES_DIR.mkdir(parents=True,exist_ok=True)
    mons_data=[]
    for m in state['monitors']:
        r={'name':m['name']}
        for f in MON_SAVE:
            v=m.get(f)
            if v is not None: r[f]=v
        mons_data.append(r)
    (PROFILES_DIR/f'{name}.json').write_text(json.dumps({
        'name':name,'image':state.get('img',''),
        'ox':state.get('ox',0),'oy':state.get('oy',0),
        'groups':state.get('groups',[]),'lock_bg':state.get('lock_bg',False),
        'monitors':mons_data,
    },indent=2))

def delete_profile(name):
    p=PROFILES_DIR/f'{name}.json'; p.exists() and p.unlink()


# ── core apply ────────────────────────────────────────────────────────────────

def _apply_mon_set(mon_list, img, ox, oy, out_crops, ts, use_physical=False):
    if not mon_list or not img or not os.path.isfile(img): return
    tx=min(m['x'] for m in mon_list); ty=min(m['y'] for m in mon_list)
    tw=max(m['x']+m['w'] for m in mon_list)-tx
    th=max(m['y']+m['h'] for m in mon_list)-ty
    uid=abs(hash(f"{img}{ox}{oy}{''.join(m['name'] for m in mon_list)}"))%100000
    scaled=WALL_DIR/f'scaled_{ts}_{uid}.jpg'
    if use_physical:
        # Scale span image to the max physical resolution for HiDPI-correct crops
        sc=max(m.get('scale',1.0) for m in mon_list)
        ptw=round(tw*sc); pth=round(th*sc)
        psw=ptw+2*round(abs(ox)*sc); psh=pth+2*round(abs(oy)*sc)
        r=subprocess.run(['magick',img,'-resize',f'{psw}x{psh}^','-gravity','Center',
                          '-crop',f'{ptw}x{pth}+{round(ox*sc)}+{round(oy*sc)}',
                          '+repage',str(scaled)],capture_output=True)
        if r.returncode: return
        for m in mon_list:
            mx=round((m['x']-tx)*sc); my=round((m['y']-ty)*sc)
            pw=m.get('phys_w',round(m['w']*sc)); ph=m.get('phys_h',round(m['h']*sc))
            cp=WALL_DIR/f"crop_{m['x']}_{m['y']}_{ts}.jpg"
            subprocess.run(['magick',str(scaled),'-crop',f"{pw}x{ph}+{mx}+{my}",'+repage',str(cp)],check=True)
            out_crops[(m.get('screen_x',m['x']),m.get('screen_y',m['y']))]=str(cp)
    else:
        sw=tw+2*abs(ox); sh=th+2*abs(oy)
        r=subprocess.run(['magick',img,'-resize',f'{sw}x{sh}^','-gravity','Center',
                          '-crop',f'{tw}x{th}+{ox}+{oy}','+repage',str(scaled)],capture_output=True)
        if r.returncode: return
        for m in mon_list:
            mx=m['x']-tx; my=m['y']-ty
            cp=WALL_DIR/f"crop_{m['x']}_{m['y']}_{ts}.jpg"
            subprocess.run(['magick',str(scaled),'-crop',f"{m['w']}x{m['h']}+{mx}+{my}",'+repage',str(cp)],check=True)
            out_crops[(m.get('screen_x',m['x']),m.get('screen_y',m['y']))]=str(cp)

def _build_crops(state, ts, use_physical=False):
    ms=state['monitors']; groups_cfg={g['name']:g for g in state.get('groups',[])}
    WALL_DIR.mkdir(parents=True,exist_ok=True)
    for f in WALL_DIR.glob('crop_*.jpg'): f.unlink(missing_ok=True)
    crops={}
    active_ms=[m for m in ms if not _mon_disabled(m, groups_cfg)]
    override_ms=[m for m in active_ms if m.get('override')]
    group_ms={}
    for m in active_ms:
        if not m.get('override') and m.get('group'):
            group_ms.setdefault(m['group'],[]).append(m)
    global_ms=[m for m in active_ms if not m.get('override') and not m.get('group')]
    for gname,gmons in list(group_ms.items()):
        g=groups_cfg.get(gname,{}); gi=g.get('img','')
        if not gi or not os.path.isfile(gi):
            global_ms.extend(gmons); del group_ms[gname]
    _apply_mon_set(global_ms,state.get('img',''),state.get('ox',0),state.get('oy',0),crops,ts,use_physical)
    for gname,gmons in group_ms.items():
        g=groups_cfg.get(gname,{})
        _apply_mon_set(gmons,g.get('img',''),g.get('ox',0),g.get('oy',0),crops,ts,use_physical)
    for m in override_ms:
        ov=m['override']
        _apply_mon_set([m],ov.get('img',''),ov.get('ox',0),ov.get('oy',0),crops,ts,use_physical)
    return crops

def _apply_state_kde(state, ts):
    ms=state['monitors']; groups_cfg={g['name']:g for g in state.get('groups',[])}
    crops=_build_crops(state,ts)
    disabled_ms=[m for m in ms if _mon_disabled(m, groups_cfg)]
    disabled_set=set((m.get('screen_x',m['x']),m.get('screen_y',m['y'])) for m in disabled_ms)
    if not crops and not disabled_ms:
        raise RuntimeError("No wallpaper images applied (check image paths).")
    lines=['var dl=desktops();','for(var i=0;i<dl.length;i++){',
           '  var d=dl[i]; if(d.screen<0) continue;',
           '  var g=screenGeometry(d.screen); var img=null; var reset=false;']
    for (cx,cy),path in crops.items():
        lines.append(f'  if(g.x==={cx}&&g.y==={cy}) img="file://{path}";')
    for cx,cy in disabled_set:
        if (cx,cy) not in crops:
            lines.append(f'  if(g.x==={cx}&&g.y==={cy}) reset=true;')
    lines+=['  if(!img&&!reset) continue;',
            "  d.wallpaperPlugin='org.kde.image';",
            "  d.currentConfigGroup=['Wallpaper','org.kde.image','General'];",
            "  if(reset) d.writeConfig('Image','');",
            "  else{ d.writeConfig('Image',img); d.writeConfig('FillMode',2); }",
            '}']
    cmd='qdbus6' if shutil.which('qdbus6') else 'qdbus'
    r=subprocess.run([cmd,'org.kde.plasmashell','/PlasmaShell',
                      'org.kde.PlasmaShell.evaluateScript','\n'.join(lines)],
                     capture_output=True,text=True)
    if r.returncode: raise RuntimeError(r.stderr or r.stdout)

def _apply_state_hyprland(state, ts):
    ms=state['monitors']; groups_cfg={g['name']:g for g in state.get('groups',[])}
    crops=_build_crops(state,ts,use_physical=True)
    if not crops:
        raise RuntimeError("No wallpaper images applied (check image paths).")
    # map (screen_x,screen_y) -> monitor name
    pos_to_name={( m.get('screen_x',m['x']), m.get('screen_y',m['y']) ):m['name']
                 for m in ms if not _mon_disabled(m,groups_cfg)}
    name_crops={pos_to_name[pos]:path for pos,path in crops.items() if pos in pos_to_name}
    if shutil.which('swww'):
        _hypr_set_swww(name_crops)
    elif shutil.which('hyprpaper') or shutil.which('hyprctl'):
        _hypr_set_hyprpaper(name_crops)
    else:
        raise RuntimeError(
            "No Hyprland wallpaper tool found. Install 'swww' or 'hyprpaper' and make sure it's running.")

def _hypr_set_swww(name_crops):
    for mon,path in name_crops.items():
        r=subprocess.run(['swww','img','--outputs',mon,path],capture_output=True,text=True)
        if r.returncode: raise RuntimeError(f"swww failed for {mon}: {r.stderr or r.stdout}")

def _hypr_set_hyprpaper(name_crops):
    for path in set(name_crops.values()):
        r=subprocess.run(['hyprctl','hyprpaper','preload',path],capture_output=True,text=True)
        if r.returncode: raise RuntimeError(f"hyprpaper preload failed: {r.stderr or r.stdout}")
    for mon,path in name_crops.items():
        r=subprocess.run(['hyprctl','hyprpaper','wallpaper',f'{mon},{path}'],
                         capture_output=True,text=True)
        if r.returncode: raise RuntimeError(f"hyprpaper set failed for {mon}: {r.stderr or r.stdout}")

def apply_state(state, save_conf=True):
    ts=int(time.time()); session=detect_session()
    if session=='hyprland': _apply_state_hyprland(state,ts)
    else: _apply_state_kde(state,ts)
    if save_conf:
        ms=state['monitors']; img=state.get('img','')
        ext=Path(img).suffix if img else '.jpg'
        sc=WALL_DIR/f'source{ext}'
        if img and os.path.isfile(img) and img!=str(sc): shutil.copy2(img,sc)
        LAST_CFG.write_text(f"IMAGE={shlex.quote(str(sc) if img else '')}\n"
                            f"X_OFF={shlex.quote(str(state.get('ox',0)))}\n"
                            f"Y_OFF={shlex.quote(str(state.get('oy',0)))}\n")
        save_monitors(ms)

def apply_wallpaper(img, ms, ox=0, oy=0, save_conf=True):
    apply_state({'monitors':ms,'img':img,'ox':ox,'oy':oy,'groups':[]},save_conf=save_conf)


# ── canvas ────────────────────────────────────────────────────────────────────

def mk_state(mons, img='', ox=0, oy=0, groups=None, lock_bg=False):
    return dict(monitors=copy.deepcopy(mons),img=img,ox=ox,oy=oy,
                groups=groups or [],lock_bg=lock_bg)

def cl(obj): return copy.deepcopy(obj)

def make_cal_image(tw, th, step):
    img=QImage(tw,th,QImage.Format.Format_RGB888); p=QPainter(img)
    pal=[QColor(220,50,50),QColor(50,200,50),QColor(50,100,220)]
    for row in range(math.ceil(th/step)):
        for col in range(math.ceil(tw/step)):
            p.fillRect(col*step,row*step,step,step,pal[(row+col)%3])
    p.end(); return img


class Canvas(QWidget):
    changed      = pyqtSignal()
    sel_changed  = pyqtSignal(int)   # monitor index or -1

    def __init__(self):
        super().__init__()
        self.setMinimumSize(650,420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.state       = mk_state([])
        self._sc         = 1.0
        self._ox         = self._oy = 0.0
        self._pm_cache: dict = {}
        self._cal_pm: QPixmap | None = None
        self._drag       = None
        self._drag_img_bbox = None
        self._sel_corner: tuple | None = None
        self._sel_mon    = -1
        self.arrow_step  = 1
        self.mode        = MODES[0]

    # ── coordinate transforms ─────────────────────────────────────────────────
    def w2c(self,x,y): return QPointF(x*self._sc+self._ox, y*self._sc+self._oy)
    def c2w(self,cx,cy): return ((cx-self._ox)/self._sc,(cy-self._oy)/self._sc)
    def mr(self,m):
        tl=self.w2c(m['x'],m['y'])
        return QRectF(tl,QSizeF(m['w']*self._sc,m['h']*self._sc))

    def m_screen_corners(self,m):
        return [self.w2c(p[0],p[1]) for p in get_corners(m)]
    def m_screen_poly(self,m):
        pts=get_corners(m)
        return QPolygonF([self.w2c(pts[i][0],pts[i][1]) for i in (0,1,3,2)])
    def m_screen_face_mids(self,m):
        pts=get_corners(m)
        return [self.w2c((pts[ca][0]+pts[cb][0])/2,(pts[ca][1]+pts[cb][1])/2)
                for ca,cb in FACE_CORNERS]

    def _img_bbox(self):
        ms=self.state['monitors']
        if not ms: return None
        tx=min(m['x'] for m in ms); ty=min(m['y'] for m in ms)
        return (tx,ty,max(m['x']+m['w'] for m in ms)-tx,max(m['y']+m['h'] for m in ms)-ty)

    # ── state management ──────────────────────────────────────────────────────
    def set_state(self,s):
        self.state=cl(s); self._pm_cache.clear(); self._refit(); self.update()

    def _refit(self):
        ms=self.state['monitors']
        if not ms: return
        min_x=min(m['x'] for m in ms); max_x=max(m['x']+m['w'] for m in ms)
        min_y=min(m['y'] for m in ms); max_y=max(m['y']+m['h'] for m in ms)
        pad=50
        self._sc=min((self.width()-2*pad)/max(max_x-min_x,1),
                     (self.height()-2*pad)/max(max_y-min_y,1),1.0)
        self._ox=(self.width() -(max_x-min_x)*self._sc)/2-min_x*self._sc
        self._oy=(self.height()-(max_y-min_y)*self._sc)/2-min_y*self._sc

    def _get_pm(self,path):
        if not path or not os.path.isfile(path): return None
        if path not in self._pm_cache: self._pm_cache[path]=QPixmap(path)
        return self._pm_cache[path]

    def set_cal(self,qimage):
        self._cal_pm=QPixmap.fromImage(qimage) if qimage else None; self.update()

    def resizeEvent(self,_): self._refit(); self.update()
    def fit_view(self): self._refit(); self.update()

    # ── per-monitor image source ──────────────────────────────────────────────
    def _mon_src(self, m, global_tx, global_ty, global_tw, global_th):
        """Return (pm, src_r, span_tl_canvas, span_w_world, span_h_world).
        src_r is in pixmap coords; span_* are the coordinate system src_r belongs to."""
        groups_map={g['name']:g for g in self.state.get('groups',[])}
        if _mon_disabled(m, groups_map): return None,None,None,None,None
        if self._cal_pm:
            pm=self._cal_pm
            return pm, QRectF(0,0,pm.width(),pm.height()), \
                   self.w2c(global_tx,global_ty), global_tw, global_th

        ov=m.get('override')
        if ov and ov.get('img'):
            pm=self._get_pm(ov['img'])
            if pm and pm.width()>0:
                ox=ov.get('ox',0); oy=ov.get('oy',0)
                tw,th=m['w'],m['h']; aox=abs(ox); aoy=abs(oy)
                sf=max((tw+2*aox)/pm.width(),(th+2*aoy)/pm.height())
                cx0=(pm.width()-tw/sf)/2+ox/sf; cy0=(pm.height()-th/sf)/2+oy/sf
                return pm, QRectF(cx0,cy0,tw/sf,th/sf), \
                       self.w2c(m['x'],m['y']), m['w'], m['h']

        grp_name=m.get('group')
        if grp_name:
            g=next((gg for gg in self.state.get('groups',[]) if gg['name']==grp_name),None)
            if g and g.get('img') and os.path.isfile(g['img']):
                pm=self._get_pm(g['img'])
                if pm and pm.width()>0:
                    # exclude override and disabled monitors from group span
                    gms=[mm for mm in self.state['monitors']
                         if mm.get('group')==grp_name
                         and not mm.get('override')
                         and not _mon_disabled(mm, groups_map)]
                    if not gms: gms=[m]
                    gtx=min(mm['x'] for mm in gms); gty=min(mm['y'] for mm in gms)
                    gtw=max(mm['x']+mm['w'] for mm in gms)-gtx
                    gth=max(mm['y']+mm['h'] for mm in gms)-gty
                    ox=g.get('ox',0); oy=g.get('oy',0)
                    aox=abs(ox); aoy=abs(oy)
                    sf=max((gtw+2*aox)/pm.width(),(gth+2*aoy)/pm.height())
                    cx0=(pm.width()-gtw/sf)/2+ox/sf; cy0=(pm.height()-gth/sf)/2+oy/sf
                    return pm, QRectF(cx0,cy0,gtw/sf,gth/sf), \
                           self.w2c(gtx,gty), gtw, gth

        # Global image — span only active monitors that effectively use global image
        pm=self._get_pm(self.state.get('img',''))
        if not pm or pm.width()==0: return None,None,None,None,None
        gms=[]
        for mm in self.state['monitors']:
            if mm.get('override') or _mon_disabled(mm, groups_map): continue
            grp2=mm.get('group')
            if grp2:
                gg=next((gg for gg in self.state.get('groups',[]) if gg['name']==grp2),None)
                if gg and gg.get('img') and os.path.isfile(gg['img']): continue
            gms.append(mm)
        if not gms: return None,None,None,None,None
        gtx=min(mm['x'] for mm in gms); gty=min(mm['y'] for mm in gms)
        gtw=max(mm['x']+mm['w'] for mm in gms)-gtx
        gth=max(mm['y']+mm['h'] for mm in gms)-gty
        ox=self.state.get('ox',0); oy=self.state.get('oy',0)
        aox=abs(ox); aoy=abs(oy)
        sf=max((gtw+2*aox)/pm.width(),(gth+2*aoy)/pm.height())
        cx0=(pm.width()-gtw/sf)/2+ox/sf; cy0=(pm.height()-gth/sf)/2+oy/sf
        return pm, QRectF(cx0,cy0,gtw/sf,gth/sf), \
               self.w2c(gtx,gty), gtw, gth

    def _mon_src_r(self, m, pm, full_src_r, span_tl, span_w, span_h):
        """Slice full_src_r to just this monitor's portion."""
        mon_tl=self.w2c(m['x'],m['y'])
        if span_w==m['w'] and span_h==m['h']:   # already per-monitor
            return QRectF(full_src_r)
        rel_x=(mon_tl.x()-span_tl.x())/(span_w*self._sc)
        rel_y=(mon_tl.y()-span_tl.y())/(span_h*self._sc)
        return QRectF(full_src_r.x()+rel_x*full_src_r.width(),
                      full_src_r.y()+rel_y*full_src_r.height(),
                      m['w']/span_w*full_src_r.width(),
                      m['h']/span_h*full_src_r.height())

    # ── paint ─────────────────────────────────────────────────────────────────
    def paintEvent(self,_):
        p=QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor(26,26,26))
        ms=self.state['monitors']
        if not ms:
            p.setPen(QColor(120,120,120))
            p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,"No monitors detected"); return

        bbox=self._drag_img_bbox or self._img_bbox()
        if not bbox: return
        tx,ty,tw,th=bbox
        groups_map={g['name']:g for g in self.state.get('groups',[])}

        # Draw images per monitor
        for i,m in enumerate(ms):
            mon_dst=QRectF(self.w2c(m['x'],m['y']),QSizeF(m['w']*self._sc,m['h']*self._sc))
            if _mon_disabled(m, groups_map):
                p.save(); p.setOpacity(0.35)
                p.setClipPath(_poly_path(self.m_screen_poly(m)))
                p.fillRect(mon_dst,QColor(55,55,55))
                p.setClipping(False); p.restore()
                continue
            pm,full_src_r,span_tl,span_w,span_h = self._mon_src(m,tx,ty,tw,th)
            if not pm: continue
            mon_src_r = self._mon_src_r(m,pm,full_src_r,span_tl,span_w,span_h)
            if m.get('orig_rect'):
                ox2,oy2,ow,oh=m['orig_rect']
                m_orig={**m,'x':int(ox2),'y':int(oy2),'w':int(ow),'h':int(oh),'pts':None}
                orig_src_r=self._mon_src_r(m_orig,pm,full_src_r,span_tl,span_w,span_h)
                self._draw_quad_effects(p,m,pm,orig_src_r,mon_dst,mon_color(m,i))
            else:
                p.setClipPath(_poly_path(self.m_screen_poly(m)))
                p.drawPixmap(mon_dst,pm,mon_src_r)
                p.setClipping(False)

        # Draw outlines and handles
        for i,m in enumerate(ms):
            is_dis=_mon_disabled(m, groups_map)
            col=mon_color(m,i); corners_c=self.m_screen_corners(m)
            poly=self.m_screen_poly(m)
            is_sel=(i==self._sel_mon); is_locked=m.get('locked',False)

            if is_dis: p.setOpacity(0.5)

            # Dotted orig_rect (fixed reference, never moves)
            if m.get('orig_rect'):
                ox2,oy2,ow,oh=m['orig_rect']
                dash=QPen(col,1,Qt.PenStyle.DashLine)
                p.setPen(dash); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(self.w2c(ox2,oy2),QSizeF(ow*self._sc,oh*self._sc)))

            pw=2.5 if is_sel else 2
            p.setPen(QPen(col,pw)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(poly)

            if is_locked:
                lp=self.w2c(m['x'],m['y'])
                p.setPen(Qt.GlobalColor.yellow)
                p.setFont(QFont()); p.drawText(QRectF(lp.x()+2,lp.y()+2,16,16),"L")

            p.setPen(Qt.GlobalColor.white)
            f=QFont(); f.setPointSize(8); p.setFont(f)
            label=f"{m['name']}\n{m['w']}×{m['h']}" + ("\n[disabled]" if is_dis else "")
            p.drawText(self.mr(m),Qt.AlignmentFlag.AlignCenter,label)

            if not is_locked and not is_dis:
                for ci,pt in enumerate(corners_c):
                    sel=self._sel_corner==(i,ci)
                    p.setBrush(QBrush(QColor(255,220,0) if sel else QColor(255,255,255,210)))
                    p.setPen(QPen(QColor(255,220,0) if sel else col,1.5))
                    r=HANDLE_R+(2 if sel else 0)
                    p.drawEllipse(pt,r,r)
                for pt in self.m_screen_face_mids(m):
                    p.setBrush(QBrush(QColor(200,200,200,160)))
                    p.setPen(QPen(col,1.2))
                    p.drawRect(QRectF(pt.x()-4,pt.y()-4,8,8))

            if is_dis: p.setOpacity(1.0)

    def _draw_quad_effects(self,p,m,pm,orig_src_r,mon_dst,col):
        ox2,oy2,ow,oh=m['orig_rect']
        otl=self.w2c(ox2,oy2); otr=self.w2c(ox2+ow,oy2)
        obr=self.w2c(ox2+ow,oy2+oh); obl=self.w2c(ox2,oy2+oh)
        orig_poly=QPolygonF([otl,otr,obr,obl])
        cs=self.m_screen_corners(m)
        quad_poly=QPolygonF([cs[0],cs[1],cs[3],cs[2]])
        op=QPainterPath(); op.addPolygon(orig_poly); op.closeSubpath()
        qp2=QPainterPath(); qp2.addPolygon(quad_poly); qp2.closeSubpath()
        smear=op.subtracted(qp2)
        if not smear.isEmpty():
            p.save(); p.setClipPath(smear)
            p.setRenderHint(QPainter.RenderHint.Antialiasing,False)
            for oc,qc in zip([otl,otr,obr,obl],[cs[0],cs[1],cs[3],cs[2]]):
                ddx=oc.x()-qc.x(); ddy=oc.y()-qc.y(); dist=math.hypot(ddx,ddy)
                if dist<0.5: continue
                ux=ddx/dist; uy=ddy/dist; nx=-uy; ny=ux
                sweep=max(ow,oh)*self._sc+20
                pen=QPen(QColor(0,0,0,110)); pen.setWidth(1); p.setPen(pen)
                t=-sweep
                while t<=sweep:
                    p.drawLine(QPointF(qc.x()+nx*t,qc.y()+ny*t),
                               QPointF(oc.x()+nx*t,oc.y()+ny*t))
                    t+=2.0
            p.setRenderHint(QPainter.RenderHint.Antialiasing,True); p.restore()
        T=QTransform()
        try: ok=QTransform.quadToQuad(orig_poly,quad_poly,T)
        except TypeError:
            res=QTransform.quadToQuad(orig_poly,quad_poly)
            ok,T=(res if isinstance(res,tuple) else (False,QTransform()))
        if not ok: return
        orig_dst=QRectF(self.w2c(ox2,oy2),QSizeF(ow*self._sc,oh*self._sc))
        p.save(); p.setTransform(T); p.setClipRect(orig_dst)
        p.drawPixmap(orig_dst,pm,orig_src_r); p.restore()

    # ── hit testing ───────────────────────────────────────────────────────────
    def _hit(self,pos):
        ms=self.state['monitors']
        for i,m in enumerate(ms):
            if m.get('locked'): continue
            for ci,pt in enumerate(self.m_screen_corners(m)):
                if math.hypot(pos.x()-pt.x(),pos.y()-pt.y())<=HANDLE_R+3:
                    return 'corner',i,ci
        for i,m in enumerate(ms):
            if m.get('locked'): continue
            for fi,pt in enumerate(self.m_screen_face_mids(m)):
                if math.hypot(pos.x()-pt.x(),pos.y()-pt.y())<=HANDLE_R+3:
                    return 'face',i,fi
        for i,m in enumerate(ms):
            if self.m_screen_poly(m).containsPoint(pos,Qt.FillRule.OddEvenFill):
                return 'body',i,-1
        return 'image',-1,-1

    # ── mouse events ──────────────────────────────────────────────────────────
    def mousePressEvent(self,e):
        if e.button()!=Qt.MouseButton.LeftButton: return
        self.setFocus()
        pos=e.position(); kind,mi,ci=self._hit(pos)
        wx,wy=self.c2w(pos.x(),pos.y())
        m=self.state['monitors'][mi] if mi>=0 else None
        prev_sel=self._sel_mon
        new_sel=mi if kind in ('body','corner','face') else -1
        if new_sel!=self._sel_mon:
            self._sel_mon=new_sel; self.sel_changed.emit(self._sel_mon)
        if kind=='corner':
            self._sel_corner=(mi,ci)
            self._drag=dict(kind='corner',mi=mi,ci=ci,wx0=wx,wy0=wy,m0=cl(m))
        elif kind=='face':
            self._sel_corner=None
            self._drag_img_bbox=self._img_bbox()
            self._drag=dict(kind='face',mi=mi,fi=ci,wx0=wx,wy0=wy,
                            pts0=get_corners(m),m0=cl(m))
        elif kind=='body':
            self._sel_corner=None
            if not m.get('locked'):
                self._drag_img_bbox=self._img_bbox()
                self._drag=dict(kind='body',mi=mi,wx0=wx,wy0=wy,mx0=m['x'],my0=m['y'],
                                pts0=get_corners(m) if m.get('pts') else None)
        else:
            self._sel_corner=None
            if not self.state.get('lock_bg'):
                pt,ox0,oy0=self._pan_target(prev_sel)
                self._drag=dict(kind='image',wx0=wx,wy0=wy,ox0=ox0,oy0=oy0,pan_target=pt)
        self.update()

    def mouseMoveEvent(self,e):
        pos=e.position()
        if not self._drag:
            kind,_,ci=self._hit(pos)
            if kind=='face':
                self.setCursor(Qt.CursorShape.SizeVerCursor if ci in (0,2)
                               else Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor({'corner':Qt.CursorShape.SizeFDiagCursor,
                                'body':Qt.CursorShape.SizeAllCursor,
                                'image':Qt.CursorShape.OpenHandCursor}[kind])
            return
        wx,wy=self.c2w(pos.x(),pos.y()); d=self._drag
        dx=wx-d['wx0']; dy=wy-d['wy0']
        if d['kind']=='image':
            nox=int(d['ox0']-dx); noy=int(d['oy0']-dy); pt=d['pan_target']
            if pt.startswith('group:'):
                gname=pt[6:]
                g=next((g for g in self.state.get('groups',[]) if g['name']==gname),None)
                if g: g['ox']=nox; g['oy']=noy
            elif pt.startswith('override:'):
                mi2=int(pt[9:]); m2=self.state['monitors'][mi2]
                if m2.get('override'): m2['override']['ox']=nox; m2['override']['oy']=noy
            else:
                self.state['ox']=nox; self.state['oy']=noy
        elif d['kind']=='body':
            m=self.state['monitors'][d['mi']]
            nx,ny=self._snap(d['mi'],int(d['mx0']+dx),int(d['my0']+dy),m['w'],m['h'])
            if d['pts0']:
                ddx=nx-d['mx0']; ddy=ny-d['my0']
                m['pts']=[[p[0]+ddx,p[1]+ddy] for p in d['pts0']]
            m['x']=nx; m['y']=ny
        elif d['kind']=='corner':
            self._corner_delta(d['mi'],d['ci'],dx,dy,d['m0'],self.mode)
        elif d['kind']=='face':
            self._face_delta(d['mi'],d['fi'],dx,dy,d['pts0'],d['m0'])
        self.update()

    def mouseReleaseEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton and self._drag:
            was_mon=self._drag['kind'] in ('body','face','corner')
            self._drag=None; self._drag_img_bbox=None
            if was_mon: self._refit()
            self.changed.emit()

    # ── keyboard ──────────────────────────────────────────────────────────────
    def keyPressEvent(self,e):
        key=e.key()
        arrows={Qt.Key.Key_Left,Qt.Key.Key_Right,Qt.Key.Key_Up,Qt.Key.Key_Down}
        if self._sel_corner and key in arrows:
            mi,ci=self._sel_corner; step=self.arrow_step
            dx={Qt.Key.Key_Left:-step,Qt.Key.Key_Right:step}.get(key,0)
            dy={Qt.Key.Key_Up:-step,Qt.Key.Key_Down:step}.get(key,0)
            self._corner_delta(mi,ci,dx,dy,cl(self.state['monitors'][mi]),self.mode)
            self._refit(); self.update(); self.changed.emit()
        elif key==Qt.Key.Key_F: self.fit_view()
        elif key==Qt.Key.Key_Escape:
            self._sel_corner=None; self._sel_mon=-1
            self.sel_changed.emit(-1); self.update()
        else: super().keyPressEvent(e)

    # ── corner / face delta ───────────────────────────────────────────────────
    def _corner_delta(self,mi,ci,dx,dy,orig,mode):
        m=self.state['monitors'][mi]
        if mode=='Warp':
            if not m.get('pts') and not m.get('orig_rect'):
                m['orig_rect']=[orig['x'],orig['y'],orig['w'],orig['h']]
            pts=get_corners(orig); pts[ci][0]+=dx; pts[ci][1]+=dy
            x,y,w,h=corners_to_rect(pts)
            if w<80 or h<80: return
            m['pts']=None if is_rect(pts) else pts
            m['x']=x; m['y']=y; m['w']=w; m['h']=h
            if m['pts'] is None: m.pop('orig_rect',None)
            return
        # Respect Aspect Ratio — always rect, always clears quad
        if ci==3:   nw=max(80,orig['w']+dx); nh=max(80,orig['h']+dy); nx=orig['x'];             ny=orig['y']
        elif ci==2: nw=max(80,orig['w']-dx); nh=max(80,orig['h']+dy); nx=int(orig['x']+orig['w']-nw); ny=orig['y']
        elif ci==1: nw=max(80,orig['w']+dx); nh=max(80,orig['h']-dy); nx=orig['x'];             ny=int(orig['y']+orig['h']-nh)
        else:       nw=max(80,orig['w']-dx); nh=max(80,orig['h']-dy); nx=int(orig['x']+orig['w']-nw); ny=int(orig['y']+orig['h']-nh)
        ar=orig.get('phys_w',orig['w'])/max(orig.get('phys_h',orig['h']),1)
        if abs(nw-orig['w'])>=abs(nh-orig['h']): nh=max(1,int(nw/ar))
        else:                                    nw=max(1,int(nh*ar))
        if ci==2:   nx=int(orig['x']+orig['w']-nw)
        elif ci==1: ny=int(orig['y']+orig['h']-nh)
        elif ci==0: nx=int(orig['x']+orig['w']-nw); ny=int(orig['y']+orig['h']-nh)
        m['w']=int(nw); m['h']=int(nh); m['x']=int(nx); m['y']=int(ny)
        m['pts']=None; m.pop('orig_rect',None)

    def _face_delta(self,mi,fi,dx,dy,orig_pts,m0):
        m=self.state['monitors'][mi]
        if self.mode=='Warp':
            if not m.get('orig_rect'):
                m['orig_rect']=[m0['x'],m0['y'],m0['w'],m0['h']]
            pts=[list(p) for p in orig_pts]
            ca,cb=FACE_CORNERS[fi]
            if fi in (0,2): pts[ca][1]+=dy; pts[cb][1]+=dy
            else:           pts[ca][0]+=dx; pts[cb][0]+=dx
            x,y,w,h=corners_to_rect(pts)
            if w<80 or h<80: return
            m['pts']=None if is_rect(pts) else pts
            m['x']=int(x); m['y']=int(y); m['w']=int(w); m['h']=int(h)
        else:
            ox,oy,ow,oh=m0['x'],m0['y'],m0['w'],m0['h']
            if fi==0:   nh=max(80,oh-dy); ny=oy+(oh-nh); nx=ox; nw=ow
            elif fi==2: nh=max(80,oh+dy); ny=oy;          nx=ox; nw=ow
            elif fi==3: nw=max(80,ow-dx); nx=ox+(ow-nw); ny=oy; nh=oh
            else:       nw=max(80,ow+dx); nx=ox;          ny=oy; nh=oh
            m['x']=int(nx); m['y']=int(ny); m['w']=int(nw); m['h']=int(nh)
            m['pts']=None; m.pop('orig_rect',None)

    def _pan_target(self, sel_mi):
        """Return (target_str, ox0, oy0) for the given monitor index."""
        if sel_mi >= 0 and sel_mi < len(self.state['monitors']):
            m = self.state['monitors'][sel_mi]
            if m.get('override'):
                ov = m['override']
                return f'override:{sel_mi}', ov.get('ox',0), ov.get('oy',0)
            grp = m.get('group')
            if grp:
                g = next((g for g in self.state.get('groups',[]) if g['name']==grp), None)
                if g and g.get('img') and os.path.isfile(g['img']):
                    return f'group:{grp}', g.get('ox',0), g.get('oy',0)
        return 'global', self.state.get('ox',0), self.state.get('oy',0)

    def _snap(self,mi,nx,ny,w,h):
        snap=SNAP_PX/self._sc; x2=nx+w; y2=ny+h
        for i,o in enumerate(self.state['monitors']):
            if i==mi: continue
            for ex in (o['x'],o['x']+o['w']):
                if abs(nx-ex)<snap: nx=ex
                elif abs(x2-ex)<snap: nx=ex-w
            for ey in (o['y'],o['y']+o['h']):
                if abs(ny-ey)<snap: ny=ey
                elif abs(y2-ey)<snap: ny=ey-h
        return nx,ny


# ── collapsible sidebar section ───────────────────────────────────────────────

class Section(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self._title = title
        vl = QVBoxLayout(self); vl.setContentsMargins(0,0,0,0); vl.setSpacing(0)
        self._btn = QPushButton(f"▶ {title}")
        self._btn.setCheckable(True); self._btn.setChecked(False)
        self._btn.setStyleSheet(
            "QPushButton{text-align:left;padding:4px 8px;font-weight:bold;"
            "background:#333;border:none;border-bottom:1px solid #555;}"
            "QPushButton:checked{background:#444;}")
        self._btn.toggled.connect(self._toggle)
        vl.addWidget(self._btn)
        self._body = QWidget(); self._body.setVisible(False)
        self._bvl = QVBoxLayout(self._body)
        self._bvl.setContentsMargins(4,4,4,4); self._bvl.setSpacing(4)
        vl.addWidget(self._body)

    def _toggle(self, on):
        self._body.setVisible(on)
        self._btn.setText(f"{'▼' if on else '▶'} {self._title}")

    def body(self): return self._bvl


# ── main window ───────────────────────────────────────────────────────────────

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Easy Wallpaper Span")
        self.resize(1380,760)
        self._sys_mons=read_monitors()
        self._undo=[]; self._redo=[]
        self._cal_active=False
        self._build_ui(); self._reset(); self._load_saved()

    # ── UI construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        cw=QWidget(); self.setCentralWidget(cw)
        root=QHBoxLayout(cw); root.setSpacing(8)

        self.cv=Canvas(); self.cv.changed.connect(self._on_changed)
        self.cv.sel_changed.connect(self._on_sel_changed)
        root.addWidget(self.cv,1)

        # Sidebar scroll area
        scroll=QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(300)
        sb_inner=QWidget(); self._sb=QVBoxLayout(sb_inner)
        self._sb.setSpacing(2); self._sb.setContentsMargins(4,4,4,4)
        scroll.setWidget(sb_inner); root.addWidget(scroll,0)

        # ── Resize mode ──────────────────────────────────────────────────────
        sec_mode=Section("Resize mode"); self._sb.addWidget(sec_mode)
        self.mode_combo=QComboBox(); self.mode_combo.addItems(MODES)
        self.mode_combo.currentTextChanged.connect(lambda t:setattr(self.cv,'mode',t))
        sec_mode.body().addWidget(self.mode_combo)

        # ── Global image ─────────────────────────────────────────────────────
        sec_gi=Section("Global image"); self._sb.addWidget(sec_gi)
        gi_w=QWidget(); fi=QFormLayout(gi_w); fi.setContentsMargins(0,0,0,0)
        self.img_lbl=QLabel("—"); self.img_lbl.setWordWrap(True); fi.addRow(self.img_lbl)
        bb=QPushButton("Browse…"); bb.clicked.connect(self._pick_img); fi.addRow(bb)
        self.sp_ox=QSpinBox(); self.sp_ox.setRange(-9999,9999); self.sp_ox.setPrefix("px  ")
        self.sp_oy=QSpinBox(); self.sp_oy.setRange(-9999,9999); self.sp_oy.setPrefix("px  ")
        self.sp_ox.editingFinished.connect(self._offset_edit)
        self.sp_oy.editingFinished.connect(self._offset_edit)
        fi.addRow("X offset:",self.sp_ox); fi.addRow("Y offset:",self.sp_oy)
        self.sp_step=QSpinBox(); self.sp_step.setRange(1,9999); self.sp_step.setValue(1)
        self.sp_step.setSuffix(" px")
        self.sp_step.valueChanged.connect(lambda v:setattr(self.cv,'arrow_step',v))
        fi.addRow("Arrow step:",self.sp_step)
        self.chk_lock_bg=QCheckBox("Lock background panning")
        self.chk_lock_bg.toggled.connect(lambda on:self.cv.state.update({'lock_bg':on}))
        fi.addRow(self.chk_lock_bg)
        sec_gi.body().addWidget(gi_w)

        # ── Monitor properties ────────────────────────────────────────────────
        sec_mon=Section("Monitor"); self._sb.addWidget(sec_mon)
        self._mon_no_sel=QLabel("(select a monitor)"); sec_mon.body().addWidget(self._mon_no_sel)
        self._gmon=QWidget(); self._gmon.setVisible(False)
        fmon=QFormLayout(self._gmon); fmon.setContentsMargins(0,0,0,0)
        self._mon_name=QLabel("—"); fmon.addRow("Name:",self._mon_name)
        self._chk_lock_mon=QCheckBox("Locked")
        self._chk_lock_mon.toggled.connect(self._mon_lock_changed)
        fmon.addRow(self._chk_lock_mon)
        self._chk_dis_mon=QCheckBox("Disabled (use system default)")
        self._chk_dis_mon.toggled.connect(self._mon_disable_changed)
        fmon.addRow(self._chk_dis_mon)
        self._sp_mx=QSpinBox(); self._sp_mx.setRange(-9999,9999)
        self._sp_my=QSpinBox(); self._sp_my.setRange(-9999,9999)
        self._sp_mw=QSpinBox(); self._sp_mw.setRange(80,9999)
        self._sp_mh=QSpinBox(); self._sp_mh.setRange(80,9999)
        for sp in (self._sp_mx,self._sp_my,self._sp_mw,self._sp_mh):
            sp.editingFinished.connect(self._mon_dims_changed)
        fmon.addRow("X:",self._sp_mx); fmon.addRow("Y:",self._sp_my)
        fmon.addRow("W:",self._sp_mw); fmon.addRow("H:",self._sp_mh)
        self._btn_color=QPushButton("Color…"); self._btn_color.clicked.connect(self._pick_mon_color)
        fmon.addRow(self._btn_color)
        self._grp_combo=QComboBox(); self._grp_combo.activated.connect(self._mon_group_changed)
        fmon.addRow("Group:",self._grp_combo)
        self._chk_ov=QCheckBox("Override image"); self._chk_ov.toggled.connect(self._ov_toggled)
        fmon.addRow(self._chk_ov)
        self._ov_widget=QWidget(); ovl=QFormLayout(self._ov_widget); ovl.setContentsMargins(0,0,0,0)
        self._ov_lbl=QLabel("—"); self._ov_lbl.setWordWrap(True)
        ov_browse=QPushButton("Browse…"); ov_browse.clicked.connect(self._pick_ov_img)
        self._sp_ov_ox=QSpinBox(); self._sp_ov_ox.setRange(-9999,9999)
        self._sp_ov_oy=QSpinBox(); self._sp_ov_oy.setRange(-9999,9999)
        self._sp_ov_ox.editingFinished.connect(self._ov_offset_changed)
        self._sp_ov_oy.editingFinished.connect(self._ov_offset_changed)
        ovl.addRow(self._ov_lbl); ovl.addRow(ov_browse)
        ovl.addRow("X off:",self._sp_ov_ox); ovl.addRow("Y off:",self._sp_ov_oy)
        self._ov_widget.setVisible(False); fmon.addRow(self._ov_widget)
        sec_mon.body().addWidget(self._gmon)

        # ── Groups ────────────────────────────────────────────────────────────
        sec_grp=Section("Screen groups"); self._sb.addWidget(sec_grp)
        grp_row=QHBoxLayout()
        self._new_grp_edit=QLineEdit(); self._new_grp_edit.setPlaceholderText("group name…")
        add_grp=QPushButton("Add"); add_grp.clicked.connect(self._add_group)
        grp_row.addWidget(self._new_grp_edit,1); grp_row.addWidget(add_grp)
        sec_grp.body().addLayout(grp_row)
        self._grp_list_layout=QVBoxLayout(); sec_grp.body().addLayout(self._grp_list_layout)

        # ── Profiles ──────────────────────────────────────────────────────────
        sec_prof=Section("Profiles"); self._sb.addWidget(sec_prof)
        row1=QHBoxLayout()
        self.prof_combo=QComboBox(); self.prof_combo.setMinimumWidth(100)
        row1.addWidget(self.prof_combo,1)
        lb=QPushButton("Load"); lb.clicked.connect(self._load_profile); row1.addWidget(lb)
        db=QPushButton("Del");  db.clicked.connect(self._delete_profile); row1.addWidget(db)
        sec_prof.body().addLayout(row1)
        row2=QHBoxLayout()
        self.prof_name=QLineEdit(); self.prof_name.setPlaceholderText("name…")
        self._prof_save_btn=QPushButton("Save"); self._prof_save_btn.clicked.connect(self._save_profile)
        self.prof_name.textChanged.connect(self._update_prof_btn)
        row2.addWidget(self.prof_name,1); row2.addWidget(self._prof_save_btn)
        sec_prof.body().addLayout(row2)
        self._refresh_profiles()

        # ── Calibration ───────────────────────────────────────────────────────
        sec_cal=Section("Calibration"); self._sb.addWidget(sec_cal)
        cal_w=QWidget(); fcc=QFormLayout(cal_w); fcc.setContentsMargins(0,0,0,0)
        self.cal_chk=QCheckBox("Enable calibration overlay")
        self.cal_chk.toggled.connect(self._cal_toggled); fcc.addRow(self.cal_chk)
        self.sp_cal=QSpinBox(); self.sp_cal.setRange(4,500); self.sp_cal.setValue(50)
        self.sp_cal.setSuffix(" px"); self.sp_cal.setEnabled(False)
        self.sp_cal.valueChanged.connect(self._cal_update_preview)
        fcc.addRow("Square size:",self.sp_cal)
        self._cal_apply_btn=QPushButton("Apply calibration"); self._cal_apply_btn.setEnabled(False)
        self._cal_apply_btn.clicked.connect(self._apply_cal); fcc.addRow(self._cal_apply_btn)
        sec_cal.body().addWidget(cal_w)

        self._sb.addStretch(1)

        # ── Bottom buttons ────────────────────────────────────────────────────
        br=QHBoxLayout(); br.setSpacing(4)
        for lbl,key,fn in [("Undo","Ctrl+Z",self._undo_fn),
                           ("Redo","Ctrl+Shift+Z",self._redo_fn),
                           ("Reset","",self._reset)]:
            b=QPushButton(lbl)
            if key: b.setShortcut(QKeySequence(key))
            b.clicked.connect(fn); br.addWidget(b)
        self._sb.addLayout(br)
        ap=QPushButton("▶  Apply"); ap.setShortcut(QKeySequence("Ctrl+Return"))
        ap.setStyleSheet("font-size:13px;font-weight:bold;background:#1b5e1b;padding:8px;")
        ap.clicked.connect(self._apply); self._sb.addWidget(ap)

    # ── monitor selection / properties ────────────────────────────────────────
    def _on_sel_changed(self, mi):
        if mi < 0:
            self._mon_no_sel.setVisible(True); self._gmon.setVisible(False); return
        self._mon_no_sel.setVisible(False); self._gmon.setVisible(True)
        m=self.cv.state['monitors'][mi]
        self._mon_name.setText(m['name'])
        self._chk_lock_mon.blockSignals(True)
        self._chk_lock_mon.setChecked(m.get('locked',False))
        self._chk_lock_mon.blockSignals(False)
        self._chk_dis_mon.blockSignals(True)
        self._chk_dis_mon.setChecked(m.get('disabled',False))
        self._chk_dis_mon.blockSignals(False)
        for sp,k in ((self._sp_mx,'x'),(self._sp_my,'y'),(self._sp_mw,'w'),(self._sp_mh,'h')):
            sp.blockSignals(True); sp.setValue(int(m[k])); sp.blockSignals(False)
        col=m.get('color')
        self._btn_color.setStyleSheet(f"background:{col};" if col else "")
        # Group combo
        self._grp_combo.blockSignals(True); self._grp_combo.clear()
        self._grp_combo.addItem("(global)","")
        for g in self.cv.state.get('groups',[]):
            self._grp_combo.addItem(g['name'],g['name'])
        idx=self._grp_combo.findData(m.get('group') or "")
        self._grp_combo.setCurrentIndex(max(0,idx))
        self._grp_combo.blockSignals(False)
        # Override
        ov=m.get('override')
        self._chk_ov.blockSignals(True); self._chk_ov.setChecked(bool(ov)); self._chk_ov.blockSignals(False)
        self._ov_widget.setVisible(bool(ov))
        if ov:
            self._ov_lbl.setText(os.path.basename(ov.get('img','')) or '—')
            self._sp_ov_ox.blockSignals(True); self._sp_ov_ox.setValue(ov.get('ox',0)); self._sp_ov_ox.blockSignals(False)
            self._sp_ov_oy.blockSignals(True); self._sp_ov_oy.setValue(ov.get('oy',0)); self._sp_ov_oy.blockSignals(False)

    def _sel_mon(self):
        mi=self.cv._sel_mon
        if mi<0 or mi>=len(self.cv.state['monitors']): return None
        return self.cv.state['monitors'][mi]

    def _mon_lock_changed(self,on):
        m=self._sel_mon()
        if m: m['locked']=on; self.cv.update()

    def _mon_disable_changed(self,on):
        m=self._sel_mon()
        if m: m['disabled']=on; self.cv.update(); self.cv.changed.emit()

    def _mon_dims_changed(self):
        m=self._sel_mon()
        if not m: return
        self._push()
        m['x']=self._sp_mx.value(); m['y']=self._sp_my.value()
        m['w']=self._sp_mw.value(); m['h']=self._sp_mh.value()
        m['pts']=None; m.pop('orig_rect',None)
        self.cv._refit(); self.cv.update(); self.cv.changed.emit()

    def _pick_mon_color(self):
        m=self._sel_mon()
        if not m: return
        cur=QColor(m['color']) if m.get('color') else QColor(255,255,255)
        col=QColorDialog.getColor(cur,self,"Monitor color")
        if col.isValid():
            m['color']=col.name()
            self._btn_color.setStyleSheet(f"background:{col.name()};")
            self.cv.update(); self.cv.changed.emit()

    def _mon_group_changed(self,_):
        m=self._sel_mon()
        if not m: return
        val=self._grp_combo.currentData()
        m['group']=val if val else None
        self.cv.update(); self.cv.changed.emit()

    def _ov_toggled(self,on):
        m=self._sel_mon()
        if not m: return
        m['override']={'img':'','ox':0,'oy':0} if on else None
        self._ov_widget.setVisible(on)
        self.cv._pm_cache.clear(); self.cv.update(); self.cv.changed.emit()

    def _pick_ov_img(self):
        m=self._sel_mon()
        if not m: return
        path,_=QFileDialog.getOpenFileName(self,"Override image",str(Path.home()),
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)")
        if path:
            if not m.get('override'): m['override']={'img':'','ox':0,'oy':0}
            m['override']['img']=path; self._ov_lbl.setText(os.path.basename(path))
            self.cv._pm_cache.clear(); self.cv.update(); self.cv.changed.emit()

    def _ov_offset_changed(self):
        m=self._sel_mon()
        if not m or not m.get('override'): return
        m['override']['ox']=self._sp_ov_ox.value()
        m['override']['oy']=self._sp_ov_oy.value()
        self.cv.update(); self.cv.changed.emit()

    # ── groups ────────────────────────────────────────────────────────────────
    def _refresh_groups(self):
        while self._grp_list_layout.count():
            item=self._grp_list_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        # Always keep group combo current regardless of monitor selection
        cur_grp=None
        if self.cv._sel_mon>=0 and self.cv._sel_mon<len(self.cv.state['monitors']):
            cur_grp=self.cv.state['monitors'][self.cv._sel_mon].get('group')
        self._grp_combo.blockSignals(True); self._grp_combo.clear()
        self._grp_combo.addItem("(global)","")
        for g in self.cv.state.get('groups',[]): self._grp_combo.addItem(g['name'],g['name'])
        self._grp_combo.setCurrentIndex(max(0,self._grp_combo.findData(cur_grp or "")))
        self._grp_combo.blockSignals(False)
        for g in self.cv.state.get('groups',[]):
            gb=QGroupBox(g['name']); gbl=QFormLayout(gb)
            img_lbl=QLabel(os.path.basename(g.get('img','')) or '—')
            img_lbl.setWordWrap(True)
            br2=QPushButton("Browse…")
            br2.clicked.connect(lambda _,gn=g['name']:self._pick_grp_img(gn))
            ox_sp=QSpinBox(); ox_sp.setRange(-9999,9999); ox_sp.setValue(g.get('ox',0))
            oy_sp=QSpinBox(); oy_sp.setRange(-9999,9999); oy_sp.setValue(g.get('oy',0))
            ox_sp.editingFinished.connect(lambda gn=g['name'],sp=ox_sp:self._grp_off(gn,'ox',sp))
            oy_sp.editingFinished.connect(lambda gn=g['name'],sp=oy_sp:self._grp_off(gn,'oy',sp))
            dis_chk=QCheckBox("Disabled (use system default)")
            dis_chk.setChecked(g.get('disabled',False))
            dis_chk.toggled.connect(lambda on,gn=g['name']:self._grp_disable_changed(gn,on))
            del_btn=QPushButton("Delete group")
            del_btn.clicked.connect(lambda _,gn=g['name']:self._del_group(gn))
            gbl.addRow(img_lbl); gbl.addRow(br2)
            gbl.addRow("X off:",ox_sp); gbl.addRow("Y off:",oy_sp)
            gbl.addRow(dis_chk); gbl.addRow(del_btn)
            self._grp_list_layout.addWidget(gb)

    def _add_group(self):
        name=self._new_grp_edit.text().strip()
        if not name: return
        groups=self.cv.state.setdefault('groups',[])
        if any(g['name']==name for g in groups): return
        groups.append({'name':name,'img':'','ox':0,'oy':0,'disabled':False})
        self._new_grp_edit.clear(); self._refresh_groups(); self.cv.changed.emit()

    def _pick_grp_img(self,gname):
        path,_=QFileDialog.getOpenFileName(self,"Group image",str(Path.home()),
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)")
        if path:
            g=next((g for g in self.cv.state.get('groups',[]) if g['name']==gname),None)
            if g:
                g['img']=path; self.cv._pm_cache.clear()
                self._refresh_groups(); self.cv.update(); self.cv.changed.emit()

    def _grp_off(self,gname,key,sp):
        g=next((g for g in self.cv.state.get('groups',[]) if g['name']==gname),None)
        if g: g[key]=sp.value(); self.cv.update(); self.cv.changed.emit()

    def _grp_disable_changed(self,gname,on):
        g=next((g for g in self.cv.state.get('groups',[]) if g['name']==gname),None)
        if g: g['disabled']=on; self.cv.update(); self.cv.changed.emit()

    def _del_group(self,gname):
        self.cv.state['groups']=[g for g in self.cv.state.get('groups',[]) if g['name']!=gname]
        for m in self.cv.state['monitors']:
            if m.get('group')==gname: m['group']=None
        self._refresh_groups(); self.cv.update(); self.cv.changed.emit()

    # ── profiles ──────────────────────────────────────────────────────────────
    def _refresh_profiles(self):
        cur=self.prof_combo.currentText(); self.prof_combo.clear()
        for n in list_profiles(): self.prof_combo.addItem(n)
        idx=self.prof_combo.findText(cur)
        if idx>=0: self.prof_combo.setCurrentIndex(idx)
        self._update_prof_btn()

    def _update_prof_btn(self):
        name=self.prof_name.text().strip()
        if not name:
            self._prof_save_btn.setText("Save"); return
        self._prof_save_btn.setText("Save" if name in list_profiles() else "Create")

    def _load_profile(self):
        name=self.prof_combo.currentText()
        if not name: return
        p=load_profile(name)
        if not p: return
        self._push()
        ms=merge_monitors(self._sys_mons,p.get('monitors',[]))
        s=mk_state(ms,p.get('image',''),p.get('ox',0),p.get('oy',0),
                   p.get('groups',[]),p.get('lock_bg',False))
        self.cv.set_state(s)
        self.img_lbl.setText(os.path.basename(s['img']) if s['img'] else '—')
        self.chk_lock_bg.blockSignals(True); self.chk_lock_bg.setChecked(s['lock_bg']); self.chk_lock_bg.blockSignals(False)
        self._sync_spin(); self._refresh_groups()
        WALL_DIR.mkdir(parents=True,exist_ok=True); LAST_PROFILE_FILE.write_text(name)

    def _save_profile(self):
        name=self.prof_name.text().strip()
        if not name: return
        save_profile(name,self.cv.state)
        self.prof_name.clear(); self._refresh_profiles()
        idx=self.prof_combo.findText(name)
        if idx>=0: self.prof_combo.setCurrentIndex(idx)

    def _delete_profile(self):
        name=self.prof_combo.currentText()
        if not name or name=='default': return
        delete_profile(name); self._refresh_profiles()

    # ── calibration ───────────────────────────────────────────────────────────
    def _cal_toggled(self,on):
        self._cal_active=on; self.sp_cal.setEnabled(on); self._cal_apply_btn.setEnabled(on)
        if on: self._cal_update_preview()
        else:  self.cv.set_cal(None); self._apply(silent=True)

    def _cal_update_preview(self):
        if not self._cal_active: return
        ms=self.cv.state['monitors']
        if not ms: return
        tx=min(m['x'] for m in ms); ty=min(m['y'] for m in ms)
        tw=max(m['x']+m['w'] for m in ms)-tx; th=max(m['y']+m['h'] for m in ms)-ty
        self.cv.set_cal(make_cal_image(tw,th,self.sp_cal.value()))

    def _apply_cal(self):
        ms=self.cv.state['monitors']
        if not ms: return
        tx=min(m['x'] for m in ms); ty=min(m['y'] for m in ms)
        tw=max(m['x']+m['w'] for m in ms)-tx; th=max(m['y']+m['h'] for m in ms)-ty
        qi=make_cal_image(tw,th,self.sp_cal.value())
        WALL_DIR.mkdir(parents=True,exist_ok=True)
        cal_src=str(WALL_DIR/'cal_source.png'); qi.save(cal_src)
        try: apply_wallpaper(cal_src,ms,0,0,save_conf=False)
        except RuntimeError as e: QMessageBox.critical(self,"Error",str(e))

    # ── undo / redo ───────────────────────────────────────────────────────────
    def _push(self):
        self._undo.append(cl(self.cv.state)); self._redo.clear()
        if len(self._undo)>100: self._undo.pop(0)

    def _on_changed(self): self._push(); self._sync_spin()

    def _undo_fn(self):
        if self._undo:
            self._redo.append(cl(self.cv.state))
            self.cv.set_state(self._undo.pop()); self._sync_spin(); self._refresh_groups()

    def _redo_fn(self):
        if self._redo:
            self._undo.append(cl(self.cv.state))
            self.cv.set_state(self._redo.pop()); self._sync_spin(); self._refresh_groups()

    def _sync_spin(self):
        s=self.cv.state
        for sp,k in ((self.sp_ox,'ox'),(self.sp_oy,'oy')):
            sp.blockSignals(True); sp.setValue(s.get(k,0)); sp.blockSignals(False)

    # ── controls ──────────────────────────────────────────────────────────────
    def _reset(self):
        self._push(); prev=self.cv.state
        self.cv.set_state(mk_state(self._sys_mons,prev.get('img',''),0,0,
                                   copy.deepcopy(prev.get('groups',[]))))
        self._sync_spin(); self._refresh_groups()

    def _pick_img(self):
        path,_=QFileDialog.getOpenFileName(self,"Select wallpaper",str(Path.home()),
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)")
        if path:
            self._push(); self.cv.state['img']=path
            self.cv._pm_cache.clear(); self.cv.update()
            self.img_lbl.setText(os.path.basename(path))

    def _offset_edit(self):
        self._push()
        self.cv.state['ox']=self.sp_ox.value(); self.cv.state['oy']=self.sp_oy.value()
        self.cv.update()

    def _load_saved(self):
        if LAST_PROFILE_FILE.exists():
            pname=LAST_PROFILE_FILE.read_text().strip()
            p=load_profile(pname)
            if p:
                ms=merge_monitors(self._sys_mons,p.get('monitors',[]))
                s=mk_state(ms,p.get('image',''),p.get('ox',0),p.get('oy',0),
                           p.get('groups',[]),p.get('lock_bg',False))
                self.cv.set_state(s)
                self.img_lbl.setText(os.path.basename(s['img']) if s['img'] else '—')
                self.chk_lock_bg.blockSignals(True); self.chk_lock_bg.setChecked(s['lock_bg']); self.chk_lock_bg.blockSignals(False)
                self._sync_spin(); self._refresh_groups(); return
        ms=load_saved_monitors(self._sys_mons); prev=self.cv.state
        self.cv.set_state(mk_state(ms,prev.get('img',''),prev.get('ox',0),prev.get('oy',0)))
        if not LAST_CFG.exists(): return
        cfg=read_last_conf()
        img=cfg.get('IMAGE','')
        try: ox=int(cfg.get('X_OFF','0'))
        except ValueError: ox=0
        try: oy=int(cfg.get('Y_OFF','0'))
        except ValueError: oy=0
        if img and os.path.isfile(img):
            self.cv.state['img']=img; self.cv.state['ox']=ox; self.cv.state['oy']=oy
            self.cv._pm_cache.clear(); self.cv.update()
            self.img_lbl.setText(os.path.basename(img)); self._sync_spin()
        self._refresh_profiles()

    def _apply(self,silent=False):
        s=self.cv.state; img=s.get('img','')
        has_img=(img and os.path.isfile(img))
        has_grp_img=any(g.get('img') and os.path.isfile(g['img']) for g in s.get('groups',[]))
        has_ov_img=any(m.get('override',{}) and m['override'].get('img') and
                       os.path.isfile(m['override']['img'])
                       for m in s['monitors'] if m.get('override'))
        if not has_img and not has_grp_img and not has_ov_img:
            if not silent: QMessageBox.warning(self,"No image","Select an image file first.")
            return
        try: apply_state(s)
        except RuntimeError as e: QMessageBox.critical(self,"Error",str(e)); return
        save_profile('default',s); self._refresh_profiles()
        if not silent: QMessageBox.information(self,"Done","Wallpaper applied.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli_restore():
    if not LAST_CFG.exists(): sys.exit("No saved wallpaper found.")
    cfg=read_last_conf()
    img=cfg.get('IMAGE',''); ox=int(cfg.get('X_OFF','0')); oy=int(cfg.get('Y_OFF','0'))
    if not img or not os.path.isfile(img): sys.exit(f"Image not found: {img}")
    apply_wallpaper(img,load_saved_monitors(read_monitors()),ox,oy)

def run_cli(argv):
    p=argparse.ArgumentParser(prog='easy-wallpaper-span',
                               description='Span a wallpaper across monitors (KDE Plasma & Hyprland).')
    sub=p.add_subparsers(dest='cmd')
    pa=sub.add_parser('apply',help='Apply a wallpaper')
    pa.add_argument('image',nargs='?'); pa.add_argument('-x',type=int,default=0)
    pa.add_argument('-y',type=int,default=0); pa.add_argument('--profile','-p',metavar='NAME')
    sub.add_parser('restore'); sub.add_parser('profiles')
    ps=sub.add_parser('save'); ps.add_argument('name')
    pd=sub.add_parser('delete'); pd.add_argument('name')
    args=p.parse_args(argv)
    if args.cmd is None: return False
    try:
        if args.cmd=='restore': _cli_restore()
        elif args.cmd=='profiles':
            ns=list_profiles(); print('\n'.join(ns) if ns else 'No profiles saved.')
        elif args.cmd=='save':
            if not LAST_CFG.exists(): sys.exit("Apply a wallpaper first.")
            cfg=read_last_conf()
            img=cfg.get('IMAGE',''); ox=int(cfg.get('X_OFF','0')); oy=int(cfg.get('Y_OFF','0'))
            fake={'monitors':load_saved_monitors(read_monitors()),'img':img,'ox':ox,'oy':oy,'groups':[]}
            save_profile(args.name,fake); print(f"Saved profile: {args.name}")
        elif args.cmd=='delete': delete_profile(args.name); print(f"Deleted: {args.name}")
        elif args.cmd=='apply':
            if args.profile:
                pr=load_profile(args.profile)
                if not pr: sys.exit(f"Profile not found: {args.profile}")
                ms=merge_monitors(read_monitors(),pr.get('monitors',[]))
                s={**pr,'monitors':ms}; apply_state(s); print(f"Applied profile: {args.profile}")
            else:
                if not args.image: sys.exit("Provide an image or --profile NAME.")
                if not os.path.isfile(args.image): sys.exit(f"Image not found: {args.image}")
                ms=load_saved_monitors(read_monitors())
                apply_wallpaper(args.image,ms,args.x,args.y); print("Wallpaper applied.")
    except RuntimeError as e: sys.exit(str(e))
    return True


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    os.environ.setdefault('QT_LOGGING_RULES','kf.kio.*=false')
    _migrate_data_dir()
    if len(sys.argv)>1 and run_cli(sys.argv[1:]): return
    app=QApplication(sys.argv); app.setStyle('Fusion')
    w=App(); w.show(); sys.exit(app.exec())

if __name__=='__main__': main()
