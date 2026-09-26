"""Windows process lifetimes. Handles are never inherited by launched commands."""
import ctypes
from ctypes import wintypes as W
import subprocess
import time


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ('ReadOperationCount', 'WriteOperationCount',
        'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class BASIC_LIMIT(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
        ('LimitFlags', W.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', W.DWORD),
        ('Affinity', ctypes.c_size_t), ('PriorityClass', W.DWORD), ('SchedulingClass', W.DWORD)]


class EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', BASIC_LIMIT), ('IoInfo', IO_COUNTERS),
        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class BASIC_ACCOUNTING(ctypes.Structure):
    _fields_ = [('TotalUserTime', ctypes.c_longlong), ('TotalKernelTime', ctypes.c_longlong),
        ('ThisPeriodTotalUserTime', ctypes.c_longlong), ('ThisPeriodTotalKernelTime', ctypes.c_longlong),
        ('TotalPageFaultCount', W.DWORD), ('TotalProcesses', W.DWORD),
        ('ActiveProcesses', W.DWORD), ('TotalTerminatedProcesses', W.DWORD)]


def kernel():
    dll = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, args, result in (
        ('CreateJobObjectW', [ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
        ('SetInformationJobObject', [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD], W.BOOL),
        ('AssignProcessToJobObject', [W.HANDLE, W.HANDLE], W.BOOL),
        ('TerminateJobObject', [W.HANDLE, W.UINT], W.BOOL),
        ('QueryInformationJobObject', [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.c_void_p], W.BOOL),
        ('OpenProcess', [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
        ('CloseHandle', [W.HANDLE], W.BOOL),
        ('WaitForSingleObject', [W.HANDLE, W.DWORD], W.DWORD),
        ('GetNamedPipeClientProcessId', [W.HANDLE, ctypes.POINTER(W.ULONG)], W.BOOL),
    ):
        fn = getattr(dll, name); fn.argtypes = args; fn.restype = result
    return dll


def checked(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


class ProcessHandle:
    def __init__(self, pid, assign=False):
        self.dll = kernel()
        self.handle = checked(self.dll.OpenProcess(0x100000 | (0x101 if assign else 0), False, pid))

    def alive(self):
        return self.dll.WaitForSingleObject(self.handle, 0) == 258

    def close(self):
        if self.handle:
            self.dll.CloseHandle(self.handle); self.handle = None


class ProcessJob:
    def __init__(self):
        self.dll = kernel()
        self.handle = checked(self.dll.CreateJobObjectW(None, None))
        limits = EXTENDED_LIMIT(); limits.BasicLimitInformation.LimitFlags = 0x2000
        try:
            checked(self.dll.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except BaseException:
            self.close(); raise

    def assign(self, handle):
        checked(self.dll.AssignProcessToJobObject(self.handle, int(handle)))

    def terminate(self):
        if self.handle:
            checked(self.dll.TerminateJobObject(self.handle, 1))

    def close(self):
        if self.handle:
            self.dll.CloseHandle(self.handle); self.handle = None

    def active_processes(self):
        accounting = BASIC_ACCOUNTING()
        checked(self.dll.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None))
        return accounting.ActiveProcesses

    def wait_empty(self, interval=.05):
        while self.active_processes():
            time.sleep(interval)

    def spawn(self, argv, creationflags=subprocess.CREATE_NO_WINDOW, **kwargs):
        # Assign before user code can start children, then resume the primary thread.
        process = subprocess.Popen(argv, creationflags=0x4 | creationflags, **kwargs)
        try:
            self.assign(process._handle)
            ntdll = ctypes.WinDLL('ntdll')
            ntdll.NtResumeProcess.argtypes = [W.HANDLE]
            ntdll.NtResumeProcess.restype = ctypes.c_long
            if ntdll.NtResumeProcess(int(process._handle)) != 0:
                raise OSError('Could not resume command')
            return process
        except BaseException:
            process.kill(); process.wait(); raise


def restrict_pipe(handle):
    """Give this pipe's owner and SYSTEM access, without a public MCP credential."""
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [W.LPCWSTR, W.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.SetKernelObjectSecurity.argtypes = [W.HANDLE, W.DWORD, ctypes.c_void_p]
    descriptor = ctypes.c_void_p()
    checked(adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;GA;;;SY)(A;;GA;;;OW)', 1, ctypes.byref(descriptor), None))
    try:
        checked(adv.SetKernelObjectSecurity(handle, 4, descriptor))
    finally:
        dll = kernel(); dll.LocalFree.argtypes = [ctypes.c_void_p]; dll.LocalFree(descriptor)


def pipe_client_pid(connection):
    value = W.ULONG()
    checked(kernel().GetNamedPipeClientProcessId(connection.fileno(), ctypes.byref(value)))
    return value.value


def pipe_listener(address):
    import _winapi
    from multiprocessing.connection import PipeListener
    class PrivateListener(PipeListener):
        def _new_handle(self, first=False):
            flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED | 0x40000
            if first: flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
            handle = _winapi.CreateNamedPipe(self._address, flags,
                _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT,
                _winapi.PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, _winapi.NULL)
            try: restrict_pipe(handle)
            except BaseException:
                _winapi.CloseHandle(handle); raise
            return handle
    return PrivateListener(address, 1)
