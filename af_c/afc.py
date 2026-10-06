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
  CChain     -> the whole loop in one call per frame   (af_chain.c)

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
    ]:
        f = getattr(lib, name); f.argtypes = args; f.restype = res
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
    """The whole AF loop in C: frame(L, R, lens_at_exposure) -> lens command; coast()."""
    def __init__(self, mode, width, height, n_zones, max_disp_px, px_per_mm, double=False):
        self._lib, self._R, self._dt, *_ = _lib(double)
        self._buf = C.create_string_buffer(int(self._lib.af_chain_sizeof()))
        assert self._lib.af_chain_init(self._buf, 1 if mode == "aft" else 0, width, height, n_zones,
                                       max_disp_px, px_per_mm) == 0
    def frame(self, L, R, lens):
        L = np.ascontiguousarray(L, dtype=np.float32); R = np.ascontiguousarray(R, dtype=np.float32)
        return float(self._lib.af_chain_frame(self._buf, _ptr(L, C.c_float), _ptr(R, C.c_float), lens))
    def coast(self):
        return float(self._lib.af_chain_coast(self._buf))
