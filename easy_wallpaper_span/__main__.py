import sys, os, re, math, copy, shutil, time, shlex, subprocess, json, argparse
from pathlib import Path

from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *

SNAP_PX      = 16
HANDLE_R     = 7
MODES        = ["Respect Aspect Ratio", "Adjust Face", "Adjust Corner"]
WALL_DIR     = Path.home() / '.local/share/wallpaper-span'
MONS_CFG     = WALL_DIR / 'monitors.json'
LAST_CFG     = WALL_DIR / 'last.conf'
PROFILES_DIR = WALL_DIR / 'profiles'

COLORS = [
    QColor(70,130,210), QColor(210,90,70),  QColor(70,190,110),
    QColor(210,170,40), QColor(150,70,210), QColor(40,190,190),
]


# ── monitor helpers ───────────────────────────────────────────────────────────

def read_monitors():
    rotations = {}; native_mm = {}
    for line in subprocess.check_output(["xrandr"]).decode().splitlines():
        m = re.match(
            r'^(\S+) connected(?: primary)?\s+\d+x\d+\+\d+\+\d+\s+'
            r'(normal|left|right|inverted)\s.*?(\d+)mm x (\d+)mm', line)
        if m:
            name, rot, wmm, hmm = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
            rotations[name] = rot; native_mm[name] = (wmm, hmm)
    mons = []
    for line in subprocess.check_output(["xrandr","--listmonitors"]).decode().splitlines()[1:]:
        m = re.match(r'\s*(\d+):\s+[+*]*(\S+)\s+(\d+)/(\d+)x(\d+)/(\d+)\+(\d+)\+(\d+)', line)
        if m:
            _, dname, pw, pmw, ph, pmh, px, py = m.groups()
            rot = rotations.get(dname, 'normal')
            wmm, hmm = native_mm.get(dname, (float(pmw), float(pmh)))
            if rot in ('left', 'right'): wmm, hmm = hmm, wmm
            mons.append(dict(name=dname, x=int(px), y=int(py),
                             screen_x=int(px), screen_y=int(py),
                             w=int(pw), h=int(ph),
                             phys_w=int(pw), phys_h=int(ph),
                             phys_w_mm=float(wmm), phys_h_mm=float(hmm),
                             rotation=rot))
    return mons

def merge_monitors(sys_mons, saved_list):
    """Apply saved x/y/w/h onto sys_mons matched by name. screen_x/y and phys_* unchanged."""
    saved = {d['name']: d for d in saved_list}
    return [{**m, 'x': s['x'], 'y': s['y'], 'w': s['w'], 'h': s['h']}
            if (s := saved.get(m['name'])) else m for m in sys_mons]

def load_saved_monitors(sys_mons):
    if not MONS_CFG.exists(): return sys_mons
    try: return merge_monitors(sys_mons, json.loads(MONS_CFG.read_text()))
    except Exception: return sys_mons

def save_monitors(mons):
    WALL_DIR.mkdir(parents=True, exist_ok=True)
    MONS_CFG.write_text(json.dumps(
        [dict(name=m['name'], x=m['x'], y=m['y'], w=m['w'], h=m['h']) for m in mons], indent=2))

def read_last_conf():
    cfg = {}
    for line in LAST_CFG.read_text().splitlines():
        if '=' in line:
            k, _, v = line.partition('=')
            try: cfg[k.strip()] = shlex.split(v.strip())[0]
            except Exception: cfg[k.strip()] = v.strip()
    return cfg


# ── profile helpers ───────────────────────────────────────────────────────────

def list_profiles():
    if not PROFILES_DIR.exists(): return []
    return sorted(p.stem for p in PROFILES_DIR.glob('*.json'))

def load_profile(name):
    p = PROFILES_DIR / f'{name}.json'
    return json.loads(p.read_text()) if p.exists() else None

def save_profile(name, img, ox, oy, monitors):
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    (PROFILES_DIR / f'{name}.json').write_text(json.dumps({
        'name': name, 'image': img, 'ox': ox, 'oy': oy,
        'monitors': [{'name': m['name'], 'x': m['x'], 'y': m['y'],
                      'w': m['w'], 'h': m['h']} for m in monitors]
    }, indent=2))

def delete_profile(name):
    p = PROFILES_DIR / f'{name}.json'
    if p.exists(): p.unlink()


# ── core apply (no Qt) ────────────────────────────────────────────────────────

def apply_wallpaper(img, ms, ox=0, oy=0, save_conf=True):
    """Apply wallpaper headlessly. Raises RuntimeError on failure."""
    tx = min(m['x'] for m in ms); ty = min(m['y'] for m in ms)
    tw = max(m['x']+m['w'] for m in ms)-tx
    th = max(m['y']+m['h'] for m in ms)-ty

    sw = tw+2*abs(ox); sh = th+2*abs(oy)
    WALL_DIR.mkdir(parents=True, exist_ok=True)
    for f in WALL_DIR.glob('crop_*.jpg'): f.unlink(missing_ok=True)
    scaled = WALL_DIR/'scaled.jpg'

    r = subprocess.run(['magick', img,
                        '-resize', f'{sw}x{sh}^', '-gravity', 'Center',
                        '-crop', f'{tw}x{th}+{ox}+{oy}', '+repage', str(scaled)],
                       capture_output=True)
    if r.returncode:
        raise RuntimeError(f"ImageMagick: {r.stderr.decode().strip()}")

    ts = int(time.time()); crops = {}
    for m in ms:
        mx = m['x']-tx; my = m['y']-ty
        cp = WALL_DIR/f"crop_{m['x']}_{m['y']}_{ts}.jpg"
        subprocess.run(['magick', str(scaled),
                        '-crop', f"{m['w']}x{m['h']}+{mx}+{my}", '+repage', str(cp)],
                       check=True)
        crops[(m.get('screen_x', m['x']), m.get('screen_y', m['y']))] = str(cp)

    lines = ['var dl=desktops();',
             'for(var i=0;i<dl.length;i++){',
             '  var d=dl[i]; if(d.screen<0) continue;',
             '  var g=screenGeometry(d.screen); var img=null;']
    for (cx,cy), path in crops.items():
        lines.append(f'  if(g.x==={cx}&&g.y==={cy}) img="file://{path}";')
    lines += ['  if(!img) continue;',
              "  d.wallpaperPlugin='org.kde.image';",
              "  d.currentConfigGroup=['Wallpaper','org.kde.image','General'];",
              "  d.writeConfig('Image',img); d.writeConfig('FillMode',2);", '}']

    cmd = 'qdbus6' if shutil.which('qdbus6') else 'qdbus'
    r = subprocess.run([cmd, 'org.kde.plasmashell', '/PlasmaShell',
                        'org.kde.PlasmaShell.evaluateScript', '\n'.join(lines)],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr or r.stdout)

    if save_conf:
        ext = Path(img).suffix; sc = WALL_DIR/f'source{ext}'
        if img != str(sc): shutil.copy2(img, sc)
        LAST_CFG.write_text(f"IMAGE={shlex.quote(str(sc))}\n"
                            f"X_OFF={shlex.quote(str(ox))}\n"
                            f"Y_OFF={shlex.quote(str(oy))}\n")
        save_monitors(ms)


# ── canvas ────────────────────────────────────────────────────────────────────

def mk_state(mons, img='', ox=0, oy=0):
    return dict(monitors=copy.deepcopy(mons), img=img, ox=ox, oy=oy)

def cl(obj): return copy.deepcopy(obj)

def make_cal_image(tw, th, step):
    img = QImage(tw, th, QImage.Format.Format_RGB888)
    p = QPainter(img)
    pal = [QColor(220,50,50), QColor(50,200,50), QColor(50,100,220)]
    for row in range(math.ceil(th/step)):
        for col in range(math.ceil(tw/step)):
            p.fillRect(col*step, row*step, step, step, pal[(row+col)%3])
    p.end()
    return img


class Canvas(QWidget):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(650, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.state = mk_state([])
        self._sc = 1.0
        self._ox = self._oy = 0.0
        self._user_zoom = False
        self._pm: QPixmap | None = None
        self._cal_pm: QPixmap | None = None
        self._drag = None
        self._sel: tuple | None = None
        self.arrow_step = 1
        self.mode = MODES[0]

    def w2c(self, x, y): return QPointF(x*self._sc+self._ox, y*self._sc+self._oy)
    def c2w(self, cx, cy): return ((cx-self._ox)/self._sc, (cy-self._oy)/self._sc)
    def mr(self, m):
        tl = self.w2c(m['x'], m['y'])
        return QRectF(tl, QSizeF(m['w']*self._sc, m['h']*self._sc))

    def set_state(self, s):
        self.state = cl(s); self._refit(); self._load_pm(); self.update()

    def _refit(self):
        ms = self.state['monitors']
        if not ms: return
        min_x = min(m['x'] for m in ms); max_x = max(m['x']+m['w'] for m in ms)
        min_y = min(m['y'] for m in ms); max_y = max(m['y']+m['h'] for m in ms)
        pad = 50
        self._sc = min((self.width()-2*pad)/max(max_x-min_x,1),
                       (self.height()-2*pad)/max(max_y-min_y,1), 1.0)
        self._ox = (self.width() -(max_x-min_x)*self._sc)/2 - min_x*self._sc
        self._oy = (self.height()-(max_y-min_y)*self._sc)/2 - min_y*self._sc

    def _load_pm(self):
        p = self.state.get('img','')
        self._pm = QPixmap(p) if p and os.path.isfile(p) else None

    def set_cal(self, qimage: QImage | None):
        self._cal_pm = QPixmap.fromImage(qimage) if qimage else None
        self.update()

    def resizeEvent(self, _):
        if not self._user_zoom: self._refit()
        self.update()

    def fit_view(self):
        self._user_zoom = False; self._refit(); self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(26,26,26))
        ms = self.state['monitors']
        if not ms:
            p.setPen(QColor(120,120,120))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No monitors detected")
            return

        tx = min(m['x'] for m in ms); ty = min(m['y'] for m in ms)
        tw = max(m['x']+m['w'] for m in ms)-tx
        th = max(m['y']+m['h'] for m in ms)-ty
        pm = self._cal_pm or self._pm

        if pm and pm.width() > 0:
            tl = self.w2c(tx, ty)
            dst_r = QRectF(tl, QSizeF(tw*self._sc, th*self._sc))
            if self._cal_pm:
                src_r = QRectF(0, 0, pm.width(), pm.height())
            else:
                ox = self.state.get('ox',0); oy = self.state.get('oy',0)
                aox = abs(ox); aoy = abs(oy)
                sf = max((tw+2*aox)/pm.width(), (th+2*aoy)/pm.height())
                cx0 = (pm.width() -tw/sf)/2 + ox/sf
                cy0 = (pm.height()-th/sf)/2 + oy/sf
                src_r = QRectF(cx0, cy0, tw/sf, th/sf)
            p.setClipRect(dst_r.toRect())
            p.drawPixmap(dst_r, pm, src_r)
            p.setClipping(False)

        for i, m in enumerate(ms):
            cr = self.mr(m); col = COLORS[i % len(COLORS)]
            p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(cr)
            p.setPen(Qt.GlobalColor.white)
            f = QFont(); f.setPointSize(8); p.setFont(f)
            p.drawText(cr, Qt.AlignmentFlag.AlignCenter, f"{m['name']}\n{m['w']}×{m['h']}")
            for ci, pt in enumerate(self._corners(cr)):
                sel = self._sel == (i, ci)
                p.setBrush(QBrush(QColor(255,220,0) if sel else QColor(255,255,255,210)))
                p.setPen(QPen(QColor(255,220,0) if sel else col, 1.5))
                p.drawEllipse(pt, HANDLE_R+(2 if sel else 0), HANDLE_R+(2 if sel else 0))

    def _corners(self, cr):
        return [cr.topLeft(), cr.topRight(), cr.bottomLeft(), cr.bottomRight()]

    def _hit(self, pos):
        for i, m in enumerate(self.state['monitors']):
            for ci, pt in enumerate(self._corners(self.mr(m))):
                if math.hypot(pos.x()-pt.x(), pos.y()-pt.y()) <= HANDLE_R+3:
                    return 'corner', i, ci
        for i, m in enumerate(self.state['monitors']):
            if self.mr(m).contains(pos): return 'body', i, -1
        return 'image', -1, -1

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton: return
        self.setFocus()
        pos = e.position(); kind, mi, ci = self._hit(pos)
        wx, wy = self.c2w(pos.x(), pos.y())
        m = self.state['monitors'][mi] if mi >= 0 else None
        if kind == 'corner':
            self._sel = (mi, ci)
            self._drag = dict(kind='corner', mi=mi, ci=ci, wx0=wx, wy0=wy, m0=cl(m))
        elif kind == 'body':
            self._sel = None
            self._drag = dict(kind='body', mi=mi, wx0=wx, wy0=wy, mx0=m['x'], my0=m['y'])
        else:
            self._sel = None
            self._drag = dict(kind='image', wx0=wx, wy0=wy,
                              ox0=self.state.get('ox',0), oy0=self.state.get('oy',0))
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if not self._drag:
            kind, _, _ = self._hit(pos)
            self.setCursor({'corner': Qt.CursorShape.SizeFDiagCursor,
                            'body':   Qt.CursorShape.SizeAllCursor,
                            'image':  Qt.CursorShape.OpenHandCursor}[kind])
            return
        wx, wy = self.c2w(pos.x(), pos.y())
        d = self._drag; dx = wx-d['wx0']; dy = wy-d['wy0']
        if d['kind'] == 'image':
            self.state['ox'] = int(d['ox0']-dx); self.state['oy'] = int(d['oy0']-dy)
        elif d['kind'] == 'body':
            m = self.state['monitors'][d['mi']]
            nx, ny = self._snap(d['mi'], int(d['mx0']+dx), int(d['my0']+dy), m['w'], m['h'])
            m['x'] = nx; m['y'] = ny
        elif d['kind'] == 'corner':
            self._corner_delta(d['mi'], d['ci'], dx, dy, d['m0'], self.mode)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._drag:
            self._drag = None; self.changed.emit()

    def keyPressEvent(self, e):
        key = e.key()
        arrow_keys = {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down}
        if self._sel and key in arrow_keys:
            mi, ci = self._sel; step = self.arrow_step
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(key, 0)
            dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(key, 0)
            self._corner_delta(mi, ci, dx, dy, cl(self.state['monitors'][mi]), self.mode)
            self.update(); self.changed.emit()
        elif key in (Qt.Key.Key_F, Qt.Key.Key_0):
            self.fit_view()
        elif key == Qt.Key.Key_Escape:
            self._sel = None; self.update()
        else:
            super().keyPressEvent(e)

    def _corner_delta(self, mi, ci, dx, dy, orig, mode):
        if mode == 'Adjust Face':
            if abs(dx) >= abs(dy): dy = 0
            else: dx = 0
        if ci == 3:   nw=max(80,orig['w']+dx); nh=max(80,orig['h']+dy); nx=orig['x'];               ny=orig['y']
        elif ci == 2: nw=max(80,orig['w']-dx); nh=max(80,orig['h']+dy); nx=int(orig['x']+orig['w']-nw); ny=orig['y']
        elif ci == 1: nw=max(80,orig['w']+dx); nh=max(80,orig['h']-dy); nx=orig['x'];               ny=int(orig['y']+orig['h']-nh)
        else:         nw=max(80,orig['w']-dx); nh=max(80,orig['h']-dy); nx=int(orig['x']+orig['w']-nw); ny=int(orig['y']+orig['h']-nh)
        if mode == 'Respect Aspect Ratio':
            ar = orig.get('phys_w', orig['w']) / max(orig.get('phys_h', orig['h']), 1)
            if abs(nw-orig['w']) >= abs(nh-orig['h']): nh = max(1, int(nw/ar))
            else:                                       nw = max(1, int(nh*ar))
            if ci == 2:   nx = int(orig['x']+orig['w']-nw)
            elif ci == 1: ny = int(orig['y']+orig['h']-nh)
            elif ci == 0: nx = int(orig['x']+orig['w']-nw); ny = int(orig['y']+orig['h']-nh)
        m = self.state['monitors'][mi]
        m['w']=int(nw); m['h']=int(nh); m['x']=int(nx); m['y']=int(ny)

    def _snap(self, mi, nx, ny, w, h):
        snap = SNAP_PX/self._sc; x2 = nx+w; y2 = ny+h
        for i, o in enumerate(self.state['monitors']):
            if i == mi: continue
            for ex in (o['x'], o['x']+o['w']):
                if abs(nx-ex)<snap: nx=ex
                elif abs(x2-ex)<snap: nx=ex-w
            for ey in (o['y'], o['y']+o['h']):
                if abs(ny-ey)<snap: ny=ey
                elif abs(y2-ey)<snap: ny=ey-h
        return nx, ny


# ── main window ───────────────────────────────────────────────────────────────

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Easy Wallpaper Span")
        self.resize(1300, 740)
        self._sys_mons = read_monitors()
        self._undo: list = []; self._redo: list = []
        self._cal_active = False
        self._build_ui(); self._reset(); self._load_saved()

    def _build_ui(self):
        cw = QWidget(); self.setCentralWidget(cw)
        root = QHBoxLayout(cw); root.setSpacing(8)
        self.cv = Canvas(); self.cv.changed.connect(self._on_changed)
        root.addWidget(self.cv, 1)
        sb = QVBoxLayout(); sb.setSpacing(6); root.addLayout(sb, 0)

        # resize mode
        gm = QGroupBox("Resize mode"); fm = QVBoxLayout(gm)
        self.mode_combo = QComboBox(); self.mode_combo.addItems(MODES)
        self.mode_combo.currentTextChanged.connect(lambda t: setattr(self.cv, 'mode', t))
        fm.addWidget(self.mode_combo); sb.addWidget(gm)

        # image
        gi = QGroupBox("Image"); fi = QFormLayout(gi)
        self.img_lbl = QLabel("—"); self.img_lbl.setWordWrap(True); fi.addRow(self.img_lbl)
        bb = QPushButton("Browse…"); bb.clicked.connect(self._pick_img); fi.addRow(bb)
        self.sp_ox = QSpinBox(); self.sp_ox.setRange(-9999,9999); self.sp_ox.setPrefix("px  ")
        self.sp_oy = QSpinBox(); self.sp_oy.setRange(-9999,9999); self.sp_oy.setPrefix("px  ")
        self.sp_ox.editingFinished.connect(self._offset_edit)
        self.sp_oy.editingFinished.connect(self._offset_edit)
        fi.addRow("X offset:", self.sp_ox); fi.addRow("Y offset:", self.sp_oy)
        self.sp_step = QSpinBox(); self.sp_step.setRange(1,9999); self.sp_step.setValue(1)
        self.sp_step.setSuffix(" px")
        self.sp_step.valueChanged.connect(lambda v: setattr(self.cv, 'arrow_step', v))
        fi.addRow("Arrow step:", self.sp_step); sb.addWidget(gi)

        # profiles
        gp = QGroupBox("Profiles"); fp = QVBoxLayout(gp)
        row1 = QHBoxLayout()
        self.prof_combo = QComboBox(); self.prof_combo.setMinimumWidth(100)
        row1.addWidget(self.prof_combo, 1)
        lb = QPushButton("Load"); lb.clicked.connect(self._load_profile); row1.addWidget(lb)
        db = QPushButton("Del");  db.clicked.connect(self._delete_profile); row1.addWidget(db)
        fp.addLayout(row1)
        row2 = QHBoxLayout()
        self.prof_name = QLineEdit(); self.prof_name.setPlaceholderText("name…")
        sb_btn = QPushButton("Save"); sb_btn.clicked.connect(self._save_profile)
        row2.addWidget(self.prof_name, 1); row2.addWidget(sb_btn)
        fp.addLayout(row2); sb.addWidget(gp)
        self._refresh_profiles()

        # calibration
        gc = QGroupBox("Calibration"); fc = QFormLayout(gc)
        self.cal_chk = QCheckBox("Enable calibration overlay")
        self.cal_chk.toggled.connect(self._cal_toggled); fc.addRow(self.cal_chk)
        self.sp_cal = QSpinBox(); self.sp_cal.setRange(4,500); self.sp_cal.setValue(50)
        self.sp_cal.setSuffix(" px"); self.sp_cal.setEnabled(False)
        self.sp_cal.valueChanged.connect(self._cal_update_preview)
        fc.addRow("Square size:", self.sp_cal)
        self._cal_apply_btn = QPushButton("Apply calibration")
        self._cal_apply_btn.setEnabled(False)
        self._cal_apply_btn.clicked.connect(self._apply_cal)
        fc.addRow(self._cal_apply_btn); sb.addWidget(gc)

        sb.addStretch(1)

        br = QHBoxLayout(); br.setSpacing(4)
        for lbl, key, fn in [("Undo","Ctrl+Z",self._undo_fn),
                              ("Redo","Ctrl+Shift+Z",self._redo_fn),
                              ("Reset","",self._reset)]:
            b = QPushButton(lbl)
            if key: b.setShortcut(QKeySequence(key))
            b.clicked.connect(fn); br.addWidget(b)
        sb.addLayout(br)

        ap = QPushButton("▶  Apply")
        ap.setShortcut(QKeySequence("Ctrl+Return"))
        ap.setStyleSheet("font-size:13px;font-weight:bold;background:#1b5e1b;padding:8px;")
        ap.clicked.connect(self._apply); sb.addWidget(ap)

    # ── profiles ──────────────────────────────────────────────────────────────
    def _refresh_profiles(self):
        cur = self.prof_combo.currentText()
        self.prof_combo.clear()
        for n in list_profiles(): self.prof_combo.addItem(n)
        idx = self.prof_combo.findText(cur)
        if idx >= 0: self.prof_combo.setCurrentIndex(idx)

    def _load_profile(self):
        name = self.prof_combo.currentText()
        if not name: return
        p = load_profile(name)
        if not p: return
        self._push()
        ms = merge_monitors(self._sys_mons, p.get('monitors', []))
        img = p.get('image',''); ox = p.get('ox',0); oy = p.get('oy',0)
        self.cv.set_state(mk_state(ms, img, ox, oy))
        self.img_lbl.setText(os.path.basename(img) if img else '—')
        self._sync_spin()

    def _save_profile(self):
        name = self.prof_name.text().strip()
        if not name: return
        s = self.cv.state
        save_profile(name, s.get('img',''), s.get('ox',0), s.get('oy',0), s['monitors'])
        self.prof_name.clear(); self._refresh_profiles()
        idx = self.prof_combo.findText(name)
        if idx >= 0: self.prof_combo.setCurrentIndex(idx)

    def _delete_profile(self):
        name = self.prof_combo.currentText()
        if not name: return
        delete_profile(name); self._refresh_profiles()

    # ── calibration ───────────────────────────────────────────────────────────
    def _cal_toggled(self, on):
        self._cal_active = on
        self.sp_cal.setEnabled(on); self._cal_apply_btn.setEnabled(on)
        if on: self._cal_update_preview()
        else:  self.cv.set_cal(None); self._apply(silent=True)

    def _cal_update_preview(self):
        if not self._cal_active: return
        ms = self.cv.state['monitors']
        if not ms: return
        tx = min(m['x'] for m in ms); ty = min(m['y'] for m in ms)
        tw = max(m['x']+m['w'] for m in ms)-tx; th = max(m['y']+m['h'] for m in ms)-ty
        self.cv.set_cal(make_cal_image(tw, th, self.sp_cal.value()))

    def _apply_cal(self):
        ms = self.cv.state['monitors']
        if not ms: return
        tx = min(m['x'] for m in ms); ty = min(m['y'] for m in ms)
        tw = max(m['x']+m['w'] for m in ms)-tx; th = max(m['y']+m['h'] for m in ms)-ty
        qi = make_cal_image(tw, th, self.sp_cal.value())
        WALL_DIR.mkdir(parents=True, exist_ok=True)
        cal_src = str(WALL_DIR/'cal_source.png'); qi.save(cal_src)
        try: apply_wallpaper(cal_src, ms, 0, 0, save_conf=False)
        except RuntimeError as e: QMessageBox.critical(self, "Error", str(e))

    # ── undo / redo ──────────────────────────────────────────────────────────
    def _push(self):
        self._undo.append(cl(self.cv.state)); self._redo.clear()
        if len(self._undo) > 100: self._undo.pop(0)

    def _on_changed(self): self._push(); self._sync_spin()

    def _undo_fn(self):
        if self._undo:
            self._redo.append(cl(self.cv.state))
            self.cv.set_state(self._undo.pop()); self._sync_spin()

    def _redo_fn(self):
        if self._redo:
            self._undo.append(cl(self.cv.state))
            self.cv.set_state(self._redo.pop()); self._sync_spin()

    def _sync_spin(self):
        s = self.cv.state
        for sp, k in ((self.sp_ox,'ox'),(self.sp_oy,'oy')):
            sp.blockSignals(True); sp.setValue(s.get(k,0)); sp.blockSignals(False)

    # ── controls ──────────────────────────────────────────────────────────────
    def _reset(self):
        self._push(); prev = self.cv.state
        self.cv.set_state(mk_state(self._sys_mons, prev.get('img',''), 0, 0))
        self._sync_spin()

    def _pick_img(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select wallpaper", str(Path.home()),
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)")
        if path:
            self._push(); self.cv.state['img'] = path
            self.cv._load_pm(); self.cv.update()
            self.img_lbl.setText(os.path.basename(path))

    def _offset_edit(self):
        self._push()
        self.cv.state['ox'] = self.sp_ox.value(); self.cv.state['oy'] = self.sp_oy.value()
        self.cv.update()

    def _load_saved(self):
        ms = load_saved_monitors(self._sys_mons); prev = self.cv.state
        self.cv.set_state(mk_state(ms, prev.get('img',''), prev.get('ox',0), prev.get('oy',0)))
        if not LAST_CFG.exists(): return
        cfg = read_last_conf()
        img = cfg.get('IMAGE','')
        try: ox = int(cfg.get('X_OFF','0'))
        except ValueError: ox = 0
        try: oy = int(cfg.get('Y_OFF','0'))
        except ValueError: oy = 0
        if img and os.path.isfile(img):
            self.cv.state['img'] = img; self.cv.state['ox'] = ox; self.cv.state['oy'] = oy
            self.cv._load_pm(); self.cv.update()
            self.img_lbl.setText(os.path.basename(img)); self._sync_spin()

    def _apply(self, silent=False):
        s = self.cv.state; img = s.get('img','')
        if not img or not os.path.isfile(img):
            if not silent: QMessageBox.warning(self, "No image", "Select an image file first.")
            return
        try:
            apply_wallpaper(img, s['monitors'], s.get('ox',0), s.get('oy',0))
        except RuntimeError as e:
            QMessageBox.critical(self, "Error", str(e)); return
        if not silent: QMessageBox.information(self, "Done", "Wallpaper applied.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli_apply_profile(name):
    p = load_profile(name)
    if not p: sys.exit(f"Profile not found: {name}")
    img = p.get('image',''); ox = p.get('ox',0); oy = p.get('oy',0)
    if not img or not os.path.isfile(img): sys.exit(f"Image not found: {img}")
    ms = merge_monitors(read_monitors(), p.get('monitors', []))
    apply_wallpaper(img, ms, ox, oy)
    print(f"Applied profile: {name}")

def _cli_restore():
    if not LAST_CFG.exists(): sys.exit("No saved wallpaper found.")
    cfg = read_last_conf()
    img = cfg.get('IMAGE',''); ox = int(cfg.get('X_OFF','0')); oy = int(cfg.get('Y_OFF','0'))
    if not img or not os.path.isfile(img): sys.exit(f"Image not found: {img}")
    apply_wallpaper(img, load_saved_monitors(read_monitors()), ox, oy)

def run_cli(argv):
    """Parse and run CLI commands. Returns True if a CLI command was handled."""
    p = argparse.ArgumentParser(prog='easy-wallpaper-span',
                                description='Span a wallpaper across KDE Plasma monitors.')
    sub = p.add_subparsers(dest='cmd')

    pa = sub.add_parser('apply', help='Apply a wallpaper without opening the GUI')
    pa.add_argument('image', nargs='?', help='Image file path')
    pa.add_argument('-x', type=int, default=0, help='Horizontal offset px')
    pa.add_argument('-y', type=int, default=0, help='Vertical offset px')
    pa.add_argument('--profile', '-p', metavar='NAME', help='Apply a saved profile')

    sub.add_parser('restore', help='Re-apply last wallpaper (used by autostart)')

    sub.add_parser('profiles', help='List saved profiles')

    ps = sub.add_parser('save', help='Save current state as a named profile')
    ps.add_argument('name')

    pd = sub.add_parser('delete', help='Delete a saved profile')
    pd.add_argument('name')

    args = p.parse_args(argv)

    if args.cmd is None:
        return False  # no subcommand → launch GUI

    try:
        if args.cmd == 'restore':
            _cli_restore()
        elif args.cmd == 'profiles':
            names = list_profiles()
            print('\n'.join(names) if names else 'No profiles saved.')
        elif args.cmd == 'save':
            if not LAST_CFG.exists(): sys.exit("Apply a wallpaper first.")
            cfg = read_last_conf()
            img = cfg.get('IMAGE',''); ox = int(cfg.get('X_OFF','0')); oy = int(cfg.get('Y_OFF','0'))
            save_profile(args.name, img, ox, oy, load_saved_monitors(read_monitors()))
            print(f"Saved profile: {args.name}")
        elif args.cmd == 'delete':
            delete_profile(args.name); print(f"Deleted profile: {args.name}")
        elif args.cmd == 'apply':
            if args.profile:
                _cli_apply_profile(args.profile)
            else:
                if not args.image: sys.exit("Provide an image path or --profile NAME.")
                if not os.path.isfile(args.image): sys.exit(f"Image not found: {args.image}")
                ms = load_saved_monitors(read_monitors())
                apply_wallpaper(args.image, ms, args.x, args.y)
                print("Wallpaper applied.")
    except RuntimeError as e:
        sys.exit(str(e))

    return True


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1 and run_cli(sys.argv[1:]):
        return
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    w = App(); w.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
