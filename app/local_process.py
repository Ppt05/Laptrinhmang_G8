"""Fast Windows process-tree cleanup without invoking taskkill on every turn."""
import ctypes
from ctypes import wintypes


def attach_job(proc):
    """Best-effort Windows job; native mode remains a debugging environment."""
    size_t = ctypes.c_size_t

    class BasicLimits(ctypes.Structure):
        _fields_ = [('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),
                    ('flags',wintypes.DWORD),('min_ws',size_t),('max_ws',size_t),
                    ('active',wintypes.DWORD),('affinity',size_t),
                    ('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name,ctypes.c_uint64) for name in
                    ('reads','writes','other','read_bytes','write_bytes','other_bytes')]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic',BasicLimits),('io',IoCounters),('process_memory',size_t),
                    ('job_memory',size_t),('peak_process',size_t),('peak_job',size_t)]

    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p,wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE,wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateJobObjectW(None,None)
    if not handle:
        return
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    process_handle = getattr(proc,'_handle',None)
    if (process_handle is not None and
        kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) and
        kernel.AssignProcessToJobObject(handle,int(process_handle))):
        proc.local_job = (kernel,handle)
    else:
        kernel.CloseHandle(handle)


def close_job(proc):
    job = getattr(proc,'local_job',None)
    if not job:
        return False
    proc.local_job = None
    kernel,handle = job
    kernel.CloseHandle(handle)
    return True
