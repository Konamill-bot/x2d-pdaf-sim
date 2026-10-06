"""ctypes bridge: the C policy behind the same interface as pdaf_sim.policy_dual.DualGated.

Build the shared libraries first (make).
"""
import ctypes as C, os
HERE = os.path.dirname(os.path.abspath(__file__))

def _load(double):
    lib = C.CDLL(os.path.join(HERE, "libafdg_d.so" if double else "libafdg_f.so"))
    R = C.c_double if double else C.c_float
    class Params(C.Structure):
        _fields_ = [("s0", R), ("p", R), ("c_floor", R), ("gate", R), ("n_confirm", C.c_int),
                    ("q_hi", R), ("q_lo", R), ("lead", R), ("pv0", R), ("sweep_step", R),
                    ("lo", R), ("hi", R), ("handover_pos", C.c_int)]
    class KF(C.Structure):
        _fields_ = [("x0", R), ("x1", R), ("p00", R), ("p01", R), ("p11", R)]
    class State(C.Structure):
        _fields_ = [("prm", Params), ("a", KF), ("s", KF), ("initialized", C.c_int), ("coasting", C.c_int),
                    ("low", C.c_int), ("dir", C.c_int), ("nrej", C.c_int), ("rsign", C.c_int)]
    lib.af_default_params.argtypes = [C.POINTER(Params)]
    lib.af_init.argtypes = [C.POINTER(State), C.POINTER(Params)]
    lib.af_step.argtypes = [C.POINTER(State), R, R, R]; lib.af_step.restype = R
    lib.af_coast.argtypes = [C.POINTER(State)]; lib.af_coast.restype = R
    return lib, Params, State

_LIBS = {}

class CDualGated:
    """Drop-in for pdaf_sim.policy_dual.DualGated, backed by the C implementation."""
    def __init__(self, double=False, **kw):
        if double not in _LIBS: _LIBS[double] = _load(double)
        self._lib, Params, State = _LIBS[double]
        prm = Params(); self._lib.af_default_params(C.byref(prm))
        for k, v in kw.items(): setattr(prm, k, int(v) if k in ("n_confirm", "handover_pos") else v)
        self._st = State(); self._lib.af_init(C.byref(self._st), C.byref(prm))
    def step(self, d, c, cs, lens):
        return float(self._lib.af_step(C.byref(self._st), d, c, lens))
    def coast(self):
        return float(self._lib.af_coast(C.byref(self._st)))
