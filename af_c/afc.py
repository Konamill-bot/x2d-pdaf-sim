# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""ctypes bridge to the C AF chain (build first: make). Each class mirrors a Python stage:

  CEyes      -> pdaf_sim.zone_tracker.zone_estimates   (af_phase.c)
  CTracker   -> pdaf_sim.zone_tracker.ZoneTracker      (af_track.c)
  CDualGated -> pdaf_sim.policy_dual.DualGated         (af_dual_gated.c)
  CChain     -> the whole loop in one call per frame   (af_chain.c; mode "roi": subject box)
  CWindowEyes-> phase_corr.estimate_disparity on one window (af_pc_window)
  CRoiGate   -> pdaf_sim.roi.RoiGate                   (af_roi.c)
  c_isp_fit  -> pdaf_sim.roi.IspWindow.fit             (af_roi.c)

double=True loads the double-precision build (used to compare against the references).
"""
import ctypes as C, os
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
_LIBS = {}


def _lib(double):
    if double in _LIBS:
        return _LIBS[double]
    lib = C.CDLL(os.path.join(HERE, "libafc_d.so" if double else "libafc_f.so"))
    R = C.c_double if double else C.c_float
    P = C.POINTER(R); U8 = C.POINTER(C.c_ubyte); F32 = C.POINTER(C.c_float)

    class Params(C.Structure):
        _fields_ = [("s0", R), ("p", R), ("c_floor", R), ("gate", R), ("n_confirm", C.c_int),
                    ("q_hi", R), ("q_lo", R), ("lead", R), ("pv0", R), ("sweep_step", R),
                    ("lo", R), ("hi", R), ("handover_pos", C.c_int)]

    class KF(C.Structure):
        _fields_ = [("x0", R), ("x1", R), ("p00", R), ("p01", R), ("p11", R)]

    class State(C.Structure):
        _fields_ = [("prm", Params), ("a", KF), ("s", KF), ("initialized", C.c_int), ("coasting", C.c_int),
                    ("low", C.c_int), ("dir", C.c_int), ("nrej", C.c_int), ("rsign", C.c_int)]

    class TrackParams(C.Structure):
        _fields_ = [("s0", R), ("p", R), ("c_floor", R), ("gate", R), ("m_min", C.c_int), ("k_occ", C.c_int),
                    ("occ_tol", R), ("occ_confirm", C.c_int), ("occ_window", C.c_int), ("max_hold", C.c_int)]

    class Win(C.Structure):
        _fields_ = [("y0", C.c_int), ("y1", C.c_int), ("x0", C.c_int), ("x1", C.c_int)]

    class Isp(C.Structure):
        _fields_ = [("align", C.c_int), ("min_w", C.c_int), ("min_h", C.c_int)]

    class Gate(C.Structure):
        _fields_ = [("gate", R), ("c_floor", R), ("s0", R), ("p", R), ("max_veto", C.c_int), ("n", C.c_int)]

    for name, args, res in [
        ("af_default_params", [C.POINTER(Params)], None), ("af_init", [C.POINTER(State), C.POINTER(Params)], None),
        ("af_step", [C.POINTER(State), R, R, R], R), ("af_coast", [C.POINTER(State)], R),
        ("af_predict", [C.POINTER(State), P, P], C.c_int),
        ("af_pc_sizeof", [], C.c_ulong), ("af_pc_init", [C.c_void_p, C.c_int, C.c_int], C.c_int),
        ("af_pc_zones", [C.c_void_p, F32, F32, C.c_int, C.c_int, P, P], None),
        ("af_pc_combine", [P, P, U8, C.c_int, P, P], None),
        ("af_track_sizeof", [], C.c_ulong), ("af_track_default_params", [C.POINTER(TrackParams)], None),
        ("af_track_init", [C.c_void_p, C.POINTER(TrackParams)], None),
        ("af_track_select", [C.c_void_p, P, P, C.c_int, C.c_int, R, R, U8], C.c_int),
        ("af_chain_sizeof", [], C.c_ulong),
        ("af_chain_init", [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, R], C.c_int),
        ("af_chain_frame", [C.c_void_p, F32, F32, R], R), ("af_chain_coast", [C.c_void_p], R),
        ("af_pc_window", [C.c_void_p, F32, F32, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, P, P], C.c_int),
        ("af_isp_fit", [C.POINTER(Isp), Win, C.c_int, C.c_int], Win),
        ("af_roi_gate_init", [C.POINTER(Gate)], None),
        ("af_roi_gate_check", [C.POINTER(Gate), R, R, C.c_int, R, R], C.c_int),
        ("af_chain_frame_roi", [C.c_void_p, C.c_int, R, R, R], R),
        ("af_chain_frame_box", [C.c_void_p, F32, F32, Win, R], R),
    ]:
        f = getattr(lib, name); f.argtypes = args; f.restype = res
    lib.Win, lib.Isp, lib.Gate = Win, Isp, Gate
    _LIBS[double] = (lib, R, np.float64 if double else np.float32, Params, State, TrackParams)
    return _LIBS[double]


def _ptr(a, R):
    return a.ctypes.data_as(C.POINTER(R))


class CDualGated:
    """Drop-in for pdaf_sim.policy_dual.DualGated."""
    def __init__(self, double=False, **kw):
        self._lib, self._R, self._dt, Params, State, _ = _lib(double)
        prm = Params(); self._lib.af_default_params(C.byref(prm))
        for k, v in kw.items():
            setattr(prm, k, int(v) if k in ("n_confirm", "handover_pos") else v)
        self._st = State(); self._lib.af_init(C.byref(self._st), C.byref(prm))
    def step(self, d, c, cs, lens):
        return float(self._lib.af_step(C.byref(self._st), d, c, lens))
    def coast(self):
        return float(self._lib.af_coast(C.byref(self._st)))
    def predict(self):
        x = self._R(); v = self._R()
        ok = self._lib.af_predict(C.byref(self._st), C.byref(x), C.byref(v))
        return (float(x.value), float(v.value)) if ok else None


class CEyes:
    """Callable(L, R) -> (per-zone disparity px, per-zone confidence), like zone_estimates."""
    def __init__(self, width, n_zones, max_disp_px, double=False):
        self._lib, self._R, self._dt, *_ = _lib(double)
        self._buf = C.create_string_buffer(int(self._lib.af_pc_sizeof()))
        assert self._lib.af_pc_init(self._buf, width, max_disp_px) == 0
        self.n_zones = n_zones
    def __call__(self, L, R):
        L = np.ascontiguousarray(L, dtype=np.float32); R = np.ascontiguousarray(R, dtype=np.float32)
        d = np.zeros(self.n_zones, dtype=self._dt); c = np.zeros(self.n_zones, dtype=self._dt)
        self._lib.af_pc_zones(self._buf, _ptr(L, C.c_float), _ptr(R, C.c_float), L.shape[0], self.n_zones,
                              _ptr(d, self._R), _ptr(c, self._R))
        return d.astype(np.float64), c.astype(np.float64)


class CTracker:
    """Drop-in for pdaf_sim.zone_tracker.ZoneTracker (select)."""
    def __init__(self, double=False):
        self._lib, self._R, self._dt, _, _, TrackParams = _lib(double)
        self._buf = C.create_string_buffer(int(self._lib.af_track_sizeof()))
        tp = TrackParams(); self._lib.af_track_default_params(C.byref(tp))
        self._lib.af_track_init(self._buf, C.byref(tp))
    def select(self, z_mm, conf, pred):
        z = np.ascontiguousarray(z_mm, dtype=self._dt); c = np.ascontiguousarray(conf, dtype=self._dt)
        m = np.zeros(len(z), dtype=np.uint8)
        has, x, v = (0, 0.0, 0.0) if pred is None else (1, pred[0], pred[1])
        ok = self._lib.af_track_select(self._buf, _ptr(z, self._R), _ptr(c, self._R), len(z), has, x, v,
                                       m.ctypes.data_as(C.POINTER(C.c_ubyte)))
        return m.astype(bool) if ok else None


class CChain:
    """The whole AF loop in C: frame(L, R, lens_at_exposure) -> lens command; coast().
    Mode "roi": frame_roi(has, disparity px, confidence, lens) with one window's result (from an
    ISP's PDAF block), or frame_box(L, R, (y0, y1, x0, x1), lens) to measure the window in C."""
    def __init__(self, mode, width, height, n_zones, max_disp_px, px_per_mm, double=False):
        self._lib, self._R, self._dt, *_ = _lib(double)
        self._buf = C.create_string_buffer(int(self._lib.af_chain_sizeof()))
        assert self._lib.af_chain_init(self._buf, {"afc": 0, "aft": 1, "roi": 2}[mode], width, height, n_zones,
                                       max_disp_px, px_per_mm) == 0
    def frame(self, L, R, lens):
        L = np.ascontiguousarray(L, dtype=np.float32); R = np.ascontiguousarray(R, dtype=np.float32)
        return float(self._lib.af_chain_frame(self._buf, _ptr(L, C.c_float), _ptr(R, C.c_float), lens))
    def coast(self):
        return float(self._lib.af_chain_coast(self._buf))
    def frame_roi(self, has, d, c, lens):
        return float(self._lib.af_chain_frame_roi(self._buf, int(bool(has)), d, c, lens))
    def frame_box(self, L, R, win, lens):
        L = np.ascontiguousarray(L, dtype=np.float32); R = np.ascontiguousarray(R, dtype=np.float32)
        return float(self._lib.af_chain_frame_box(self._buf, _ptr(L, C.c_float), _ptr(R, C.c_float),
                                                  self._lib.Win(*(int(v) for v in win)), lens))


class CWindowEyes:
    """Callable(L, R, (y0, y1, x0, x1)) -> (disparity px, confidence) of one window, like
    estimate_disparity(L[y0:y1, x0:x1], R[y0:y1, x0:x1]); None if C refuses the window."""
    def __init__(self, max_disp_px, width=512, double=False):
        self._lib, self._R, self._dt, *_ = _lib(double)
        self._buf = C.create_string_buffer(int(self._lib.af_pc_sizeof()))
        assert self._lib.af_pc_init(self._buf, width, max_disp_px) == 0
    def __call__(self, L, R, win):
        L = np.ascontiguousarray(L, dtype=np.float32); R = np.ascontiguousarray(R, dtype=np.float32)
        d = self._R(); c = self._R(); y0, y1, x0, x1 = (int(v) for v in win)
        ok = self._lib.af_pc_window(self._buf, _ptr(L, C.c_float), _ptr(R, C.c_float), L.shape[1],
                                    y0, y1, x0, x1, C.byref(d), C.byref(c))
        return (float(d.value), float(c.value)) if ok == 0 else None


class CRoiGate:
    """Drop-in for pdaf_sim.roi.RoiGate (check)."""
    def __init__(self, double=False):
        self._lib = _lib(double)[0]; self._g = self._lib.Gate(); self._lib.af_roi_gate_init(C.byref(self._g))
    def check(self, z, conf, pred):
        has, x, v = (0, 0.0, 0.0) if pred is None else (1, pred[0], pred[1])
        return bool(self._lib.af_roi_gate_check(C.byref(self._g), z, conf, has, x, v))


def c_isp_fit(box, width, height, align=8, min_w=48, min_h=8, double=False):
    """af_isp_fit on box (y0, y1, x0, x1) -> the window (y0, y1, x0, x1); mirrors IspWindow.fit."""
    lib = _lib(double)[0]
    w = lib.af_isp_fit(C.byref(lib.Isp(align, min_w, min_h)), lib.Win(*(int(v) for v in box)), width, height)
    return w.y0, w.y1, w.x0, w.x1
