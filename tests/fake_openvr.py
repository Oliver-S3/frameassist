"""A stand-in for the openvr module, enough to drive SteamVR.step() and the
gesture engine from tests. Mirrors the attribute names FrameAssist uses."""

import sys

# state pushed by a test before entering step():
BUTTON_STATES = {}  # button name -> bool (bState)


class _Data:
    def __init__(self, bActive=True, bState=False):
        self.bActive = bActive
        self.bState = bState


class _Input:
    def __init__(self):
        self._manifest = None
        self.update_calls = 0

    def setActionManifestPath(self, path):
        self._manifest = path

    def getActionSetHandle(self, name):
        return ("set", name)

    def getActionHandle(self, name):
        # "/actions/grip_l/in/press" -> button "grip_l"
        button = name.split("/")[2]
        return ("act", button)

    def updateActionState(self, active):
        self.update_calls += 1

    def getDigitalActionData(self, handle, dev):
        kind, button = handle
        return _Data(bState=BUTTON_STATES.get(button, False))

    def openBindingUI(self, *args):
        return True


class _System:
    def __init__(self):
        self.events = []

    def pollNextEvent(self, event):
        if self.events:
            event.eventType = self.events.pop(0)
            return True
        return False

    def acknowledgeQuit_Exiting(self):
        pass

    def getTrackedDeviceClass(self, index):
        raise ValueError("no controllers")

    def getControllerRoleForTrackedDeviceIndex(self, index):
        raise ValueError

    def getStringTrackedDeviceProperty(self, index, prop):
        raise ValueError

    def getControllerState(self, index):
        return False, None


class _Apps:
    def __init__(self):
        self.manifest = None
        self.autolaunch = None
        self.scene_pid = 0
        self.scene_key = ""

    def addApplicationManifest(self, path, replace):
        self.manifest = path

    def identifyApplication(self, pid, key):
        pass

    def setApplicationAutoLaunch(self, key, on):
        self.autolaunch = (key, on)

    def getCurrentSceneProcessId(self):
        return self.scene_pid

    def getApplicationKeyByProcessId(self, pid):
        return self.scene_key


class _Settings:
    def getBool(self, section, key):
        return False

    def setBool(self, section, key, value):
        pass


class _Overlay:
    def __init__(self):
        self.handles = set()
        self.dashboard = False

    def createOverlay(self, key, name):
        self.handles.add(key)
        return key

    def setOverlayWidthInMeters(self, *a):
        pass

    def setOverlayTransformTrackedDeviceRelative(self, *a):
        pass

    def showOverlay(self, key):
        pass

    def hideOverlay(self, key):
        pass

    def setOverlayRaw(self, *a):
        pass

    def isDashboardVisible(self):
        return self.dashboard


class _ActiveActionSet:
    def __init__(self):
        self.ulActionSet = None
        self.ulRestrictedToDevice = None
        self.nPriority = None


class _Event:
    def __init__(self):
        self.eventType = 0


class _Matrix:
    def __init__(self):
        self.m = [[0] * 4 for _ in range(3)]


class _Array:
    """A ctypes-like array: VRActiveActionSet_t * n creates an array *type*
    which, when called, instantiates n elements (like ctypes' struct arrays)."""

    def __init__(self, item_factory):
        self._item = item_factory

    def __mul__(self, n):
        return _ArrayInstance(self._item, n)

    def __call__(self):
        return self._item()


class _ArrayInstance:
    def __init__(self, item_factory, n):
        self._item = item_factory
        self._n = n

    def __call__(self):
        return [self._item() for _ in range(self._n)]


_active_array = _Array(_ActiveActionSet)


class _OpenVr:
    """Fake openvr module: intercept attribute access for the names used."""

    def __init__(self):
        self.VRApplication_Background = "Background"
        self.VRApplication_Overlay = "Overlay"
        self.k_nActionSetOverlayGlobalPriorityMin = 1
        self.k_nActionSetOverlayGlobalPriorityMax = 1000000
        self.k_ulInvalidInputValueHandle = 0
        self.k_unMaxTrackedDeviceCount = 64
        self.k_unTrackedDeviceIndex_Hmd = 0
        self.TrackedDeviceClass_Controller = 2
        self.TrackedControllerRole_LeftHand = 1
        self.TrackedControllerRole_RightHand = 2
        self.Prop_ControllerType_String = 1042
        self.VREvent_Quit = 100
        self.VREvent_Input_BindingLoadFailed = 200
        self.VREvent_Input_BindingLoadSuccessful = 201
        self.VREvent_Input_ActionManifestReloaded = 202
        self.VREvent_Input_ActionManifestLoadFailed = 203
        self.VREvent_Input_BindingsUpdated = 204
        self.VREvent_ActionBindingReloaded = 205
        self._inited = False

    def _instances(self):
        if "_objs" not in self.__dict__:
            self._objs = {
                "VRInput": _Input(),
                "VRSystem": _System(),
                "VRApplications": _Apps(),
                "VRSettings": _Settings(),
                "VROverlay": _Overlay(),
            }
        return self._objs

    def __getattr__(self, name):
        objs = self._instances()
        if name in ("VRInput", "VRSystem", "VRApplications", "VRSettings", "VROverlay"):
            return lambda: objs[name]
        if name == "VRActiveActionSet_t":
            return _active_array
        if name == "VREvent_t":
            return _Event
        if name == "HmdMatrix34_t":
            return _Matrix
        raise AttributeError(name)

    def init(self, app_type):
        self._inited = True

    def shutdown(self):
        self._inited = False


fake_openvr = _OpenVr()


def make_openvr():
    return _OpenVr()


def install():
    """Point `import openvr` at the fake for tests that load app paths."""
    sys.modules.setdefault("openvr", fake_openvr)
    return fake_openvr
